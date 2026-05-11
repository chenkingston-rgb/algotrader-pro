"""
AlgoTrader Pro — GitHub Actions Strategy Runner  (v4)
Dual-frequency: daily bars for trend strategies, 15-min bars for mean-rev/momentum.
Writes JSON log files back to the repo so Base44 can read them via raw.githubusercontent.com.

V4 additions:
  - Section A: Engine exports (get_trading_symbols, is_market_hours, run_all_strategies)
  - Section B: 7th strategy — VWAP Breakout
  - Section C: VWAP-adjusted take-profit multiplier
  - Section D: RSI(2) position-size multiplier
  - Section E: ADX(14) regime size scaler

Environment variables (GitHub Secrets):
  ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET
  ALPACA_LIVE_KEY  / ALPACA_LIVE_SECRET
  BASE44_APP_ID    (optional, for legacy b44 calls)
  TRADING_MODE     (paper | live, default: paper)
  STRATEGY_FILTER  (optional: run only one strategy by name)
  STRATEGY_MODE    (daily | intraday, set by workflow)
  GITHUB_TOKEN     (auto-injected by GitHub Actions)
  GITHUB_REPOSITORY (auto-injected by GitHub Actions)
"""

import os, sys, json, math, time, base64, logging, requests
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import numpy as np
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
MODE            = os.getenv("TRADING_MODE", "paper").lower()
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()
STRATEGY_MODE   = os.getenv("STRATEGY_MODE", "daily").lower()   # daily | intraday
BASE44_APP_ID   = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")

GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")          # e.g. "chenkingston-rgb/algotrader-pro"

ALPACA_KEY    = os.getenv("ALPACA_PAPER_KEY")    if MODE == "paper" else os.getenv("ALPACA_LIVE_KEY")
ALPACA_SECRET = os.getenv("ALPACA_PAPER_SECRET") if MODE == "paper" else os.getenv("ALPACA_LIVE_SECRET")
ALPACA_BASE   = "https://paper-api.alpaca.markets" if MODE == "paper" else "https://api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"

RISK_PCT         = 0.01    # Risk 1% of portfolio per trade
MAX_POSITION_PCT = 0.10    # Cap any single position at 10% of portfolio
ATR_STOP_MULT    = 1.5     # Stop loss = entry ± 1.5 × ATR
ATR_TP_MULT      = 3.0     # Take profit default = entry ± 3.0 × ATR (overridden by VWAP tp_mult in v4)
MAX_DRAWDOWN_PCT = 25.0    # Kill switch threshold

