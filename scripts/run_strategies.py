"""
AlgoTrader Pro — GitHub Actions Strategy Runner  (v6)
Dual-frequency: daily bars for trend strategies, 15-min bars for mean-rev/momentum.
Writes JSON log files back to the repo so Base44 can read them via raw.githubusercontent.com.

V6 fixes:
  - FIX 1: Bollinger exit raised from bb_mid to bb_upper (no more premature exits)
  - FIX 2: Momentum ROC entry changed from zero-crossing to sustained-above logic
  - FIX 3: Bollinger buy filter: require price ABOVE 50-MA (not just != NaN)
  - FIX 4: daily.yml cron re-enabled (handled separately in workflow file)
  - FIX 5: scan_symbols.py: ADX filter split — mean-rev candidates use ADX < 22
  - FIX 6: scan_daily.py: pre-market start uses ET-localised datetime (no DST bug)

Environment variables (GitHub Secrets):
  ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET
  ALPACA_LIVE_KEY  / ALPACA_LIVE_SECRET
  ALPACA_IS_PAPER  (true | false, default: true)
  BASE44_APP_ID    (optional)
  ALPACA_IS_PAPER  ("true" | "false") — sole source of truth for paper/live mode
  STRATEGY_FILTER  (optional: run only one strategy by name)
  STRATEGY_MODE    (daily | intraday, set by workflow)
  GITHUB_TOKEN     (auto-injected by GitHub Actions)
  GITHUB_REPOSITORY (auto-injected by GitHub Actions)
"""

import os, sys, json, math, base64, logging, requests
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import numpy as np
import pytz

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
IS_PAPER        = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
# MODE derives from IS_PAPER — always matches the actual broker in use.
# Previously set from TRADING_MODE env var, which could diverge from IS_PAPER
# and cause misleading "mode=paper" log entries during live runs.
MODE            = "paper" if IS_PAPER else "live"
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()
STRATEGY_MODE   = os.getenv("STRATEGY_MODE", "daily").lower()
BASE44_APP_ID   = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")

GITHUB_TOKEN      = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")

# Paper/live key split — never use ALPACA_API_KEY / ALPACA_API_SECRET
if IS_PAPER:
    ALPACA_KEY    = os.environ.get("ALPACA_PAPER_KEY")
    ALPACA_SECRET = os.environ.get("ALPACA_PAPER_SECRET")
    ALPACA_BASE   = "https://paper-api.alpaca.markets"
else:
    ALPACA_KEY    = os.environ.get("ALPACA_LIVE_KEY")
    ALPACA_SECRET = os.environ.get("ALPACA_LIVE_SECRET")
    ALPACA_BASE   = "https://api.alpaca.markets"

ALPACA_DATA = "https://data.alpaca.markets"

RISK_PCT         = 0.01    # Risk 1% of portfolio per trade
MAX_POSITION_PCT = 0.10    # Cap any single position at 10% of portfolio
ATR_STOP_MULT    = 1.5     # Stop loss = entry ± 1.5 × ATR
ATR_TP_MULT      = 3.0     # Take profit default (overridden by VWAP tp_mult)
MAX_DRAWDOWN_PCT = 25.0    # Kill switch threshold

# ── PDT GUARD retired 2026-06-05 — FINRA rule removed, buying_power is now sole constraint

# ── KILL SWITCH ───────────────────────────────────────────────────────────────
# Set once per run in main() after computing trailing drawdown.
# Streaming strategies read this module-level flag to block new buys.
# TO DISABLE: set MAX_DRAWDOWN_PCT higher (e.g. 50.0) or env DISABLE_KILL_SWITCH=true.
_kill_switch_active: bool = False
_ma20_bear_block:   bool = False   # True when SPY < 20-day MA — blocks new BUY entries (v7.1)

