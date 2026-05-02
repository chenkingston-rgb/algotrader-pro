"""
AlgoTrader Pro — GitHub Actions Strategy Runner
Runs hourly via GitHub Actions cron. Fetches bars, computes signals,
applies risk checks, places Alpaca orders, and posts results to Base44.

Environment variables required (stored as GitHub Secrets):
  ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET
  ALPACA_LIVE_KEY  / ALPACA_LIVE_SECRET
  BASE44_API_KEY   / BASE44_APP_ID
  TRADING_MODE     (paper | live, default: paper)
  STRATEGY_FILTER  (optional: run only one strategy by name)
"""

import os, sys, json, math, requests
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np
import pytz

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
MODE            = os.getenv("TRADING_MODE", "paper").lower()
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()
BASE44_APP_ID   = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_API_KEY  = os.getenv("BASE44_API_KEY", "")
BASE44_BASE_URL = f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities"

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
# STRATEGY REGISTRY
# ─────────────────────────────────────────────
STRATEGIES = {
    "rsi_macd_combo": {
        "symbols": ["SPY", "QQQ", "IWM"],
        "vix_type": "COMBO",
        "vix_block": 30, "vix_reduce": 22, "vix_reduce_pct": 0.50,
        "params": {"rsi_period": 14, "rsi_os": 35, "rsi_ob": 65,
                   "macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
    },
    "macd_crossover": {
        "symbols": ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"macd_fast": 12, "macd_slow": 26, "macd_sig": 9},
    },
    "triple_ema": {
        "symbols": ["SPY", "QQQ"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast": 8, "ema_mid": 21, "ema_slow": 55},
    },
    "ema_crossover": {
        "symbols": ["SPY", "QQQ", "IWM"],
        "vix_type": "TREND",
        "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast": 12, "ema_slow": 26},
    },
    "bollinger_bands": {
        "symbols": ["SPY", "QQQ"],
        "vix_type": "MEAN_REV",
        "vix_block": 22, "vix_reduce": 18, "vix_reduce_pct": 0.40,
        "params": {"bb_period": 20, "bb_std": 2.0, "ma_filter": 200},
    },
    "momentum_roc": {
        "symbols": ["SPY", "QQQ", "XLK", "XLE", "XLF"],
        "vix_type": "MOMENTUM",
        "vix_block": 35, "vix_reduce": 25, "vix_reduce_pct": 0.50,
        "params": {"roc_period": 10, "roc_threshold": 1.5},
    },
}

# ─────────────────────────────────────────────
# HELPERS: ALPACA
# ─────────────────────────────────────────────
def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_bars(symbol: str, limit: int = 250) -> pd.DataFrame:
    """Fetch daily OHLCV bars from Alpaca market data."""
    params = {"symbols": symbol, "timeframe": "1Day", "limit": limit, "feed": "iex"}
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars",
                     headers=alpaca_headers(), params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df = df.set_index("t").sort_index()
    return df[["open","high","low","close","volume"]]

def get_vix() -> Optional[float]:
    """Fetch latest VIX value (using VIXY ETF as proxy if VIX not available)."""
    try:
        df = get_bars("VIXY", limit=5)
        if df.empty:
            return None
        # VIXY ≈ VIX/10 roughly, but for threshold comparison we use raw price
        # Better: use SPY implied vol or fetch ^VIX via yfinance in a separate step
        # For now use VIXY*10 as rough proxy
        return round(df["close"].iloc[-1] * 10, 2)
    except Exception as e:
        print(f"  [WARN] Could not fetch VIX proxy: {e}")
        return None

