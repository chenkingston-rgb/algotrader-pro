"""
AlgoTrader Pro — Strategy Runner
Base44 logging is best-effort (never crashes the trading loop).
"""
import os, sys, json, traceback, math, requests
from datetime import datetime
from typing import Optional
import pytz

print("=== AlgoTrader Pro Starting ===")
print(f"Python: {sys.version.split()[0]}")
print(f"Time:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

MODE           = os.getenv("TRADING_MODE", "paper").lower()
BASE44_APP_ID  = os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_API_KEY = os.getenv("BASE44_API_KEY", "")
ALPACA_KEY     = os.getenv("ALPACA_PAPER_KEY")    if MODE == "paper" else os.getenv("ALPACA_LIVE_KEY")
ALPACA_SECRET  = os.getenv("ALPACA_PAPER_SECRET") if MODE == "paper" else os.getenv("ALPACA_LIVE_SECRET")
ALPACA_BASE    = "https://paper-api.alpaca.markets" if MODE == "paper" else "https://api.alpaca.markets"
ALPACA_DATA    = "https://data.alpaca.markets"
STRATEGY_FILTER = os.getenv("STRATEGY_FILTER", "").strip()

print(f"Mode:            {MODE.upper()}")
print(f"Alpaca key:      {'YES (' + ALPACA_KEY[:6] + '...)' if ALPACA_KEY else 'MISSING!'}")
print(f"Alpaca secret:   {'YES' if ALPACA_SECRET else 'MISSING!'}")
print(f"Base44 key:      {'YES' if BASE44_API_KEY else 'NO — signals wont be posted to dashboard'}")

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

def get_bars(symbol, limit=250):
    params = {"symbols": symbol, "timeframe": "1Day", "limit": limit, "feed": "iex"}
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars", headers=ah(), params=params, timeout=15)
    r.raise_for_status()
    import pandas as pd
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df.set_index("t").sort_index()[["open","high","low","close","volume"]]

def get_positions():
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=ah(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

# ── Base44 helpers (BEST-EFFORT — never crash the trading loop) ───
def b44_post(entity, record):
    """Post a record to Base44. Silent on failure."""
    if not BASE44_API_KEY:
        return
    for base_url in [
        f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
        f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
    ]:
        try:
            r = requests.post(base_url,
                headers={"Authorization": f"Bearer {BASE44_API_KEY}", "Content-Type": "application/json"},
                json=record, timeout=8)
            if r.ok:
                return
            if r.status_code == 404:
                continue   # try next URL
            print(f"  [Base44 WARN] {entity}: HTTP {r.status_code}")
            return
        except Exception:
            continue
    print(f"  [Base44 WARN] Could not post to {entity} — dashboard logging skipped")

def b44_get(entity, params=None):
    """Fetch records from Base44. Returns [] on any failure."""
    if not BASE44_API_KEY:
        return []
    for base_url in [
        f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
        f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
    ]:
        try:
            r = requests.get(base_url,
                headers={"Authorization": f"Bearer {BASE44_API_KEY}"},
                params=params, timeout=8)
            if r.ok:
                return r.json().get("items", r.json()) if isinstance(r.json(), dict) else r.json()
            if r.status_code == 404:
                continue
        except Exception:
            continue
    return []

# ── Indicators ────────────────────────────────────────────────────
def calc_rsi(close, period=14):
    import pandas as pd, numpy as np
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period-1,adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1,adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, float('nan'))))

def calc_macd(close, fast=12, slow=26, sig=9):
    macd = close.ewm(span=fast,adjust=False).mean() - close.ewm(span=slow,adjust=False).mean()
    signal = macd.ewm(span=sig,adjust=False).mean()
    return macd, signal, macd - signal

def calc_atr(df, period=14):
    import pandas as pd
    tr = pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(span=period,adjust=False).mean()

def vix_mult(strat, vix):
    if vix is None: return 1.0, "vix_unavailable"
    if vix >= strat["vix_block"]: return 0.0, f"vix_blocked({vix:.0f}>={strat['vix_block']})"
    if vix >= strat["vix_reduce"]: return strat["vix_reduce_pct"], f"vix_reduced({vix:.0f}>={strat['vix_reduce']})"
    return 1.0, "vix_clear"

def pos_size(equity, price, atr, vm=1.0):
    if atr<=0 or price<=0: return 0
    return max(1, int(min((equity*RISK_PCT*vm)/(ATR_STOP_MULT*atr),(equity*MAX_POSITION_PCT)/price)))

# ── Signal functions ──────────────────────────────────────────────
def sig_rsi_macd(df, p):
    rsi = calc_rsi(df["close"], p["rsi_period"])
    _,_,hist = calc_macd(df["close"],p["macd_fast"],p["macd_slow"],p["macd_sig"])
    r,h,ph = rsi.iloc[-1],hist.iloc[-1],hist.iloc[-2]
    if r<p["rsi_os"] and h>0 and ph<0: return "buy",  {"rsi":round(r,2),"hist":round(h,4)}
    if r>p["rsi_ob"] and h<0 and ph>0: return "sell", {"rsi":round(r,2),"hist":round(h,4)}
    return "hold", {"rsi":round(r,2),"hist":round(h,4)}

