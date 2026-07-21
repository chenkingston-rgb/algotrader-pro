"""
AlgoTrader Pro — GitHub Actions Strategy Runner  (v8.9)
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
STRATEGY_MODE   = os.getenv("STRATEGY_MODE", "intraday").lower()  # FIX-6: was "daily" default — Render now defaults to intraday
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
ATR_STOP_MULT    = 2.0   # FIX-H v8.1: raised from 1.5 — safety net for trailing stop     # Stop loss = entry ± 1.5 × ATR
ATR_TP_MULT      = 3.0     # Take profit default (overridden by VWAP tp_mult)
MAX_DRAWDOWN_PCT = 25.0    # Kill switch threshold

# ── FIX-L v8.5: Breakeven trail upgrade ──────────────────────────────────────
# Two-phase stop system: Phase 1 = static 2.0×ATR stop for entry breathing room.
# Phase 2 = once price clears +BREAKEVEN_ATR_TRIGGER above entry, cancel the
# existing trailing stop and re-attach a tighter PROFIT_LOCK_ATR_MULT trail,
# guaranteeing worst-case = no loss once the price has moved in our favour.
# Checked each 15-min cron cycle via _upgrade_trail_to_breakeven().
BREAKEVEN_ATR_TRIGGER  = 1.0   # Price must exceed entry + 1×ATR before upgrade
PROFIT_LOCK_ATR_MULT   = 0.5   # Trail distance after upgrade (same 0.5×ATR)
BREAKEVEN_PROFIT_FLOOR = 0.0   # Worst-case P&L once trail is upgraded (0 = break even)
TRAIL_ACTIVATION_MIN   = 30    # FIX-M v8.6: delay trail activation 30min from entry — prevents
                                #             noise-stop exits in first 2 cron cycles. Static 2.0×ATR
                                #             safety net still active during this window.

# ── MA20 REGIME HYSTERESIS BAND (v7.3) ──────────────────────────────────────
# Prevents whipsawing around the MA20 line. SPY must breach these thresholds
# before regime flips. Asymmetric: tighter on recovery (bull) than on decline.
# Override via env: MA20_BEAR_BAND (default 0.010 = 1.0%) MA20_BULL_BAND (0.005 = 0.5%)
MA20_BEAR_BAND = 0.010   # flip BEAR only if SPY close < MA20 × (1 - 0.010)
MA20_BULL_BAND = 0.005   # flip BULL only if SPY close > MA20 × (1 + 0.005)

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
        "params": {"rsi_period": 14, "rsi_os": 40, "rsi_ob": 70,  # FIX-N v8.6: os 35→40, ob 65→70. SPY RSI stays 45-70 in bull market; tight os=35 never fires on large-cap ETFs.
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
        "symbols":  ["SPY", "QQQ", "XLK", "XLF", "XLE"],  # FIX-N v8.6: expanded from 2→5 to increase signal opportunities while staying liquid
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
        "symbols":  ["SPY", "QQQ", "HOOD", "SCHW", "AMAT", "PYPL", "BAC"],  # FIX-S v8.9: added volatile single stocks — SPY/QQQ never touch lower BB in bull market
        "vix_type": "MEAN_REV",
        "vix_block": 25, "vix_reduce": 18, "vix_reduce_pct": 0.40,
        "params": {"bb_period": 20, "bb_std": 1.8, "ma_filter": 20},  # FIX-N v8.6: bb_std 2.0→1.8 (tighter bands → price touches lower more often), ma_filter 50→20 (50 bars=12.5hr unavailable intraday; 20 bars=5hr achievable). Root cause of 139/140 no_signal.
        "timeframe": "15Min", "bar_days": 20,
    },
    "rsi_mean_reversion_15m": {
        # FIX-S v8.9: RSI8 mean-reversion on volatile single stocks.
        # These have enough intraday range to hit genuine RSI extremes (<32/>68).
        # All symbols are in the existing weekly watchlist and have strong liquidity.
        "symbols":  ["HOOD", "SCHW", "AMAT", "PYPL", "BAC", "TSLA", "AAPL"],
        "vix_type": "MEAN_REV",
        "vix_block": 30, "vix_reduce": 22, "vix_reduce_pct": 0.40,
        "params": {
            "rsi_period":     8,
            "rsi_oversold":   32,   # RSI8 < 32 = genuinely oversold single stock
            "rsi_overbought": 68,   # RSI8 > 68 = overbought, exit
        },
        "timeframe": "15Min", "bar_days": 20,
    },
    "momentum_roc_15m": {
        "symbols":  ["SPY", "QQQ", "XLK", "XLE", "XLF"],
        "vix_type": "MOMENTUM",
        "vix_block": 28, "vix_reduce": 20, "vix_reduce_pct": 0.50,
        "params": {"roc_period": 10, "roc_threshold": 0.8,  # FIX-I v8.3: raised 0.3→0.8 (weak 0.3-0.8% band was 100% losses)
                   "atr_ext_mult": 1.0,    # FIX-C v7.4: block entry if bar moved >1x ATR from open
                   "roc_max_extension": 1.8},  # FIX-I v8.3: tightened 2.0→1.8 (blow-off top guard, matches only-win upper bound)
                                                # (blow-off top, not a fresh breakout)
        "timeframe": "15Min", "bar_days": 20,
    },
}

STRATEGIES            = DAILY_STRATEGIES if STRATEGY_MODE == "daily" else INTRADAY_STRATEGIES
LOG_FILE              = f"logs/{STRATEGY_MODE}_latest.json"
HISTORY_FILE          = "logs/run_history.json"
DAILY_EQUITY_FILE     = "logs/daily_equity_history.json"  # v8.4 — FIX-J: Board Roundtable Fixes — session ban, 14:00 gate, max hold 390min, weekly cap (2026-07-17)
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


def append_daily_equity_history(date_str: str, equity: float, last_equity: float):
    """
    v7.8: Upsert today's row in logs/daily_equity_history.json — ONE entry per
    calendar day (ET), never truncated. run_history.json is capped at the last
    200 runs (~1.5 weeks) by design to keep the file small for the hourly view;
    this file exists specifically so the dashboard can render a true all-time
    equity curve at daily granularity. Each run overwrites *today's* row with
    the latest equity so intraday moves show up; once a day ends, its row is
    frozen (never touched again by subsequent days' runs).
    """
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{DAILY_EQUITY_FILE}"
    history = []
    get_r = requests.get(api_url, headers=headers, timeout=10)
    if get_r.ok:
        try:
            existing = json.loads(base64.b64decode(get_r.json()["content"]).decode())
            history = existing if isinstance(existing, list) else []
        except Exception:
            history = []
    sha = get_r.json().get("sha") if get_r.ok else None
    row = {"date": date_str, "equity": round(equity, 2), "last_equity": round(last_equity, 2)}
    if history and history[-1].get("date") == date_str:
        history[-1] = row
    else:
        history.append(row)
    content_b64 = base64.b64encode(
        json.dumps(history, indent=2, default=str).encode()
    ).decode()
    payload = {"message": f"[bot] Upsert {DAILY_EQUITY_FILE} [skip render]", "content": content_b64}
    if sha:
        payload["sha"] = sha
    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        print(f"  [LOG] Upserted {DAILY_EQUITY_FILE} ({len(history)} days total) ✓")
    else:
        print(f"  [WARN] Failed to update daily equity history: {put_r.status_code} {put_r.text[:200]}")


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
# FIX-K T5: Module-level requests.Session for connection pooling
# Re-using TCP connections reduces per-call latency ~20% and GitHub API rate pressure.
_http_session = requests.Session()
_http_session.headers.update({
    "User-Agent": "AlgoTrader-Pro/8.4",
    "Accept-Encoding": "gzip, deflate",
})


def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = _http_session.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=10)
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
    """
    Returns the real CBOE VIX index (implied volatility).
    Primary source: Yahoo Finance ^VIX daily close (free, no API key).
    Fallback:       SPY 10-day realized vol x 1.2 if Yahoo is unreachable.

    Replaces the old realized-vol estimate which overstated VIX by ~50%
    after sell-offs (e.g. June 2026: engine read 26.85, real VIX was 16.85).
    """
    import urllib.request as _ul, json as _json
    # -- Primary: real CBOE ^VIX via Yahoo Finance --
    for _host in ("query1", "query2"):
        try:
            _url = "https://" + _host + ".finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=5d"
            _req = _ul.Request(_url, headers={"User-Agent": "Mozilla/5.0"})
            with _ul.urlopen(_req, timeout=8) as _resp:
                _yd = _json.loads(_resp.read().decode())
            _closes = _yd["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            real_vix = next((v for v in reversed(_closes) if v is not None), None)
            if real_vix and 5 < real_vix < 90:
                print(f"  VIX (CBOE ^VIX via Yahoo): {real_vix:.2f}")
                return round(real_vix, 2)
        except Exception as _e:
            print(f"  [WARN] VIX Yahoo failed: {_e}")
    # -- Fallback: SPY 10-day realized vol x 1.2 --
    print("  [WARN] Real ^VIX unavailable -- falling back to SPY realized vol estimate")
    try:
        df = get_bars("SPY", timeframe="1Day", bar_days=60)
        if len(df) < 22:
            return None
        log_returns  = np.log(df["close"] / df["close"].shift(1)).dropna()
        realized_vol = log_returns.rolling(10).std().iloc[-1] * math.sqrt(252) * 100
        vix_est      = round(realized_vol * 1.2, 2)
        print(f"  VIX fallback (SPY 10d realized x 1.2): {vix_est:.1f} -- may overestimate after sell-offs")
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
def get_spy_ma20_regime(prior_is_bull: bool = True) -> tuple:
    """
    Returns (is_bull, spy_close, ma20).
    is_bull = True  → regime is BULL, new BUY entries permitted.
    is_bull = False → regime is BEAR, all new BUY entries blocked.
    Falls back to True (permissive) if data unavailable.

    HYSTERESIS BAND (v7.3):
    ─────────────────────────────────────────────────────────────────
    Problem with a simple close-vs-MA20 cross: SPY can oscillate
    ±0.5% around the MA20 for days, causing rapid BULL/BEAR flips
    (regime whipsaws) that block legitimate entries. Evidence:
      - June 17: BULL→BEAR at -0.77% gap (closed $741 vs MA20 $746.79)
      - June 18: still BEAR at -0.042% gap (SPY $746.75 vs MA20 $747.06)
    Both blocked the engine on trivial sub-1% distances.

    Solution — asymmetric band:
      FLIP TO BEAR:  SPY daily close < MA20 × (1 - BEAR_BAND)   i.e. -1.0% below
      FLIP TO BULL:  SPY daily close > MA20 × (1 + BULL_BAND)   i.e. +0.5% above
      NEUTRAL ZONE:  between bands → hold current regime (no flip)

    The asymmetry (tighter bull threshold) ensures we don't stay blocked
    too long after a recovery, while the 1% bear threshold filters noise.

    BEAR_BAND and BULL_BAND are configurable via env vars.
    """
    if os.getenv("DISABLE_MA20_FILTER", "false").lower() == "true":
        return True, 0.0, 0.0

    bear_band = float(os.getenv("MA20_BEAR_BAND", str(MA20_BEAR_BAND)))
    bull_band = float(os.getenv("MA20_BULL_BAND", str(MA20_BULL_BAND)))

    try:
        df = get_bars("SPY", timeframe="1Day", bar_days=40)
        if df.empty or len(df) < 20:
            logging.warning("[MA20] Insufficient SPY bars — skipping regime filter")
            return True, 0.0, 0.0

        # ── Drop today's partial intraday candle ─────────────────────────────
        # Alpaca 1Day bars include today's partial session during market hours.
        # A partial candle's "close" = current price, NOT a completed session close.
        # This causes regime whipsaws on Monday mornings when SPY gaps up from
        # a bearish Friday close — the partial candle appears BULL mid-session.
        # Fix: exclude any bar whose date == today (ET) so we only compare
        # completed daily closes, matching the nightly regime update contract.
        today_et = datetime.now(ET_TZ).date()
        df_completed = df[df.index.date < today_et] if hasattr(df.index, 'date') else df.iloc[:-1]
        if df_completed.empty or len(df_completed) < 20:
            # Fallback to all bars if filtering leaves too few (e.g. early morning data gap)
            df_completed = df
        spy_close = float(df_completed["close"].iloc[-1])
        ma20      = float(df_completed["close"].iloc[-20:].mean())
        gap_pct   = (spy_close - ma20) / ma20 * 100

        bear_threshold = ma20 * (1 - bear_band)  # e.g. MA20 × 0.990
        bull_threshold = ma20 * (1 + bull_band)  # e.g. MA20 × 1.005

        if spy_close < bear_threshold:
            is_bull = False
            zone    = "BEAR  (below -{:.1f}% band)".format(bear_band * 100)
        elif spy_close > bull_threshold:
            is_bull = True
            zone    = "BULL  (above +{:.1f}% band)".format(bull_band * 100)
        else:
            # Neutral zone — hold prior confirmed regime read from live_baseline.json.
            # Defaults to BULL (permissive) if no persisted state exists.
            is_bull = prior_is_bull
            zone    = "NEUTRAL (within band) → holding {} (persisted)".format("BULL" if is_bull else "BEAR")

        label = "BULL" if is_bull else "BEAR"
        print(f"  [MA20_REGIME] SPY close=${spy_close:.2f}  MA20=${ma20:.2f}  "
              f"gap={gap_pct:+.3f}%  bear<${bear_threshold:.2f}  bull>${bull_threshold:.2f}  → {label} [{zone}]")
        return is_bull, spy_close, ma20

    except Exception as e:
        logging.warning(f"[MA20] Regime check failed: {e} — defaulting to BULL (permissive)")
        return True, 0.0, 0.0


def get_positions() -> dict:
    r = _http_session.get(f"{ALPACA_BASE}/v2/positions", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def place_order(symbol: str, qty: int, side: str,
                stop_price: float, take_profit: float) -> dict:
    """
    FIX-H v8.1: Submit a market buy with a static protective stop only.
    The take_profit bracket is removed — trailing stop is attached separately
    via attach_trailing_stop() immediately after fill, which provides
    unlimited upside capture while protecting against whipsaws.
    The static stop_price serves as a hard safety net if the trailing
    stop order fails to submit.
    """
    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          side,
        "type":          "market",
        "time_in_force": "gtc",
        "order_class":   "oto",          # one-triggers-other: market → stop safety net
        "stop_loss":     {"stop_price": str(round(stop_price, 2))},
        # No take_profit bracket: trailing stop handles upside (FIX-H)
    }
    r = requests.post(f"{ALPACA_BASE}/v2/orders",
                      headers=alpaca_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()


def attach_trailing_stop(symbol: str, qty: int, trail_price: float,
                          parent_order_id: str = "") -> dict:
    """
    FIX-H v8.1: Submit a trailing stop sell order immediately after the
    market buy is filled.  trail_price is the dollar distance to trail
    (e.g. 1.0×ATR).  Alpaca will automatically move the stop up as the
    price rises, locking in profit once the position is in the green.

    This is submitted as a SEPARATE order (not a bracket leg) so it can
    be cancelled independently at EOD before the market-close sweep.

    Args:
        symbol:          ticker
        qty:             shares to protect (= full position size)
        trail_price:     dollar distance to trail (= 1.0 × ATR, min $0.05)
        parent_order_id: logged for traceability
    Returns: Alpaca order dict with {"id": ..., "trail_price": ...}
    """
    # FIX-Q v8.7: Guard against qty=0 — causes Alpaca to accept then immediately
    # cancel the stop order (observed: multiple stops with qty=0 in live orders).
    # Root cause: race between buy fill and position cache update.
    if qty <= 0:
        logging.warning(f"[FIX-Q] attach_trailing_stop called with qty={qty} for {symbol} — skipping")
        return {}
    trail_price = max(round(trail_price, 2), 0.05)   # minimum $0.05 trail
    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "trailing_stop",
        "trail_price":   str(trail_price),
        "time_in_force": "gtc",
    }
    r = requests.post(f"{ALPACA_BASE}/v2/orders",
                      headers=alpaca_headers(), json=payload, timeout=10)
    if not r.ok:
        logging.warning(f"[TRAIL_STOP] Failed to attach trailing stop for {symbol}: "
                        f"{r.status_code} {r.text[:200]}")
        return {}
    result = r.json()
    logging.info(f"[TRAIL_STOP] Attached trailing stop for {symbol}: "
                 f"trail=${trail_price:.2f}  order_id={result.get('id')}")
    return result


def cancel_order(order_id: str) -> bool:
    """Cancel a specific open order by ID. Returns True if cancelled."""
    if not order_id:
        return False
    r = requests.delete(f"{ALPACA_BASE}/v2/orders/{order_id}",
                        headers=alpaca_headers(), timeout=10)
    return r.status_code in (200, 204)


def cancel_all_trailing_stops_for_symbol(symbol: str) -> int:
    """
    FIX-H v8.1 EOD helper: Cancel all open trailing_stop orders for a
    symbol before the EOD market-close sweep.  Returns number cancelled.
    """
    r = _http_session.get(f"{ALPACA_BASE}/v2/orders",
                     headers=alpaca_headers(),
                     params={"status": "open", "symbols": symbol, "limit": 50},
                     timeout=10)
    if not r.ok:
        return 0
    orders = r.json() if isinstance(r.json(), list) else []
    cancelled = 0
    for o in orders:
        if o.get("type") == "trailing_stop" and o.get("symbol") == symbol:
            if cancel_order(o["id"]):
                cancelled += 1
                logging.info(f"[TRAIL_STOP] Cancelled trailing stop {o['id']} for {symbol} (EOD sweep)")
    return cancelled


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


# ─────────────────────────────────────────────────────────────────────────────
# STOP-COOLDOWN (v7.4)
# After a bracket stop-loss fills, block re-entry on that symbol for
# STOP_COOLDOWN_MINUTES (default 30). Prevents immediately chasing back
# into a position that just proved the signal was wrong / timing was off.
# Does NOT block entries that arrive after the cooldown expires —
# respecting our rule that profitable re-entries are allowed.
# ─────────────────────────────────────────────────────────────────────────────
STOP_COOLDOWN_MINUTES = int(os.getenv("STOP_COOLDOWN_MINUTES", "30"))


def load_stop_cooldowns(baseline: dict) -> dict:
    """Return {symbol: iso_expiry_string} from live_baseline. Empty dict if absent."""
    return dict(baseline.get("stop_cooldowns", {}))


def is_in_stop_cooldown(symbol: str, cooldowns: dict, now_et: datetime) -> bool:
    """True if symbol is still within its post-stop cooldown window."""
    expiry_str = cooldowns.get(symbol)
    if not expiry_str:
        return False
    try:
        # FIX v7.4a: compare in UTC to avoid pytz LMT offset mismatch
        # datetime.fromisoformat returns fixed-offset tz; pytz tz objects
        # use LMT on first construction causing -04:56 vs -04:00 skew.
        expiry_utc = datetime.fromisoformat(expiry_str).astimezone(pytz.utc)
        now_utc    = now_et.astimezone(pytz.utc)
        return now_utc < expiry_utc
    except Exception:
        return False


def update_stop_cooldowns_from_fills(baseline: dict, now_et: datetime) -> dict:
    """
    Scan Alpaca closed orders in the last 60 min for stop-loss fills.
    For each hit, write cooldown expiry = fill_time + STOP_COOLDOWN_MINUTES
    into baseline["stop_cooldowns"] and return the updated dict.
    Only bracket stop children (type=stop, side=sell, status=filled) are matched.
    """
    import datetime as _dt
    cooldowns = load_stop_cooldowns(baseline)
    cutoff_utc = (now_et.astimezone(pytz.utc) - _dt.timedelta(minutes=60)).isoformat()
    try:
        r = _http_session.get(
            f"{ALPACA_BASE}/v2/orders",
            headers=alpaca_headers(),
            params={"status": "closed", "limit": 50, "after": cutoff_utc},
            timeout=10,
        )
        if not r.ok:
            return cooldowns
        raw = r.json()
        if not isinstance(raw, list):
            return cooldowns   # FIX v7.4a: guard against non-list API responses
        for order in raw:
            if (order.get("order_type") in ("stop", "stop_limit")
                    and order.get("side") == "sell"
                    and order.get("status") == "filled"
                    and order.get("filled_at")):
                sym      = order["symbol"]
                fill_ts  = datetime.fromisoformat(
                    order["filled_at"].replace("Z", "+00:00")).astimezone(ET)
                # FIX-J R1 v8.4: Same-day SESSION BAN after stop-loss (was 30-min cooldown)
                # 252-trade audit: GOOGL stopped 12:10 → re-entered 13:17 → -$34.70 repeat loss
                session_end = fill_ts.replace(hour=15, minute=0, second=0, microsecond=0)
                if session_end <= fill_ts:
                    session_end = fill_ts + _dt.timedelta(minutes=STOP_COOLDOWN_MINUTES)
                expiry   = session_end
                existing = cooldowns.get(sym)
                if not existing or expiry.isoformat() > existing:
                    cooldowns[sym] = expiry.isoformat()
                    print(f"  [STOP_COOLDOWN] {sym} stop fill @ "
                          f"{fill_ts.strftime('%H:%M ET')} "
                          f"→ cooldown until {expiry.strftime('%H:%M ET')}")
    except Exception as e:
        logging.warning(f"[STOP_COOLDOWN] fill scan failed: {e}")
    baseline["stop_cooldowns"] = cooldowns
    return cooldowns


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
                      vix_mult: float = 1.0,
                      realized_vol: float | None = None) -> int:
    """
    ATR-based position sizing with volatility targeting (Harvey et al. 2018).
    Base: equity × RISK_PCT / (ATR_STOP_MULT × ATR), capped at MAX_POSITION_PCT.
    Enhancement (FIX-G v8.0): if realized_vol is provided, scale RISK_PCT so
    that the portfolio targets a constant 12% annualized volatility.  When the
    market is calm (low vol), we size up slightly; when volatile, we scale down.
    This dynamically equalizes risk contribution per trade rather than using a
    fixed dollar amount, boosting the risk-adjusted return (Sharpe) by ~+0.15–0.30
    per Harvey et al. (2018), "Impact of Volatility Targeting".
    """
    if atr <= 0 or price <= 0 or vix_mult <= 0:
        return 0   # FIX v7.4a: vix_mult=0 (blocked) must return 0, not 1
    # ── Volatility targeting adjustment ───────────────────────────────────
    # Target: 12% annualized portfolio vol (= 0.12/sqrt(252) ≈ 0.00756 daily)
    VOL_TARGET_DAILY = 0.12 / (252 ** 0.5)
    if realized_vol is not None and realized_vol > 0:
        vol_scalar = min(VOL_TARGET_DAILY / realized_vol, 2.0)  # cap at 2x
    else:
        vol_scalar = 1.0   # no adjustment if vol not provided
    effective_risk_pct = RISK_PCT * vol_scalar * vix_mult
    # ── Core ATR sizing ───────────────────────────────────────────────────
    dollar_risk    = equity * effective_risk_pct
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
    """
    FIX-S v8.9: Bollinger mean-reversion buy/sell.
    BUY:  price below lower band + MA filter + RSI8 < 45 confirmation (not a knife-catch).
    SELL: price above upper band (full mean-reversion target).
    Symbols expanded from SPY/QQQ-only to include volatile single stocks (HOOD, SCHW, etc.)
    which actually touch lower bands in normal intraday conditions.
    """
    c = df["close"]
    lower, mid, upper = calc_bollinger(c, p["bb_period"], p["bb_std"])
    ma = c.rolling(p["ma_filter"]).mean()
    price, l, m, u, ma_v = c.iloc[-1], lower.iloc[-1], mid.iloc[-1], upper.iloc[-1], ma.iloc[-1]

    # FIX-S v8.9: RSI8 confirmation — prevents buying into a freefall
    # If RSI8 is already >45 when price touches lower band, the move is not
    # genuinely oversold (could be a momentary wick). Permissive: pass if NaN.
    _rsi_delta = c.diff()
    _rsi_gain  = _rsi_delta.clip(lower=0).rolling(8).mean()
    _rsi_loss  = (-_rsi_delta.clip(upper=0)).rolling(8).mean()
    _rsi8_raw  = _rsi_gain / _rsi_loss.replace(0, np.nan)
    _rsi8_val  = float((100 - (100 / (1 + _rsi8_raw))).iloc[-1])
    rsi8_ok    = np.isnan(_rsi8_val) or _rsi8_val < 45  # pass if unavailable

    inds = {"price": round(price,2), "bb_lower": round(l,2), "bb_mid": round(m,2),
            "bb_upper": round(u,2), "ma_filter": round(ma_v,2) if not np.isnan(ma_v) else None,
            "rsi8": round(_rsi8_val, 1) if not np.isnan(_rsi8_val) else None}

    ma_valid = not np.isnan(ma_v)
    # BUY: price below lower band + valid MA trend filter + RSI8 oversold confirmation
    if price < l and ma_valid and price > ma_v and rsi8_ok:
        return "buy", inds
    # SELL: price above upper band (full mean-reversion target)
    if price > u:
        return "sell", inds
    return "hold", inds

def signal_momentum_roc_15m(df: pd.DataFrame, p: dict) -> tuple:
    c   = df["close"]
    vol = df["volume"] if "volume" in df.columns else None
    roc = (c / c.shift(p["roc_period"]) - 1) * 100
    r, prev_r = roc.iloc[-1], roc.iloc[-2]

    # FIX-A (v7.2): MA50 trend filter — only buy when price is above the 50-bar MA.
    # Prevents entering long positions in a downtrend (rulebook STRATEGY_RULEBOOK FIX-A).
    ma50 = float(c.iloc[-50:].mean()) if len(c) >= 50 else None

    # FIX-B (v7.2): Volume confirmation — require current bar volume > 1.2× 20-bar avg.
    # Filters out low-participation momentum signals on IEX thin data (rulebook FIX-B).
    vol_ok = True
    vol_ratio = None
    if vol is not None and len(vol) >= 20:
        avg_vol   = float(vol.iloc[-20:].mean())
        cur_vol   = float(vol.iloc[-1])
        vol_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else None
        vol_ok    = (vol_ratio is not None and vol_ratio >= 1.5)  # FIX-I v8.3: raised 1.2→1.5 (1.2-1.9× range was 91% losses)

    inds = {
        "roc":       round(r, 3),
        "roc_prev":  round(prev_r, 3),
        "threshold": p["roc_threshold"],
        "ma50":      round(ma50, 2) if ma50 else None,
        "price_vs_ma50": round((c.iloc[-1] - ma50) / ma50 * 100, 3) if ma50 else None,
        "vol_ratio": vol_ratio,
    }

    # BUY: ROC above threshold + accelerating + above MA50 + volume confirmed
    above_ma50 = (ma50 is None or float(c.iloc[-1]) >= ma50)  # permissive if MA50 unavailable

    # FIX-C (v7.4): ATR-extension guard
    # Block entry if the current bar has already moved > atr_ext_mult x ATR from its open.
    # Entering after a full-ATR spike means the stop (1.5x ATR below entry) is nearly
    # guaranteed to be hit on mean-reversion, compressing R:R to near zero.
    bar_open   = float(df["open"].iloc[-1]) if "open" in df.columns else float(c.iloc[-1])
    bar_move   = float(c.iloc[-1]) - bar_open   # positive = up-bar
    atr_val    = calc_atr(df).iloc[-1]
    ext_mult   = float(p.get("atr_ext_mult", 1.0))
    atr_ext_ok = (bar_move < atr_val * ext_mult)
    inds["bar_open"]   = round(bar_open, 2)
    inds["bar_move"]   = round(bar_move, 3)
    inds["atr_val"]    = round(atr_val, 3)
    inds["atr_ext_ok"] = atr_ext_ok

    # FIX-D (v7.7): Rolling blow-off guard. The single-bar ATR-extension guard
    # (FIX-C) only checks the CURRENT bar's move, so it misses multi-candle
    # blow-off tops where the cumulative ROC move (over the full lookback window)
    # is already extreme before entry. Data validation (Jul 6): 8 of 8 normal
    # momentum entries clustered at ROC 0.75-1.04%; the 2 outliers at ROC 6.74%
    # (INTC) and 11.07% (AMD) both lost money on immediate mean-reversion.
    # Cap set with ~2x headroom above the highest normal historical entry.
    roc_not_extended = r <= p.get("roc_max_extension", 1.8)  # FIX-I v8.3: default fallback updated
    inds["roc_max_extension"] = p.get("roc_max_extension", 1.8)
    inds["roc_not_extended"] = roc_not_extended

    if r > p["roc_threshold"] and r > prev_r and above_ma50 and vol_ok and atr_ext_ok and roc_not_extended:
        return "buy", inds

    # SELL: ROC below negative threshold + decelerating (no trend/vol filter on sells)
    if r < -p["roc_threshold"] and r < prev_r:
        return "sell", inds

    return "hold", inds



def signal_rsi_mean_reversion_15m(df: pd.DataFrame, p: dict) -> tuple:
    """
    FIX-S v8.9: RSI8 mean-reversion intraday strategy — the missing intraday fallback.

    Problem it solves: In calm bull markets (VIX<20), SPY/QQQ never touch Bollinger
    lower bands so bollinger_bands_15m generates ZERO buy signals. Single stocks like
    HOOD, TSLA, AAPL have enough intraday volatility for RSI to reach extreme levels.

    BUY signal:  RSI8 < rsi_oversold (32) AND RSI8 rising vs prior bar (bounce confirmed)
                 AND price above MA20 (not catching a falling knife in a downtrend).
    SELL signal: RSI8 > rsi_overbought (68) — price is overextended to the upside.

    RSI8 (8-period) is used instead of RSI14 for intraday speed — it needs only 8 bars
    (2hr of data) vs RSI14's 3.5hr, and is more responsive to short-duration exhaustion.
    Prior-day bars (bar_days=20) ensure RSI is always valid from the first bar of the day.
    """
    c   = df["close"]
    vol = df["volume"] if "volume" in df.columns else None

    rsi_period = p.get("rsi_period", 8)
    delta = c.diff()
    # Wilder smoothing (EWM alpha=1/period) — standard RSI; never NaN after warm-up
    gain  = delta.clip(lower=0).ewm(alpha=1/rsi_period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/rsi_period, adjust=False).mean()
    rsi   = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))
    rsi_val  = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2]) if len(rsi) > 1 else rsi_val

    # MA20 trend filter (always valid with prior-day bars in bar_days=20)
    ma20    = float(c.rolling(20).mean().iloc[-1])
    ma20_ok = not np.isnan(ma20)
    price   = float(c.iloc[-1])
    above_ma20 = ma20_ok and price > ma20

    # Volume ratio (looser than momentum_roc — mean-reversion doesn't require surge)
    vol_ratio = None
    if vol is not None and len(vol) >= 5:
        avg_vol   = float(vol.iloc[-10:].mean()) if len(vol) >= 10 else float(vol.mean())
        cur_vol   = float(vol.iloc[-1])
        vol_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else None

    inds = {
        "rsi8":        round(rsi_val, 1) if not np.isnan(rsi_val) else None,
        "rsi8_prev":   round(rsi_prev, 1) if not np.isnan(rsi_prev) else None,
        "rsi_rising":  rsi_val > rsi_prev,
        "ma20":        round(ma20, 2) if ma20_ok else None,
        "above_ma20":  above_ma20,
        "vol_ratio":   vol_ratio,
        "price":       round(price, 2),
    }

    if np.isnan(rsi_val):
        return "hold", inds

    # BUY: RSI oversold + rising (bounce starting, not still falling) + above MA20
    if rsi_val < p["rsi_oversold"] and rsi_val > rsi_prev and above_ma20:
        return "buy", inds

    # SELL: RSI overbought — mean-reversion exit (no trend filter; overextension = sell)
    if rsi_val > p["rsi_overbought"]:
        return "sell", inds

    return "hold", inds

SIGNAL_FNS = {
    "rsi_macd_combo":      signal_rsi_macd_combo,
    "macd_crossover":      signal_macd_crossover,
    "triple_ema":          signal_triple_ema,
    "ema_crossover":       signal_ema_crossover,
    "bollinger_bands_15m":        signal_bollinger_bands_15m,
    "momentum_roc_15m":           signal_momentum_roc_15m,
    "rsi_mean_reversion_15m":     signal_rsi_mean_reversion_15m,  # FIX-S v8.9
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
EOD_EXIT_MIN   = 0   # BUG-011: cron ends 19:00 UTC = 3:00pm ET; was 30, now 0
MAX_HOLD_MINUTES = 390  # FIX-J R2 v8.4: Jun 8 MRVL held 1350min overnight → -$282.73
                         # Hard session limit: force-close any intraday position held > 6.5h
MAX_WEEKLY_ENTRIES_PER_SYMBOL = 3  # FIX-J R3 v8.4: concentration cap
                                    # HOOD = 42.9% of all gross gains; MRVL = 98% of loss magnitude
                                    # Capping at 3 intraday entries/symbol/week forces diversification: Jun 8 MRVL held 1350min overnight → -$282.73
                         # Hard session limit: force-close any intraday position held > 6.5h
EOD_TAG_FILE   = "logs/intraday_position_tags.json"
STRATEGY_LOG_FILE = "logs/strategy_trade_log.json"  # v7.5: permanent, strategy-tagged trade outcomes
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
    """
    Force-close all intraday-tagged positions at/after 3:00 PM ET.
    FIX-H v8.1: Before market-closing, cancel ALL open trailing stop orders
    for the symbol to prevent them from triggering overnight on the next day's
    open after the position has already been closed by this sweep.

    Also handles daily-strategy positions that have a trail_order_id stored —
    these are NOT force-closed (daily strategies hold overnight) but their
    trailing stop orders are refreshed if stale.
    """
    now_et  = datetime.now(ET)
    if STRATEGY_MODE != "intraday":
        # ── FIX-H: Even in daily mode, do a trailing stop audit ─────────────
        # If any daily-strategy position has moved against us by >2×ATR,
        # and the trailing stop hasn't triggered, log a warning.
        _daily_trail_audit(positions, position_tags)
        return position_tags
    cutoff = now_et.replace(hour=EOD_EXIT_HOUR, minute=EOD_EXIT_MIN, second=0, microsecond=0)

    # FIX-J R2 v8.4: Max hold time guard — force-close intraday positions held > 390 min
    # regardless of current time. Prevents overnight carry from EOD cron failure.
    # Jun 8: MRVL held 1350 min → -$282.73; MU held 1350 min → -$71.10; total day -$504
    import datetime as _hdt
    for _sym_mh, _tag_mh in list(position_tags.items()):
        if _tag_mh.get("strategy_type") != "intraday":
            continue
        _entry_iso = _tag_mh.get("entry_time") or _tag_mh.get("timestamp")
        if not _entry_iso:
            continue
        try:
            _entry_dt  = datetime.fromisoformat(str(_entry_iso).replace("Z","+00:00")).astimezone(ET)
            _hold_mins = (now_et - _entry_dt).total_seconds() / 60
            if _hold_mins > MAX_HOLD_MINUTES:
                _pos_mh = positions.get(_sym_mh) if positions else None
                if _pos_mh:
                    _qty_mh = int(float(_pos_mh.get("qty", 0)))
                    if _qty_mh > 0:
                        print(f"  [MAX_HOLD] {_sym_mh}: held {_hold_mins:.0f}min > {MAX_HOLD_MINUTES}min "
                              f"— force-closing NOW (FIX-J R2)")
                        cancel_all_trailing_stops_for_symbol(_sym_mh)
                        _oid_mh = place_order(_sym_mh, _qty_mh, "sell", "market")
                        orders_placed.append({"symbol": _sym_mh, "side": "sell",
                                              "qty": _qty_mh, "order_id": _oid_mh,
                                              "reason": "max_hold_exceeded"})
                        all_signals.append({"symbol": _sym_mh, "signal": "sell",
                                            "skip_reason": None, "executed": True,
                                            "strategy": "max_hold_exit",
                                            "timestamp": now_et.isoformat()})
        except Exception as _mh_e:
            logging.warning(f"[MAX_HOLD] {_sym_mh} check failed: {_mh_e}")

    if now_et < cutoff:
        return position_tags   # too early for full EOD sweep, but max-hold ran above
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

        # ── FIX-H v8.1: Cancel trailing stop BEFORE market-closing ──────────
        trail_order_id = tag.get("trail_order_id", "")
        n_cancelled = cancel_all_trailing_stops_for_symbol(sym)
        if n_cancelled:
            print(f"  [TRAIL_STOP] Cancelled {n_cancelled} trailing stop order(s) for {sym} (EOD)")
        elif trail_order_id:
            # Belt-and-suspenders: also try cancelling by stored ID directly
            if cancel_order(trail_order_id):
                print(f"  [TRAIL_STOP] Cancelled trailing stop {trail_order_id} for {sym} (EOD, direct)")
        # ── end FIX-H EOD cancel ────────────────────────────────────────────

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
                               "entry_strategy": tag.get("strategy"),
                               "trail_order_id": trail_order_id},
            })
            print(f"  ✓ EOD CLOSED {sym}  order_id={order_id}")
            del position_tags[sym]
        except Exception as e:
            print(f"  [WARN] EOD exit failed for {sym}: {e}")
    return position_tags


def _daily_trail_audit(positions: dict, position_tags: dict) -> None:
    """
    FIX-H v8.1: For daily-strategy positions, verify their trailing stop
    orders are still open on Alpaca.  If a trail order was filled (position
    closed by Alpaca trailing stop) but position_tags still has the symbol,
    log the outcome.  If the trail order is missing (e.g. after a restart),
    re-attach it using the stored ATR and current price.
    """
    for sym, tag in list(position_tags.items()):
        if tag.get("strategy_type") != "daily":
            continue
        trail_id = tag.get("trail_order_id", "")
        if not trail_id:
            continue
        try:
            r = _http_session.get(f"{ALPACA_BASE}/v2/orders/{trail_id}",
                             headers=alpaca_headers(), timeout=8)
            if not r.ok:
                continue
            o = r.json()
            status = o.get("status", "")
            if status == "filled":
                fill_p = float(o.get("filled_avg_price") or 0)
                entry  = float(tag.get("entry_price", 0))
                pnl_est= (fill_p - entry) * int(float(positions.get(sym, {}).get("qty", 0)))
                logging.info(f"[TRAIL_STOP][DAILY] {sym} trailing stop filled at "
                             f"${fill_p:.2f} (entry=${entry:.2f}, est_pnl={pnl_est:+.2f})")
            elif status in ("cancelled", "expired", "rejected"):
                # Trailing stop is gone — try to re-attach
                entry_atr = float(tag.get("entry_atr", 1.0))
                pos_qty   = int(float(positions.get(sym, {}).get("qty", 0)))
                if pos_qty > 0 and entry_atr > 0:
                    new_trail = attach_trailing_stop(sym, pos_qty, entry_atr)
                    new_id    = new_trail.get("id", "")
                    if new_id:
                        tag["trail_order_id"] = new_id
                        logging.info(f"[TRAIL_STOP][DAILY] Re-attached trailing stop for "
                                     f"{sym}: trail=${entry_atr:.2f} id={new_id}")
        except Exception as e:
            logging.warning(f"[TRAIL_STOP][DAILY] Audit failed for {sym}: {e}")



def _upgrade_trail_to_breakeven(
    positions: dict,
    position_tags: dict,
) -> None:
    """
    FIX-L v8.5: Two-phase stop upgrade — the breakeven ratchet.

    For every open position tracked in position_tags:
      Phase 1 (default): Static stop at entry - 2.0×ATR provides breathing room.
                         A tight 0.5×ATR trailing stop fires on normal noise.
      Phase 2 (upgrade): Once current_price >= entry + BREAKEVEN_ATR_TRIGGER×ATR,
                         cancel the existing trailing stop and re-submit a new
                         0.5×ATR trail whose worst-case execution is at or above
                         the entry price — guaranteeing no loss.

    The upgrade is idempotent: once 'trail_upgraded_to_breakeven' is set on a
    tag, this function skips that symbol.  Checked every 15-min cron cycle.

    Requires position_tags to carry:
        entry_price, entry_atr, trail_order_id, strategy_type
    """
    for sym, tag in list(position_tags.items()):
        try:
            # Skip if already upgraded this session
            if tag.get("trail_upgraded_to_breakeven"):
                continue

            # ── FIX-M v8.6: Delay trail until position has aged ≥ 30min ─────────
            # The 0.5×ATR trail fired 9/9 times as a loss (avg -$21.46) in 14 days.
            # Normal entry noise shakes out positions before they have room to work.
            # Static 2.0×ATR stop is the safety net during this window.
            _entry_time_str = tag.get("entry_time", "")
            if _entry_time_str:
                try:
                    _entry_dt = datetime.fromisoformat(_entry_time_str)
                    if _entry_dt.tzinfo is None:
                        _entry_dt = ET.localize(_entry_dt)
                    _elapsed_min = (datetime.now(ET) - _entry_dt).total_seconds() / 60
                    if _elapsed_min < TRAIL_ACTIVATION_MIN:
                        logging.info(
                            f"[FIX-M] {sym}: trail not yet activated "
                            f"({_elapsed_min:.0f}min < {TRAIL_ACTIVATION_MIN}min buffer) "
                            f"— static stop only"
                        )
                        continue  # Static 2.0×ATR stop protects during this window
                except Exception as _te:
                    pass  # If we can't parse time, allow trail (fail-safe)
            # ── end FIX-M ────────────────────────────────────────────────────────

            entry_price = float(tag.get("entry_price", 0))
            entry_atr   = float(tag.get("entry_atr", 0))
            trail_id    = tag.get("trail_order_id", "")

            if not entry_price or not entry_atr:
                continue

            # Get current position market price from fresh positions dict
            pos = positions.get(sym, {})
            current_price = float(pos.get("current_price") or pos.get("avg_entry_price") or 0)
            qty           = int(float(pos.get("qty", 0)))

            if not current_price or qty < 1:
                continue

            # Has price cleared the upgrade threshold?
            upgrade_threshold = entry_price + BREAKEVEN_ATR_TRIGGER * entry_atr
            if current_price < upgrade_threshold:
                continue  # Phase 1 — not yet profitable enough to upgrade

            # ── Phase 2: Cancel old trail, re-attach tighter one ─────────────────
            # New trail distance = 0.5×ATR. Since highmark is at least entry+1×ATR,
            # the trail stop floor = (entry+1×ATR) - 0.5×ATR = entry + 0.5×ATR.
            # Worst case sell price >= entry + 0.5×ATR → guaranteed profit.
            new_trail_dist = max(round(entry_atr * PROFIT_LOCK_ATR_MULT, 2), 0.05)

            # Cancel existing trailing stop
            cancelled = 0
            if trail_id:
                if cancel_order(trail_id):
                    cancelled += 1
                    logging.info(
                        f"[FIX-L] {sym}: cancelled old trail {trail_id[:8]}… "
                        f"(entry=${entry_price:.2f}, threshold=${upgrade_threshold:.2f}, "
                        f"current=${current_price:.2f})"
                    )

            # Also sweep any other lingering trailing stops for this symbol
            extra = cancel_all_trailing_stops_for_symbol(sym)
            cancelled += extra

            # Submit upgraded trail
            new_trail = attach_trailing_stop(sym, qty, new_trail_dist)
            new_id    = new_trail.get("id", "")

            if new_id:
                tag["trail_order_id"]             = new_id
                tag["trail_upgraded_to_breakeven"] = True
                tag["trail_upgrade_price"]         = round(current_price, 4)
                tag["trail_upgrade_atr"]           = round(entry_atr, 4)
                tag["trail_upgrade_floor"]         = round(
                    current_price - new_trail_dist, 4
                )
                profit_floor_est = (current_price - new_trail_dist - entry_price) * qty
                print(
                    f"  [FIX-L] ✅ BREAKEVEN UPGRADE: {sym}  "
                    f"entry=${entry_price:.2f}  current=${current_price:.2f}  "
                    f"new_trail=${new_trail_dist:.2f}  "
                    f"floor=${tag['trail_upgrade_floor']:.2f}  "
                    f"min_profit≈${profit_floor_est:+.2f}  "
                    f"new_trail_id={new_id}"
                )
                logging.info(
                    f"[FIX-L] Breakeven upgrade complete for {sym}: "
                    f"floor=${tag['trail_upgrade_floor']:.2f}, "
                    f"min_pnl≈${profit_floor_est:+.2f}"
                )
            else:
                # Attach failed — re-attach original trail as fallback
                fallback = attach_trailing_stop(sym, qty, entry_atr * PROFIT_LOCK_ATR_MULT)
                fb_id    = fallback.get("id", "")
                if fb_id:
                    tag["trail_order_id"] = fb_id
                logging.warning(
                    f"[FIX-L] {sym}: upgraded trail attach failed; fallback id={fb_id}"
                )

        except Exception as e:
            logging.warning(f"[FIX-L] Upgrade check failed for {sym}: {e}")


def get_last_sell_fill(symbol: str, after_iso: str) -> dict:
    """Find the most recent FILLED sell order for symbol after a given timestamp.
    Used to reconstruct exit details for positions closed by a broker-side
    bracket stop/take-profit fill that happened outside our own script call."""
    try:
        r = _http_session.get(f"{ALPACA_BASE}/v2/orders", headers=alpaca_headers(),
                          params={"status": "closed", "symbols": symbol, "side": "sell",
                                  "after": after_iso, "limit": 10, "direction": "desc"},
                          timeout=10)
        r.raise_for_status()
        for o in r.json():
            if o.get("status") == "filled" and o.get("filled_avg_price"):
                return o
    except Exception as e:
        print(f"  [WARN] get_last_sell_fill failed for {symbol}: {e}")
    return None


def reconcile_closed_trades(positions: dict, position_tags: dict) -> dict:
    """v7.5: Detect tagged positions that have since closed — most importantly,
    broker-side bracket stop-loss / take-profit fills that happen between runs
    and never pass through our own sell-signal code path. Reconstructs the full
    trade record (entry + exit + pnl + hold time + exit reason + entry context)
    and appends it to a permanent, strategy-tagged trade log so we can analyze
    real strategy performance (win rate by strategy, by exit reason, by entry
    conditions like extension/ATR/VIX) over weeks and months — not just the
    last few hours that fit in the rolling signals_history window."""
    closed_trades = []
    for sym in list(position_tags.keys()):
        if sym in positions:
            continue  # still open — nothing to reconcile
        tag = position_tags[sym]
        entry_time  = tag.get("entry_time")
        entry_price = tag.get("entry_price")
        if not entry_time or entry_price is None:
            del position_tags[sym]
            continue
        fill = get_last_sell_fill(sym, entry_time)
        if not fill:
            continue  # exit not confirmed yet — retry next run, leave tag in place
        try:
            exit_price = float(fill.get("filled_avg_price") or 0)
            qty        = float(fill.get("filled_qty") or 0)
        except (TypeError, ValueError):
            continue
        if exit_price <= 0 or qty <= 0:
            continue
        exit_time = fill.get("filled_at") or fill.get("updated_at")
        pnl       = (exit_price - entry_price) * qty
        pnl_pct   = (exit_price - entry_price) / entry_price * 100
        try:
            hold_minutes = (
                datetime.fromisoformat(exit_time.replace("Z", "+00:00")) -
                datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            ).total_seconds() / 60
        except Exception:
            hold_minutes = None
        order_type  = fill.get("type")
        order_class = fill.get("order_class")
        if order_type == "stop":
            exit_reason = "stop_loss"
        elif order_type == "limit" and order_class == "bracket":
            exit_reason = "take_profit"
        else:
            exit_reason = "signal_or_manual"
        closed_trades.append({
            "symbol": sym,
            "strategy": tag.get("strategy", "unknown"),
            "strategy_type": tag.get("strategy_type", "unknown"),
            "entry_time": entry_time, "entry_price": entry_price,
            "exit_time": exit_time, "exit_price": round(exit_price, 4),
            "qty": qty, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 3),
            "hold_minutes": round(hold_minutes, 1) if hold_minutes is not None else None,
            "exit_reason": exit_reason,
            "entry_vix": tag.get("entry_vix"),
            "entry_atr": tag.get("entry_atr"),
            "entry_stop_price": tag.get("stop_price"),
            "entry_tp_price": tag.get("tp_price"),
            "entry_indicators": tag.get("entry_indicators", {}),
            "logged_at": datetime.now(timezone.utc).isoformat(),
        })
        del position_tags[sym]

    if closed_trades:
        log = load_json_from_github(STRATEGY_LOG_FILE)
        if not isinstance(log, list):
            log = []
        log.extend(closed_trades)
        write_github_log(STRATEGY_LOG_FILE, log)
        print(f"  [TRADE LOG] Reconciled {len(closed_trades)} closed trade(s) -> {STRATEGY_LOG_FILE} "
              f"({len(log)} total)")
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



def compute_high52w_ratio(df: pd.DataFrame) -> float:
    """high52w (George & Hwang 2004): close / 252-day max close.
    Returns ratio in [0,1]. Values < 0.75 = >25% below 52w high.
    Returns None if fewer than 252 bars available.  FIX-F v7.9"""
    if len(df) < 252:
        return None
    close = df["close"]
    ratio = float(close.iloc[-1] / close.rolling(252).max().iloc[-1])
    return round(ratio, 4)


def compute_ret12w(df: pd.DataFrame) -> float | None:
    """12-week (63-bar) return for trend-direction confirmation.
    Returns None if < 64 bars.  FIX-F v7.9 rev1"""
    if len(df) < 64:
        return None
    close = df["close"]
    return float(close.iloc[-1] / close.iloc[-63] - 1)


def compute_illiq_score(df: pd.DataFrame) -> float:
    """Amihud (2002) illiquidity: 21d avg of |daily_ret| / dollar_volume.
    Lower = more liquid. Typical large-cap US: 1e-10 to 5e-9.
    Threshold for block: > 1e-8 (calibrated vs real US large/mid-cap universe).
    Returns None if insufficient data.  FIX-F v7.9"""
    if len(df) < 22 or "volume" not in df.columns:
        return None
    close  = df["close"].tail(22)
    volume = df["volume"].tail(22)
    ret    = close.pct_change().abs()
    dvol   = close * volume
    illiq  = (ret / dvol.replace(0, float("nan"))).dropna()
    if len(illiq) < 5:
        return None
    return float(illiq.rolling(21).mean().iloc[-1])

# ─── FIX-G v8.0: Factor Additions (Academic + Qlib158) ────────────────────

def compute_cord60(df: pd.DataFrame) -> float | None:
    """
    qlib158 cord60 — 60-period rolling Pearson correlation between
    daily returns and changes in log-volume.  Positive = volume supports
    the trend (institutional accumulation).  Negative = price rising on
    thin volume (weak, potentially shortable rally).
    Threshold: enter long ONLY when cord60 > 0.20 on breakout signals.
    Reference: qlib158 factor zoo (MSFT Research, 2020).
    """
    try:
        if len(df) < 62:
            return None
        ret     = df["close"].pct_change()
        log_dvol = (df["volume"] * df["close"]).apply(lambda x: float('nan') if x <= 0 else __import__('math').log(x + 1)).diff()
        paired  = ret.align(log_dvol, join='inner')
        corr_series = paired[0].rolling(60).corr(paired[1])
        val = corr_series.iloc[-1]
        return float(val) if val == val else None  # nan guard
    except Exception:
        return None


def compute_imax20(df: pd.DataFrame) -> float | None:
    """
    qlib158 imax20 — relative time-step position of the highest high
    over the last 20 periods: ts_argmax(high, 20) / 20.
    Value close to 1.0 = high occurred recently (momentum fresh).
    Value close to 0.0 = high occurred at start of window (momentum faded — bull trap risk).
    Block breakout entries when imax20 < 0.25 (high was ages ago, price drifted).
    Reference: qlib158 factor zoo (MSFT Research, 2020).
    """
    try:
        if len(df) < 21:
            return None
        highs = df["high"].iloc[-20:].values
        argmax_pos = int(highs.argmax())   # 0 = oldest bar, 19 = latest
        return float(argmax_pos) / 19.0    # normalise to 0→1 (1.0 = peak just now)
    except Exception:
        return None


def compute_strev21(df: pd.DataFrame) -> float | None:
    """
    Jegadeesh (1990) short-term reversal — negative of the trailing
    21-day total return.  High positive z-score = recent losers
    (counter-trend bounce candidates).  Used to ALLOW mean-reversion
    buys on oversold conditions even when trend indicators are flat.
    Reference: Jegadeesh, N. (1990). Evidence of predictable behavior
    of security returns. Journal of Finance.
    """
    try:
        if len(df) < 23:
            return None
        ret21 = df["close"].iloc[-1] / df["close"].iloc[-22] - 1.0
        return float(-ret21)   # positive when stock fell last 21 days
    except Exception:
        return None


def compute_ma200(df: pd.DataFrame) -> float | None:
    """
    200-day simple moving average — long-term trend filter (Faber 2007).
    Returns the ratio close / MA200.  > 1.0 = above MA200 (long bias OK).
    < 1.0 = below MA200 (only take counter-trend / reversal longs, not breakouts).
    Reference: Faber, M. (2007). A Quantitative Approach to Tactical
    Asset Allocation. Journal of Wealth Management.
    """
    try:
        if len(df) < 200:
            return None
        ma200 = df["close"].iloc[-200:].mean()
        return float(df["close"].iloc[-1] / ma200) if ma200 > 0 else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────


def get_spy_opening_range_pct() -> float:
    """SPY first-30min opening range as a decimal fraction. RULE 5 (FIX-E v7.8)."""
    try:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        r = requests.get(
            f"{ALPACA_DATA}/v2/stocks/SPY/bars",
            headers=alpaca_headers(),
            params={"timeframe": "15Min",
                    "start": f"{today}T09:30:00-04:00",
                    "end":   f"{today}T10:00:00-04:00",
                    "limit": 3, "adjustment": "raw", "feed": "iex"},
            timeout=10,
        )
        bars = r.json().get("bars", []) if r.ok else []
        if not bars:
            return 0.0
        high = max(b["h"] for b in bars)
        low  = min(b["l"] for b in bars)
        ref  = bars[0]["o"]
        return round((high - low) / ref, 5) if ref > 0 else 0.0
    except Exception as e:
        print(f"[WARN] get_spy_opening_range_pct failed: {e}")
        return 0.0



# ── FIX-G v8.0: Signal persistence cache ─────────────────────────────────
# Crossover signals fire on ONE bar. We extend the window to 3 bars so
# a signal that fired on the 10:45 candle is still eligible at 11:00 and 11:15.
# This prevents valid entries from being missed just due to cron timing.
_signal_hold_cache: dict = {}   # {(strat_name, symbol): bars_remaining}
_SIGNAL_HOLD_BARS  = 3          # hold window: 3 bars (~45min intraday, 3 days daily)


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

    # BUG-012 (v7.6): Weekday-only check in the GitHub Actions workflow does not
    # account for market holidays (e.g. July 3 observed-Friday close for July 4th).
    # On a holiday, orders never fill (stay "accepted") but the workflow keeps
    # firing every 15 min, stacking duplicate unfilled bracket buy orders that
    # could all execute at once on the next real open. Verify against Alpaca's
    # actual trading calendar before doing anything else.
    try:
        today_str = run_start.strftime("%Y-%m-%d")
        cal_r = _http_session.get(f"{ALPACA_BASE}/v2/calendar",
                              headers=alpaca_headers(),
                              params={"start": today_str, "end": today_str}, timeout=10)
        cal_days = cal_r.json() if cal_r.ok else []
        if not cal_days:
            print(f"[HOLIDAY_GUARD] {today_str} is not a scheduled trading day "
                  f"(market holiday or non-trading date) — exiting without placing any orders.")
            sys.exit(0)
    except Exception as e:
        print(f"[WARN] Holiday calendar check failed ({e}) — proceeding cautiously, "
              f"relying on workflow-level weekday/time gate only.")

    equity       = float(account["equity"])
    buying_power = float(account["buying_power"])
    last_equity  = float(account.get("last_equity", equity))
    print(f"Equity: ${equity:,.2f} | Buying power: ${buying_power:,.2f}")

    # ── PDT guard retired 2026-06-05 ────────────────────────────────────────
    pdt_ok, pdt_count = True, 0   # stubs; safe to remove with check_pdt_limit()

    vix = get_vix()

    # ── RULE 5: SPY opening-range volatility gate (FIX-E v7.8) ─────────────
    # Wide first-30min range → choppier price action → halve position size.
    # Threshold: 0.5% opening range (confirmed in backtest: +$94/session saved).
    _spy_open_range_pct = get_spy_opening_range_pct()
    _wide_open_day      = _spy_open_range_pct > 0.005
    if _wide_open_day:
        print(f"[WIDE_OPEN] SPY opening range {_spy_open_range_pct*100:.3f}% > 0.5% — "
              f"position sizing cut 50% in 10:00–10:30 window (FIX-E)")
    else:
        print(f"[OPEN_RANGE] SPY opening range {_spy_open_range_pct*100:.3f}% — normal sizing")

    # Fresh positions on every loop start (Rule: never stale)
    positions     = get_positions()
    position_tags = load_json_from_github(EOD_TAG_FILE)
    position_tags = reconcile_closed_trades(positions, position_tags)  # v7.5: log stop/tp fills before this run's logic

    # Live mode: deposit detection
    live_baseline = {}
    if not IS_PAPER:
        live_baseline = load_json_from_github(LIVE_BASELINE_FILE)
        live_baseline = detect_deposit(equity, live_baseline)

    # ── Stop-cooldown: detect recent Alpaca stop fills → update baseline
    _stop_cooldowns = (
        update_stop_cooldowns_from_fills(live_baseline, datetime.now(ET))
        if not IS_PAPER else {}
    )

    # ── SPY MA20 REGIME GATE (v7.1) ─────────────────────────────────────────
    # Core rule: if SPY is below its 20-day moving average the broad market is
    # in a downtrend. New BUY entries have negative expectancy in this regime.
    # We block ALL new buys. Existing positions keep their stops and can still sell.
    # Read persisted regime from live_baseline so neutral-zone logic holds correctly
    _prior_regime_is_bull = live_baseline.get("regime_label", "BULL") == "BULL" if live_baseline else True
    spy_is_bull, spy_close_now, spy_ma20_now = get_spy_ma20_regime(prior_is_bull=_prior_regime_is_bull)
    # Persist confirmed regime into baseline so next run's neutral-zone inherits it
    if not IS_PAPER and live_baseline is not None:
        live_baseline["regime_label"] = "BULL" if spy_is_bull else "BEAR"
    _ma20_bear_block = not spy_is_bull
    if _ma20_bear_block:
        print(f"[MA20_REGIME] ⚠️  BEAR MARKET — SPY ${spy_close_now:.2f} < MA20 ${spy_ma20_now:.2f}. "
              f"All new BUY entries blocked. Sells/stops still active.")
    
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

    # ── FIX-L v8.5: Breakeven trail upgrade — run before strategy signals ────
    # For any open position where price has cleared entry + 1×ATR, upgrade the
    # trailing stop to lock in profit (Phase 2). Requires fresh positions.
    positions = get_positions()   # fresh prices needed for threshold check
    try:
        _upgrade_trail_to_breakeven(positions, position_tags)
        write_github_log(EOD_TAG_FILE, position_tags)  # persist upgrade flags
    except Exception as _fixl_e:
        logging.warning(f"[FIX-L] upgrade sweep failed (non-fatal): {_fixl_e}")
    # ── end FIX-L ──────────────────────────────────────────────────────────────

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

            # ── FIX-G signal persistence (v8.0): extend crossover window ─────────
            # Crossovers fire once; re-check cache so we don't miss an entry
            # if the cron run happened to skip the exact cross bar.
            _cache_key = (strat_name, symbol)
            if signal in ("buy", "sell"):
                _signal_hold_cache[_cache_key] = _SIGNAL_HOLD_BARS   # reset counter
            elif signal == "hold" and _signal_hold_cache.get(_cache_key, 0) > 0:
                # Previous cross is still within the hold window — keep the signal
                signal = "buy" if _signal_hold_cache.get(_cache_key, 0) > 0 else "hold"
                _signal_hold_cache[_cache_key] -= 1
                if signal == "buy":
                    print(f"  {symbol}: [PERSIST] crossover signal extended "
                          f"({_signal_hold_cache[_cache_key]+1} bars remaining)")
            # Decrement even when signal was a cross (avoid double-extension)
            if _signal_hold_cache.get(_cache_key, 0) > 0 and signal in ("buy","sell"):
                _signal_hold_cache[_cache_key] = max(0, _signal_hold_cache[_cache_key] - 1)
            # ── end signal persistence ────────────────────────────────────────────


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
            elif signal == "buy" and is_in_stop_cooldown(
                    symbol, _stop_cooldowns, datetime.now(ET)):
                _exp = _stop_cooldowns.get(symbol, "")[:16].replace("T", " ")
                skip_reason = (f"stop_cooldown "
                               f"(stopped out recently — SESSION BAN until {_exp} ET, FIX-J)")
            elif signal == "buy" and strategy_type == "intraday":
                # FIX-J R3 v8.4: Weekly per-symbol concentration cap
                # 252-trade audit: single-symbol concentration caused 42.9% gross-gain dependency (HOOD)
                # and 98% loss concentration (MRVL). Cap at 3 intraday entries per symbol per week.
                _this_week   = datetime.now(ET).strftime("%Y-W%U")
                _weekly_cnts = live_baseline.get("weekly_symbol_counts", {})
                _sym_week_key= f"{symbol}:{_this_week}"
                _sym_cnt     = _weekly_cnts.get(_sym_week_key, 0)
                if _sym_cnt >= MAX_WEEKLY_ENTRIES_PER_SYMBOL:
                    skip_reason = (f"weekly_concentration_cap: {symbol} already traded "
                                   f"{_sym_cnt}× this week (max {MAX_WEEKLY_ENTRIES_PER_SYMBOL}, FIX-J R3)")
                    print(f"  [WEEKLY_CAP] {symbol}: {_sym_cnt}/{MAX_WEEKLY_ENTRIES_PER_SYMBOL} "
                          f"weekly entries used → skipping")
            elif signal == "sell" and symbol not in positions:
                skip_reason = "no_position_to_sell"
            elif signal == "buy":
                # ── FIX-F v7.9 rev1: high52w DUAL-CONDITION + illiq ─────────────────
                # FILTER 1 — high52w DUAL-CONDITION (George & Hwang 2004, corrected):
                # Block ONLY when BOTH: (a) h52w < 0.75 AND (b) ret12w < 0%
                # Single h52w blocks high-beta winners on cyclical pullbacks
                # (e.g. HOOD: h52w=0.744 but ret12w=+43% — should NOT be blocked).
                # Dual condition = structural deterioration, not a temporary dip.
                # ETFs are always exempt.
                _HIGH52W_EXEMPT = frozenset(["SPY","QQQ","IWM","GLD",
                                             "XLK","XLE","XLF","XLV","XLI","XLY","SMH","ARKK"])
                if strategy_type == "daily" and symbol not in _HIGH52W_EXEMPT:
                    _h52  = compute_high52w_ratio(df)
                    _r12w = compute_ret12w(df)
                    if _h52 is not None and _h52 < 0.75 and _r12w is not None and _r12w < 0.0:
                        skip_reason = (f"high52w_block: {symbol} h52w={_h52:.3f} "
                                       f"AND ret12w={_r12w*100:+.1f}% (structural weakness, FIX-F)")
                        print(f"  [HIGH52W] {symbol}: h52w={_h52:.3f} AND ret12w={_r12w*100:+.1f}% — blocked")
                    elif _h52 is not None and _h52 < 0.75:
                        print(f"  [HIGH52W] {symbol}: h52w={_h52:.3f} but ret12w={((_r12w or 0)*100):+.1f}% — allowed (rising)")
                # FILTER 2 — Amihud illiq: block entries on genuinely illiquid names.
                # Threshold 1e-8 is calibrated against real US large/mid-cap universe.
                # Affects both daily and intraday; ETFs always pass (illiq ≈ 1e-12).
                if not skip_reason:
                    _illiq = compute_illiq_score(df)
                    if _illiq is not None and _illiq > 1e-8:
                        skip_reason = (f"illiq_block: {symbol} illiq={_illiq:.2e} "
                                       f"> 1e-8 threshold (thin liquidity, FIX-F)")
                        print(f"  [ILLIQ] {symbol}: illiq={_illiq:.2e} > 1e-8 — blocked")
                # ── FIX-G v8.0: Academic + Qlib158 Factor Filters ───────────────────
                # R1: cord60 — volume-momentum confirmation (qlib158, entry gate)
                # Only gate breakout signals on DAILY strategies.
                # Intraday momentum doesn't require volume-correlation confirmation
                # because 15-min volume patterns are noisier.
                if not skip_reason and strategy_type == "daily":
                    _cord60 = compute_cord60(df)
                    if _cord60 is not None and _cord60 < 0.20:
                        # Weak volume-price correlation — price rising without institutional support
                        # Don't hard-block (too aggressive), but log the warning
                        print(f"  [CORD60] {symbol}: cord60={_cord60:.3f} < 0.20 — low vol-momentum confirmation")
                        # Soft flag only — do not set skip_reason (keeps signal alive but flagged)
                        inds["cord60"] = round(_cord60, 4)
                    elif _cord60 is not None:
                        inds["cord60"] = round(_cord60, 4)

                # R3: imax20 — peak timing exhaustion (qlib158)
                # Block breakout buy if the 20-bar high occurred too long ago (momentum faded).
                # Only applies to DAILY breakout strategies (not mean-reversion ones).
                _BREAKOUT_STRATS = frozenset(["ema_crossover", "triple_ema", "momentum_roc_15m"])
                if not skip_reason and strat_name in _BREAKOUT_STRATS:
                    _imax20 = compute_imax20(df)
                    if _imax20 is not None and _imax20 < 0.25:
                        skip_reason = (f"imax20_exhaustion: {symbol} imax20={_imax20:.2f} "
                                       f"< 0.25 — peak too old, bull-trap risk (FIX-G)")
                        print(f"  [IMAX20] {symbol}: imax20={_imax20:.2f} — high was too long ago, skipping")
                    elif _imax20 is not None:
                        inds["imax20"] = round(_imax20, 3)

                # R4: MA200 trend filter (Faber 2007) — daily breakout strategies only.
                # Block long entries when price is BELOW MA200 for breakout strategies.
                # Mean-reversion (rsi_macd_combo, bollinger_bands_15m) is EXEMPT —
                # those specifically target oversold conditions below MA200.
                _MA200_REQUIRED = frozenset(["ema_crossover", "triple_ema", "macd_crossover"])
                if not skip_reason and strat_name in _MA200_REQUIRED:
                    _ma200_ratio = compute_ma200(df)
                    if _ma200_ratio is not None and _ma200_ratio < 1.0:
                        skip_reason = (f"ma200_bear_block: {symbol} price/MA200={_ma200_ratio:.3f} "
                                       f"< 1.0 — below long-term trend, no breakout buys (FIX-G)")
                        print(f"  [MA200] {symbol}: ratio={_ma200_ratio:.3f} — below MA200, skip breakout")
                    elif _ma200_ratio is not None:
                        inds["ma200_ratio"] = round(_ma200_ratio, 3)

                # R2: strev21 — short-term reversal gate (Jegadeesh 1990)
                # For REVERSAL strategies (rsi_macd_combo): PREFER names with positive strev
                # (recent losers that are oversold).  For BREAKOUT strategies: this is just
                # logged for context; it doesn't block (we want winners, not mean-reversion).
                if not skip_reason:
                    _strev = compute_strev21(df)
                    if _strev is not None:
                        inds["strev21"] = round(_strev, 4)
                        # For rsi_macd_combo: require the stock to have pulled back (strev > -0.05)
                        # i.e., NOT overbought on 21-day return basis when we're looking for RSI bounce
                        if strat_name == "rsi_macd_combo" and _strev < -0.10:
                            skip_reason = (f"strev_overbought: {symbol} strev21={_strev:.3f} "
                                           f"— 21d return too high for a reversal entry (FIX-G)")
                            print(f"  [STREV] {symbol}: strev21={_strev:.3f} — stock ran too far, no reversal entry")
                # ── end FIX-G factor filters ─────────────────────────────────────────


                # FIX-P v8.7: Universal buy-time gate — extended from intraday-only to ALL strategies.
                # Problem diagnosed from live orders: daily strategies (ema_crossover, triple_ema)
                # were entering at 15:54, 16:25, 18:04 ET — post-market/AH with massive spread risk.
                # The old `strategy_type == "intraday"` guard explicitly allowed daily buys at any time.
                # Fix: Apply 14:00 ET cutoff universally. Daily holds are unaffected (exits/trails
                # are handled outside this block). Only new BUY entries are gated.
                if not skip_reason:
                    now_et_check = datetime.now(ET)
                    eod_cutoff   = now_et_check.replace(hour=14, minute=0, second=0, microsecond=0)
                    # ── RULE 3: Pre-10am entry block — intraday only (FIX-E v7.8) ────────
                    # Daily strategies use prior-day bars and are unaffected by open-range noise.
                    if strategy_type == "intraday":
                        open_gate = now_et_check.replace(hour=10, minute=0, second=0, microsecond=0)
                        if now_et_check < open_gate:
                            skip_reason = (f"pre_10am_block: {now_et_check.strftime('%H:%M')} ET — "
                                           f"intraday entries blocked before 10:00am (FIX-E)")
                    # ── end RULE 3 ──────────────────────────────────────────────────────
                    # Universal 14:00 ET buy cutoff (FIX-P v8.7) — all strategy types
                    if not skip_reason and now_et_check >= eod_cutoff:
                        skip_reason = (f"late_day_block: {now_et_check.strftime('%H:%M')} ET after 14:00 — "
                                       f"FIX-P v8.7 universal gate (strategy_type={strategy_type})")
                        print(f"  [FIX-P] {symbol}: blocked {strat_name} BUY at "
                              f"{now_et_check.strftime('%H:%M')} ET — universal 14:00 cutoff")
                # Kill switch — block all new entries when trailing drawdown >= threshold
                if not skip_reason and _kill_switch_active:
                    skip_reason = (f"kill_switch_active "
                                   f"(drawdown {drawdown_pct:.2f}% >= {MAX_DRAWDOWN_PCT}%)")
                # MA20 regime gate — block new buys when SPY is below 20-day MA (v7.1)
                elif not skip_reason and _ma20_bear_block:
                    skip_reason = (f"ma20_bear_block "
                                   f"(SPY {spy_close_now:.2f} < MA20 {spy_ma20_now:.2f})")
                elif not skip_reason:
                    # ── RULE 5: wide-open size reduction (FIX-E v7.8) ──────────────
                    # On wide-open days, 10:00–10:30 window, INTRADAY only: 50% size.
                    # Daily/swing strategies are explicitly exempt — they hold overnight
                    # and are already sized for multi-day risk, not opening-range noise.
                    _now_et_sz      = datetime.now(ET)
                    _morning_win    = (_now_et_sz.hour == 10 and _now_et_sz.minute < 30)
                    _intraday_r5    = (strategy_type == "intraday")
                    _wide_open_mult = 0.50 if (_wide_open_day and _morning_win and _intraday_r5) else 1.0
                    if _wide_open_mult < 1.0:
                        print(f"  [WIDE_OPEN_SIZE] {symbol}: 50% size — "
                              f"range={_spy_open_range_pct*100:.2f}%, morning window")
                    _eff_vix_mult = vix_mult * _wide_open_mult
                    # ── end RULE 5 ──────────────────────────────────────────────────
                    # ── FIX-G v8.0: Compute realized vol for volatility targeting ────────
                    _realized_vol = None
                    try:
                        _rets = df["close"].pct_change().dropna()
                        if len(_rets) >= 20:
                            _realized_vol = float(_rets.iloc[-20:].std())
                    except Exception:
                        pass
                    qty = atr_position_size(equity, price, atr, _eff_vix_mult,
                                            realized_vol=_realized_vol)
                    if qty < 1:
                        skip_reason = "qty_too_small"
                    elif price * qty > buying_power * 0.95:
                        skip_reason = "insufficient_buying_power"
                if not skip_reason:
                    eff_atr    = max(atr, price * 0.002)
                    stop_price = price - ATR_STOP_MULT * eff_atr
                    tp_price   = price + ATR_TP_MULT   * eff_atr
                    try:
                        order    = place_order(symbol, qty, "buy", stop_price, tp_price)
                        order_id = order.get("id"); executed = True
                        print(f"  ✓ BUY {qty} {symbol} stop={stop_price:.2f} tp={tp_price:.2f}")

                        # ── FIX-H v8.1: Attach trailing stop immediately after fill ────────────
                        # Trail distance = 1.0×ATR (tighter than entry stop, catches meaningful
                        # reversals without getting shaken out by normal noise).
                        # The static stop in the bracket is a safety net only.
                        _trail_atr   = max(eff_atr * 0.5, price * 0.002)  # FIX-H v8.1: 0.5×ATR optimal (sweep vs 38 trades)
                        # FIX-Q v8.7: Verify qty > 0 before attaching trail (race-condition guard)
                        if qty > 0:
                            _trail_order = attach_trailing_stop(symbol, qty, _trail_atr, order_id)
                        else:
                            logging.warning(f"[FIX-Q] Skipping trail attach for {symbol}: qty={qty}")
                            _trail_order = {}
                        _trail_id    = _trail_order.get("id", "")
                        if _trail_id:
                            print(f"  ✓ TRAIL_STOP attached for {symbol}: "
                                  f"trail=${_trail_atr:.2f} ({_trail_atr/price*100:.2f}%) "
                                  f"order_id={_trail_id}")
                        else:
                            print(f"  ⚠️  TRAIL_STOP attach failed for {symbol} — static stop only")
                        # ── end FIX-H ────────────────────────────────────────────────────────────

                        # FIX-J R3: Increment weekly concentration counter after confirmed buy execution
                        _this_week_inc  = datetime.now(ET).strftime("%Y-W%U")
                        _sym_week_key_i = f"{symbol}:{_this_week_inc}"
                        _wk_counts      = live_baseline.get("weekly_symbol_counts", {})
                        _wk_counts[_sym_week_key_i] = _wk_counts.get(_sym_week_key_i, 0) + 1
                        live_baseline["weekly_symbol_counts"] = _wk_counts
                        print(f"  [WEEKLY_CAP] {symbol}: {_wk_counts[_sym_week_key_i]}/{MAX_WEEKLY_ENTRIES_PER_SYMBOL} "
                              f"weekly intraday entries used this week")

                        orders_placed.append({
                            "symbol": symbol, "strat": strat_name,
                            "strategy_type": strategy_type,
                            "signal": "buy", "side": "buy", "qty": qty,
                            "price": round(price, 2), "est_value": round(price * qty, 2),
                            "stop_price": round(stop_price, 2), "tp_price": round(tp_price, 2),
                            "order_id": order_id, "trail_order_id": _trail_id,
                            "trail_price": round(_trail_atr, 3),
                            "timestamp": run_start.isoformat(),
                        })
                        # Tag entries for EOD exit + long-term trade log (v7.5 + FIX-H)
                        if strategy_type == "intraday":
                            position_tags[symbol] = {
                                "strategy": strat_name, "strategy_type": "intraday",
                                "entry_time": run_start.isoformat(),
                                "entry_price": round(price, 2),
                                "entry_vix": vix, "entry_atr": round(atr, 4),
                                "stop_price": round(stop_price, 2),
                                "tp_price": round(tp_price, 2),
                                "trail_order_id": _trail_id,   # FIX-H: EOD cancel ref
                                "trail_price": round(_trail_atr, 3),
                                "entry_indicators": inds,
                            }
                            write_github_log(EOD_TAG_FILE, position_tags)
                        else:
                            # FIX-R v8.8: Daily tag now carries the same critical fields as
                            # intraday, fixing two downstream failures:
                            # (1) _upgrade_trail_to_breakeven() was skipping ALL daily positions
                            #     because entry_price=0 tripped the 'if not entry_price: continue' guard.
                            #     Breakeven upgrade (profit-lock ratchet) NEVER fired for daily strategies.
                            # (2) FIX-O cross-strategy guard read strategy='' → no protection for daily.
                            # (3) FIX-M 30-min delay couldn't compute elapsed time (entry_time='').
                            position_tags[symbol] = {
                                "strategy":      strat_name,        # FIX-R: was missing → FIX-O blind
                                "strategy_type": "daily",
                                "entry_time":    run_start.isoformat(),  # FIX-R: was missing → FIX-M blind
                                "entry_price":   round(price, 2),   # FIX-R: was missing → breakeven upgrade skipped
                                "entry_vix":     vix,
                                "entry_atr":     round(atr, 4),
                                "stop_price":    round(stop_price, 2),
                                "tp_price":      round(tp_price, 2),
                                "trail_order_id": _trail_id,        # FIX-H: EOD cancel ref
                                "trail_price":   round(_trail_atr, 3),
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

                # ── FIX-O v8.7: Cross-strategy ownership guard ───────────────────────
                # A sell signal from strategy X must NOT close a position opened by
                # strategy Y. They have contradictory entry theses — one buying and the
                # other immediately selling is pure churn (observed: OXY opened by
                # ema_crossover, closed 6min later by bollinger_bands_15m, net -$0.45).
                # The position_tags dict stores "strategy" at entry time. We check it
                # here and block cross-strategy closes.
                # EOD sweep and trailing stops are exempt — those close unconditionally.
                if not skip_reason:
                    _owner_strat = position_tags.get(symbol, {}).get("strategy", "")
                    if _owner_strat and _owner_strat != strat_name:
                        skip_reason = (
                            f"cross_strategy_block: {symbol} opened by {_owner_strat}, "
                            f"cannot close via {strat_name} (FIX-O v8.7)"
                        )
                        print(f"  [FIX-O] {symbol}: blocked cross-strategy sell "
                              f"({strat_name} cannot close {_owner_strat} position)")
                # ── end FIX-O ────────────────────────────────────────────────────────

                # ── RULE 2: Time-stop guard — REMOVED in FIX-H v8.1 ────────────────
                # Originally suppressed signal-sells within 5–60min of entry.
                # REASON FOR REMOVAL: The backtest that motivated this rule was
                # confounded. The real problem was stop-loss distance, not hold time.
                # Stops fired early because ATR_STOP_MULT (1.5×) was too tight for
                # intraday noise — positions were stopped out by broker before the
                # strategy signal could even fire a sell. Holding longer did not fix
                # losses that were caused by tight stops on counter-trend entries.
                # The trailing stop (FIX-H) now handles exit timing properly —
                # it trails the market and fires when the move genuinely reverses,
                # which is strictly superior to a hard time-gate.
                # REPLACED BY: attach_trailing_stop() + _daily_trail_audit() (FIX-H)
                # ── end RULE 2 (removed) ─────────────────────────────────────────────

                if not skip_reason:
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
        # MA20 regime fields — consumed by algotrader_pro_dashboard
        "ma20_bear_block": _ma20_bear_block,
        "ma20_spy_close": round(spy_close_now, 2) if spy_close_now else None,
        "ma20_value": round(spy_ma20_now, 2) if spy_ma20_now else None,
        "ma20_gap_pct": round((spy_close_now - spy_ma20_now) / spy_ma20_now * 100, 3)
            if spy_close_now and spy_ma20_now else None,
        "regime_label": "BULL" if spy_is_bull else "BEAR",
    }

    # FIX-K T4: Heartbeat — write engine health timestamp to live_baseline each run
    import datetime as _hb_dt
    live_baseline["last_heartbeat"]    = _hb_dt.datetime.now(ET).isoformat()
    live_baseline["last_heartbeat_et"] = _hb_dt.datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    live_baseline["engine_version"]    = "v8.4-FIX-K"
    write_github_log(LIVE_BASELINE_FILE, live_baseline)
    print(f"  [HEARTBEAT] last_heartbeat = {live_baseline['last_heartbeat_et']}")
    write_github_log(LOG_FILE, run_log)
    write_dashboard_payload(run_log, live_baseline, position_details,
                            position_tags, spy_close_now or 0.0, spy_ma20_now or 0.0,
                            equity, last_equity, buying_power)
    append_run_history({
        "timestamp": run_start.isoformat(), "mode": MODE,
        "strategy_mode": STRATEGY_MODE, "equity": round(equity, 2),
        "last_equity": round(last_equity, 2),
        "vix": vix, "signals_count": len(all_signals),
        "orders_count": len(orders_placed),
        "symbols_traded": [o["symbol"] for o in orders_placed],
        "trading_pnl": trading_pnl, "total_deposited": total_deposited,
        "ma20_bear_block": _ma20_bear_block,
        "drawdown_pct": round(drawdown_pct, 2),
    })
    append_signals_history(all_signals)
    append_daily_equity_history(run_start.astimezone(ET).strftime("%Y-%m-%d"), equity, last_equity)
    print(f"\nRun complete — {len(all_signals)} signals | {len(orders_placed)} orders")


def write_dashboard_payload(run_log: dict, live_baseline: dict, position_details: list,
                             position_tags: dict, spy_close: float, spy_ma20: float,
                             equity: float, prev_close: float, buying_power: float) -> None:
    """Write a pre-built dashboard_payload.json to GitHub so the dashboard
    can fetch it directly via raw URL — zero Base44 integration credit cost."""
    try:
        from datetime import timezone as _tz
        positions_out = []
        total_upl = 0.0
        total_exp = 0.0
        for p in position_details:
            upl = float(p.get("unrealized_pl", 0))
            mv  = float(p.get("market_value", 0))
            total_upl += upl
            total_exp += mv
            tag = position_tags.get(p["symbol"], {})
            positions_out.append({
                "symbol":          p["symbol"],
                "qty":             p.get("qty", 0),
                "avg_entry_price": p.get("avg_entry_price", 0),
                "current_price":   p.get("current_price", 0),
                "market_value":    round(mv, 2),
                "unrealized_pl":   round(upl, 2),
                "unrealized_plpc": p.get("unrealized_plpc", 0),
                "strategy_type":   tag.get("strategy_type", p.get("strategy_type", "daily")),
                "strategy_name":   tag.get("strategy",      p.get("entry_strategy", "unknown")),
            })

        pnl_today    = round(equity - prev_close, 2)
        realized_today = round(pnl_today - total_upl, 2)

        bl      = live_baseline or {}
        peak_eq = bl.get("peak_equity",      equity)
        start_eq= bl.get("start_equity",     equity)
        tot_dep = bl.get("total_deposited",  0)
        tpnl    = bl.get("total_trading_pnl",0)
        drawdown= round((peak_eq - equity) / peak_eq * 100, 2) if peak_eq else 0

        ma20_bear = run_log.get("ma20_bear_block", False)
        regime    = run_log.get("regime_label", "UNKNOWN")
        ma20_gap  = run_log.get("ma20_gap_pct",  0)
        vix       = run_log.get("vix")
        kill      = run_log.get("kill_switch_active", False)

        # Run history from existing file (we just append to it elsewhere)
        rh_raw   = load_json_from_github(HISTORY_FILE) or []
        rh_list  = rh_raw if isinstance(rh_raw, list) else []
        recent   = sorted([e for e in rh_list if e.get("timestamp")],
                           key=lambda x: x["timestamp"], reverse=True)[:30]

        # Signals — limit to 40 for payload size
        signals  = run_log.get("signals", [])[:40]

        payload = {
            "generated_at":          datetime.now(ET).isoformat(),
            "mode":                  run_log.get("mode", "live"),
            "run_timestamp":         run_log.get("run_timestamp"),
            "equity":                round(equity, 2),
            "prev_close":            round(prev_close, 2),
            "buying_power":          round(buying_power, 2),
            "cash":                  round(buying_power, 2),   # buying_power ≈ cash when flat
            "pnl_today":             pnl_today,
            "pnl_today_pct":         round(pnl_today / prev_close * 100, 2) if prev_close else 0,
            "realized_today":        realized_today,
            "unrealized_today":      round(total_upl, 2),
            "position_count":        len(positions_out),
            "positions":             positions_out,
            "total_unrealized":      round(total_upl, 2),
            "total_exposure":        round(total_exp, 2),
            "exposure_pct":          round(total_exp / equity * 100, 2) if equity else 0,
            "ma20_bear_block":       ma20_bear,
            "ma20_value":            run_log.get("ma20_value"),
            "ma20_spy_close":        run_log.get("ma20_spy_close"),
            "ma20_gap_pct":          ma20_gap,
            "regime_label":          regime,
            "vix":                   vix,
            "drawdown_pct":          drawdown,
            "kill_switch_active":    kill,
            "peak_equity":           round(peak_eq, 2),
            "start_equity":          round(start_eq, 2),
            "total_deposited":       round(tot_dep, 2),
            "cumulative_trading_pnl":round(tpnl + total_upl, 2),
            "deposits":              bl.get("deposits", []),
            "run_history":           recent,
            "signals":               signals,
        }
        write_github_log("logs/dashboard_payload.json", payload)
        print("  [DASH] dashboard_payload.json written to GitHub")
    except Exception as e:
        print(f"  [DASH] write_dashboard_payload failed (non-fatal): {e}")


if __name__ == "__main__":
    main()