def get_positions() -> dict:
    """Return dict of symbol -> position info."""
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def place_order(symbol: str, qty: int, side: str,
                stop_price: float, take_profit: float) -> dict:
    """Place a bracket order (market entry + stop loss + take profit)."""
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "order_class": "bracket",
        "stop_loss": {"stop_price": str(round(stop_price, 2))},
        "take_profit": {"limit_price": str(round(take_profit, 2))},
    }
    r = requests.post(f"{ALPACA_BASE}/v2/orders",
                      headers=alpaca_headers(), json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

# ─────────────────────────────────────────────
# HELPERS: BASE44
# ─────────────────────────────────────────────
def b44_headers():
    return {"Authorization": f"Bearer {BASE44_API_KEY}", "Content-Type": "application/json"}

def b44_post(entity: str, record: dict):
    if not BASE44_API_KEY:
        print(f"  [SKIP] No BASE44_API_KEY — not posting to {entity}")
        return
    r = requests.post(f"{BASE44_BASE_URL}/{entity}",
                      headers=b44_headers(), json=record, timeout=10)
    if not r.ok:
        print(f"  [WARN] Base44 {entity} post failed: {r.status_code} {r.text[:200]}")

def b44_get(entity: str, params: dict = None) -> list:
    if not BASE44_API_KEY:
        return []
    r = requests.get(f"{BASE44_BASE_URL}/{entity}",
                     headers=b44_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("items", [])

def b44_patch(entity: str, record_id: str, updates: dict):
    if not BASE44_API_KEY:
        return
    r = requests.patch(f"{BASE44_BASE_URL}/{entity}/{record_id}",
                       headers=b44_headers(), json=updates, timeout=10)
    if not r.ok:
        print(f"  [WARN] Base44 PATCH {entity}/{record_id}: {r.status_code}")

# ─────────────────────────────────────────────
# INDICATORS
# ─────────────────────────────────────────────
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_macd(close: pd.Series, fast=12, slow=26, sig=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
    hist = macd_line - signal_line
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
    return mid - std_mult*std, mid, mid + std_mult*std

# ─────────────────────────────────────────────
# RISK MANAGEMENT
# ─────────────────────────────────────────────
def vix_size_multiplier(strategy: dict, vix: float) -> tuple[float, str]:
    """Returns (multiplier, reason). 0.0 = blocked."""
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
    """Risk-based position size using ATR."""
    if atr <= 0 or price <= 0:
        return 0
    dollar_risk   = equity * RISK_PCT * vix_mult
    shares_by_risk = dollar_risk / (ATR_STOP_MULT * atr)
    max_by_cap     = (equity * MAX_POSITION_PCT) / price
    return max(1, int(min(shares_by_risk, max_by_cap)))

def check_kill_switch(portfolio_records: list) -> tuple[bool, str]:
    """Return (is_halted, reason)."""
    if not portfolio_records:
        return False, ""
    latest = sorted(portfolio_records, key=lambda x: x["created_date"])[-1]
    if latest.get("is_halted"):
        return True, latest.get("halt_reason", "manual_halt")
    equity = latest.get("equity", 0)
    peak   = latest.get("peak_equity", 0)
    if peak > 0 and equity > 0:
        dd = (peak - equity) / peak * 100
        if dd >= MAX_DRAWDOWN_PCT:
            return True, f"max_drawdown_{dd:.1f}pct_exceeded"
    return False, ""

# ─────────────────────────────────────────────
# STRATEGY SIGNAL FUNCTIONS
# ─────────────────────────────────────────────
def signal_rsi_macd_combo(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    rsi = calc_rsi(df["close"], p["rsi_period"])
    macd, sig, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    r, m, s, h = rsi.iloc[-1], macd.iloc[-1], sig.iloc[-1], hist.iloc[-1]
    prev_h = hist.iloc[-2]
    inds = {"rsi": round(r,2), "macd": round(m,4), "macd_sig": round(s,4), "macd_hist": round(h,4)}
    if r < p["rsi_os"] and h > 0 and prev_h < 0:  # RSI oversold + MACD bullish cross
        return "buy", inds
    if r > p["rsi_ob"] and h < 0 and prev_h > 0:  # RSI overbought + MACD bearish cross
        return "sell", inds
    return "hold", inds

def signal_macd_crossover(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    macd, sig, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    h, prev_h = hist.iloc[-1], hist.iloc[-2]
    inds = {"macd": round(macd.iloc[-1],4), "macd_sig": round(sig.iloc[-1],4), "macd_hist": round(h,4)}
    if h > 0 and prev_h <= 0:
        return "buy", inds
    if h < 0 and prev_h >= 0:
        return "sell", inds
    return "hold", inds

def signal_triple_ema(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    c = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    em = c.ewm(span=p["ema_mid"],  adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    f, m, s = ef.iloc[-1], em.iloc[-1], es.iloc[-1]
    pf, pm, ps = ef.iloc[-2], em.iloc[-2], es.iloc[-2]
    inds = {"ema_fast": round(f,2), "ema_mid": round(m,2), "ema_slow": round(s,2)}
    if f > m > s and not (pf > pm > ps):  # All aligned bullish — new crossover
        return "buy", inds
    if f < m < s and not (pf < pm < ps):  # All aligned bearish — new crossover
        return "sell", inds
    return "hold", inds

def signal_ema_crossover(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    c = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    diff, prev_diff = ef.iloc[-1] - es.iloc[-1], ef.iloc[-2] - es.iloc[-2]
    inds = {"ema_fast": round(ef.iloc[-1],2), "ema_slow": round(es.iloc[-1],2), "diff": round(diff,4)}
    if diff > 0 and prev_diff <= 0:
        return "buy", inds
    if diff < 0 and prev_diff >= 0:
        return "sell", inds
    return "hold", inds

def signal_bollinger_bands(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    c = df["close"]
    lower, mid, upper = calc_bollinger(c, p["bb_period"], p["bb_std"])
    ma200 = c.rolling(p["ma_filter"]).mean()
    price, l, m, u, ma = c.iloc[-1], lower.iloc[-1], mid.iloc[-1], upper.iloc[-1], ma200.iloc[-1]
    inds = {"price": round(price,2), "bb_lower": round(l,2), "bb_mid": round(m,2),
            "bb_upper": round(u,2), "ma200": round(ma,2) if not np.isnan(ma) else None}
    if price < l and price > ma:   # Touch lower band AND above 200d MA
        return "buy", inds
    if price > m:                   # Return to mid-band — exit
        return "sell", inds
    return "hold", inds

def signal_momentum_roc(df: pd.DataFrame, p: dict) -> tuple[str, dict]:
    c = df["close"]
    roc = (c / c.shift(p["roc_period"]) - 1) * 100
    r, prev_r = roc.iloc[-1], roc.iloc[-2]
    inds = {"roc": round(r,3), "roc_prev": round(prev_r,3), "threshold": p["roc_threshold"]}
    if r > p["roc_threshold"] and prev_r <= p["roc_threshold"]:   # Cross above threshold
        return "buy", inds
    if r < -p["roc_threshold"] and prev_r >= -p["roc_threshold"]: # Cross below negative threshold
        return "sell", inds
    return "hold", inds

SIGNAL_FNS = {
    "rsi_macd_combo":  signal_rsi_macd_combo,
    "macd_crossover":  signal_macd_crossover,
    "triple_ema":      signal_triple_ema,
    "ema_crossover":   signal_ema_crossover,
    "bollinger_bands": signal_bollinger_bands,
    "momentum_roc":    signal_momentum_roc,
}

# ─────────────────────────────────────────────
# MAIN EXECUTION LOOP
# ─────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"AlgoTrader Pro — {datetime.now(ET).strftime('%Y-%m-%d %H:%M %Z')} [{MODE.upper()}]")
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

    # 2. Check kill switch via Base44
    portfolio_records = b44_get("portfolio_state", {"limit": 10, "sort": "-created_date"})
    is_halted, halt_reason = check_kill_switch(portfolio_records)
    if is_halted:
        print(f"\n[KILL SWITCH ACTIVE] {halt_reason} — all trading halted.")
        b44_post("portfolio_state", {
            "timestamp": datetime.now(ET).isoformat(),
            "equity": equity, "buying_power": buying_power,
            "cash": float(account.get("cash", 0)),
            "peak_equity": max([r.get("peak_equity",0) for r in portfolio_records] + [equity]),
            "current_drawdown_pct": 0,
            "is_halted": True, "halt_reason": halt_reason,
            "mode": MODE,
        })
        sys.exit(0)

    # 3. Fetch VIX
    vix = get_vix()
    print(f"VIX proxy: {vix}")

    # 4. Fetch current positions
    positions = get_positions()
    print(f"Open positions: {list(positions.keys()) or 'none'}")

    # 5. Calculate peak equity for drawdown tracking
    peak_equity = max([r.get("peak_equity", 0) for r in portfolio_records] + [equity])
    drawdown_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0

    # 6. Run each strategy
    results = []
    strats_to_run = {k: v for k, v in STRATEGIES.items()
                     if not STRATEGY_FILTER or k == STRATEGY_FILTER}

    for strat_name, strat_cfg in strats_to_run.items():
        print(f"\n--- {strat_name} [{strat_cfg['vix_type']}] ---")
        signal_fn = SIGNAL_FNS[strat_name]
        p = strat_cfg["params"]

        for symbol in strat_cfg["symbols"]:
            # Fetch bars
            try:
                df = get_bars(symbol, limit=250)
                if len(df) < 60:
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
            executed   = False
            skip_reason = None
            order_id    = None
            qty         = 0

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
                    # Place order
                    stop  = price * (1 - ATR_STOP_MULT * atr / price) if signal == "buy" else price * (1 + ATR_STOP_MULT * atr / price)
                    tp    = price * (1 + ATR_TP_MULT * atr / price)   if signal == "buy" else price * (1 - ATR_TP_MULT * atr / price)
                    try:
                        order = place_order(symbol, qty, signal, stop, tp)
                        order_id = order.get("id")
                        executed = True
                        print(f"  ✓ ORDER PLACED: {signal} {qty} {symbol} @ market | stop={stop:.2f} tp={tp:.2f} | id={order_id}")
                    except Exception as e:
                        skip_reason = f"order_error: {e}"
                        print(f"  {symbol}: order failed — {e}")

            # Post signal to Base44
            b44_post("signal_log", {
                "timestamp":       datetime.now(ET).isoformat(),
                "strategy_name":   strat_name,
                "symbol":          symbol,
                "signal":          signal,
                "vix_at_signal":   vix,
                "size_multiplier": vix_mult,
                "suggested_qty":   qty,
                "atr_value":       round(atr, 4),
                "price_at_signal": round(price, 2),
                "executed":        executed,
                "skip_reason":     skip_reason,
                "indicator_values": inds,
            })

            if executed:
                b44_post("trade_log", {
                    "symbol":         symbol,
                    "strategy_name":  strat_name,
                    "side":           signal,
                    "qty":            qty,
                    "price":          round(price, 2),
                    "timestamp":      datetime.now(ET).isoformat(),
                    "alpaca_order_id": order_id,
                    "mode":           MODE,
                    "stop_price":     round(stop, 2),
                    "take_profit_price": round(tp, 2),
                    "atr_at_entry":   round(atr, 4),
                    "vix_at_entry":   vix,
                    "status":         "open",
                })
                results.append({"symbol": symbol, "strat": strat_name, "signal": signal, "qty": qty})

    # 7. Snapshot portfolio state to Base44
    b44_post("portfolio_state", {
        "timestamp":          datetime.now(ET).isoformat(),
        "equity":             equity,
        "buying_power":       buying_power,
        "cash":               float(account.get("cash", 0)),
        "peak_equity":        peak_equity,
        "current_drawdown_pct": round(drawdown_pct, 2),
        "is_halted":          False,
        "mode":               MODE,
        "vix_current":        vix,
        "open_positions_count": len(positions),
    })

    # 8. Log VIX snapshot
    if vix is not None:
        regime = ("low" if vix < 15 else "elevated" if vix < 25 else "high" if vix < 35 else "extreme")
        b44_post("vix_history", {
            "date":      datetime.now(ET).strftime("%Y-%m-%d"),
            "vix_close": vix,
            "regime":    regime,
        })

    # 9. Summary
    print(f"\n{'='*60}")
    print(f"Run complete. Orders placed: {len(results)}")
    for r in results:
        print(f"  {r['signal'].upper()} {r['qty']} {r['symbol']} via {r['strat']}")
    print(f"Drawdown from peak: {drawdown_pct:.2f}%")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