def sig_macd(df, p):
    _,_,hist = calc_macd(df["close"],p["macd_fast"],p["macd_slow"],p["macd_sig"])
    h,ph = hist.iloc[-1],hist.iloc[-2]
    if h>0 and ph<=0: return "buy",  {"hist":round(h,4)}
    if h<0 and ph>=0: return "sell", {"hist":round(h,4)}
    return "hold", {"hist":round(h,4)}

def sig_triple_ema(df, p):
    c=df["close"]
    ef,em,es=[c.ewm(span=p[k],adjust=False).mean() for k in ("ema_fast","ema_mid","ema_slow")]
    f,m,s,pf,pm,ps=ef.iloc[-1],em.iloc[-1],es.iloc[-1],ef.iloc[-2],em.iloc[-2],es.iloc[-2]
    if f>m>s and not(pf>pm>ps): return "buy",  {"ef":round(f,2),"em":round(m,2),"es":round(s,2)}
    if f<m<s and not(pf<pm<ps): return "sell", {"ef":round(f,2),"em":round(m,2),"es":round(s,2)}
    return "hold", {"ef":round(f,2),"em":round(m,2),"es":round(s,2)}

def sig_ema(df, p):
    c=df["close"]
    ef,es=c.ewm(span=p["ema_fast"],adjust=False).mean(),c.ewm(span=p["ema_slow"],adjust=False).mean()
    d,pd_=ef.iloc[-1]-es.iloc[-1],ef.iloc[-2]-es.iloc[-2]
    if d>0 and pd_<=0: return "buy",  {"diff":round(d,3)}
    if d<0 and pd_>=0: return "sell", {"diff":round(d,3)}
    return "hold", {"diff":round(d,3)}

def sig_bb(df, p):
    c=df["close"]
    mid=c.rolling(p["bb_period"]).mean(); std=c.rolling(p["bb_period"]).std()
    lower,ma200=mid-p["bb_std"]*std,c.rolling(p["ma_filter"]).mean()
    price,l,m,ma=c.iloc[-1],lower.iloc[-1],mid.iloc[-1],ma200.iloc[-1]
    if price<l and (not math.isnan(ma)) and price>ma: return "buy",  {"price":round(price,2),"lower":round(l,2)}
    if price>m:                                        return "sell", {"price":round(price,2),"mid":round(m,2)}
    return "hold", {"price":round(price,2),"lower":round(l,2),"mid":round(m,2)}

def sig_roc(df, p):
    roc=(df["close"]/df["close"].shift(p["roc_period"])-1)*100
    r,pr=roc.iloc[-1],roc.iloc[-2]
    t=p["roc_threshold"]
    if r>t  and pr<=t:  return "buy",  {"roc":round(r,3),"threshold":t}
    if r<-t and pr>=-t: return "sell", {"roc":round(r,3),"threshold":t}
    return "hold", {"roc":round(r,3)}

SIG = {"rsi_macd_combo":sig_rsi_macd,"macd_crossover":sig_macd,"triple_ema":sig_triple_ema,
       "ema_crossover":sig_ema,"bollinger_bands":sig_bb,"momentum_roc":sig_roc}