ET = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────
# STRATEGY REGISTRY — DAILY (1Day bars)
# ─────────────────────────────────────────────
DAILY_STRATEGIES = {
    "rsi_macd_combo": {
        "symbols":      ["SPY", "QQQ", "IWM"],
        "vix_type":     "COMBO",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
        "params": {"rsi_period": 14, "rsi_os": 35, "rsi_ob": 65,
                   "macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
        "timeframe": "1Day", "bar_days": 300,
    },
    "macd_crossover": {
        "symbols":  ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
        "params": {"macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
        "timeframe": "1Day", "bar_days": 300,
    },
    "triple_ema": {
        "symbols":  ["SPY", "QQQ"],
        "vix_type": "TREND",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
        "params": {"ema_fast": 8, "ema_mid": 21, "ema_slow": 55},
        "timeframe": "1Day", "bar_days": 300,
    },
    "ema_crossover": {
        "symbols":  ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
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
        "vix_block": 25, "vix_reduce": 18, "vix_reduce_pct": 0.40,
        "params": {"bb_period": 20, "bb_std": 2.0, "ma_filter": 50},
        "timeframe": "15Min", "bar_days": 20,
    },
    "momentum_roc_15m": {
        "symbols":  ["SPY", "QQQ", "XLK", "XLE", "XLF"],
        "vix_type": "MOMENTUM",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
        "params": {"roc_period": 10, "roc_threshold": 0.3},
        "timeframe": "15Min", "bar_days": 20,
    },
}

STRATEGIES            = DAILY_STRATEGIES if STRATEGY_MODE == "daily" else INTRADAY_STRATEGIES
LOG_FILE              = f"logs/{STRATEGY_MODE}_latest.json"
HISTORY_FILE          = "logs/run_history.json"
SIGNALS_HISTORY_FILE  = "logs/signals_history.json"
MAX_SIGNALS_HISTORY   = 500   # rolling window kept in repo

WATCHLIST_WEEKLY_FILE = "logs/watchlist_weekly.json"
WATCHLIST_DAILY_FILE  = "logs/watchlist_daily.json"

# ─────────────────────────────────────────────
# DYNAMIC SYMBOL LOADING FROM WATCHLISTS
# ─────────────────────────────────────────────
def load_dynamic_symbols() -> list:
    """
    Fetch the appropriate watchlist for the current STRATEGY_MODE from GitHub.
    - intraday mode → watchlist_daily.json  (daily pre-market picks + 7 core)
    - daily mode    → watchlist_weekly.json (weekly scan top 25 + 7 core)
    Falls back to None if the file is missing or the fetch fails.
    """
    repo = GITHUB_REPOSITORY or "chenkingston-rgb/algotrader-pro"
    filename = WATCHLIST_DAILY_FILE if STRATEGY_MODE == "intraday" else WATCHLIST_WEEKLY_FILE
    url = (
        f"https://raw.githubusercontent.com/{repo}/main/{filename}"
        f"?t={int(_time.time())}"
    )
    try:
        r = requests.get(url, timeout=10)
        if r.ok:
            data = r.json()
            symbols = data.get("symbols", [])
            if symbols:
                logging.info(
                    f"[WATCHLIST] Loaded {len(symbols)} symbols from {filename}: {symbols}"
                )
                return symbols
        logging.warning(f"[WATCHLIST] {filename} fetch returned {r.status_code}")
    except Exception as e:
        logging.warning(f"[WATCHLIST] Error fetching {filename}: {e}")
    return []


# ─────────────────────────────────────────────
# HELPERS: GITHUB LOGGING
# ─────────────────────────────────────────────
def write_github_log(filepath: str, content_dict: dict):
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
    get_r = requests.get(api_url, headers=headers, timeout=10)
    sha = get_r.json().get("sha") if get_r.ok else None
    payload = {
        "message": f"[bot] Update {filepath} — {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} [skip render]",
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
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{HISTORY_FILE}"
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
    history = history[-200:]
    content_b64 = base64.b64encode(
        json.dumps(history, indent=2, default=str).encode()
    ).decode()
    payload = {"message": f"[bot] Append {HISTORY_FILE} [skip render]", "content": content_b64}
    if sha:
        payload["sha"] = sha
    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        print(f"  [LOG] Appended to {HISTORY_FILE} ({len(history)} entries) ✓")
    else:
        print(f"  [WARN] Failed to append history: {put_r.status_code} {put_r.text[:200]}")


def append_signals_history(new_signals: list):
    """
    Append this run's signals to logs/signals_history.json (rolling MAX_SIGNALS_HISTORY entries).
    Each entry keeps: timestamp, strategy, symbol, signal, price, qty, executed, skip_reason,
    order_id, vix, stop_price, tp_price, indicators.
    Skips write if new_signals is empty.
    """
    if not new_signals:
        return
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{SIGNALS_HISTORY_FILE}"
    history = []
    get_r = requests.get(api_url, headers=headers, timeout=10)
    if get_r.ok:
        try:
            existing = json.loads(base64.b64decode(get_r.json()["content"]).decode())
            history = existing if isinstance(existing, list) else []
        except Exception:
            history = []
    sha = get_r.json().get("sha") if get_r.ok else None

    # Normalise each signal to a compact dashboard-friendly record
    for sig in new_signals:
        history.append({
            "timestamp":    sig.get("timestamp"),
            "strategy":     sig.get("strategy"),
            "strategy_mode": STRATEGY_MODE,
            "symbol":       sig.get("symbol"),
            "signal":       sig.get("signal"),
            "price":        sig.get("price"),
            "qty":          sig.get("qty"),
            "stop_price":   sig.get("stop_price"),
            "tp_price":     sig.get("tp_price"),
            "executed":     sig.get("executed"),
            "skip_reason":  sig.get("skip_reason"),
            "order_id":     sig.get("order_id"),
            "vix":          sig.get("vix"),
            "atr":          sig.get("atr"),
            "indicators":   sig.get("indicators", {}),
        })

    history = history[-MAX_SIGNALS_HISTORY:]
    content_b64 = base64.b64encode(
        json.dumps(history, indent=2, default=str).encode()
    ).decode()
    payload = {
        "message": (
            f"[bot] Signal history — "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        ),
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        print(f"  [LOG] Appended {len(new_signals)} signal(s) to {SIGNALS_HISTORY_FILE} ({len(history)} total) ✓")
    else:
        print(f"  [WARN] Failed to append signals history: {put_r.status_code} {put_r.text[:200]}")


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
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=bar_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "symbols": symbol, "timeframe": timeframe,
        "start": start, "end": end,
        "limit": 10000, "feed": "iex", "sort": "asc",
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

# ── SPY MA20 REGIME FILTER ────────────────────────────────────────────────────
# Added v7.1 (2026-06-09): blocks ALL new BUY entries when SPY is below its
# 20-day moving average — indicating a broad market downtrend.
#
# This is the primary market regime gate. It fires before VIX checks.
# Logic: if SPY's latest daily close < mean of last 20 daily closes → BEAR.
#
# To disable for testing: set env DISABLE_MA20_FILTER=true
#
# Returns: (is_bull: bool, spy_close: float, ma20: float)
def get_spy_ma20_regime() -> tuple:
    """
    Returns (is_bull, spy_close, ma20).
    is_bull = True  → SPY above 20-day MA, entries permitted.
    is_bull = False → SPY below 20-day MA, all new BUY entries blocked.
    Falls back to True (permissive) if data unavailable — never silently halts.
    """
    if os.getenv("DISABLE_MA20_FILTER", "false").lower() == "true":
        return True, 0.0, 0.0
    try:
        df = get_bars("SPY", timeframe="1Day", bar_days=40)
        if df.empty or len(df) < 20:
            logging.warning("[MA20] Insufficient SPY bars — skipping regime filter")
            return True, 0.0, 0.0
        spy_close = float(df["close"].iloc[-1])
        ma20      = float(df["close"].iloc[-20:].mean())
        is_bull   = spy_close >= ma20
        gap_pct   = (spy_close - ma20) / ma20 * 100
        label     = "BULL" if is_bull else "BEAR"
        print(f"  [MA20_REGIME] SPY close=${spy_close:.2f}  MA20=${ma20:.2f}  "
              f"gap={gap_pct:+.2f}%  → {label}")
        return is_bull, spy_close, ma20
    except Exception as e:
        logging.warning(f"[MA20] Regime check failed: {e} — defaulting to BULL (permissive)")
        return True, 0.0, 0.0



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


def close_position_order(symbol: str, qty: int) -> dict:
    """Close an existing long position, cancelling any open stop/limit orders for
    the symbol first so all shares are freed up before the market close is placed."""
    # Step 1: cancel any open orders holding shares for this symbol
    open_orders_r = requests.get(
        f"{ALPACA_BASE}/v2/orders",
        headers=alpaca_headers(),
        params={"status": "open", "symbols": symbol, "limit": 50},
        timeout=10,
    )
    if open_orders_r.ok:
        for order in open_orders_r.json():
            oid = order.get("id")
            if oid:
                requests.delete(
                    f"{ALPACA_BASE}/v2/orders/{oid}",
                    headers=alpaca_headers(),
                    timeout=10,
                )
                print(f"  [CLOSE] Cancelled open order {oid} ({order.get('type','?')} {order.get('side','?')}) for {symbol}")
    import time as _t; _t.sleep(0.5)   # brief pause so cancels settle

    # Step 2: close the position
    r = requests.delete(
        f"{ALPACA_BASE}/v2/positions/{symbol}",
        headers=alpaca_headers(),
        params={"percentage": "100"},
        timeout=10,
    )
    if r.status_code == 404:
        raise ValueError(f"No open position found for {symbol}")
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
    rsi             = calc_rsi(df["close"], p["rsi_period"])
    macd, sig, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    r, m, s, h      = rsi.iloc[-1], macd.iloc[-1], sig.iloc[-1], hist.iloc[-1]
    prev_h          = hist.iloc[-2]
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
    c   = df["close"]
    ef  = c.ewm(span=p["ema_fast"], adjust=False).mean()
    em  = c.ewm(span=p["ema_mid"],  adjust=False).mean()
    es  = c.ewm(span=p["ema_slow"], adjust=False).mean()
    f, m, s    = ef.iloc[-1], em.iloc[-1], es.iloc[-1]
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
    # FIX 1: Entry — price must be BELOW lower band AND ABOVE 50-MA (trend filter)
    #         The original allowed entry when ma_v was NaN (insufficient data).
    #         Now we require a valid, confirmed uptrend filter before buying.
    ma_valid = not np.isnan(ma_v)
    if price < l and ma_valid and price > ma_v:
        return "buy", inds
    # FIX 2: Exit — raise from bb_mid to bb_upper so winners run to full mean-reversion
    #         Old logic sold the moment price crossed the midband, cutting all profit.
    if price > u:
        return "sell", inds
    return "hold", inds

def signal_momentum_roc_15m(df: pd.DataFrame, p: dict) -> tuple:
    c = df["close"]
    roc = (c / c.shift(p["roc_period"]) - 1) * 100
    r, prev_r = roc.iloc[-1], roc.iloc[-2]
    inds = {"roc": round(r,3), "roc_prev": round(prev_r,3), "threshold": p["roc_threshold"]}
    # FIX 3: Changed from zero-crossing to sustained-momentum logic.
    # Old: required prev_r to be BELOW threshold (zero-crossing). On volatile watchlist
    #      names (RKLB, IONQ, AMD) ROC stays above threshold across bars — crossing was
    #      never re-triggered, missing the best continuation entries.
    # New: fire buy if ROC currently above threshold AND was accelerating (r > prev_r).
    #      This catches sustained momentum moves, not just the initial cross.
    #      Sell fires if ROC is below negative threshold AND decelerating (r < prev_r).
    if r > p["roc_threshold"] and r > prev_r:
        return "buy", inds
    if r < -p["roc_threshold"] and r < prev_r:
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

# ══════════════════════════════════════════════
# V5 — SECTION C: VWAP TAKE-PROFIT MULTIPLIER
# ══════════════════════════════════════════════

def compute_vwap(candles: list) -> Optional[float]:
    """Intraday VWAP from candle list. Returns None if no valid volume."""
    cum_tpv = cum_vol = 0.0
    for c in candles:
        if c.get("volume", 0) <= 0:
            continue
        typical  = (c["high"] + c["low"] + c["close"]) / 3.0
        cum_tpv += typical * c["volume"]
        cum_vol  += c["volume"]
    return None if cum_vol == 0 else round(cum_tpv / cum_vol, 4)


def get_tp_multiplier(entry_price: float, vwap: Optional[float]) -> float:
    """
    Dynamic ATR take-profit multiplier based on distance from VWAP.
    Returns 3.0 (default) if VWAP unavailable. Stop loss is NEVER affected.
    Floor: 0.8 — prevents spread from instantly triggering TP.
    """
    if vwap is None or vwap == 0:
        return ATR_TP_MULT
    dist_pct = ((entry_price - vwap) / vwap) * 100.0
    if dist_pct >= 1.5:
        tp = 2.0
    elif dist_pct >= 0.5:
        tp = 3.0
    elif dist_pct >= -0.5:
        tp = 3.5
    else:
        tp = 2.0
    return max(tp, 0.8)


# ══════════════════════════════════════════════
# V5 — SECTION D: RSI(2) POSITION-SIZE MULTIPLIER
# ══════════════════════════════════════════════

def compute_rsi_n(closes: list, period: int = 14) -> Optional[float]:
    """
    Generic RSI with Wilder smoothing. Works for any period (14, 2, etc.).
    Returns None if insufficient data (need period + 2 bars minimum).
    """
    if len(closes) < period + 2:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100.0 - (100.0 / (1.0 + avg_g / avg_l)), 2)


def get_rsi2_size_multiplier(rsi2: Optional[float]) -> float:
    """
    Position size modifier based on RSI(2). Range: 0.8–1.2.
    Never blocks a trade — minimum 0.8 means trade always happens.
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


# ══════════════════════════════════════════════
# V5 — SECTION E: ADX(14) REGIME SIZE SCALER
# Uses StockHistoricalDataClient from alpaca-py >= 0.30.0
# ══════════════════════════════════════════════

def compute_adx14_spy(rest_client) -> float:
    """
    ADX(14) on SPY 30-min bars using Wilder's directional movement method.
    Returns 20.0 (neutral default) on ANY failure — never raises.
    rest_client: StockHistoricalDataClient instance.
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        now = datetime.now(timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols="SPY",
            timeframe=TimeFrame.Minute * 30,
            start=now - timedelta(hours=8),
            end=now,
        )
        df = rest_client.get_stock_bars(req).df
        if len(df) < 15:
            return 20.0

        highs  = df["high"].values.tolist()
        lows   = df["low"].values.tolist()
        closes = df["close"].values.tolist()

        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(highs)):
            tr   = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            up   = highs[i]  - highs[i-1]
            down = lows[i-1] - lows[i]
            tr_list.append(tr)
            plus_dm.append(up   if up > down   and up > 0   else 0.0)
            minus_dm.append(down if down > up   and down > 0 else 0.0)

        def wilder_smooth(data, n=14):
            s = [sum(data[:n])]
            for v in data[n:]:
                s.append(s[-1] - s[-1] / n + v)
            return s

        atr14  = wilder_smooth(tr_list)
        plus14 = wilder_smooth(plus_dm)
        minus14 = wilder_smooth(minus_dm)

        dx = []
        for a, p, m in zip(atr14, plus14, minus14):
            if a == 0:
                continue
            pd_ = 100.0 * p / a
            md_ = 100.0 * m / a
            denom = pd_ + md_
            dx.append(0.0 if denom == 0 else 100.0 * abs(pd_ - md_) / denom)

        return round(wilder_smooth(dx)[-1], 2) if len(dx) >= 14 else 20.0

    except Exception as e:
        logging.warning(f"[REGIME] ADX failed: {e}")
        return 20.0


def compute_spy_slope(rest_client) -> float:
    """
    5-period EMA slope on SPY 30-min bars, normalised as % change over 4 bars.
    Returns 0.0 on any failure.
    rest_client: StockHistoricalDataClient instance.
    """
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        now = datetime.now(timezone.utc)
        req = StockBarsRequest(
            symbol_or_symbols="SPY",
            timeframe=TimeFrame.Minute * 30,
            start=now - timedelta(hours=6),
            end=now,
        )
        closes = rest_client.get_stock_bars(req).df["close"].values.tolist()
        if len(closes) < 6:
            return 0.0

        k   = 2.0 / 6.0
        ema = closes[0]
        ema_series = [ema]
        for c in closes[1:]:
            ema = c * k + ema * (1.0 - k)
            ema_series.append(ema)

        if ema_series[-4] == 0:
            return 0.0
        return round(((ema_series[-1] - ema_series[-4]) / ema_series[-4]) * 100.0, 4)

    except Exception:
        return 0.0


# ADX cache — refresh every 30 minutes, uses StockHistoricalDataClient
_adx_cache: dict = {"adx": 20.0, "slope": 0.0, "ts": 0.0}

# Singleton StockHistoricalDataClient — created once, reused for ADX refreshes
_hist_client = None

def _get_hist_client():
    """Return a cached StockHistoricalDataClient, creating it only once."""
    global _hist_client
    if _hist_client is None:
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            _is_paper = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
            key    = os.environ["ALPACA_PAPER_KEY"]    if _is_paper else os.environ["ALPACA_LIVE_KEY"]
            secret = os.environ["ALPACA_PAPER_SECRET"] if _is_paper else os.environ["ALPACA_LIVE_SECRET"]
            _hist_client = StockHistoricalDataClient(key, secret)
            logging.info("[REGIME] StockHistoricalDataClient created (singleton)")
        except Exception as e:
            logging.warning(f"[REGIME] Could not create hist client: {e}")
    return _hist_client


def get_cached_adx_slope() -> tuple:
    """
    Returns (adx, slope) for SPY. Refreshes at most every 30 minutes.
    Falls back to cached values on any failure — never raises.
    """
    now = _time.time()
    if now - _adx_cache["ts"] > 1800:
        try:
            client = _get_hist_client()
            if client is not None:
                adx   = compute_adx14_spy(client)
                slope = compute_spy_slope(client)
            _adx_cache.update({"adx": adx, "slope": slope, "ts": now})
        except Exception as e:
            logging.warning(f"[REGIME] ADX refresh failed: {e} — using cached")
    return _adx_cache["adx"], _adx_cache["slope"]


def get_regime_size_multiplier(adx: float, slope: float) -> tuple:
    """
    Returns (multiplier, label). Minimum 0.5 — always trades.
    Thresholds are loose by design to avoid 'always neutral' trap.
    """
    if adx >= 23 and slope > 0:
        regime, mult = "TRENDING", 1.0
    elif adx < 18:
        regime, mult = "SIDEWAYS", 0.5
    else:
        regime, mult = "NEUTRAL", 0.75
    logging.info(f"[REGIME] adx={adx:.1f} slope={slope:+.3f}% → {regime} → mult={mult}")
    return mult, regime


# ══════════════════════════════════════════════
# V5 — SECTION A: ENGINE EXPORTS
# ══════════════════════════════════════════════

def get_trading_symbols() -> list:
    """
    Returns symbols for the WebSocket engine to subscribe to.
    Reads from logs/watchlist_weekly.json via GitHub raw on startup
    (populated by the weekly symbol scanner workflow).
    Falls back to a curated core + static list if unavailable.
    Core symbols (SPY, QQQ, IWM, GLD, XLK, XLE, XLF) are ALWAYS included —
    IWM is required by the daily strategies (rsi_macd_combo, macd_crossover, ema_crossover).
    """
    CORE = ["SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF"]
    FALLBACK_EXTRAS = [
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META",
        "GOOGL", "AMD", "PLTR", "JPM", "BAC", "XOM", "CVX",
    ]

    # Try loading dynamic weekly watchlist from GitHub raw
    try:
        import requests as _req
        repo = os.environ.get("GITHUB_REPOSITORY", "chenkingston-rgb/algotrader-pro")
        url  = f"https://raw.githubusercontent.com/{repo}/main/logs/watchlist_weekly.json"
        r = _req.get(url, timeout=5)
        if r.ok:
            symbols = r.json().get("symbols", [])
            if len(symbols) >= 5:
                combined = list(dict.fromkeys(CORE + symbols))
                logging.info(f"[SYMBOLS] Loaded {len(combined)} symbols from weekly watchlist")
                return combined
    except Exception as e:
        logging.warning(f"[SYMBOLS] Could not load weekly watchlist: {e} — using fallback")

    fallback = list(dict.fromkeys(CORE + FALLBACK_EXTRAS))
    logging.info(f"[SYMBOLS] Using fallback static list ({len(fallback)} symbols)")
    return fallback


def is_market_hours(now_et) -> bool:
    """True if now_et is within the engine active window (9:35am–3:50pm ET, Mon–Fri)."""
    if now_et.weekday() >= 5:
        return False
    hhmm = now_et.hour * 100 + now_et.minute
    return 935 <= hhmm <= 1550


def log_engine_status(status: str):
    """Status log for the persistent engine."""
    logging.info(f"[ENGINE_STATUS] {status}")


# ─────────────────────────────────────────────
# V5 — HELPER FUNCTIONS for streaming strategies
# (aliases and list-based equivalents of existing pandas helpers)
# ─────────────────────────────────────────────

def get_current_vix() -> Optional[float]:
    """Alias for get_vix() — used by run_vwap_breakout_strategy."""
    return get_vix()


def has_open_position(symbol: str) -> bool:
    """True if there is an open position in `symbol`."""
    try:
        return symbol in get_positions()
    except Exception:
        return False


def compute_atr(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """
    List-based ATR using Wilder's smoothing.
    Returns None if insufficient data.
    """
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        )
        trs.append(tr)
    if not trs:
        return None
    atr_val = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return round(atr_val, 4)


def compute_position_size(entry_price: float, stop_price: float,
                          risk_pct: float = 0.01) -> int:
    """
    Shares based on account equity × risk_pct / per-share risk.
    Capped at MAX_POSITION_PCT of equity. Returns 0 on failure.
    """
    try:
        acct        = get_account()
        equity      = float(acct["equity"])
        dollar_risk = equity * risk_pct
        per_share   = abs(entry_price - stop_price)
        if per_share <= 0:
            return 0
        shares_by_risk = dollar_risk / per_share
        max_by_cap     = (equity * MAX_POSITION_PCT) / entry_price
        return max(1, int(min(shares_by_risk, max_by_cap)))
    except Exception:
        return 0


def place_bracket_order(symbol: str, qty: int, side: str,
                        stop_price: float, target_price: float,
                        strategy_tag: str = "") -> dict:
    """Wrapper around place_order for named bracket orders from streaming strategies."""
    logging.info(f"[ORDER] {strategy_tag} {side} {qty}×{symbol} "
                 f"stop={stop_price:.2f} target={target_price:.2f}")
    return place_order(symbol, qty, side, stop_price, target_price)


# ─────────────────────────────────────────────
# V5 — SECTION A: ACCOUNT/VIX CACHES for streaming
# ─────────────────────────────────────────────
_account_cache: dict = {
    "equity": 100000.0, "buying_power": 100000.0,
    "positions": {}, "last_updated": 0.0,
}
_vix_cache: dict = {"vix": None, "last_updated": 0.0}


def _refresh_account_cache():
    now = _time.time()
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
    now = _time.time()
    if now - _vix_cache["last_updated"] > 1800:
        _vix_cache["vix"]          = get_vix()
        _vix_cache["last_updated"] = now
    return _vix_cache["vix"]


# ══════════════════════════════════════════════
# V5 — SECTION B: VWAP BREAKOUT (7th strategy)
# Academic basis: Zarattini, Aziz & Barbon (SFI, 2024)
# 5-condition entry, 9:45–11:30 ET window only
# ══════════════════════════════════════════════

def run_vwap_breakout_strategy(
    symbol: str,
    closes: list, highs: list, lows: list, volumes: list,
    shared: dict,
) -> None:
    """
    Strategy 7: VWAP Breakout Momentum
    Trades fresh VWAP crossovers with volume confirmation during the
    institutional order flow window (9:45–11:30 ET only).
    VIX block: > 35. Uses shared VWAP, RSI2, regime, and TP multipliers.
    All 5 entry conditions must pass — no trade otherwise.
    """
    logger = logging.getLogger(__name__)

    # ── Time gate: 9:45–11:30 ET only ─────────────────────────────────────
    now_et = datetime.now(ET)
    hhmm   = now_et.hour * 100 + now_et.minute
    if not (945 <= hhmm <= 1130):
        return

    if len(closes) < 20 or len(volumes) < 20:
        return

    # ── VIX gate (> 35 blocks) ─────────────────────────────────────────────
    vix = get_current_vix()
    if vix is not None and vix > 35:
        return

    # ── No existing position ───────────────────────────────────────────────
    if has_open_position(symbol):
        return

    entry_price = closes[-1]
    vwap        = shared.get("vwap")

    # ── Condition 1: price above VWAP ──────────────────────────────────────
    if vwap is None or entry_price <= vwap:
        return

    # ── Condition 2: fresh breakout (previous close was AT or BELOW VWAP)
    if len(closes) >= 2 and closes[-2] > vwap:
        return   # Already extended above VWAP — not a fresh cross

    # ── Condition 3: volume spike (>= 1.6× 20-bar average) ────────────────
    avg_vol_20 = sum(volumes[-20:]) / 20
    if volumes[-1] < avg_vol_20 * 1.6:
        logger.info(f"[VWAP_BO] {symbol} — volume {volumes[-1]:.0f} < 1.6× avg ({avg_vol_20:.0f})")
        return

    # ── Condition 4: RSI(14) in momentum zone (52–73) ──────────────────────
    rsi14 = compute_rsi_n(closes, period=14)
    if rsi14 is None or not (52 <= rsi14 <= 73):
        logger.info(f"[VWAP_BO] {symbol} — RSI14={rsi14} outside 52–73 window")
        return

    # ── Condition 5: ATR minimum (>= 0.25) ────────────────────────────────
    atr = compute_atr(highs, lows, closes, period=14)
    if atr is None or atr < 0.25:
        return

    # ── All 5 conditions passed — compute order ────────────────────────────
    vwap_dist_pct = ((entry_price - vwap) / vwap) * 100.0
    tp_mult       = shared["tp_mult"]
    rsi2_mult     = shared["rsi2_mult"]
    regime_mult   = shared["regime_mult"]

    stop_price   = entry_price - (ATR_STOP_MULT * atr)           # stop unchanged (1.5×ATR)
    target_price = entry_price + (tp_mult * atr)                 # TP uses shared VWAP mult
    target_price = max(target_price, entry_price + (0.5 * atr))  # floor at 0.5×ATR

    base_shares  = compute_position_size(entry_price, stop_price, risk_pct=RISK_PCT)
    final_shares = max(1, int(base_shares * rsi2_mult * regime_mult))

    logger.info(
        f"[VWAP_BO] ENTRY {symbol} | price={entry_price:.2f} | vwap={vwap:.2f} "
        f"| dist={vwap_dist_pct:.2f}% | rsi14={rsi14:.1f} "
        f"| vol_ratio={volumes[-1]/avg_vol_20:.1f}x "
        f"| tp_mult={tp_mult:.1f} | shares={final_shares} "
        f"| stop={stop_price:.2f} | target={target_price:.2f} "
        f"| regime={shared['regime_label']}"
    )

    place_bracket_order(
        symbol       = symbol,
        qty          = final_shares,
        side         = "buy",
        stop_price   = round(stop_price,   2),
        target_price = round(target_price, 2),
        strategy_tag = "VWAP_BREAKOUT",
    )


# ─────────────────────────────────────────────
# V5 — STREAMING STRATEGY RUNNERS (called from run_all_strategies)
# Generic helper: applies VIX × regime × RSI2 combined size mult + VWAP TP
# ─────────────────────────────────────────────

def _run_streaming_strategy(
    strategy_name: str,
    symbol: str,
    closes: list, highs: list, lows: list, volumes: list,
    signal: str, inds: dict, shared: dict,
    vix_block: float, vix_reduce: float, vix_reduce_pct: float,
):
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

    # Use list-based ATR to avoid creating a pandas DataFrame on every cycle
    price = closes[-1]
    atr   = compute_atr(highs, lows, closes)
    if atr is None:
        logging.warning(f"[{strategy_name}] {symbol}: insufficient data for ATR, skipping")
        return

    # Combined: VIX × regime × RSI(2)
    size_mult = vix_mult * shared["regime_mult"] * shared["rsi2_mult"]
    qty = atr_position_size(equity, price, atr, size_mult)

    if qty < 1:
        return
    if price * qty > buying_power * 0.95:
        logging.info(f"[{strategy_name}] {symbol}: insufficient buying power for {qty} shares")
        return

    if signal == "buy":
        # Kill switch — block all new entries when trailing drawdown >= threshold
        if _kill_switch_active:
            logging.warning(
                f"[KILL_SWITCH] {symbol} BUY skipped — "
                f"drawdown >= {MAX_DRAWDOWN_PCT}% threshold"
            )
            return
        # PDT guard retired Jun 2026 — buying_power is the sole entry constraint
        tp_mult    = shared["tp_mult"]
        # FIX 4: Enforce a minimum stop distance of 0.20% of price.
        # 15-min ATR on low-volatility windows (e.g. GLD at market open) can
        # be severely compressed, producing stop/TP distances of <$1 on $400 stocks.
        # 0.20% floor = ~$0.83 on GLD, ~$1.50 on SPY — sensible minimums.
        min_atr = price * 0.0020
        eff_atr = max(atr, min_atr)
        stop_price = price - (ATR_STOP_MULT * eff_atr)
        tp_price   = price + (tp_mult * eff_atr)
        try:
            place_order(symbol, qty, "buy", stop_price, tp_price)
            logging.info(
                f"[{strategy_name}] ✓ BUY {qty}×{symbol} @ {price:.2f} | "
                f"stop={stop_price:.2f} tp={tp_price:.2f} tp_mult={tp_mult:.1f} | "
                f"regime={shared['regime_label']} rsi2_mult={shared['rsi2_mult']:.2f}"
            )
            _account_cache["last_updated"] = 0.0   # force refresh after trade
        except Exception as e:
            logging.error(f"[{strategy_name}] {symbol}: buy order failed — {e}")
    else:
        # SELL: plain market close — bracket orders rejected on position closes
        pos_qty = int(float(positions.get(symbol, {}).get("qty", 0)))
        close_qty = pos_qty if pos_qty > 0 else qty
        try:
            close_position_order(symbol, close_qty)
            logging.info(
                f"[{strategy_name}] ✓ SELL {close_qty}×{symbol} @ {price:.2f} (close) | "
                f"regime={shared['regime_label']}"
            )
            _account_cache["last_updated"] = 0.0   # force refresh after trade
        except Exception as e:
            logging.error(f"[{strategy_name}] {symbol}: sell order failed — {e}")


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


# ══════════════════════════════════════════════
# V5 — MASTER DISPATCHER (called by run_engine.py per-symbol per-5-min-candle)
# ══════════════════════════════════════════════

def run_all_strategies(symbol: str, candles: list) -> None:
    """
    Entry point for the persistent WebSocket engine.
    Called each time a new 5-min candle completes for `symbol`.
    `candles`: list of dicts — keys: timestamp, open, high, low, close, volume
               sorted oldest → newest.
    """
    logger = logging.getLogger(__name__)

    if len(candles) < 30:
        return

    closes  = [c["close"]  for c in candles]
    highs   = [c["high"]   for c in candles]
    lows    = [c["low"]    for c in candles]
    volumes = [c["volume"] for c in candles]

    vwap           = compute_vwap(candles)
    rsi2_val       = compute_rsi_n(closes, period=2)
    adx, slope     = get_cached_adx_slope()
    regime_mult, regime_label = get_regime_size_multiplier(adx, slope)
    rsi2_mult      = get_rsi2_size_multiplier(rsi2_val)
    tp_mult        = get_tp_multiplier(closes[-1], vwap)

    shared = {
        "vwap":         vwap,
        "rsi2":         rsi2_val,
        "regime_mult":  regime_mult,
        "regime_label": regime_label,
        "rsi2_mult":    rsi2_mult,
        "tp_mult":      tp_mult,
    }

    logger.info(
        f"[CYCLE] {symbol} | regime={regime_label} | "
        f"size_mult={regime_mult} | rsi2={rsi2_val} | vwap={vwap}"
    )

    run_rsi_macd_strategy(symbol,       closes, highs, lows, volumes, shared)
    run_bollinger_strategy(symbol,      closes, highs, lows, volumes, shared)
    run_macd_crossover_strategy(symbol, closes, highs, lows, volumes, shared)
    run_triple_ema_strategy(symbol,     closes, highs, lows, volumes, shared)
    run_ema_crossover_strategy(symbol,  closes, highs, lows, volumes, shared)
    run_momentum_roc_strategy(symbol,   closes, highs, lows, volumes, shared)
    run_vwap_breakout_strategy(symbol,  closes, highs, lows, volumes, shared)


# ─────────────────────────────────────────────
# MAIN EXECUTION LOOP (GitHub Actions / manual workflow_dispatch)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# EOD EXIT + DEPOSIT DETECTION + MAIN (v7)
# ─────────────────────────────────────────────
INTRADAY_STRATEGY_NAMES = set(INTRADAY_STRATEGIES.keys())
EOD_EXIT_HOUR  = 15
EOD_EXIT_MIN   = 30
EOD_TAG_FILE   = "logs/intraday_position_tags.json"
LIVE_BASELINE_FILE = "logs/live_baseline.json"


def load_json_from_github(filepath: str) -> dict:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return {}
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{filepath}",
        headers=headers, timeout=10
    )
    if r.ok:
        try:
            return json.loads(base64.b64decode(r.json()["content"]).decode())
        except Exception:
            return {}
    return {}


def run_eod_exit(positions, position_tags, all_signals, orders_placed, run_start):
    """Force-close all intraday-tagged positions at/after 3:30 PM ET."""
    now_et  = datetime.now(ET)
    if STRATEGY_MODE != "intraday":
        return position_tags
    cutoff = now_et.replace(hour=EOD_EXIT_HOUR, minute=EOD_EXIT_MIN, second=0, microsecond=0)
    if now_et < cutoff:
        return position_tags
    print(f"\n[EOD EXIT] {now_et.strftime('%H:%M %Z')} — forcing close of intraday-tagged positions.")
    for sym in list(position_tags.keys()):
        tag = position_tags[sym]
        if tag.get("strategy_type") != "intraday":
            continue
        if sym not in positions:
            del position_tags[sym]
            continue
        pos   = positions[sym]
        qty   = int(float(pos.get("qty", 0)))
        price = float(pos.get("current_price", 0))
        upl   = float(pos.get("unrealized_pl", 0))
        uplpc = float(pos.get("unrealized_plpc", 0)) * 100
        print(f"  [EOD] Closing {sym} qty={qty}  unreal=${upl:+.2f} ({uplpc:+.2f}%)")
        try:
            order    = close_position_order(sym, qty)
            order_id = order.get("id")
            orders_placed.append({
                "symbol": sym, "strat": "eod_exit", "strategy_type": "intraday",
                "signal": "eod_exit", "side": "sell", "qty": qty,
                "price": round(price, 2), "est_value": round(price * qty, 2),
                "stop_price": None, "tp_price": None,
                "order_id": order_id, "timestamp": run_start.isoformat(), "eod_exit": True,
            })
            all_signals.append({
                "timestamp": run_start.isoformat(), "strategy": "eod_exit",
                "strategy_type": "intraday", "vix_type": "EOD", "symbol": sym,
                "signal": "eod_exit", "price": round(price, 2), "atr": 0, "qty": qty,
                "stop_price": None, "tp_price": None, "executed": True,
                "skip_reason": None, "order_id": order_id, "vix": None,
                "vix_reason": "eod_forced_exit",
                "indicators": {"unrealized_pl": round(upl, 2),
                               "unrealized_plpc": round(uplpc, 2),
                               "entry_strategy": tag.get("strategy")},
            })
            print(f"  ✓ EOD CLOSED {sym}  order_id={order_id}")
            del position_tags[sym]
        except Exception as e:
            print(f"  [WARN] EOD exit failed for {sym}: {e}")
    return position_tags


def detect_deposit(current_equity: float, baseline: dict) -> dict:
    """
    Detects cash deposits by comparing current equity to last known equity.
    A jump > $500 AND > 1% in one engine cycle is flagged as a deposit.
    Normal trading P&L is tracked separately.
    """
    if not baseline:
        baseline = {
            "start_equity":      current_equity,
            "last_known_equity": current_equity,
            "total_deposited":   0.0,
            "total_trading_pnl": 0.0,
            "deposits":          [],
            "initialized_at":    datetime.now(ET).isoformat(),
        }
        print(f"  [LIVE BASELINE] Initialized at ${current_equity:,.2f}")
        write_github_log(LIVE_BASELINE_FILE, baseline)
        return baseline

    last_eq   = baseline.get("last_known_equity", current_equity)
    delta     = current_equity - last_eq
    delta_pct = (delta / last_eq * 100) if last_eq > 0 else 0

    if delta > 500 and delta_pct > 1.0:
        # Deposit detected
        dep = {
            "timestamp":     datetime.now(ET).isoformat(),
            "amount":        round(delta, 2),
            "equity_before": round(last_eq, 2),
            "equity_after":  round(current_equity, 2),
        }
        baseline.setdefault("deposits", []).append(dep)
        baseline["total_deposited"] = round(baseline.get("total_deposited", 0) + delta, 2)
        print(f"  [DEPOSIT] ${delta:,.2f} deposit detected "
              f"(${last_eq:,.2f} → ${current_equity:,.2f})")
    else:
        baseline["total_trading_pnl"] = round(
            baseline.get("total_trading_pnl", 0) + delta, 2
        )

    baseline["last_known_equity"] = round(current_equity, 2)

    # Trailing high-watermark — peak_equity only moves up, never down.
    # The 25% kill switch is always measured from this rolling peak.
    prev_peak = baseline.get("peak_equity", baseline.get("start_equity", current_equity))
    if current_equity > prev_peak:
        baseline["peak_equity"] = round(current_equity, 2)
        print(f"  [WATERMARK] New peak equity: ${current_equity:,.2f}")
    else:
        baseline["peak_equity"] = round(prev_peak, 2)

    write_github_log(LIVE_BASELINE_FILE, baseline)
    return baseline



# check_pdt_limit() — retired 2026-06-05 (FINRA PDT rule removed)
# Stub retained for call-site compat. Safe to delete along with its callers.
def check_pdt_limit() -> tuple[bool, int]:
    """Retired stub — always returns (True, 0). PDT rule no longer applies."""
    return True, 0


def main():
    global _kill_switch_active, _ma20_bear_block
    run_start    = datetime.now(ET)
    print(f"\n{'='*60}")
    print(f"AlgoTrader Pro v7 — {run_start.strftime('%Y-%m-%d %H:%M %Z')} "
          f"[{'LIVE' if not IS_PAPER else 'PAPER'}] [{STRATEGY_MODE.upper()}]")
    print(f"{'='*60}")

    try:
        account = get_account()
    except Exception as e:
        print(f"[FATAL] Cannot reach Alpaca API: {e}")
        sys.exit(1)

    equity       = float(account["equity"])
    buying_power = float(account["buying_power"])
    last_equity  = float(account.get("last_equity", equity))
    print(f"Equity: ${equity:,.2f} | Buying power: ${buying_power:,.2f}")

    # ── PDT guard retired 2026-06-05 ────────────────────────────────────────
    pdt_ok, pdt_count = True, 0   # stubs; safe to remove with check_pdt_limit()

    vix = get_vix()

    # ── SPY MA20 REGIME GATE (v7.1) ─────────────────────────────────────────
    # Core rule: if SPY is below its 20-day moving average the broad market is
    # in a downtrend. New BUY entries have negative expectancy in this regime.
    # We block ALL new buys. Existing positions keep their stops and can still sell.
    spy_is_bull, spy_close_now, spy_ma20_now = get_spy_ma20_regime()
    _ma20_bear_block = not spy_is_bull
    if _ma20_bear_block:
        print(f"[MA20_REGIME] ⚠️  BEAR MARKET — SPY ${spy_close_now:.2f} < MA20 ${spy_ma20_now:.2f}. "
              f"All new BUY entries blocked. Sells/stops still active.")
    
    # Fresh positions on every loop start (Rule: never stale)
    positions     = get_positions()
    position_tags = load_json_from_github(EOD_TAG_FILE)

    # Live mode: deposit detection
    live_baseline = {}
    if not IS_PAPER:
        live_baseline = load_json_from_github(LIVE_BASELINE_FILE)
        live_baseline = detect_deposit(equity, live_baseline)

    # ── Trailing drawdown (live) / per-run reset (paper) ────────────────────
    # Live: peak_equity persisted in live_baseline.json — survives across runs.
    # Paper: resets to current equity each run (paper mode has no persisted state).
    if not IS_PAPER and live_baseline:
        peak_equity  = float(live_baseline.get("peak_equity",
                             live_baseline.get("start_equity", equity)))
    else:
        peak_equity  = equity  # paper mode: no trailing watermark
    drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0
    _kill_switch_active = (
        os.getenv("DISABLE_KILL_SWITCH", "false").lower() != "true"
        and drawdown_pct >= MAX_DRAWDOWN_PCT
    )
    if _kill_switch_active:
        print(f"[KILL_SWITCH] ⚠️  Drawdown {drawdown_pct:.2f}% >= {MAX_DRAWDOWN_PCT}% threshold. "
              f"Peak=${peak_equity:,.2f}  Current=${equity:,.2f}. "
              f"All new BUY entries blocked. Existing stops remain active.")
    else:
        print(f"[DRAWDOWN] {drawdown_pct:.2f}% from peak ${peak_equity:,.2f} "
              f"(threshold: {MAX_DRAWDOWN_PCT}%)")

    all_signals   = []
    orders_placed = []
    sold_this_run = set()

    # Apply dynamic watchlist
    position_symbols = list(positions.keys())
    dyn_symbols = load_dynamic_symbols()
    if dyn_symbols:
        merged = list(dict.fromkeys(position_symbols + dyn_symbols))
        for sc in STRATEGIES.values():
            sc["symbols"] = merged
        dropped = [s for s in position_symbols if s not in dyn_symbols]
        print(f"[WATCHLIST] {len(merged)} symbols applied "
              f"({len(dropped)} position-only: {dropped or 'none'})")
    else:
        for sc in STRATEGIES.values():
            sc["symbols"] = list(dict.fromkeys(position_symbols + sc["symbols"]))
        print("[WATCHLIST] Using hardcoded lists; open positions prepended")

    # ── PHASE 1: EOD exit sweep ──────────────────────────────────────────
    positions     = get_positions()
    position_tags = run_eod_exit(
        positions, position_tags, all_signals, orders_placed, run_start
    )
    write_github_log(EOD_TAG_FILE, position_tags)

    # ── PHASE 2: Refresh + run strategy signals ───────────────────────────
    positions = get_positions()
    strats_to_run = {k: v for k, v in STRATEGIES.items()
                     if not STRATEGY_FILTER or k == STRATEGY_FILTER}

    for strat_name, strat_cfg in strats_to_run.items():
        strategy_type = "intraday" if strat_name in INTRADAY_STRATEGY_NAMES else "daily"
        signal_fn     = SIGNAL_FNS[strat_name]
        p             = strat_cfg["params"]
        tf            = strat_cfg.get("timeframe", "1Day")
        bar_days      = strat_cfg.get("bar_days", 300)
        print(f"\n--- {strat_name} [{strat_cfg['vix_type']}] [{strategy_type.upper()}] ---")

        for symbol in strat_cfg["symbols"]:
            try:
                df = get_bars(symbol, timeframe=tf, bar_days=bar_days)
                if len(df) < (30 if tf != "1Day" else 60):
                    continue
            except Exception as e:
                print(f"  {symbol}: bar fetch error — {e}"); continue

            price = df["close"].iloc[-1]
            atr   = calc_atr(df).iloc[-1]
            try:
                signal, inds = signal_fn(df, p)
            except Exception as e:
                print(f"  {symbol}: signal error — {e}"); continue

            print(f"  {symbol}: signal={signal} price={price:.2f} atr={atr:.3f}")

            vix_mult, vix_reason = vix_size_multiplier(strat_cfg, vix or 0.0)
            executed = False; skip_reason = None; order_id = None
            qty = 0; stop_price = None; tp_price = None

            if signal == "hold":
                skip_reason = "no_signal"
            elif vix_mult == 0.0:
                skip_reason = vix_reason
            elif signal == "buy" and symbol in positions:
                skip_reason = "already_in_position"
            elif signal == "buy" and symbol in sold_this_run:
                skip_reason = "sold_this_run"   # BUG-001 guard
            elif signal == "sell" and symbol not in positions:
                skip_reason = "no_position_to_sell"
            elif signal == "buy":
                # Kill switch — block all new entries when trailing drawdown >= threshold
                if _kill_switch_active:
                    skip_reason = (f"kill_switch_active "
                                   f"(drawdown {drawdown_pct:.2f}% >= {MAX_DRAWDOWN_PCT}%)")
                # MA20 regime gate — block new buys when SPY is below 20-day MA (v7.1)
                elif _ma20_bear_block:
                    skip_reason = (f"ma20_bear_block "
                                   f"(SPY {spy_close_now:.2f} < MA20 {spy_ma20_now:.2f})")
                else:
                    qty = atr_position_size(equity, price, atr, vix_mult)
                if not skip_reason and qty < 1:
                    skip_reason = "qty_too_small"
                elif not skip_reason and price * qty > buying_power * 0.95:
                    skip_reason = "insufficient_buying_power"
                else:
                    eff_atr    = max(atr, price * 0.002)
                    stop_price = price - ATR_STOP_MULT * eff_atr
                    tp_price   = price + ATR_TP_MULT   * eff_atr
                    try:
                        order    = place_order(symbol, qty, "buy", stop_price, tp_price)
                        order_id = order.get("id"); executed = True
                        print(f"  ✓ BUY {qty} {symbol} stop={stop_price:.2f} tp={tp_price:.2f}")
                        orders_placed.append({
                            "symbol": symbol, "strat": strat_name,
                            "strategy_type": strategy_type,
                            "signal": "buy", "side": "buy", "qty": qty,
                            "price": round(price, 2), "est_value": round(price * qty, 2),
                            "stop_price": round(stop_price, 2), "tp_price": round(tp_price, 2),
                            "order_id": order_id, "timestamp": run_start.isoformat(),
                        })
                        # Tag intraday entries for EOD exit
                        if strategy_type == "intraday":
                            position_tags[symbol] = {
                                "strategy": strat_name, "strategy_type": "intraday",
                                "entry_time": run_start.isoformat(),
                                "entry_price": round(price, 2),
                            }
                            write_github_log(EOD_TAG_FILE, position_tags)
                        # Immediate in-memory cache update (BUG-001)
                        positions[symbol] = {"symbol": symbol, "qty": str(qty),
                                             "current_price": str(price),
                                             "avg_entry_price": str(price)}
                    except Exception as e:
                        skip_reason = f"order_error: {e}"
            else:  # SELL
                pos_qty = int(float(positions[symbol].get("qty", 0)))
                qty     = pos_qty if pos_qty > 0 else atr_position_size(equity, price, atr, vix_mult)
                try:
                    order    = close_position_order(symbol, qty)
                    order_id = order.get("id"); executed = True
                    print(f"  ✓ SELL {qty} {symbol}")
                    orders_placed.append({
                        "symbol": symbol, "strat": strat_name,
                        "strategy_type": strategy_type,
                        "signal": "sell", "side": "sell", "qty": qty,
                        "price": round(price, 2), "est_value": round(price * qty, 2),
                        "stop_price": None, "tp_price": None,
                        "order_id": order_id, "timestamp": run_start.isoformat(),
                    })
                    # BUG-001: update cache + blocklist immediately
                    sold_this_run.add(symbol)
                    positions.pop(symbol, None)
                    position_tags.pop(symbol, None)
                    write_github_log(EOD_TAG_FILE, position_tags)
                except Exception as e:
                    skip_reason = f"order_error: {e}"

            all_signals.append({
                "timestamp": run_start.isoformat(), "strategy": strat_name,
                "strategy_type": strategy_type, "vix_type": strat_cfg["vix_type"],
                "symbol": symbol, "signal": signal, "price": round(price, 2),
                "atr": round(atr, 4), "qty": qty,
                "stop_price": round(stop_price, 2) if stop_price else None,
                "tp_price": round(tp_price, 2) if tp_price else None,
                "executed": executed, "skip_reason": skip_reason,
                "order_id": order_id, "vix": vix, "vix_reason": vix_reason,
                "indicators": inds,
            })

    # Final position snapshot
    positions = get_positions()
    position_details = []
    for sym, pos in positions.items():
        try:
            entry = float(pos.get("avg_entry_price", 0))
            cur   = float(pos.get("current_price", 0))
            qty_p = float(pos.get("qty", 0))
            mval  = float(pos.get("market_value", 0))
            upl   = float(pos.get("unrealized_pl", 0))
            uplpc = float(pos.get("unrealized_plpc", 0)) * 100
            tag   = position_tags.get(sym, {})
            position_details.append({
                "symbol": sym, "qty": qty_p, "side": pos.get("side", "long"),
                "avg_entry_price": round(entry, 2), "current_price": round(cur, 2),
                "market_value": round(mval, 2), "unrealized_pl": round(upl, 2),
                "unrealized_plpc": round(uplpc, 3),
                "cost_basis": round(float(pos.get("cost_basis", entry * qty_p)), 2),
                "strategy_type": tag.get("strategy_type", "daily"),
                "entry_strategy": tag.get("strategy", "unknown"),
                "watching_strategies": [sn for sn, sc in STRATEGIES.items()
                                        if sym in sc.get("symbols", [])],
            })
        except Exception as e:
            position_details.append({"symbol": sym, "error": str(e)})

    # Live-mode deposit-aware P&L fields
    trading_pnl = total_deposited = deposit_count = None
    if not IS_PAPER and live_baseline:
        trading_pnl     = round(live_baseline.get("total_trading_pnl", 0), 2)
        total_deposited = round(live_baseline.get("total_deposited", 0), 2)
        deposit_count   = len(live_baseline.get("deposits", []))
        print(f"\n[LIVE P&L] Trading P&L: ${trading_pnl:+,.2f} | "
              f"Deposits: ${total_deposited:,.2f} ({deposit_count}x)")

    run_log = {
        "run_timestamp": run_start.isoformat(), "mode": MODE,
        "strategy_mode": STRATEGY_MODE,
        "equity": round(equity, 2), "last_equity": round(last_equity, 2),
        "buying_power": round(buying_power, 2), "vix": vix,
        "drawdown_pct": round(drawdown_pct, 2),
        "peak_equity": round(peak_equity, 2),
        "kill_switch_active": _kill_switch_active,
        # pdt_count / pdt_guard_active retired Jun 2026 (FINRA PDT rule removed)
        "positions": list(positions.keys()),
        "position_details": position_details,
        "signals": all_signals, "orders_placed": orders_placed,
        "trading_pnl": trading_pnl,
        "total_deposited": total_deposited,
        "deposit_count": deposit_count,
    }

    write_github_log(LOG_FILE, run_log)
    append_run_history({
        "timestamp": run_start.isoformat(), "mode": MODE,
        "strategy_mode": STRATEGY_MODE, "equity": round(equity, 2),
        "vix": vix, "signals_count": len(all_signals),
        "orders_count": len(orders_placed),
        "symbols_traded": [o["symbol"] for o in orders_placed],
        "trading_pnl": trading_pnl, "total_deposited": total_deposited,
    })
    append_signals_history(all_signals)
    print(f"\nRun complete — {len(all_signals)} signals | {len(orders_placed)} orders")


if __name__ == "__main__":
    main()
