"""
AlgoTrader Pro — GitHub Actions Strategy Runner
Dual-frequency: daily bars for trend strategies, 15-min bars for mean-rev/momentum.
Writes JSON log files back to the repo so Base44 can read them via raw.githubusercontent.com.

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

import os, sys, json, math, base64, requests
from datetime import datetime, timedelta, timezone
from typing import Optional
import pandas as pd
import numpy as np
import pytz

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
ATR_STOP_MULT    = 1.5     # Stop loss = entry - 1.5 * ATR
ATR_TP_MULT      = 3.0     # Take profit = entry + 3.0 * ATR
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
# INDICATORS
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
# STRATEGY SIGNAL FUNCTIONS
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

# ─────────────────────────────────────────────
# MAIN EXECUTION LOOP
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
    peak_equity  = equity   # We no longer have Base44 history, track conservatively
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
                    stop_price = price * (1 - ATR_STOP_MULT * atr / price) if signal == "buy" \
                                 else price * (1 + ATR_STOP_MULT * atr / price)
                    tp_price   = price * (1 + ATR_TP_MULT * atr / price)   if signal == "buy" \
                                 else price * (1 - ATR_TP_MULT * atr / price)
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
                "timestamp":        run_start.isoformat(),
                "strategy":         strat_name,
                "vix_type":         strat_cfg["vix_type"],
                "symbol":           symbol,
                "signal":           signal,
                "price":            round(price, 2),
                "atr":              round(atr, 4),
                "qty":              qty,
                "stop_price":       round(stop_price, 2) if stop_price else None,
                "tp_price":         round(tp_price, 2)   if tp_price   else None,
                "executed":         executed,
                "skip_reason":      skip_reason,
                "order_id":         order_id,
                "vix":              vix,
                "vix_reason":       vix_reason,
                "indicators":       inds,
            })

    # 6. Build and write the run log
    run_log = {
        "run_timestamp":   run_start.isoformat(),
        "mode":            MODE,
        "strategy_mode":   STRATEGY_MODE,
        "equity":          round(equity, 2),
        "buying_power":    round(buying_power, 2),
        "vix":             vix,
        "drawdown_pct":    round(drawdown_pct, 2),
        "positions":       list(positions.keys()),
        "signals":         all_signals,
        "orders_placed":   orders_placed,
    }

    print(f"\n{'='*60}")
    print(f"Run complete — {len(all_signals)} signals, {len(orders_placed)} orders placed")
    print(f"{'='*60}\n")

    # Write logs to GitHub repo
    write_github_log(LOG_FILE, run_log)

    # Append compact entry to run history
    run_summary = {
        "timestamp":       run_start.isoformat(),
        "mode":            MODE,
        "strategy_mode":   STRATEGY_MODE,
        "equity":          round(equity, 2),
        "vix":             vix,
        "signals_count":   len(all_signals),
        "orders_count":    len(orders_placed),
        "symbols_traded":  [o["symbol"] for o in orders_placed],
    }
    append_run_history(run_summary)


if __name__ == "__main__":
    main()