# ── Main ──────────────────────────────────────────────────────────
def main():
    import pandas as pd, numpy as np
    print(f"pandas {pd.__version__} | numpy {np.__version__}")

    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[FATAL] Alpaca keys missing. Check GitHub Secrets.")
        sys.exit(1)

    try:
        account      = get_account()
        equity       = float(account["equity"])
        buying_power = float(account["buying_power"])
        print(f"Alpaca OK — equity: ${equity:,.2f} | buying power: ${buying_power:,.2f}")
    except requests.HTTPError as e:
        print(f"[FATAL] Alpaca HTTP {e.response.status_code}: {e.response.text[:300]}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] Alpaca connection error: {e}")
        sys.exit(1)

    # Kill switch (non-fatal if Base44 unavailable)
    portfolio_records = b44_get("portfolio_state", {"limit": 5})
    if portfolio_records:
        latest = sorted(portfolio_records, key=lambda x: x.get("created_date",""))[-1]
        if latest.get("is_halted"):
            print("[KILL SWITCH ACTIVE] Reset in Base44 dashboard.")
            sys.exit(0)

    # VIX proxy
    try:
        df_v = get_bars("VIXY", 5)
        vix = round(float(df_v["close"].iloc[-1]) * 10, 1) if not df_v.empty else None
    except Exception:
        vix = None
    print(f"VIX proxy: {vix}")

    # Positions
    try:
        positions = get_positions()
        print(f"Open positions: {list(positions.keys()) or 'none'}")
    except Exception as e:
        print(f"[WARN] Positions unavailable: {e}")
        positions = {}

    peak_equity  = max([r.get("peak_equity",0) for r in portfolio_records]+[equity])
    drawdown_pct = (peak_equity-equity)/peak_equity*100 if peak_equity>0 else 0.0
    orders_placed = []

    for name, cfg in STRATEGIES.items():
        if STRATEGY_FILTER and name != STRATEGY_FILTER:
            continue
        print(f"\n--- {name} [{cfg['vix_type']}] ---")
        for symbol in cfg["symbols"]:
            try:
                df = get_bars(symbol, 250)
                if len(df) < 60:
                    print(f"  {symbol}: {len(df)} bars (need 60), skip")
                    continue
                price   = float(df["close"].iloc[-1])
                atr     = float(calc_atr(df).iloc[-1])
                signal, inds = SIG[name](df, cfg["params"])
                vm, vreason  = vix_mult(cfg, vix or 0.0)
                print(f"  {symbol}: {signal} | price={price:.2f} atr={atr:.3f} vix_mult={vm} | {inds}")

                executed = False; skip_reason = None; qty = 0; order_id = None
                stop = tp = 0.0

                if signal == "hold":
                    skip_reason = "no_signal"
                elif vm == 0.0:
                    skip_reason = vreason
                    print(f"    BLOCKED: {vreason}")
                elif signal == "buy" and symbol in positions:
                    skip_reason = "already_in_position"
                elif signal == "sell" and symbol not in positions:
                    skip_reason = "no_position_to_sell"
                else:
                    qty = pos_size(equity, price, atr, vm)
                    if qty < 1:
                        skip_reason = "qty_too_small"
                    elif price*qty > buying_power*0.95:
                        skip_reason = f"insufficient_buying_power(need ${price*qty:,.0f})"
                    else:
                        stop = price*(1-ATR_STOP_MULT*atr/price) if signal=="buy" else price*(1+ATR_STOP_MULT*atr/price)
                        tp   = price*(1+ATR_TP_MULT*atr/price)   if signal=="buy" else price*(1-ATR_TP_MULT*atr/price)
                        payload = {"symbol":symbol,"qty":str(qty),"side":signal,"type":"market",
                                   "time_in_force":"day","order_class":"bracket",
                                   "stop_loss":{"stop_price":str(round(stop,2))},
                                   "take_profit":{"limit_price":str(round(tp,2))}}
                        try:
                            r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=ah(), json=payload, timeout=10)
                            r.raise_for_status()
                            order_id = r.json().get("id","")
                            executed = True
                            print(f"    ✓ ORDER: {signal.upper()} {qty} {symbol} stop={stop:.2f} tp={tp:.2f} id={order_id[:8]}")
                            orders_placed.append(f"{signal.upper()} {qty} {symbol} via {name}")
                        except Exception as oe:
                            skip_reason = f"order_error"
                            print(f"    ORDER FAILED: {oe}")

                # Log to Base44 (best-effort)
                b44_post("signal_log", {"timestamp":datetime.now(ET).isoformat(),
                    "strategy_name":name,"symbol":symbol,"signal":signal,
                    "vix_at_signal":vix,"size_multiplier":vm,"suggested_qty":qty,
                    "atr_value":round(atr,4),"price_at_signal":round(price,2),
                    "executed":executed,"skip_reason":skip_reason,"indicator_values":inds})
                if executed:
                    b44_post("trade_log", {"symbol":symbol,"strategy_name":name,"side":signal,
                        "qty":qty,"price":round(price,2),"timestamp":datetime.now(ET).isoformat(),
                        "alpaca_order_id":order_id,"mode":MODE,"stop_price":round(stop,2),
                        "take_profit_price":round(tp,2),"atr_at_entry":round(atr,4),
                        "vix_at_entry":vix,"status":"open"})

            except Exception as e:
                print(f"  {symbol}: ERROR — {e}")
                traceback.print_exc()

    # Portfolio snapshot
    b44_post("portfolio_state", {"timestamp":datetime.now(ET).isoformat(),
        "equity":equity,"buying_power":buying_power,"peak_equity":peak_equity,
        "current_drawdown_pct":round(drawdown_pct,2),"is_halted":False,
        "mode":MODE,"vix_current":vix,"open_positions_count":len(positions)})
    if vix:
        regime = "low" if vix<15 else "elevated" if vix<25 else "high" if vix<35 else "extreme"
        b44_post("vix_history", {"date":datetime.now(ET).strftime("%Y-%m-%d"),"vix_close":vix,"regime":regime})

    print(f"\n{'='*50}")
    print(f"Done — {len(orders_placed)} order(s) placed | drawdown {drawdown_pct:.2f}%")
    for o in orders_placed: print(f"  {o}")
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