ET = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────
# STRATEGY REGISTRY — DAILY (1Day bars)
# ─────────────────────────────────────────────
DAILY_STRATEGIES = {
    "rsi_macd_combo": {
        "symbols":      ["SPY", "QQQ", "IWM"],
        "vix_type":     "COMBO",
        "vix_block": 30, "vix_reduce": 22, "vix_reduce_pct": 0.50,
        "params": {"rsi_period": 14, "rsi_os": 35, "rsi_ob": 65,
                   "macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
        "timeframe": "1Day", "bar_days": 300,
    },
    "macd_crossover": {
        "symbols":  ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
        "timeframe": "1Day", "bar_days": 300,
    },
    "triple_ema": {
        "symbols":  ["SPY", "QQQ"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast": 8, "ema_mid": 21, "ema_slow": 55},
        "timeframe": "1Day", "bar_days": 300,
    },
    "ema_crossover": {
        "symbols":  ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast": 12, "ema_slow": 26},
        "timeframe": "1Day", "bar_days": 300,
    },
}

# ─────────────────────────────────────────────
# STRATEGY REGISTRY — INTRADAY (15Min bars)
# ─────────────────────────────────────────────
INTRADAY_STRATEGIES = {
    "bollinger_bands_15m": {
        "symbols":  ["SPY", "QQQ"],
        "vix_type": "MEAN_REV",
        "vix_block": 22, "vix_reduce": 18, "vix_reduce_pct": 0.40,
        "params": {"bb_period": 20, "bb_std": 2.0, "ma_filter": 50},   # 50-bar 15m MA ≈ ~12h
        "timeframe": "15Min", "bar_days": 20,
    },
    "momentum_roc_15m": {
        "symbols":  ["SPY", "QQQ", "XLK", "XLE", "XLF"],
        "vix_type": "MOMENTUM",
        "vix_block": 35, "vix_reduce": 25, "vix_reduce_pct": 0.50,
        "params": {"roc_period": 10, "roc_threshold": 0.3},             # 0.3% on 15m bars
        "timeframe": "15Min", "bar_days": 20,
    },
}

# Active registry for this run
STRATEGIES = DAILY_STRATEGIES if STRATEGY_MODE == "daily" else INTRADAY_STRATEGIES

# Log file paths in the repo
LOG_FILE = f"logs/{STRATEGY_MODE}_latest.json"
HISTORY_FILE = "logs/run_history.json"

# ─────────────────────────────────────────────
# HELPERS: GITHUB LOGGING
# ─────────────────────────────────────────────
def write_github_log(filepath: str, content_dict: dict):
    """Write a JSON file to the GitHub repo using the built-in GITHUB_TOKEN."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print(f"  [SKIP] GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping log write to {filepath}")
        return False

    content_b64 = base64.b64encode(
        json.dumps(content_dict, indent=2, default=str).encode()
    ).decode()

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type":  "application/json",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{filepath}"

    # Get current SHA (needed for update)
    get_r = requests.get(api_url, headers=headers, timeout=10)
    sha = get_r.json().get("sha") if get_r.ok else None

    payload = {
        "message": f"[bot] Update {filepath} — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        print(f"  [LOG] Wrote {filepath} to repo ✓")
        return True
    else:
        print(f"  [WARN] Failed to write {filepath}: {put_r.status_code} {put_r.text[:300]}")
        return False


def append_run_history(run_summary: dict):
    """Append a compact run summary to logs/run_history.json (capped at 200 entries)."""
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{HISTORY_FILE}"

    # Read existing history
    history = []
    get_r = requests.get(api_url, headers=headers, timeout=10)
    if get_r.ok:
        try:
            existing = json.loads(base64.b64decode(get_r.json()["content"]).decode())
            history = existing if isinstance(existing, list) else []
        except Exception:
            history = []
    sha = get_r.json().get("sha") if get_r.ok else None

    history.append(run_summary)
    history = history[-200:]   # Keep rolling window

    content_b64 = base64.b64encode(
        json.dumps(history, indent=2, default=str).encode()
    ).decode()
    payload = {
        "message": f"[bot] Append {HISTORY_FILE}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        print(f"  [LOG] Appended to {HISTORY_FILE} ({len(history)} entries) ✓")
    else:
        print(f"  [WARN] Failed to append history: {put_r.status_code} {put_r.text[:200]}")


# ─────────────────────────────────────────────
# HELPERS: ALPACA
# ─────────────────────────────────────────────
def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_bars(symbol: str, timeframe: str = "1Day", bar_days: int = 300) -> pd.DataFrame:
    """Fetch OHLCV bars from Alpaca market data with explicit date window."""
    now  = datetime.now(timezone.utc)
    start = (now - timedelta(days=bar_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "symbols":   symbol,
        "timeframe": timeframe,
        "start":     start,
        "end":       end,
        "limit":     10000,
        "feed":      "iex",
        "sort":      "asc",
    }
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars",
                     headers=alpaca_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df.set_index("t").sort_index()
    return df[["open", "high", "low", "close", "volume"]]

def get_vix() -> Optional[float]:
    """
    Estimate VIX from SPY 21-day realized annualized volatility × 1.2.
    Much more reliable than VIXY price × 10.
    """
    try:
        df = get_bars("SPY", timeframe="1Day", bar_days=60)
        if len(df) < 22:
            return None
        log_returns = np.log(df["close"] / df["close"].shift(1)).dropna()
        realized_vol = log_returns.rolling(21).std().iloc[-1] * math.sqrt(252) * 100
        vix_est = round(realized_vol * 1.2, 2)
        print(f"  VIX estimate (SPY 21d realized vol × 1.2): {vix_est:.1f}")
        return vix_est
    except Exception as e:
        print(f"  [WARN] Could not estimate VIX: {e}")
        return None

def get_positions() -> dict:
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def place_order(symbol: str, qty: int, side: str,
                stop_price: float, take_profit: float) -> dict:
    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price": str(round(stop_price, 2))},
        "take_profit":   {"limit_price": str(round(take_profit, 2))},
    }
    r = requests.post(f"{ALPACA_BASE}/v2/orders",
                      headers=alpaca_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

# ─────────────────────────────────────────────
# INDICATORS (pandas-based, for GitHub Actions main loop)
# ─────────────────────────────────────────────
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(close: pd.Series, fast=12, slow=26, sig=9):
    ema_fast    = close.ewm(span=fast, adjust=False).mean()
    ema_slow    = close.ewm(span=slow, adjust=False).mean()
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
    hist        = macd_line - signal_line
    return macd_line, signal_line, hist

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def calc_bollinger(close: pd.Series, period=20, std_mult=2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid - std_mult * std, mid, mid + std_mult * std

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
def vix_size_multiplier(strategy: dict, vix: float) -> tuple:
    if vix is None:
        return 1.0, "vix_unavailable_allow"
    if vix >= strategy["vix_block"]:
        return 0.0, f"vix_blocked ({vix:.1f} >= {strategy['vix_block']})"
    if vix >= strategy["vix_reduce"]:
        m = strategy["vix_reduce_pct"]
        return m, f"vix_reduced_to_{int(m*100)}pct ({vix:.1f} >= {strategy['vix_reduce']})"
    return 1.0, "vix_clear"

def atr_position_size(equity: float, price: float, atr: float,
                      vix_mult: float = 1.0) -> int:
    if atr <= 0 or price <= 0:
        return 0
    dollar_risk    = equity * RISK_PCT * vix_mult
    shares_by_risk = dollar_risk / (ATR_STOP_MULT * atr)
    max_by_cap     = (equity * MAX_POSITION_PCT) / price
    return max(1, int(min(shares_by_risk, max_by_cap)))

# ─────────────────────────────────────────────
# STRATEGY SIGNAL FUNCTIONS (DataFrame-based)
# ─────────────────────────────────────────────
def signal_rsi_macd_combo(df: pd.DataFrame, p: dict) -> tuple:
    rsi            = calc_rsi(df["close"], p["rsi_period"])
    macd, sig, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    r, m, s, h     = rsi.iloc[-1], macd.iloc[-1], sig.iloc[-1], hist.iloc[-1]
    prev_h         = hist.iloc[-2]
    inds = {"rsi": round(r,2), "macd": round(m,4), "macd_sig": round(s,4), "macd_hist": round(h,4)}
    if r < p["rsi_os"] and h > 0 and prev_h < 0:
        return "buy", inds
    if r > p["rsi_ob"] and h < 0 and prev_h > 0:
        return "sell", inds
    return "hold", inds

def signal_macd_crossover(df: pd.DataFrame, p: dict) -> tuple:
    macd, sig, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    h, prev_h = hist.iloc[-1], hist.iloc[-2]
    inds = {"macd": round(macd.iloc[-1],4), "macd_sig": round(sig.iloc[-1],4), "macd_hist": round(h,4)}
    if h > 0 and prev_h <= 0:
        return "buy", inds
    if h < 0 and prev_h >= 0:
        return "sell", inds
    return "hold", inds

def signal_triple_ema(df: pd.DataFrame, p: dict) -> tuple:
    c  = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    em = c.ewm(span=p["ema_mid"],  adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    f, m, s   = ef.iloc[-1], em.iloc[-1], es.iloc[-1]
    pf, pm, ps = ef.iloc[-2], em.iloc[-2], es.iloc[-2]
    inds = {"ema_fast": round(f,2), "ema_mid": round(m,2), "ema_slow": round(s,2)}
    if f > m > s and not (pf > pm > ps):
        return "buy", inds
    if f < m < s and not (pf < pm < ps):
        return "sell", inds
    return "hold", inds

def signal_ema_crossover(df: pd.DataFrame, p: dict) -> tuple:
    c    = df["close"]
    ef   = c.ewm(span=p["ema_fast"], adjust=False).mean()
    es   = c.ewm(span=p["ema_slow"], adjust=False).mean()
    diff, prev_diff = ef.iloc[-1] - es.iloc[-1], ef.iloc[-2] - es.iloc[-2]
    inds = {"ema_fast": round(ef.iloc[-1],2), "ema_slow": round(es.iloc[-1],2), "diff": round(diff,4)}
    if diff > 0 and prev_diff <= 0:
        return "buy", inds
    if diff < 0 and prev_diff >= 0:
        return "sell", inds
    return "hold", inds

def signal_bollinger_bands_15m(df: pd.DataFrame, p: dict) -> tuple:
    c = df["close"]
    lower, mid, upper = calc_bollinger(c, p["bb_period"], p["bb_std"])
    ma = c.rolling(p["ma_filter"]).mean()
    price, l, m, u, ma_v = c.iloc[-1], lower.iloc[-1], mid.iloc[-1], upper.iloc[-1], ma.iloc[-1]
    inds = {"price": round(price,2), "bb_lower": round(l,2), "bb_mid": round(m,2),
            "bb_upper": round(u,2), "ma_filter": round(ma_v,2) if not np.isnan(ma_v) else None}
    if price < l and (np.isnan(ma_v) or price > ma_v):
        return "buy", inds
    if price > m:
        return "sell", inds
    return "hold", inds

def signal_momentum_roc_15m(df: pd.DataFrame, p: dict) -> tuple:
    c = df["close"]
    roc = (c / c.shift(p["roc_period"]) - 1) * 100
    r, prev_r = roc.iloc[-1], roc.iloc[-2]
    inds = {"roc": round(r,3), "roc_prev": round(prev_r,3), "threshold": p["roc_threshold"]}
    if r > p["roc_threshold"] and prev_r <= p["roc_threshold"]:
        return "buy", inds
    if r < -p["roc_threshold"] and prev_r >= -p["roc_threshold"]:
        return "sell", inds
    return "hold", inds

SIGNAL_FNS = {
    "rsi_macd_combo":      signal_rsi_macd_combo,
    "macd_crossover":      signal_macd_crossover,
    "triple_ema":          signal_triple_ema,
    "ema_crossover":       signal_ema_crossover,
    "bollinger_bands_15m": signal_bollinger_bands_15m,
    "momentum_roc_15m":    signal_momentum_roc_15m,
}

# ═════════════════════════════════════════════
# V4 — SECTION C: VWAP TAKE-PROFIT MULTIPLIER
# ═════════════════════════════════════════════

def compute_vwap(candles: list) -> Optional[float]:
    """Compute session VWAP from a list of candle dicts (each with high/low/close/volume)."""
    cum_tpv = 0.0
    cum_vol = 0.0
    for c in candles:
        if c.get("volume", 0) <= 0:
            continue
        typical = (c["high"] + c["low"] + c["close"]) / 3.0
        cum_tpv += typical * c["volume"]
        cum_vol  += c["volume"]
    return None if cum_vol == 0 else round(cum_tpv / cum_vol, 4)


def get_tp_multiplier(entry_price: float, vwap: Optional[float]) -> float:
    """
    VWAP-distance-adjusted take-profit multiplier.
    dist = (entry_price - vwap) / vwap × 100  (positive = above VWAP)

    dist >= +1.5%  →  tp_mult = 2.0  (already extended, tighter target)
    dist  +0.5..+1.5%  →  tp_mult = 3.0  (normal)
    dist  -0.5..+0.5%  →  tp_mult = 3.5  (near VWAP, room to run)
    dist  < -0.5%  →  tp_mult = 2.0  (below VWAP, be cautious on buys)
    floor: 0.8 (never tighter than 0.8× ATR take-profit)
    """
    if vwap is None or vwap == 0:
        return ATR_TP_MULT
    dist = ((entry_price - vwap) / vwap) * 100.0
    if dist >= 1.5:
        tp = 2.0
    elif dist >= 0.5:
        tp = 3.0
    elif dist >= -0.5:
        tp = 3.5
    else:
        tp = 2.0
    return max(tp, 0.8)


# ═════════════════════════════════════════════
# V4 — SECTION D: RSI(2) POSITION-SIZE MULTIPLIER
# ═════════════════════════════════════════════

def compute_rsi_n(closes: list, period: int = 14) -> Optional[float]:
    """
    Compute RSI with Wilder smoothing using raw close list.
    Handles both period=14 (regime) and period=2 (size modifier).
    """
    if len(closes) < period + 2:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    # Wilder's initial average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)), 2)


def get_rsi2_size_multiplier(rsi2: Optional[float]) -> float:
    """
    RSI(2) position-size modifier — rewards entries at extremes,
    reduces size when price is overextended.

    RSI2  56–72   → 1.20  (strong momentum, increase size)
    RSI2  31–55   → 1.00  (neutral)
    RSI2  73–85   → 1.00  (momentum stalling, hold size)
    RSI2  >  85   → 0.85  (overbought, reduce)
    RSI2  10–30   → 0.90  (oversold pullback risk)
    RSI2  <  10   → 0.80  (extreme, smallest size)
    """
    if rsi2 is None:
        return 1.0
    if 56 <= rsi2 <= 72:
        return 1.2
    elif 31 <= rsi2 <= 55:
        return 1.0
    elif 73 <= rsi2 <= 85:
        return 1.0
    elif rsi2 > 85:
        return 0.85
    elif 10 <= rsi2 < 31:
        return 0.9
    else:
        return 0.8


# ═════════════════════════════════════════════
# V4 — SECTION E: ADX(14) REGIME SIZE SCALER
# ═════════════════════════════════════════════

def compute_adx14_spy() -> float:
    """
    Compute ADX(14) on SPY 30-minute bars using Wilder's smoothing.
    Returns 20.0 (neutral default) on any failure.
    """
    try:
        df = get_bars("SPY", timeframe="30Min", bar_days=5)
        if len(df) < 30:
            return 20.0

        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        period = 14

        # True Range
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)

        # Directional movement
        up_move   = high.diff()
        down_move = low.diff().mul(-1)
        plus_dm   = np.where((up_move > down_move) & (up_move > 0),   up_move,   0.0)
        minus_dm  = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm_s  = pd.Series(plus_dm,  index=df.index)
        minus_dm_s = pd.Series(minus_dm, index=df.index)

        # Wilder smoothing (com = period - 1)
        atr_w    = tr.ewm(com=period - 1, adjust=False).mean()
        plus_di  = 100.0 * plus_dm_s.ewm(com=period - 1,  adjust=False).mean() / atr_w.replace(0, np.nan)
        minus_di = 100.0 * minus_dm_s.ewm(com=period - 1, adjust=False).mean() / atr_w.replace(0, np.nan)

        denom = (plus_di + minus_di).replace(0, np.nan)
        dx    = 100.0 * (plus_di - minus_di).abs() / denom
        adx   = dx.ewm(com=period - 1, adjust=False).mean().iloc[-1]

        return round(float(adx), 2) if not np.isnan(adx) else 20.0
    except Exception as e:
        logging.warning(f"[ADX] compute_adx14_spy failed: {e}")
        return 20.0


def compute_spy_slope() -> float:
    """
    Linear-regression slope of SPY 30-min closes over the last 20 bars,
    normalised as % per bar of the last close price.
    Positive = upward trend, negative = downward.
    Returns 0.0 on failure.
    """
    try:
        df = get_bars("SPY", timeframe="30Min", bar_days=3)
        if len(df) < 20:
            return 0.0
        c = df["close"].values[-20:].astype(float)
        x = np.arange(len(c), dtype=float)
        slope = np.polyfit(x, c, 1)[0]
        slope_pct = (slope / c[-1]) * 100.0
        return round(float(slope_pct), 4)
    except Exception as e:
        logging.warning(f"[ADX] compute_spy_slope failed: {e}")
        return 0.0


# 30-minute ADX cache (refresh every 30 min)
_adx_cache: dict = {"adx": 20.0, "slope": 0.0, "last_updated": 0.0}


def get_cached_adx_slope() -> tuple:
    """Return (adx, slope) for SPY, refreshing at most every 30 minutes."""
    now = time.time()
    if now - _adx_cache["last_updated"] > 1800:
        try:
            adx   = compute_adx14_spy()
            slope = compute_spy_slope()
            _adx_cache.update({"adx": adx, "slope": slope, "last_updated": now})
        except Exception as e:
            logging.warning(f"[REGIME] ADX/slope refresh failed: {e}")
    return _adx_cache["adx"], _adx_cache["slope"]


def get_regime_size_multiplier(adx: float, slope: float) -> tuple:
    """
    Map ADX + slope to a position-size multiplier and label.

    TRENDING  (adx >= 23 AND slope > 0) → 1.00  (full size, trend is your friend)
    NEUTRAL   (18 <= adx < 23)          → 0.75  (moderate size)
    SIDEWAYS  (adx < 18)                → 0.50  (half size, chop risk)
    """
    if adx >= 23 and slope > 0:
        regime, mult = "TRENDING", 1.0
    elif adx < 18:
        regime, mult = "SIDEWAYS", 0.5
    else:
        regime, mult = "NEUTRAL", 0.75
    logging.info(f"[REGIME] adx={adx:.1f} slope={slope:+.3f}% → {regime} → size_mult={mult}")
    return mult, regime


# ═════════════════════════════════════════════
# V4 — SECTION A: ENGINE EXPORTS & CACHES
# ═════════════════════════════════════════════

def get_trading_symbols() -> list:
    """Symbols the WebSocket engine subscribes to."""
    return [
        "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA",
        "AMZN", "META", "GOOGL", "AMD", "PLTR", "COIN",
        "SOFI", "RIVN", "HOOD",
    ]


def is_market_hours(now_et) -> bool:
    """Return True if now_et falls within the tradeable window (9:35am–3:50pm ET, Mon–Fri)."""
    if now_et.weekday() >= 5:
        return False
    hhmm = now_et.hour * 100 + now_et.minute
    return 935 <= hhmm <= 1550


def log_engine_status(status: str):
    """Lightweight status logger for the persistent engine."""
    logging.info(f"[ENGINE_STATUS] {status}")


# 60-second account/position cache (avoids per-symbol REST calls in the streaming path)
_account_cache: dict = {
    "equity": 100000.0, "buying_power": 100000.0,
    "positions": {}, "last_updated": 0.0,
}

# 30-minute VIX cache
_vix_cache: dict = {"vix": None, "last_updated": 0.0}


def _refresh_account_cache():
    now = time.time()
    if now - _account_cache["last_updated"] > 60:
        try:
            acct = get_account()
            _account_cache["equity"]       = float(acct["equity"])
            _account_cache["buying_power"] = float(acct["buying_power"])
            _account_cache["positions"]    = get_positions()
            _account_cache["last_updated"] = now
        except Exception as e:
            logging.warning(f"[CACHE] Account refresh failed: {e}")


def _get_cached_vix() -> Optional[float]:
    now = time.time()
    if now - _vix_cache["last_updated"] > 1800:
        _vix_cache["vix"]          = get_vix()
        _vix_cache["last_updated"] = now
    return _vix_cache["vix"]


# ═════════════════════════════════════════════
# V4 — SECTION B: VWAP BREAKOUT SIGNAL (7th strategy)
# ═════════════════════════════════════════════

def signal_vwap_breakout_list(
    closes: list, highs: list, lows: list, volumes: list,
    vwap: Optional[float], p: dict
) -> tuple:
    """
    Buy : price crosses ABOVE vwap with a volume surge.
    Sell: price crosses BELOW vwap.
    p keys: vol_surge_mult (default 1.5), vol_lookback (default 20)
    """
    lookback = p.get("vol_lookback", 20)
    if vwap is None or len(closes) < lookback + 2:
        return "hold", {}

    price      = closes[-1]
    prev_price = closes[-2]

    recent_vols = volumes[-(lookback + 1):-1]
    avg_vol     = sum(recent_vols) / len(recent_vols) if recent_vols else 0
    cur_vol     = volumes[-1]
    vol_ratio   = round(cur_vol / avg_vol, 2) if avg_vol > 0 else None
    vol_surge   = (vol_ratio is not None) and (vol_ratio >= p.get("vol_surge_mult", 1.5))

    inds = {
        "price":     round(price, 2),
        "vwap":      round(vwap,  2),
        "vol_ratio": vol_ratio,
        "vol_surge": vol_surge,
    }

    if prev_price <= vwap < price and vol_surge:
        return "buy", inds
    if prev_price >= vwap > price:
        return "sell", inds
    return "hold", inds


# ═════════════════════════════════════════════
# V4 — STREAMING STRATEGY RUNNERS
# (called by run_engine.py via run_all_strategies)
# ═════════════════════════════════════════════

def _run_streaming_strategy(
    strategy_name: str,
    symbol: str,
    closes: list, highs: list, lows: list, volumes: list,
    signal: str, inds: dict, shared: dict,
    vix_block: float, vix_reduce: float, vix_reduce_pct: float,
):
    """
    Core order-placement helper for the WebSocket streaming engine.
    Applies VIX × regime × RSI2 combined size multiplier and VWAP TP multiplier.
    """
    if signal == "hold":
        return

    _refresh_account_cache()
    equity       = _account_cache["equity"]
    buying_power = _account_cache["buying_power"]
    positions    = _account_cache["positions"]

    if signal == "buy"  and symbol in positions:
        return
    if signal == "sell" and symbol not in positions:
        return

    vix     = _get_cached_vix()
    strat_mock = {"vix_block": vix_block, "vix_reduce": vix_reduce, "vix_reduce_pct": vix_reduce_pct}
    vix_mult, vix_reason = vix_size_multiplier(strat_mock, vix or 0.0)

    if vix_mult == 0.0:
        logging.info(f"[{strategy_name}] {symbol}: blocked — {vix_reason}")
        return

    df    = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    price = closes[-1]
    atr   = calc_atr(df).iloc[-1]

    # Combined multiplier: VIX × ADX-regime × RSI(2)
    size_mult = vix_mult * shared["regime_mult"] * shared["rsi2_mult"]
    qty = atr_position_size(equity, price, atr, size_mult)

    if qty < 1:
        return
    if price * qty > buying_power * 0.95:
        logging.info(f"[{strategy_name}] {symbol}: insufficient buying power for {qty} shares")
        return

    tp_mult    = shared["tp_mult"]   # VWAP-adjusted multiplier from Section C
    stop_price = (price * (1.0 - ATR_STOP_MULT * atr / price) if signal == "buy"
                  else price * (1.0 + ATR_STOP_MULT * atr / price))
    tp_price   = (price * (1.0 + tp_mult * atr / price) if signal == "buy"
                  else price * (1.0 - tp_mult * atr / price))

    try:
        order = place_order(symbol, qty, signal, stop_price, tp_price)
        logging.info(
            f"[{strategy_name}] ✓ ORDER: {signal} {qty}×{symbol} @ {price:.2f} | "
            f"stop={stop_price:.2f} tp={tp_price:.2f} tp_mult={tp_mult:.1f} | "
            f"regime={shared['regime_label']} rsi2_mult={shared['rsi2_mult']:.2f}"
        )
        _account_cache["last_updated"] = 0.0   # invalidate so next call refreshes
    except Exception as e:
        logging.error(f"[{strategy_name}] {symbol}: order failed — {e}")


def run_rsi_macd_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 60:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = DAILY_STRATEGIES["rsi_macd_combo"]
    signal, inds = signal_rsi_macd_combo(df, cfg["params"])
    _run_streaming_strategy("rsi_macd_combo", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_bollinger_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 30:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = INTRADAY_STRATEGIES["bollinger_bands_15m"]
    signal, inds = signal_bollinger_bands_15m(df, cfg["params"])
    _run_streaming_strategy("bollinger_bands_15m", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_macd_crossover_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 60:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = DAILY_STRATEGIES["macd_crossover"]
    signal, inds = signal_macd_crossover(df, cfg["params"])
    _run_streaming_strategy("macd_crossover", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_triple_ema_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 60:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = DAILY_STRATEGIES["triple_ema"]
    signal, inds = signal_triple_ema(df, cfg["params"])
    _run_streaming_strategy("triple_ema", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_ema_crossover_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 30:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = DAILY_STRATEGIES["ema_crossover"]
    signal, inds = signal_ema_crossover(df, cfg["params"])
    _run_streaming_strategy("ema_crossover", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_momentum_roc_strategy(symbol, closes, highs, lows, volumes, shared):
    if len(closes) < 15:
        return
    df     = pd.DataFrame({"close": closes, "high": highs, "low": lows, "volume": volumes})
    cfg    = INTRADAY_STRATEGIES["momentum_roc_15m"]
    signal, inds = signal_momentum_roc_15m(df, cfg["params"])
    _run_streaming_strategy("momentum_roc_15m", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, cfg["vix_block"], cfg["vix_reduce"], cfg["vix_reduce_pct"])


def run_vwap_breakout_strategy(symbol, closes, highs, lows, volumes, shared):
    """VWAP Breakout — 7th strategy (v4). Uses volume surge + VWAP cross."""
    if len(closes) < 25:
        return
    p = {"vol_surge_mult": 1.5, "vol_lookback": 20}
    signal, inds = signal_vwap_breakout_list(closes, highs, lows, volumes, shared["vwap"], p)
    # MOMENTUM risk params: vix_block=35, vix_reduce=25, vix_reduce_pct=0.50
    _run_streaming_strategy("vwap_breakout", symbol, closes, highs, lows, volumes,
                            signal, inds, shared, 35.0, 25.0, 0.50)


# ═════════════════════════════════════════════
# V4 — MASTER DISPATCHER (called by run_engine.py per-symbol per-candle)
# ═════════════════════════════════════════════

def run_all_strategies(symbol: str, candles: list) -> None:
    """
    Entry point for the persistent WebSocket engine.
    Called each time a new 5-min candle completes for `symbol`.
    `candles` is a list of dicts: [{timestamp, open, high, low, close, volume}, ...]
    """
    if len(candles) < 30:
        return

    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]

    # Compute shared context once per symbol per bar
    vwap         = compute_vwap(candles)
    rsi2_val     = compute_rsi_n(closes, period=2)
    adx_val, slope = get_cached_adx_slope()
    regime_mult, regime_label = get_regime_size_multiplier(adx_val, slope)
    rsi2_mult    = get_rsi2_size_multiplier(rsi2_val)
    tp_mult      = get_tp_multiplier(closes[-1], vwap)

    shared = {
        "vwap":         vwap,
        "rsi2":         rsi2_val,
        "regime_mult":  regime_mult,
        "regime_label": regime_label,
        "rsi2_mult":    rsi2_mult,
        "tp_mult":      tp_mult,
    }

    run_rsi_macd_strategy(symbol,     closes, highs, lows, volumes, shared)
    run_bollinger_strategy(symbol,    closes, highs, lows, volumes, shared)
    run_macd_crossover_strategy(symbol, closes, highs, lows, volumes, shared)
    run_triple_ema_strategy(symbol,   closes, highs, lows, volumes, shared)
    run_ema_crossover_strategy(symbol, closes, highs, lows, volumes, shared)
    run_momentum_roc_strategy(symbol, closes, highs, lows, volumes, shared)
    run_vwap_breakout_strategy(symbol, closes, highs, lows, volumes, shared)


# ─────────────────────────────────────────────
# MAIN EXECUTION LOOP (GitHub Actions / manual dispatch)
# ─────────────────────────────────────────────
def main():
    run_start = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"AlgoTrader Pro — {run_start.strftime('%Y-%m-%d %H:%M %Z')} [{MODE.upper()}] [{STRATEGY_MODE.upper()}]")
    print(f"{'='*60}")

    # 1. Fetch account state
    try:
        account = get_account()
    except Exception as e:
        print(f"[FATAL] Cannot reach Alpaca API: {e}")
        sys.exit(1)

    equity       = float(account["equity"])
    buying_power = float(account["buying_power"])
    print(f"Account equity: ${equity:,.2f} | Buying power: ${buying_power:,.2f}")

    # 2. Fetch VIX
    vix = get_vix()

    # 3. Fetch current positions
    positions = get_positions()
    print(f"Open positions: {list(positions.keys()) or 'none'}")

    # 4. Calculate peak equity / drawdown (best-effort from local state)
    peak_equity  = equity
    drawdown_pct = 0.0

    # 5. Run each strategy
    all_signals   = []
    orders_placed = []
    strats_to_run = {k: v for k, v in STRATEGIES.items()
                     if not STRATEGY_FILTER or k == STRATEGY_FILTER}

    for strat_name, strat_cfg in strats_to_run.items():
        print(f"\n--- {strat_name} [{strat_cfg['vix_type']}] ---")
        signal_fn  = SIGNAL_FNS[strat_name]
        p          = strat_cfg["params"]
        timeframe  = strat_cfg.get("timeframe", "1Day")
        bar_days   = strat_cfg.get("bar_days", 300)

        for symbol in strat_cfg["symbols"]:
            # Fetch bars
            try:
                df = get_bars(symbol, timeframe=timeframe, bar_days=bar_days)
                min_bars = 30 if timeframe != "1Day" else 60
                if len(df) < min_bars:
                    print(f"  {symbol}: insufficient bars ({len(df)}), skipping")
                    continue
            except Exception as e:
                print(f"  {symbol}: bar fetch error — {e}")
                continue

            price = df["close"].iloc[-1]
            atr   = calc_atr(df).iloc[-1]

            # Compute signal
            try:
                signal, inds = signal_fn(df, p)
            except Exception as e:
                print(f"  {symbol}: signal error — {e}")
                continue

            print(f"  {symbol}: signal={signal} price={price:.2f} atr={atr:.3f} | {inds}")

            # Apply VIX regime filter
            vix_mult, vix_reason = vix_size_multiplier(strat_cfg, vix or 0.0)
            executed    = False
            skip_reason = None
            order_id    = None
            qty         = 0
            stop_price  = None
            tp_price    = None

            if signal == "hold":
                skip_reason = "no_signal"
            elif vix_mult == 0.0:
                skip_reason = vix_reason
                print(f"  {symbol}: SKIPPED — {vix_reason}")
            elif signal == "buy" and symbol in positions:
                skip_reason = "already_in_position"
                print(f"  {symbol}: already holding position, skipping buy")
            elif signal == "sell" and symbol not in positions:
                skip_reason = "no_position_to_sell"
            else:
                qty = atr_position_size(equity, price, atr, vix_mult)
                if qty < 1:
                    skip_reason = "qty_too_small"
                    print(f"  {symbol}: position size rounds to 0, skipping")
                elif price * qty > buying_power * 0.95:
                    skip_reason = "insufficient_buying_power"
                    print(f"  {symbol}: not enough buying power for {qty} shares at ${price:.2f}")
                else:
                    stop_price = (price * (1 - ATR_STOP_MULT * atr / price) if signal == "buy"
                                  else price * (1 + ATR_STOP_MULT * atr / price))
                    tp_price   = (price * (1 + ATR_TP_MULT * atr / price)   if signal == "buy"
                                  else price * (1 - ATR_TP_MULT * atr / price))
                    try:
                        order    = place_order(symbol, qty, signal, stop_price, tp_price)
                        order_id = order.get("id")
                        executed = True
                        print(f"  ✓ ORDER PLACED: {signal} {qty} {symbol} @ market | "
                              f"stop={stop_price:.2f} tp={tp_price:.2f} | id={order_id}")
                        orders_placed.append({
                            "symbol": symbol, "strat": strat_name,
                            "signal": signal,  "qty": qty,
                            "price": round(price, 2), "order_id": order_id,
                        })
                    except Exception as e:
                        skip_reason = f"order_error: {e}"
                        print(f"  {symbol}: order failed — {e}")

            # Collect for log file
            all_signals.append({
                "timestamp":   run_start.isoformat(),
                "strategy":    strat_name,
                "vix_type":    strat_cfg["vix_type"],
                "symbol":      symbol,
                "signal":      signal,
                "price":       round(price, 2),
                "atr":         round(atr, 4),
                "qty":         qty,
                "stop_price":  round(stop_price, 2) if stop_price else None,
                "tp_price":    round(tp_price, 2)   if tp_price   else None,
                "executed":    executed,
                "skip_reason": skip_reason,
                "order_id":    order_id,
                "vix":         vix,
                "vix_reason":  vix_reason,
                "indicators":  inds,
            })

    # 6. Build and write the run log
    run_log = {
        "run_timestamp": run_start.isoformat(),
        "mode":          MODE,
        "strategy_mode": STRATEGY_MODE,
        "equity":        round(equity, 2),
        "buying_power":  round(buying_power, 2),
        "vix":           vix,
        "drawdown_pct":  round(drawdown_pct, 2),
        "positions":     list(positions.keys()),
        "signals":       all_signals,
        "orders_placed": orders_placed,
    }

    print(f"\n{'='*60}")
    print(f"Run complete — {len(all_signals)} signals, {len(orders_placed)} orders placed")
    print(f"{'='*60}\n")

    write_github_log(LOG_FILE, run_log)

    run_summary = {
        "timestamp":      run_start.isoformat(),
        "mode":           MODE,
        "strategy_mode":  STRATEGY_MODE,
        "equity":         round(equity, 2),
        "vix":            vix,
        "signals_count":  len(all_signals),
        "orders_count":   len(orders_placed),
        "symbols_traded": [o["symbol"] for o in orders_placed],
    }
    append_run_history(run_summary)


if __name__ == "__main__":
    main()
