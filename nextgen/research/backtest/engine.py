from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable

import numpy as np
import pandas as pd


CASH = "BIL"


@dataclass(frozen=True)
class Result:
    name: str
    start: str
    end: str
    cagr: float
    vol: float
    sharpe: float
    excess_sharpe: float
    maxdd: float
    calmar: float
    turnover_ann: float
    beta_spy: float
    corr_spy: float
    final: float
    observations: int

    def record(self) -> dict:
        return asdict(self)


def _safe_ret(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
        return 1.0
    return a / b


def _validate_target(target: dict[str, float], allowed: set[str]) -> dict[str, float]:
    clean: dict[str, float] = {}
    for symbol, weight in target.items():
        if symbol not in allowed:
            raise ValueError(f"Target contains unknown/non-risk symbol {symbol}")
        w = float(weight)
        if not np.isfinite(w) or w < -1e-12:
            raise ValueError(f"Invalid target weight {symbol}={weight}")
        if w > 1e-12:
            clean[symbol] = w
    gross = sum(clean.values())
    if gross > 1.0 + 1e-9:
        raise ValueError(f"Target gross {gross:.8f} exceeds 1.0")
    return clean


def _required_ret(a: float, b: float, symbol: str, when: pd.Timestamp) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
        raise RuntimeError(f"Missing/invalid executable price for held {symbol} at {when.date()}")
    return a / b


def run_monthly(
    name: str,
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    target_fn: Callable[[int, pd.Timestamp], dict[str, float]],
    start: str,
    end: str,
    cost_bps: float = 10.0,
) -> tuple[Result, pd.Series, pd.Series]:
    idx = closes.index
    start_i = int(idx.searchsorted(pd.Timestamp(start)))
    end_i = int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    start_i = max(1, start_i)
    symbols = [c for c in closes.columns if c != CASH]
    holdings = {s: 0.0 for s in symbols}
    cash = 1.0
    allowed = set(symbols)
    # The portfolio existed before the reporting boundary.  All configured
    # periods start immediately after a prior month-end, so form the causal
    # signal from that completed close and execute on the first simulated open.
    pending: dict[str, float] | None = _validate_target(target_fn(start_i - 1, idx[start_i - 1]), allowed)
    navs: list[float] = []
    nav_dates: list[pd.Timestamp] = []
    turnovers: list[float] = []

    for i in range(start_i, end_i + 1):
        prev = i - 1
        if pending is not None:
            # Previous close -> current open under old holdings.
            for s in symbols:
                if holdings[s]:
                    holdings[s] *= _required_ret(opens[s].iat[i], closes[s].iat[prev], s, idx[i])
            cash *= _safe_ret(opens[CASH].iat[i], closes[CASH].iat[prev])
            pre = cash + sum(holdings.values())

            turnover_dollars = 0.0
            gross = sum(pending.values())
            for s in symbols:
                desired = pre * pending.get(s, 0.0)
                turnover_dollars += abs(desired - holdings[s])
            # Residual capital is an actual BIL position in this research, so
            # entering or exiting the defensive leg also pays its spread/cost.
            desired_cash = pre * (1.0 - gross)
            turnover_dollars += abs(desired_cash - cash)
            turnover = turnover_dollars / pre if pre > 0 else 0.0
            turnovers.append(turnover)
            post = pre * (1.0 - turnover * cost_bps / 10000.0)
            for s in symbols:
                holdings[s] = post * pending.get(s, 0.0)
            cash = post - sum(holdings.values())

            # Current open -> close under new holdings.
            for s in symbols:
                if holdings[s]:
                    holdings[s] *= _required_ret(closes[s].iat[i], opens[s].iat[i], s, idx[i])
            cash *= _safe_ret(closes[CASH].iat[i], opens[CASH].iat[i])
            pending = None
        else:
            for s in symbols:
                if holdings[s]:
                    holdings[s] *= _required_ret(closes[s].iat[i], closes[s].iat[prev], s, idx[i])
            cash *= _safe_ret(closes[CASH].iat[i], closes[CASH].iat[prev])

        nav = cash + sum(holdings.values())
        navs.append(nav)
        nav_dates.append(idx[i])

        if i == end_i or idx[i].to_period("M") != idx[i + 1].to_period("M"):
            pending = _validate_target(target_fn(i, idx[i]), allowed)

    nav = pd.Series(navs, index=pd.DatetimeIndex(nav_dates), name=name)
    returns = nav.pct_change()
    result = metrics(name, nav, returns, closes["SPY"].reindex(nav.index), closes[CASH].reindex(nav.index), turnovers)
    return result, nav, returns


def run_static(
    name: str,
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    weights: dict[str, float],
    start: str,
    end: str,
    cost_bps: float = 10.0,
) -> tuple[Result, pd.Series, pd.Series]:
    # Monthly rebalancing gives static portfolios a fair and deployable implementation.
    def target(_i: int, _date: pd.Timestamp) -> dict[str, float]:
        return dict(weights)
    return run_monthly(name, opens, closes, target, start, end, cost_bps)


def metrics(
    name: str,
    nav: pd.Series,
    returns: pd.Series,
    spy: pd.Series,
    cash: pd.Series,
    turnovers: list[float],
) -> Result:
    years = max((nav.index[-1] - nav.index[0]).days / 365.2425, 1 / 365.2425)
    cagr = float(nav.iat[-1] ** (1.0 / years) - 1.0)
    r = returns.dropna()
    vol = float(r.std(ddof=1) * np.sqrt(252))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(252)) if vol > 1e-12 else 0.0
    cash_ret = cash.pct_change()
    excess = (returns - cash_ret).dropna()
    excess_vol = float(excess.std(ddof=1) * np.sqrt(252))
    ex_sharpe = float(excess.mean() / excess.std(ddof=1) * np.sqrt(252)) if excess_vol > 1e-12 else 0.0
    dd = nav / nav.cummax() - 1.0
    maxdd = float(dd.min())
    calmar = cagr / abs(maxdd) if maxdd < -1e-9 else 0.0
    spy_ret = spy.pct_change()
    aligned = pd.concat([returns, spy_ret], axis=1).dropna()
    beta = float(np.cov(aligned.iloc[:, 0], aligned.iloc[:, 1], ddof=1)[0, 1] / np.var(aligned.iloc[:, 1], ddof=1))
    corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
    turn_ann = float(np.sum(turnovers) / years)
    return Result(
        name=name, start=str(nav.index[0].date()), end=str(nav.index[-1].date()),
        cagr=cagr, vol=vol, sharpe=sharpe, excess_sharpe=ex_sharpe,
        maxdd=maxdd, calmar=calmar, turnover_ann=turn_ann,
        beta_spy=beta, corr_spy=corr, final=float(nav.iat[-1]), observations=len(returns),
    )


def inverse_vol_weights(closes: pd.DataFrame, i: int, symbols: list[str], gross: float, lookback: int = 63, cap: float = 0.40) -> dict[str, float]:
    vols: dict[str, float] = {}
    for s in symbols:
        r = closes[s].iloc[max(0, i-lookback):i+1].pct_change().dropna()
        if len(r) >= max(20, lookback // 2) and r.std() > 0:
            vols[s] = float(r.std())
    if not vols:
        return {}
    inv = {s: 1.0 / v for s, v in vols.items()}
    total = sum(inv.values())
    raw = {s: gross * v / total for s, v in inv.items()}
    # Iterative cap-and-redistribute.
    out = {s: 0.0 for s in raw}
    remaining = gross
    active = set(raw)
    while active and remaining > 1e-12:
        denom = sum(inv[s] for s in active)
        changed = False
        for s in list(active):
            w = remaining * inv[s] / denom
            if w > cap:
                out[s] = cap
                remaining -= cap
                active.remove(s)
                changed = True
        if not changed:
            for s in active:
                out[s] = remaining * inv[s] / denom
            break
    return out
