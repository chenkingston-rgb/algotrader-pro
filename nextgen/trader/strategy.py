from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import (
    ALLOWED_ASSETS,
    CASH_ASSET,
    CORE_QQQ_WEIGHT,
    DYNAMIC_SLEEVE_WEIGHT,
    MAX_TARGET_WEIGHTS,
    RISK_ASSETS,
    SMA_DAYS,
)


class SignalDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class SleeveSignal:
    symbol: str
    close: float
    sma: float
    risk_on: bool


@dataclass(frozen=True)
class Decision:
    signal_date: pd.Timestamp
    signals: tuple[SleeveSignal, ...]
    weights: dict[str, float]


def validate_closes(closes: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    missing = set(ALLOWED_ASSETS) - set(closes.columns)
    if missing:
        raise SignalDataError(f"Missing symbols: {sorted(missing)}")
    x = closes.loc[:pd.Timestamp(signal_date), list(ALLOWED_ASSETS)].copy()
    if len(x) < SMA_DAYS:
        raise SignalDataError(f"Need {SMA_DAYS} completed sessions; got {len(x)}")
    if x.index.has_duplicates or not x.index.is_monotonic_increasing:
        raise SignalDataError("Price index must be unique and increasing")
    if x.iloc[-SMA_DAYS:].isna().any().any():
        raise SignalDataError("Missing value inside the required signal window")
    if not np.isfinite(x.iloc[-SMA_DAYS:].to_numpy(float)).all():
        raise SignalDataError("Non-finite value inside the required signal window")
    if x.index[-1].normalize() != pd.Timestamp(signal_date).normalize():
        raise SignalDataError("Latest completed bar does not match the requested signal date")
    return x


def decide(closes: pd.DataFrame, signal_date: pd.Timestamp) -> Decision:
    """Causal month-end decision; never reads a row after ``signal_date``."""
    x = validate_closes(closes, signal_date)
    signals: list[SleeveSignal] = []
    weights: dict[str, float] = {s: 0.0 for s in RISK_ASSETS}
    # The permanent QQQ core is the one frozen growth overlay.  QQQ therefore
    # ranges from 20.00% when its trend sleeve is off to 46.67% when it is on.
    weights["QQQ"] = CORE_QQQ_WEIGHT
    for symbol in RISK_ASSETS:
        window = x[symbol].iloc[-SMA_DAYS:]
        last = float(window.iat[-1])
        sma = float(window.mean())
        on = last >= sma
        signals.append(SleeveSignal(symbol, last, sma, on))
        if on:
            weights[symbol] += DYNAMIC_SLEEVE_WEIGHT
    weights[CASH_ASSET] = 1.0 - sum(weights.values())
    _validate_weights(weights)
    return Decision(pd.Timestamp(signal_date), tuple(signals), weights)


def _validate_weights(weights: dict[str, float]) -> None:
    if set(weights) - set(ALLOWED_ASSETS):
        raise ValueError("Target contains an unauthorized asset")
    if any((not np.isfinite(w)) or w < -1e-12 for w in weights.values()):
        raise ValueError("Target contains an invalid weight")
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError(f"Target weights must sum to 1.0, got {sum(weights.values())}")
    if any(weights.get(s, 0.0) > MAX_TARGET_WEIGHTS[s] + 1e-9 for s in ALLOWED_ASSETS):
        raise ValueError("Target exceeds the frozen per-asset maximum")
