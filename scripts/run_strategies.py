"""
AlgoTrader Pro — Strategy Runner v5
Fixes: daily bar start date, SPY realized-vol VIX proxy, Base44 best-effort logging.
"""
import os, sys, json, traceback, math, requests
from datetime import datetime, timedelta, timezone
from typing import Optional
import pytz

print("=== AlgoTrader Pro Starting ===")
print(f"Python: {sys.version.split()[0]}  |  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

MODE            = os.getenv("TRADING_MODE", "paper").lower()
BASE44_APP_ID   = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_API_KEY  = os.getenv("BASE44_API_KEY", "")
ALPACA_KEY      = os.getenv("ALPACA_PAPER_KEY")    if MODE == "paper" else os.getenv("ALPACA_LIVE_KEY")
ALPACA_SECRET   = os.getenv("ALPACA_PAPER_SECRET") if MODE == "paper" else os.getenv("ALPACA_LIVE_SECRET")
ALPACA_BASE     = "https://paper-api.alpaca.markets" if MODE == "paper" else "https://api.alpaca.markets"
ALPACA_DATA     = "https://data.alpaca.markets"
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()

print(f"Mode: {MODE.upper()} | Alpaca key: {'YES ('+ALPACA_KEY[:6]+'...)' if ALPACA_KEY else 'MISSING'} | Base44: {'YES' if BASE44_API_KEY else 'NO'}")

RISK_PCT         = 0.01
MAX_POSITION_PCT = 0.10
ATR_STOP_MULT    = 1.5
ATR_TP_MULT      = 3.0
MAX_DRAWDOWN_PCT = 25.0
ET = pytz.timezone("America/New_York")

STRATEGIES = {
    "rsi_macd_combo":  {"symbols":["SPY","QQQ","IWM"],"vix_type":"COMBO","vix_block":30,"vix_reduce":22,"vix_reduce_pct":0.50,"params":{"rsi_period":14,"rsi_os":35,"rsi_ob":65,"macd_fast":12,"macd_slow":26,"macd_sig":9}},
    "macd_crossover":  {"symbols":["SPY","QQQ","IWM"],"vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"macd_fast":12,"macd_slow":26,"macd_sig":9}},
    "triple_ema":      {"symbols":["SPY","QQQ"],       "vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"ema_fast":8,"ema_mid":21,"ema_slow":55}},
    "ema_crossover":   {"symbols":["SPY","QQQ","IWM"],"vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"ema_fast":12,"ema_slow":26}},
    "bollinger_bands": {"symbols":["SPY","QQQ"],       "vix_type":"MEAN_REV","vix_block":22,"vix_reduce":18,"vix_reduce_pct":0.40,"params":{"bb_period":20,"bb_std":2.0,"ma_filter":200}},
    "momentum_roc":    {"symbols":["SPY","QQQ","XLK","XLE","XLF"],"vix_type":"MOMENTUM","vix_block":35,"vix_reduce":25,"vix_reduce_pct":0.50,"params":{"roc_period":10,"roc_threshold":1.5}},
}

# ── Alpaca helpers ────────────────────────────────────────────────
def ah():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=ah(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_bars(symbol, days=300):
    """
    Fetch daily OHLCV bars for the past N calendar days.
    Always passes explicit start/end dates so Alpaca returns full history.
    """
    import pandas as pd
    end   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "symbols":   symbol,
        "timeframe": "1Day",
        "start":     start,
        "end":       end,
        "limit":     250,
        "feed":      "iex",
        "adjustment": "split",
    }
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars", headers=ah(), params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df = df.set_index("t").sort_index()
    # Drop today's incomplete intraday bar if market is open
    now_et = datetime.now(ET)
    mkt_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    mkt_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    if mkt_open <= now_et <= mkt_close:
        today = now_et.strftime("%Y-%m-%d")
        df = df[df.index.strftime("%Y-%m-%d") < today]
    return df[["open","high","low","close","volume"]]

def get_positions():
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=ah(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def get_vix_estimate(spy_df):
    """
    Estimate VIX from SPY 21-day realized annualized volatility.
    This is the standard VIX proxy when ^VIX data isn't available.
    Realized vol tends to track VIX with ~0.85 correlation.
    """
    if len(spy_df) < 22:
        return None
    returns = spy_df["close"].pct_change().dropna().tail(21)
    realized_vol = returns.std() * (252 ** 0.5) * 100  # annualized %
    # Apply a small upward bias (implied vol typically > realized vol by ~20%)
    implied_proxy = round(realized_vol * 1.2, 1)
    return implied_proxy

# ── Base44 helpers (BEST-EFFORT) ─────────────────────────────────
B44_URLS = [
    f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities",
    f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities",
]

def b44_post(entity, record):
    if not BASE44_API_KEY:
        return
    hdrs = {"Authorization": f"Bearer {BASE44_API_KEY}", "Content-Type": "application/json"}
    for base in B44_URLS:
        try:
            r = requests.post(f"{base}/{entity}", headers=hdrs, json=record, timeout=8)
            if r.ok:
                return
            if r.status_code not in (404, 403):
                print(f"  [Base44] {entity}: HTTP {r.status_code}")
                return
        except Exception:
            pass

def b44_get(entity, params=None):
    if not BASE44_API_KEY:
        return []
    hdrs = {"Authorization": f"Bearer {BASE44_API_KEY}"}
    for base in B44_URLS:
        try:
            r = requests.get(f"{base}/{entity}", headers=hdrs, params=params, timeout=8)
            if r.ok:
                body = r.json()
                return body.get("items", body) if isinstance(body, dict) else body
        except Exception:
            pass
    return []

# ── Indicators ────────────────────────────────────────────────────
def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, float('nan'))))

def calc_macd(close, fast=12, slow=26, sig=9):
    macd = close.ewm(span=fast,adjust=False).mean() - close.ewm(span=slow,adjust=False).mean()
    signal = macd.ewm(span=sig,adjust=False).mean()
    return macd, signal, macd - signal

def calc_atr(df, period=14):
    import pandas as pd
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift()).abs(),
                    (df["low"]-df["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def vix_mult(strat, vix):
    if vix is None: return 1.0, "vix_unavailable"
    if vix >= strat["vix_block"]:   return 0.0, f"vix_blocked({vix:.0f}>={strat['vix_block']})"
    if vix >= strat["vix_reduce"]:  return strat["vix_reduce_pct"], f"vix_reduced({vix:.0f}>={strat['vix_reduce']})"
    return 1.0, "vix_clear"

def pos_size(equity, price, atr, vm=1.0):
    if atr <= 0 or price <= 0: return 0
    return max(1, int(min(
        (equity * RISK_PCT * vm) / (ATR_STOP_MULT * atr),
        (equity * MAX_POSITION_PCT) / price
    )))

# ── Signal functions ──────────────────────────────────────────────
def sig_rsi_macd(df, p):
    rsi = calc_rsi(df["close"], p["rsi_period"])
    _,_,hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    r, h, ph = rsi.iloc[-1], hist.iloc[-1], hist.iloc[-2]
    inds = {"rsi": round(r,2), "macd_hist": round(h,4)}
    if r < p["rsi_os"] and h > 0 and ph < 0: return "buy",  inds
    if r > p["rsi_ob"] and h < 0 and ph > 0: return "sell", inds
    return "hold", inds

def sig_macd(df, p):
    _,_,hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    h, ph = hist.iloc[-1], hist.iloc[-2]
    inds = {"macd_hist": round(h,4)}
    if h > 0 and ph <= 0: return "buy",  inds
    if h < 0 and ph >= 0: return "sell", inds
    return "hold", inds

def sig_triple_ema(df, p):
    c = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    em = c.ewm(span=p["ema_mid"],  adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    f, m, s   = ef.iloc[-1], em.iloc[-1], es.iloc[-1]
    pf, pm, ps = ef.iloc[-2], em.iloc[-2], es.iloc[-2]
    inds = {"ema_fast": round(f,2), "ema_mid": round(m,2), "ema_slow": round(s,2)}
    if f > m > s and not (pf > pm > ps): return "buy",  inds
    if f < m < s and not (pf < pm < ps): return "sell", inds
    return "hold", inds

def sig_ema(df, p):
    c = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    d, pd_ = ef.iloc[-1]-es.iloc[-1], ef.iloc[-2]-es.iloc[-2]
    inds = {"ema_diff": round(d,3)}
    if d > 0 and pd_ <= 0: return "buy",  inds
    if d < 0 and pd_ >= 0: return "sell", inds
    return "hold", inds

def sig_bb(df, p):
    c   = df["close"]
    mid = c.rolling(p["bb_period"]).mean()
    std = c.rolling(p["bb_period"]).std()
    lower = mid - p["bb_std"] * std
    ma200 = c.rolling(p["ma_filter"]).mean()
    price, l, m, ma = c.iloc[-1], lower.iloc[-1], mid.iloc[-1], ma200.iloc[-1]
    inds = {"price": round(price,2), "bb_lower": round(l,2), "bb_mid": round(m,2)}
    if price < l and not math.isnan(ma) and price > ma: return "buy",  inds
    if price > m:                                        return "sell", inds
    return "hold", inds

def sig_roc(df, p):
    roc = (df["close"] / df["close"].shift(p["roc_period"]) - 1) * 100
    r, pr = roc.iloc[-1], roc.iloc[-2]
    t = p["roc_threshold"]
    inds = {"roc": round(r,3), "threshold": t}
    if r >  t and pr <=  t: return "buy",  inds
    if r < -t and pr >= -t: return "sell", inds
    return "hold", inds

SIG = {
    "rsi_macd_combo": sig_rsi_macd,
    "macd_crossover": sig_macd,
    "triple_ema":     sig_triple_ema,
    "ema_crossover":  sig_ema,
    "bollinger_bands":sig_bb,
    "momentum_roc":   sig_roc,
}

# ── Main ──────────────────────────────────────────────────────────
def main():
    import pandas as pd, numpy as np
    print(f"pandas {pd.__version__} | numpy {np.__version__}")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[FATAL] Alpaca keys missing in GitHub Secrets.")
        sys.exit(1)

    # Account
    try:
        acct         = get_account()
        equity       = float(acct["equity"])
        buying_power = float(acct["buying_power"])
        print(f"Alpaca OK — equity: ${equity:,.2f} | buying power: ${buying_power:,.2f}")
    except requests.HTTPError as e:
        print(f"[FATAL] Alpaca HTTP {e.response.status_code}: {e.response.text[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] Alpaca error: {e}"); sys.exit(1)

    # Kill switch
    portfolio_records = b44_get("portfolio_state", {"limit": 5})
    if portfolio_records:
        latest = sorted(portfolio_records, key=lambda x: x.get("created_date",""))[-1]
        if latest.get("is_halted"):
            print("[KILL SWITCH] Halted — reset in Base44 dashboard.")
            sys.exit(0)

    # Fetch SPY bars first (used for VIX estimate + strategy)
    print("\nFetching SPY bars for VIX estimate...")
    try:
        spy_df = get_bars("SPY", days=300)
        print(f"SPY: {len(spy_df)} daily bars fetched (latest: {spy_df.index[-1].strftime('%Y-%m-%d') if len(spy_df) else 'none'})")
        vix = get_vix_estimate(spy_df)
        print(f"VIX (21d realized vol proxy): {vix}")
    except Exception as e:
        print(f"[WARN] SPY fetch failed: {e}")
        spy_df = None
        vix = None

    if vix and vix > 80:
        print(f"[WARN] VIX estimate {vix} seems high — capping at 80 for safety")
        vix = 80.0

    # Positions
    try:
        positions = get_positions()
        print(f"Open positions: {list(positions.keys()) or 'none'}")
    except Exception as e:
        print(f"[WARN] Positions unavailable: {e}")
        positions = {}

    peak_equity  = max([r.get("peak_equity",0) for r in portfolio_records] + [equity])
    drawdown_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0.0
    orders_placed = []

    # Strategy loop
    for name, cfg in STRATEGIES.items():
        if STRATEGY_FILTER and name != STRATEGY_FILTER:
            continue
        print(f"\n{'─'*50}")
        print(f"  {name} [{cfg['vix_type']}]  VIX={vix}  block@{cfg['vix_block']}")
        print(f"{'─'*50}")

        for symbol in cfg["symbols"]:
            try:
                # Use pre-fetched SPY df if symbol is SPY, else fetch fresh
                if symbol == "SPY" and spy_df is not None and len(spy_df) >= 60:
                    df = spy_df
                else:
                    df = get_bars(symbol, days=300)

                if len(df) < 60:
                    print(f"  {symbol}: only {len(df)} bars — need 60, skip")
                    continue

                price = float(df["close"].iloc[-1])
                atr   = float(calc_atr(df).iloc[-1])
                signal, inds = SIG[name](df, cfg["params"])
                vm, vreason  = vix_mult(cfg, vix or 0.0)

                status_icon = "🔵" if signal=="hold" else ("🟢" if signal=="buy" else "🔴")
                print(f"  {status_icon} {symbol}: signal={signal.upper()} price=${price:.2f} atr={atr:.2f} vix_mult={vm} | {inds}")

                executed = False; skip_reason = None; qty = 0; stop = tp = 0.0; order_id = None

                if signal == "hold":
                    skip_reason = "no_signal"
                elif vm == 0.0:
                    skip_reason = vreason
                    print(f"    ⛔ BLOCKED: {vreason}")
                elif signal == "buy" and symbol in positions:
                    skip_reason = "already_in_position"
                    print(f"    ⏭ Already holding {symbol}")
                elif signal == "sell" and symbol not in positions:
                    skip_reason = "no_position_to_sell"
                else:
                    qty = pos_size(equity, price, atr, vm)
                    cost = price * qty
                    if qty < 1:
                        skip_reason = "qty_too_small"
                    elif cost > buying_power * 0.95:
                        skip_reason = f"insufficient_buying_power(need ${cost:,.0f} have ${buying_power:,.0f})"
                        print(f"    💰 {skip_reason}")
                    else:
                        stop = price * (1 - ATR_STOP_MULT * atr / price) if signal == "buy" \
                               else price * (1 + ATR_STOP_MULT * atr / price)
                        tp   = price * (1 + ATR_TP_MULT * atr / price) if signal == "buy" \
                               else price * (1 - ATR_TP_MULT * atr / price)
                        payload = {
                            "symbol": symbol, "qty": str(qty),
                            "side": signal, "type": "market", "time_in_force": "day",
                            "order_class": "bracket",
                            "stop_loss":   {"stop_price":    str(round(stop, 2))},
                            "take_profit": {"limit_price":   str(round(tp,   2))},
                        }
                        try:
                            r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=ah(), json=payload, timeout=10)
                            r.raise_for_status()
                            order_id = r.json().get("id", "")
                            executed = True
                            orders_placed.append(f"{signal.upper()} {qty} {symbol} via {name}")
                            print(f"    ✅ ORDER: {signal.upper()} {qty} {symbol} @ market")
                            print(f"       stop=${stop:.2f}  tp=${tp:.2f}  id={order_id[:8]}")
                        except requests.HTTPError as oe:
                            skip_reason = f"order_http_{oe.response.status_code}"
                            print(f"    ❌ ORDER FAILED ({oe.response.status_code}): {oe.response.text[:200]}")
                        except Exception as oe:
                            skip_reason = "order_error"
                            print(f"    ❌ ORDER ERROR: {oe}")

                # Log to Base44 (best-effort)
                b44_post("signal_log", {
                    "timestamp": datetime.now(ET).isoformat(),
                    "strategy_name": name, "symbol": symbol, "signal": signal,
                    "vix_at_signal": vix, "size_multiplier": vm, "suggested_qty": qty,
                    "atr_value": round(atr,4), "price_at_signal": round(price,2),
                    "executed": executed, "skip_reason": skip_reason, "indicator_values": inds,
                })
                if executed:
                    b44_post("trade_log", {
                        "symbol": symbol, "strategy_name": name, "side": signal,
                        "qty": qty, "price": round(price,2),
                        "timestamp": datetime.now(ET).isoformat(),
                        "alpaca_order_id": order_id, "mode": MODE,
                        "stop_price": round(stop,2), "take_profit_price": round(tp,2),
                        "atr_at_entry": round(atr,4), "vix_at_entry": vix, "status": "open",
                    })

            except Exception as e:
                print(f"  {symbol}: UNEXPECTED ERROR — {e}")
                traceback.print_exc()

    # Portfolio snapshot
    b44_post("portfolio_state", {
        "timestamp": datetime.now(ET).isoformat(),
        "equity": equity, "buying_power": buying_power,
        "peak_equity": peak_equity, "current_drawdown_pct": round(drawdown_pct, 2),
        "is_halted": False, "mode": MODE, "vix_current": vix,
        "open_positions_count": len(positions),
    })
    if vix:
        regime = "low" if vix<15 else "elevated" if vix<25 else "high" if vix<35 else "extreme"
        b44_post("vix_history", {"date": datetime.now(ET).strftime("%Y-%m-%d"), "vix_close": vix, "regime": regime})

    print(f"\n{'='*50}")
    print(f"✅ Complete — {len(orders_placed)} order(s) | drawdown {drawdown_pct:.2f}%")
    for o in orders_placed:
        print(f"   {o}")
    print('='*50)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[UNHANDLED ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)

