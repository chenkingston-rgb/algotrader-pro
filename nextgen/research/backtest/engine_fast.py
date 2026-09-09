"""Exact but accelerated monthly simulator.

It produces the same close-to-close NAV path as ``engine.run_monthly`` while
vectorizing the days between monthly rebalances.  The slow reference engine is
retained and is used by ``verify_fast_engine.py`` as an independent oracle.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

from engine import CASH, Result, metrics, _validate_target


def _safe_ratio(a: float, b: float) -> float:
    if not np.isfinite(a) or not np.isfinite(b) or b <= 0:
        return 1.0
    return float(a / b)


def _cash_indices(opens: pd.Series, closes: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """Synthetic indices preserving the reference engine's missing-data rule."""
    n = len(closes)
    ci = np.ones(n, dtype=float)
    oi = np.ones(n, dtype=float)
    for i in range(1, n):
        oi[i] = ci[i - 1] * _safe_ratio(float(opens.iat[i]), float(closes.iat[i - 1]))
        # On non-rebalance days the reference engine applies one close/close
        # ratio, not separate overnight and intraday ratios.  This distinction
        # matters only at the first valid BIL observation after leading NaNs.
        ci[i] = ci[i - 1] * _safe_ratio(float(closes.iat[i]), float(closes.iat[i - 1]))
    return oi, ci


def run_monthly_fast(
    name: str,
    opens: pd.DataFrame,
    closes: pd.DataFrame,
    target_fn: Callable[[int, pd.Timestamp], dict[str, float]],
    start: str,
    end: str,
    cost_bps: float = 10.0,
    delay_sessions: int = 0,
) -> tuple[Result, pd.Series, pd.Series]:
    idx = closes.index
    start_i = max(1, int(idx.searchsorted(pd.Timestamp(start))))
    end_i = int(idx.searchsorted(pd.Timestamp(end), side="right")) - 1
    symbols = [c for c in closes.columns if c != CASH]
    sym_pos = {s: j for j, s in enumerate(symbols)}
    cash_open_idx, cash_close_idx = _cash_indices(opens[CASH], closes[CASH])

    nav_arr = np.full(end_i - start_i + 1, np.nan, dtype=float)
    turnovers: list[float] = []

    if delay_sessions < 0:
        raise ValueError("delay_sessions must be >= 0")
    all_signal_i = [start_i - 1] + [
        i for i in range(start_i, end_i)
        if idx[i].to_period("M") != idx[i + 1].to_period("M")
    ]
    all_exec_i = [start_i + delay_sessions] + [i + 1 + delay_sessions for i in all_signal_i[1:]]
    pairs = [(s, e) for s, e in zip(all_signal_i, all_exec_i) if e <= end_i]
    signal_i = [x[0] for x in pairs]
    exec_i = [x[1] for x in pairs]

    base_cash = cash_close_idx[start_i - 1]
    first_exec = exec_i[0] if exec_i else end_i + 1
    nav_arr[:max(0, first_exec - start_i)] = cash_close_idx[start_i:first_exec] / base_cash

    # State after the previous rebalance: dollar NAV immediately after costs,
    # weights, and the open-price indices at which those weights were entered.
    post_nav = 1.0
    old_w: dict[str, float] = {}
    old_cash_w = 1.0
    old_entry_open: dict[str, float] = {}
    old_cash_entry = base_cash

    for k, e in enumerate(exec_i):
        sidx = signal_i[k]
        target = _validate_target(target_fn(sidx, idx[sidx]), set(symbols))

        # Mark the old portfolio to this execution open.
        current = np.zeros(len(symbols), dtype=float)
        for s, w in old_w.items():
            entry = old_entry_open[s]
            op = float(opens[s].iat[e])
            if not np.isfinite(op) or not np.isfinite(entry) or entry <= 0:
                raise RuntimeError(f"Missing/invalid executable price for held {s} at {idx[e].date()}")
            current[sym_pos[s]] = post_nav * w * op / entry
        current_cash = post_nav * old_cash_w * _safe_ratio(float(cash_open_idx[e]), old_cash_entry)
        pre = float(current.sum() + current_cash)

        # Match the reference engine: cost is charged on risky-asset one-way
        # turnover.  Residual cash is a broker cash proxy rather than a BIL trade.
        turnover_dollars = 0.0
        for s in symbols:
            desired = pre * float(target.get(s, 0.0))
            turnover_dollars += abs(desired - current[sym_pos[s]])
        desired_cash = pre * (1.0 - sum(target.values()))
        turnover_dollars += abs(desired_cash - current_cash)
        turnover = turnover_dollars / pre if pre > 0 else 0.0
        turnovers.append(turnover)
        post_nav = pre * (1.0 - turnover * cost_bps / 10000.0)

        old_w = {s: float(w) for s, w in target.items() if s in sym_pos and w != 0}
        gross = float(sum(old_w.values()))
        old_cash_w = 1.0 - gross
        old_entry_open = {s: float(opens[s].iat[e]) for s in old_w}
        for s, op in old_entry_open.items():
            if not np.isfinite(op) or op <= 0:
                raise RuntimeError(f"Missing/invalid execution open for target {s} at {idx[e].date()}")
        old_cash_entry = float(cash_open_idx[e])

        next_e = exec_i[k + 1] if k + 1 < len(exec_i) else end_i + 1
        stop = min(next_e, end_i + 1)
        days = np.arange(e, stop)
        vals = np.zeros(len(days), dtype=float)
        for s, w in old_w.items():
            op = old_entry_open[s]
            px = closes[s].iloc[e:stop].to_numpy(float)
            if not np.isfinite(px).all():
                raise RuntimeError(f"Missing held close for {s} between {idx[e].date()} and {idx[stop-1].date()}")
            ratios = px / op
            vals += post_nav * w * ratios
        vals += post_nav * old_cash_w * cash_close_idx[e:stop] / old_cash_entry
        nav_arr[e - start_i:stop - start_i] = vals

    if np.isnan(nav_arr).any():
        raise RuntimeError(f"Fast engine left {int(np.isnan(nav_arr).sum())} NAV observations unset")

    nav = pd.Series(nav_arr, index=idx[start_i:end_i + 1], name=name)
    returns = nav.pct_change()
    result = metrics(name, nav, returns, closes["SPY"].reindex(nav.index), closes[CASH].reindex(nav.index), turnovers)
    return result, nav, returns
