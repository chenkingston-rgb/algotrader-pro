from __future__ import annotations

from datetime import datetime, timedelta, timezone

import hashlib
import json
import time

import pandas as pd
import numpy as np
import requests

from .config import ALLOWED_ASSETS, RISK_ASSETS, SMA_DAYS, STRATEGY_VERSION


def make_signal_snapshot(frame: pd.DataFrame, signal_date, source: str, captured_at: datetime) -> dict:
    """Canonical, hash-addressed 200-session input retained for audit/restart."""
    signal_date = pd.Timestamp(signal_date).normalize()
    window = frame.loc[:signal_date, list(ALLOWED_ASSETS)].iloc[-SMA_DAYS:].copy()
    if len(window) != SMA_DAYS or window.index[-1].normalize() != signal_date:
        raise RuntimeError("Cannot snapshot an incomplete signal window")
    if window.isna().any().any() or not np.isfinite(window.to_numpy(float)).all():
        raise RuntimeError("Cannot snapshot missing or non-finite signal data")
    body = {
        "source": source,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "signal_date": signal_date.date().isoformat(),
        "dates": [pd.Timestamp(x).date().isoformat() for x in window.index],
        "adjusted_closes": {s: [float(x) for x in window[s]] for s in ALLOWED_ASSETS},
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    body["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


def restore_signal_snapshot(snapshot: dict) -> pd.DataFrame:
    supplied_hash = snapshot.get("sha256")
    body = {k: v for k, v in snapshot.items() if k != "sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if supplied_hash != actual_hash:
        raise RuntimeError("Signal snapshot hash mismatch")
    frame = pd.DataFrame(body["adjusted_closes"], index=pd.to_datetime(body["dates"]))
    # Durable stores are allowed to canonicalize JSON object keys.  Validate
    # the symbol set, then restore the frozen model order explicitly instead
    # of mistaking harmless key sorting for schema corruption.
    if set(frame.columns) != set(ALLOWED_ASSETS) or len(frame) != SMA_DAYS:
        raise RuntimeError("Signal snapshot schema mismatch")
    return frame.loc[:, list(ALLOWED_ASSETS)].astype(float)


def fetch_adjusted_closes(settings, end: datetime) -> pd.DataFrame:
    """Fetch completed SIP daily bars with all corporate-action adjustments.

    Alpaca Basic permits historical SIP queries whose explicit ``end`` is at
    least 15 minutes old.  The monthly strategy passes midnight after the prior
    month-end, normally several days old.  The guard below prevents this helper
    from ever becoming an accidental paid/recent-SIP dependency.
    """
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=16)
    normalized_end = end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)
    if normalized_end > cutoff:
        raise RuntimeError("Historical SIP end must be at least 16 minutes old on Alpaca Basic")
    if settings.signal_data_feed != "sip_delayed":
        raise RuntimeError("Signal source must be delayed historical SIP")

    client = StockHistoricalDataClient(settings.api_key, settings.api_secret)
    request = StockBarsRequest(
        symbol_or_symbols=list(ALLOWED_ASSETS),
        timeframe=TimeFrame.Day,
        start=end - timedelta(days=700),
        end=end,
        adjustment=Adjustment.ALL,
        feed=DataFeed.SIP,
    )
    bars = client.get_stock_bars(request).df
    if bars.empty:
        raise RuntimeError("Alpaca returned no daily bars")
    if isinstance(bars.index, pd.MultiIndex):
        closes = bars["close"].unstack(0)
    else:
        raise RuntimeError("Unexpected Alpaca bar response shape")
    closes.index = pd.to_datetime(closes.index, utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
    closes = closes.sort_index()
    if len(closes) < SMA_DAYS:
        raise RuntimeError("Insufficient daily history")
    return closes


def assert_provider_agreement(
    primary: pd.DataFrame,
    secondary: pd.DataFrame,
    signal_date,
    tolerance_bps: float = 25.0,
) -> None:
    """Fail closed when the independent signal-data provider materially disagrees."""
    signal_date = pd.Timestamp(signal_date).normalize()
    common = [c for c in ALLOWED_ASSETS if c in primary and c in secondary]
    if set(common) != set(ALLOWED_ASSETS):
        raise RuntimeError("Secondary provider does not cover every required ETF")
    if signal_date not in primary.index or signal_date not in secondary.index:
        raise RuntimeError("Both providers must contain the exact completed signal session")
    p = primary.loc[:signal_date, common]
    s = secondary.loc[:signal_date, common]
    if len(p) < SMA_DAYS or len(s) < SMA_DAYS:
        raise RuntimeError("Provider comparison has insufficient signal history")
    if p.index.has_duplicates or s.index.has_duplicates or not p.index.is_monotonic_increasing or not s.index.is_monotonic_increasing:
        raise RuntimeError("Provider index must be unique and increasing")
    p_dates = [pd.Timestamp(x).normalize() for x in p.index[-SMA_DAYS:]]
    s_dates = [pd.Timestamp(x).normalize() for x in s.index[-SMA_DAYS:]]
    if p_dates != s_dates:
        raise RuntimeError("Providers do not contain the same 200 completed sessions")
    if p.iloc[-SMA_DAYS:].isna().any().any() or s.iloc[-SMA_DAYS:].isna().any().any():
        raise RuntimeError("Provider comparison contains a missing value in the signal window")
    if not np.isfinite(p.iloc[-SMA_DAYS:].to_numpy(float)).all() or not np.isfinite(s.iloc[-SMA_DAYS:].to_numpy(float)).all():
        raise RuntimeError("Provider comparison contains a non-finite value")
    rel_bps = ((p.loc[signal_date] / s.loc[signal_date]) - 1.0).abs() * 10000
    if (rel_bps > tolerance_bps).any():
        raise RuntimeError(f"Signal providers disagree: {rel_bps.to_dict()}")
    p_states = {symbol: bool(p[symbol].iloc[-1] >= p[symbol].iloc[-SMA_DAYS:].mean()) for symbol in RISK_ASSETS}
    s_states = {symbol: bool(s[symbol].iloc[-1] >= s[symbol].iloc[-SMA_DAYS:].mean()) for symbol in RISK_ASSETS}
    if p_states != s_states:
        raise RuntimeError(f"Signal providers produce different trend states: primary={p_states}, secondary={s_states}")


def assert_exchange_sessions(frame: pd.DataFrame, expected_dates, signal_date) -> None:
    expected = [pd.Timestamp(x).normalize() for x in expected_dates][-SMA_DAYS:]
    observed = [pd.Timestamp(x).normalize() for x in frame.loc[:pd.Timestamp(signal_date)].index[-SMA_DAYS:]]
    if len(expected) != SMA_DAYS:
        raise RuntimeError("Market calendar did not provide 200 completed sessions")
    if observed != expected:
        missing = sorted({x.date().isoformat() for x in expected} - {x.date().isoformat() for x in observed})
        extra = sorted({x.date().isoformat() for x in observed} - {x.date().isoformat() for x in expected})
        raise RuntimeError(f"Signal bars do not match the exchange calendar; missing={missing}, extra={extra}")


def fetch_yahoo_adjusted_closes(end: datetime) -> pd.DataFrame:
    """Independent signal cross-check; never used as an execution quote source."""
    frames = {}
    period2 = int((end.astimezone(timezone.utc) if end.tzinfo else end.replace(tzinfo=timezone.utc)).timestamp())
    period1 = period2 - 700 * 86400
    for symbol in ALLOWED_ASSETS:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
        last = None
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers={"User-Agent": STRATEGY_VERSION},
                    timeout=20,
                    allow_redirects=False,
                )
                response.raise_for_status()
                payload = response.json()["chart"]["result"][0]
                dates = pd.to_datetime(payload["timestamp"], unit="s", utc=True).tz_convert("America/New_York").normalize().tz_localize(None)
                values = payload["indicators"]["adjclose"][0]["adjclose"]
                frames[symbol] = pd.Series(values, index=dates, dtype=float)
                break
            except Exception as exc:
                last = exc
                time.sleep(1 + attempt)
        else:
            raise RuntimeError(f"Secondary provider failed for {symbol}: {last}")
    return pd.DataFrame(frames).sort_index()
