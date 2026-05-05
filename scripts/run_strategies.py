"""
AlgoTrader Pro — Unified Strategy Runner v6
STRATEGY_MODE=daily   → 4 trend strategies on daily bars, run at 9:45am + 3:30pm ET
STRATEGY_MODE=intraday → 2 mean-rev/momentum strategies on 15-min bars, run every 15min
"""
import os, sys, json, traceback, math, requests
from datetime import datetime, timedelta, timezone
from typing import Optional
import pytz

ET = pytz.timezone("America/New_York")
UTC = timezone.utc

MODE            = os.getenv("TRADING_MODE", "paper").lower()
STRATEGY_MODE   = os.getenv("STRATEGY_MODE", "daily").lower()   # "daily" or "intraday"
BASE44_APP_ID   = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_API_KEY  = os.getenv("BASE44_API_KEY", "")
ALPACA_KEY      = os.getenv("ALPACA_PAPER_KEY")    if MODE == "paper" else os.getenv("ALPACA_LIVE_KEY")
ALPACA_SECRET   = os.getenv("ALPACA_PAPER_SECRET") if MODE == "paper" else os.getenv("ALPACA_LIVE_SECRET")
ALPACA_BASE     = "https://paper-api.alpaca.markets" if MODE == "paper" else "https://api.alpaca.markets"
ALPACA_DATA     = "https://data.alpaca.markets"
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()

RISK_PCT         = 0.01
MAX_POSITION_PCT = 0.10
ATR_STOP_MULT    = 1.5
ATR_TP_MULT      = 3.0
MAX_DRAWDOWN_PCT = 25.0

# ── Strategy registry ─────────────────────────────────────────────
# DAILY strategies — run on 1Day bars, triggered at 9:45am + 3:30pm ET
DAILY_STRATEGIES = {
    "rsi_macd_combo": {
        "symbols": ["SPY","QQQ","IWM"],
        "vix_type": "COMBO", "vix_block": 30, "vix_reduce": 22, "vix_reduce_pct": 0.50,
        "params": {"rsi_period":14,"rsi_os":35,"rsi_ob":65,"macd_fast":12,"macd_slow":26,"macd_sig":9},
    },
    "macd_crossover": {
        "symbols": ["SPY","QQQ","IWM"],
        "vix_type": "TREND", "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"macd_fast":12,"macd_slow":26,"macd_sig":9},
    },
    "triple_ema": {
        "symbols": ["SPY","QQQ"],
        "vix_type": "TREND", "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast":8,"ema_mid":21,"ema_slow":55},
    },
    "ema_crossover": {
        "symbols": ["SPY","QQQ","IWM"],
        "vix_type": "TREND", "vix_block": 45, "vix_reduce": 35, "vix_reduce_pct": 0.60,
        "params": {"ema_fast":12,"ema_slow":26},
    },
}

# INTRADAY strategies — run on 15Min bars, triggered every 15min
# Parameters re-calibrated for 15-minute timeframe:
#   BB: 50-period MA filter (50 * 15min ≈ 2 trading days as trend filter)
#   ROC: 0.3% threshold (daily 1.5% / 5 intraday sessions = 0.3% per 15min window)
INTRADAY_STRATEGIES = {
    "bollinger_bands_15m": {
        "symbols": ["SPY","QQQ","IWM"],
        "vix_type": "MEAN_REV", "vix_block": 22, "vix_reduce": 18, "vix_reduce_pct": 0.40,
        "params": {"bb_period":20,"bb_std":2.0,"ma_filter":50},
        "timeframe": "15Min", "bar_days": 20,
    },
    "momentum_roc_15m": {
        "symbols": ["SPY","QQQ","XLK","XLE","XLF"],
        "vix_type": "MOMENTUM", "vix_block": 35, "vix_reduce": 25, "vix_reduce_pct": 0.50,
        "params": {"roc_period":10,"roc_threshold":0.3},
        "timeframe": "15Min", "bar_days": 20,
    },
}

STRATEGIES = DAILY_STRATEGIES if STRATEGY_MODE == "daily" else INTRADAY_STRATEGIES
TIMEFRAME   = "1Day"  # overridden per-strategy for intraday
BAR_DAYS    = 300     # overridden per-strategy for intraday

# ── Alpaca helpers ────────────────────────────────────────────────
def ah():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=ah(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_bars(symbol, timeframe="1Day", days=300):
    """Fetch OHLCV bars. For 15Min bars, drop the last incomplete bar."""
    import pandas as pd
    now_utc = datetime.now(UTC)
    end     = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    start   = (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params  = {
        "symbols": symbol, "timeframe": timeframe,
        "start": start, "end": end,
        "limit": 500, "feed": "iex", "adjustment": "split",
    }
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars", headers=ah(), params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    df = df.set_index("t").sort_index()[["open","high","low","close","volume"]]

    # Drop last bar if it's incomplete (market still open)
    now_et    = datetime.now(ET)
    mkt_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    mkt_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    if mkt_open <= now_et <= mkt_close:
        df = df.iloc[:-1]   # drop the still-forming bar

    return df

def get_positions():
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=ah(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def get_vix_estimate(spy_df):
    """21-day realized vol × 1.2 as VIX proxy."""
    if len(spy_df) < 22:
        return None
    ret = spy_df["close"].pct_change().dropna().tail(21)
    rv  = ret.std() * (252 ** 0.5) * 100
    return round(min(rv * 1.2, 80.0), 1)

# ── Base44 (best-effort, with auth format detection) ─────────────
B44_ATTEMPTS = [
    ("https://app.base44.com/api/apps/{APP}/entities/{ENT}",  {"Authorization": "Bearer {KEY}", "Content-Type": "application/json"}),
    ("https://app.base44.com/api/apps/{APP}/entities/{ENT}",  {"x-api-key": "{KEY}", "Content-Type": "application/json"}),
    ("https://api.base44.com/api/apps/{APP}/entities/{ENT}",  {"Authorization": "Bearer {KEY}", "Content-Type": "application/json"}),
    ("https://api.base44.com/api/apps/{APP}/entities/{ENT}",  {"x-api-key": "{KEY}", "Content-Type": "application/json"}),
]

def _b44_headers(template):
    return {k: v.replace("{KEY}", BASE44_API_KEY) for k, v in template.items()}

def _b44_url(url_template, entity):
    return url_template.replace("{APP}", BASE44_APP_ID).replace("{ENT}", entity)

def b44_post(entity, record):
    if not BASE44_API_KEY:
        print(f"  [Base44] No API key — skipping {entity} post")
        return
    for url_tmpl, hdr_tmpl in B44_ATTEMPTS:
        url = _b44_url(url_tmpl, entity)
        hdrs = _b44_headers(hdr_tmpl)
        try:
            r = requests.post(url, headers=hdrs, json=record, timeout=8)
            if r.ok:
                print(f"  [Base44] ✅ Posted to {entity} via {url_tmpl[:40]}")
                return
            print(f"  [Base44] {r.status_code} on {url_tmpl[:40]} — {r.text[:80]}")
        except Exception as e:
            print(f"  [Base44] Connection error: {e}")

def b44_get(entity, params=None):
    if not BASE44_API_KEY: return []
    for url_tmpl, hdr_tmpl in B44_ATTEMPTS:
        url = _b44_url(url_tmpl, entity)
        hdrs = {k: v.replace("{KEY}", BASE44_API_KEY) for k, v in hdr_tmpl.items()}
        hdrs.pop("Content-Type", None)
        try:
            r = requests.get(url, headers=hdrs, params=params, timeout=8)
            if r.ok:
                body = r.json()
                return body.get("items", body) if isinstance(body, dict) else body
        except Exception:
            pass
    return []

# ── Indicators ────────────────────────────────────────────────────
def calc_rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, float("nan"))))

def calc_macd(close, fast=12, slow=26, sig=9):
    macd   = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal = macd.ewm(span=sig, adjust=False).mean()
    return macd, signal, macd - signal

def calc_atr(df, period=14):
    import pandas as pd
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def vix_mult(strat, vix):
    if vix is None: return 1.0, "vix_unavailable"
    if vix >= strat["vix_block"]:  return 0.0, f"vix_blocked({vix:.0f}>={strat['vix_block']})"
    if vix >= strat["vix_reduce"]: return strat["vix_reduce_pct"], f"vix_reduced({vix:.0f}>={strat['vix_reduce']})"
    return 1.0, "vix_clear"

def pos_size(equity, price, atr, vm=1.0):
    if atr <= 0 or price <= 0: return 0
    return max(1, int(min(
        (equity * RISK_PCT * vm) / (ATR_STOP_MULT * atr),
        (equity * MAX_POSITION_PCT) / price,
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
    c  = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    em = c.ewm(span=p["ema_mid"],  adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    f, m, s   = ef.iloc[-1], em.iloc[-1], es.iloc[-1]
    pf,pm,ps  = ef.iloc[-2], em.iloc[-2], es.iloc[-2]
    inds = {"ema_fast":round(f,2),"ema_mid":round(m,2),"ema_slow":round(s,2)}
    if f > m > s and not (pf > pm > ps): return "buy",  inds
    if f < m < s and not (pf < pm < ps): return "sell", inds
    return "hold", inds

def sig_ema(df, p):
    c  = df["close"]
    ef = c.ewm(span=p["ema_fast"], adjust=False).mean()
    es = c.ewm(span=p["ema_slow"], adjust=False).mean()
    d, pd_ = ef.iloc[-1]-es.iloc[-1], ef.iloc[-2]-es.iloc[-2]
    inds = {"ema_diff": round(d,3)}
    if d > 0 and pd_ <= 0: return "buy",  inds
    if d < 0 and pd_ >= 0: return "sell", inds
    return "hold", inds

def sig_bb(df, p):
    """Bollinger Bands mean reversion — works on any timeframe."""
    c     = df["close"]
    mid   = c.rolling(p["bb_period"]).mean()
    std   = c.rolling(p["bb_period"]).std()
    lower = mid - p["bb_std"] * std
    ma    = c.rolling(p["ma_filter"]).mean()
    price, l, m, ma_val = c.iloc[-1], lower.iloc[-1], mid.iloc[-1], ma.iloc[-1]
    inds  = {"price":round(price,2),"bb_lower":round(l,2),"bb_mid":round(m,2)}
    above_ma = (not math.isnan(ma_val)) and price > ma_val
    if price < l and above_ma: return "buy",  inds
    if price > m:               return "sell", inds
    return "hold", inds

def sig_roc(df, p):
    """Momentum ROC — works on any timeframe. Threshold calibrated per TF."""
    roc  = (df["close"] / df["close"].shift(p["roc_period"]) - 1) * 100
    r, pr = roc.iloc[-1], roc.iloc[-2]
    t     = p["roc_threshold"]
    inds  = {"roc":round(r,3),"threshold":t}
    if r >  t and pr <=  t: return "buy",  inds
    if r < -t and pr >= -t: return "sell", inds
    return "hold", inds

SIG = {
    "rsi_macd_combo":      sig_rsi_macd,
    "macd_crossover":      sig_macd,
    "triple_ema":          sig_triple_ema,
    "ema_crossover":       sig_ema,
    "bollinger_bands_15m": sig_bb,
    "momentum_roc_15m":    sig_roc,
}

# ── Main ──────────────────────────────────────────────────────────
def main():
    import pandas as pd, numpy as np
    now_et = datetime.now(ET)
    print(f"=== AlgoTrader Pro [{STRATEGY_MODE.upper()}] ===")
    print(f"Time: {now_et.strftime('%Y-%m-%d %H:%M %Z')} | Mode: {MODE.upper()}")
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
        print(f"[FATAL] Alpaca: {e}"); sys.exit(1)

    # Kill switch
    portfolio_records = b44_get("portfolio_state", {"limit": 5})
    if portfolio_records:
        latest = sorted(portfolio_records, key=lambda x: x.get("created_date",""))[-1]
        if latest.get("is_halted"):
            print("[KILL SWITCH] Halted — reset in Base44 dashboard."); sys.exit(0)

    # VIX estimate from SPY daily bars
    try:
        spy_daily = get_bars("SPY", "1Day", 60)
        vix = get_vix_estimate(spy_daily)
        print(f"VIX (realized vol proxy): {vix}")
    except Exception as e:
        print(f"[WARN] VIX estimate failed: {e}")
        spy_daily = None; vix = None

    # Positions
    try:
        positions = get_positions()
        print(f"Open positions: {list(positions.keys()) or 'none'}")
    except Exception as e:
        print(f"[WARN] Positions unavailable: {e}"); positions = {}

    peak_equity  = max([r.get("peak_equity",0) for r in portfolio_records]+[equity])
    drawdown_pct = (peak_equity-equity)/peak_equity*100 if peak_equity > 0 else 0.0
    orders_placed = []

    # ── Strategy loop ─────────────────────────────────────────────
    for name, cfg in STRATEGIES.items():
        if STRATEGY_FILTER and name != STRATEGY_FILTER:
            continue

        tf       = cfg.get("timeframe", "1Day")
        bar_days = cfg.get("bar_days", 300)

        print(f"\n{'─'*55}")
        print(f"  {name} [{cfg['vix_type']}] | {tf} bars | VIX={vix} block@{cfg['vix_block']}")
        print(f"{'─'*55}")

        for symbol in cfg["symbols"]:
            try:
                # Reuse spy_daily if symbol=SPY and timeframe=1Day
                if symbol == "SPY" and tf == "1Day" and spy_daily is not None and len(spy_daily) >= 60:
                    df = spy_daily
                else:
                    df = get_bars(symbol, tf, bar_days)

                if len(df) < 30:
                    print(f"  {symbol}: only {len(df)} bars (need 30), skip")
                    continue

                price = float(df["close"].iloc[-1])
                atr   = float(calc_atr(df).iloc[-1])
                signal, inds = SIG[name](df, cfg["params"])
                vm, vreason  = vix_mult(cfg, vix or 0.0)

                icon = "🔵" if signal=="hold" else ("🟢" if signal=="buy" else "🔴")
                print(f"  {icon} {symbol}: {signal.upper()} | ${price:.2f} | atr={atr:.3f} | vm={vm} | {inds}")

                executed = False; skip_reason = None; qty = 0; stop = tp = 0.0; order_id = None

                if signal == "hold":
                    skip_reason = "no_signal"
                elif vm == 0.0:
                    skip_reason = vreason
                    print(f"     ⛔ {vreason}")
                elif signal == "buy" and symbol in positions:
                    skip_reason = "already_in_position"
                elif signal == "sell" and symbol not in positions:
                    skip_reason = "no_position_to_sell"
                else:
                    qty  = pos_size(equity, price, atr, vm)
                    cost = price * qty
                    if qty < 1:
                        skip_reason = "qty_too_small"
                    elif cost > buying_power * 0.95:
                        skip_reason = f"insufficient_buying_power(need ${cost:,.0f})"
                    else:
                        stop = price*(1-ATR_STOP_MULT*atr/price) if signal=="buy" \
                               else price*(1+ATR_STOP_MULT*atr/price)
                        tp   = price*(1+ATR_TP_MULT*atr/price) if signal=="buy" \
                               else price*(1-ATR_TP_MULT*atr/price)
                        payload = {
                            "symbol":symbol,"qty":str(qty),"side":signal,
                            "type":"market","time_in_force":"day","order_class":"bracket",
                            "stop_loss":  {"stop_price":   str(round(stop,2))},
                            "take_profit":{"limit_price":  str(round(tp,2))},
                        }
                        try:
                            r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=ah(), json=payload, timeout=10)
                            r.raise_for_status()
                            order_id  = r.json().get("id","")
                            executed  = True
                            orders_placed.append(f"{signal.upper()} {qty} {symbol} via {name} ({tf})")
                            print(f"     ✅ ORDER: {signal.upper()} {qty} {symbol} stop=${stop:.2f} tp=${tp:.2f} id={order_id[:8]}")
                        except requests.HTTPError as oe:
                            skip_reason = f"order_http_{oe.response.status_code}"
                            print(f"     ❌ ORDER FAILED {oe.response.status_code}: {oe.response.text[:150]}")
                        except Exception as oe:
                            skip_reason = "order_error"
                            print(f"     ❌ ORDER ERROR: {oe}")

                b44_post("signal_log", {
                    "timestamp":datetime.now(ET).isoformat(),"strategy_name":name,
                    "symbol":symbol,"signal":signal,"vix_at_signal":vix,
                    "size_multiplier":vm,"suggested_qty":qty,"atr_value":round(atr,4),
                    "price_at_signal":round(price,2),"executed":executed,
                    "skip_reason":skip_reason,"indicator_values":inds,
                })
                if executed:
                    b44_post("trade_log", {
                        "symbol":symbol,"strategy_name":name,"side":signal,"qty":qty,
                        "price":round(price,2),"timestamp":datetime.now(ET).isoformat(),
                        "alpaca_order_id":order_id,"mode":MODE,"stop_price":round(stop,2),
                        "take_profit_price":round(tp,2),"atr_at_entry":round(atr,4),
                        "vix_at_entry":vix,"status":"open",
                    })

            except Exception as e:
                print(f"  {symbol}: ERROR — {e}")
                traceback.print_exc()

    # Portfolio snapshot
    b44_post("portfolio_state", {
        "timestamp":datetime.now(ET).isoformat(),"equity":equity,
        "buying_power":buying_power,"peak_equity":peak_equity,
        "current_drawdown_pct":round(drawdown_pct,2),"is_halted":False,
        "mode":MODE,"vix_current":vix,"open_positions_count":len(positions),
    })
    if vix:
        regime = "low" if vix<15 else "elevated" if vix<25 else "high" if vix<35 else "extreme"
        b44_post("vix_history",{"date":datetime.now(ET).strftime("%Y-%m-%d"),"vix_close":vix,"regime":regime})

    print(f"\n{'='*55}")
    print(f"✅ [{STRATEGY_MODE.upper()}] Done — {len(orders_placed)} order(s) | drawdown {drawdown_pct:.2f}%")
    for o in orders_placed: print(f"   {o}")
    print('='*55)

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[UNHANDLED ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)

