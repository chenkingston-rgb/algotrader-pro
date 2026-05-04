"""
AlgoTrader Pro — Strategy Runner (with full diagnostics)
"""
import os, sys, json, traceback, math, requests
from datetime import datetime
from typing import Optional
import pytz

print("=== AlgoTrader Pro Starting ===")
print(f"Python: {sys.version}")
print(f"Time: {datetime.now()}")

# Check env vars (mask secrets)
MODE         = os.getenv("TRADING_MODE", "paper").lower()
BASE44_APP_ID= os.getenv("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_API_KEY = os.getenv("BASE44_API_KEY", "")

ALPACA_KEY    = os.getenv("ALPACA_PAPER_KEY")    if MODE == "paper" else os.getenv("ALPACA_LIVE_KEY")
ALPACA_SECRET = os.getenv("ALPACA_PAPER_SECRET") if MODE == "paper" else os.getenv("ALPACA_LIVE_SECRET")
ALPACA_BASE   = "https://paper-api.alpaca.markets" if MODE == "paper" else "https://api.alpaca.markets"
ALPACA_DATA   = "https://data.alpaca.markets"

print(f"Mode: {MODE}")
print(f"Alpaca key present:    {'YES (' + ALPACA_KEY[:6] + '...)' if ALPACA_KEY else 'NO — secret missing!'}")
print(f"Alpaca secret present: {'YES' if ALPACA_SECRET else 'NO — secret missing!'}")
print(f"Base44 key present:    {'YES' if BASE44_API_KEY else 'NO (signals wont be posted)'}")
print()

# --- Import check ---
try:
    import pandas as pd
    import numpy as np
    print(f"pandas {pd.__version__} OK | numpy {np.__version__} OK")
except ImportError as e:
    print(f"[FATAL] Import error: {e}")
    sys.exit(1)

ET = pytz.timezone("America/New_York")

RISK_PCT         = 0.01
MAX_POSITION_PCT = 0.10
ATR_STOP_MULT    = 1.5
ATR_TP_MULT      = 3.0
MAX_DRAWDOWN_PCT = 25.0

STRATEGIES = {
    "rsi_macd_combo":  {"symbols":["SPY","QQQ","IWM"],"vix_type":"COMBO","vix_block":30,"vix_reduce":22,"vix_reduce_pct":0.50,"params":{"rsi_period":14,"rsi_os":35,"rsi_ob":65,"macd_fast":12,"macd_slow":26,"macd_sig":9}},
    "macd_crossover":  {"symbols":["SPY","QQQ","IWM"],"vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"macd_fast":12,"macd_slow":26,"macd_sig":9}},
    "triple_ema":      {"symbols":["SPY","QQQ"],       "vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"ema_fast":8,"ema_mid":21,"ema_slow":55}},
    "ema_crossover":   {"symbols":["SPY","QQQ","IWM"],"vix_type":"TREND","vix_block":45,"vix_reduce":35,"vix_reduce_pct":0.60,"params":{"ema_fast":12,"ema_slow":26}},
    "bollinger_bands": {"symbols":["SPY","QQQ"],       "vix_type":"MEAN_REV","vix_block":22,"vix_reduce":18,"vix_reduce_pct":0.40,"params":{"bb_period":20,"bb_std":2.0,"ma_filter":200}},
    "momentum_roc":    {"symbols":["SPY","QQQ","XLK","XLE","XLF"],"vix_type":"MOMENTUM","vix_block":35,"vix_reduce":25,"vix_reduce_pct":0.50,"params":{"roc_period":10,"roc_threshold":1.5}},
}

def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def get_account():
    r = requests.get(f"{ALPACA_BASE}/v2/account", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_bars(symbol, limit=250):
    params = {"symbols": symbol, "timeframe": "1Day", "limit": limit, "feed": "iex"}
    r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars", headers=alpaca_headers(), params=params, timeout=15)
    r.raise_for_status()
    data = r.json().get("bars", {}).get(symbol, [])
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df["t"] = pd.to_datetime(df["t"])
    df = df.rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df.set_index("t").sort_index()[["open","high","low","close","volume"]]

def get_positions():
    r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=alpaca_headers(), timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def b44_post(entity, record):
    if not BASE44_API_KEY:
        return
    headers = {"Authorization": f"Bearer {BASE44_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
                      headers=headers, json=record, timeout=10)
    if not r.ok:
        print(f"  [WARN] Base44 {entity}: {r.status_code}")

def b44_get(entity, params=None):
    if not BASE44_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {BASE44_API_KEY}"}
    r = requests.get(f"https://api.base44.com/api/apps/{BASE44_APP_ID}/entities/{entity}",
                     headers=headers, params=params, timeout=10)
    return r.json().get("items", []) if r.ok else []

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs = gain / loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_macd(close, fast=12, slow=26, sig=9):
    macd = close.ewm(span=fast,adjust=False).mean() - close.ewm(span=slow,adjust=False).mean()
    signal = macd.ewm(span=sig,adjust=False).mean()
    return macd, signal, macd - signal

def calc_atr(df, period=14):
    tr = pd.concat([df["high"]-df["low"],(df["high"]-df["close"].shift()).abs(),(df["low"]-df["close"].shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(span=period,adjust=False).mean()

def vix_size_multiplier(strat, vix):
    if vix is None: return 1.0, "vix_unavailable"
    if vix >= strat["vix_block"]: return 0.0, f"vix_blocked({vix:.1f}>={strat['vix_block']})"
    if vix >= strat["vix_reduce"]: return strat["vix_reduce_pct"], f"vix_reduced({vix:.1f}>={strat['vix_reduce']})"
    return 1.0, "vix_clear"

def atr_position_size(equity, price, atr, vix_mult=1.0):
    if atr <= 0 or price <= 0: return 0
    return max(1, int(min((equity*RISK_PCT*vix_mult)/(ATR_STOP_MULT*atr), (equity*MAX_POSITION_PCT)/price)))

def signal_rsi_macd_combo(df, p):
    rsi = calc_rsi(df["close"], p["rsi_period"])
    _, _, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    r,h,ph = rsi.iloc[-1],hist.iloc[-1],hist.iloc[-2]
    inds = {"rsi":round(r,2),"macd_hist":round(h,4)}
    if r < p["rsi_os"] and h > 0 and ph < 0: return "buy", inds
    if r > p["rsi_ob"] and h < 0 and ph > 0: return "sell", inds
    return "hold", inds

def signal_macd_crossover(df, p):
    _, _, hist = calc_macd(df["close"], p["macd_fast"], p["macd_slow"], p["macd_sig"])
    h,ph = hist.iloc[-1],hist.iloc[-2]
    inds = {"macd_hist":round(h,4)}
    if h > 0 and ph <= 0: return "buy", inds
    if h < 0 and ph >= 0: return "sell", inds
    return "hold", inds

def signal_triple_ema(df, p):
    c = df["close"]
    ef,em,es = c.ewm(span=p["ema_fast"],adjust=False).mean(),c.ewm(span=p["ema_mid"],adjust=False).mean(),c.ewm(span=p["ema_slow"],adjust=False).mean()
    f,m,s = ef.iloc[-1],em.iloc[-1],es.iloc[-1]
    pf,pm,ps = ef.iloc[-2],em.iloc[-2],es.iloc[-2]
    inds = {"ema_fast":round(f,2),"ema_mid":round(m,2),"ema_slow":round(s,2)}
    if f>m>s and not(pf>pm>ps): return "buy", inds
    if f<m<s and not(pf<pm<ps): return "sell", inds
    return "hold", inds

def signal_ema_crossover(df, p):
    c = df["close"]
    ef,es = c.ewm(span=p["ema_fast"],adjust=False).mean(),c.ewm(span=p["ema_slow"],adjust=False).mean()
    d,pd_ = ef.iloc[-1]-es.iloc[-1],ef.iloc[-2]-es.iloc[-2]
    inds = {"ema_diff":round(d,4)}
    if d>0 and pd_<=0: return "buy", inds
    if d<0 and pd_>=0: return "sell", inds
    return "hold", inds

def signal_bollinger_bands(df, p):
    c = df["close"]
    mid = c.rolling(p["bb_period"]).mean()
    std = c.rolling(p["bb_period"]).std()
    lower,upper = mid-p["bb_std"]*std, mid+p["bb_std"]*std
    ma200 = c.rolling(p["ma_filter"]).mean()
    price,l,m,ma = c.iloc[-1],lower.iloc[-1],mid.iloc[-1],ma200.iloc[-1]
    inds = {"price":round(price,2),"bb_lower":round(l,2),"bb_mid":round(m,2)}
    if price<l and price>ma: return "buy", inds
    if price>m: return "sell", inds
    return "hold", inds

def signal_momentum_roc(df, p):
    roc = (df["close"]/df["close"].shift(p["roc_period"])-1)*100
    r,pr = roc.iloc[-1],roc.iloc[-2]
    inds = {"roc":round(r,3),"threshold":p["roc_threshold"]}
    if r>p["roc_threshold"] and pr<=p["roc_threshold"]: return "buy", inds
    if r<-p["roc_threshold"] and pr>=-p["roc_threshold"]: return "sell", inds
    return "hold", inds

SIGNAL_FNS = {"rsi_macd_combo":signal_rsi_macd_combo,"macd_crossover":signal_macd_crossover,
              "triple_ema":signal_triple_ema,"ema_crossover":signal_ema_crossover,
              "bollinger_bands":signal_bollinger_bands,"momentum_roc":signal_momentum_roc}

def main():
    # 1. Alpaca account
    if not ALPACA_KEY or not ALPACA_SECRET:
        print("[FATAL] Alpaca keys not found in environment. Check GitHub Secrets:")
        print("  ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET must be set.")
        sys.exit(1)

    try:
        account = get_account()
        equity       = float(account["equity"])
        buying_power = float(account["buying_power"])
        print(f"Alpaca account OK — equity: ${equity:,.2f} | buying power: ${buying_power:,.2f}")
    except requests.HTTPError as e:
        print(f"[FATAL] Alpaca API HTTP error: {e.response.status_code} {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"[FATAL] Cannot reach Alpaca: {e}")
        traceback.print_exc()
        sys.exit(1)

    # 2. Kill switch check
    portfolio_records = b44_get("portfolio_state", {"limit": 5, "sort": "-created_date"})
    if portfolio_records:
        latest = sorted(portfolio_records, key=lambda x: x.get("created_date",""))[-1]
        if latest.get("is_halted"):
            print("[KILL SWITCH] Trading halted. Reset in Base44 dashboard.")
            sys.exit(0)

    # 3. VIX proxy
    try:
        df_vixy = get_bars("VIXY", limit=5)
        vix = round(df_vixy["close"].iloc[-1] * 10, 2) if not df_vixy.empty else None
        print(f"VIX proxy: {vix}")
    except Exception as e:
        print(f"VIX fetch failed ({e}), proceeding without VIX filter")
        vix = None

    # 4. Positions
    try:
        positions = get_positions()
        print(f"Open positions: {list(positions.keys()) or 'none'}")
    except Exception as e:
        print(f"[WARN] Could not fetch positions: {e}")
        positions = {}

    # 5. Peak equity
    peak_equity = max([r.get("peak_equity",0) for r in portfolio_records] + [equity])
    drawdown_pct = (peak_equity-equity)/peak_equity*100 if peak_equity > 0 else 0.0

    # 6. Run strategies
    STRATEGY_FILTER = os.getenv("STRATEGY_FILTER","").strip()
    orders_placed = []

    for strat_name, strat_cfg in STRATEGIES.items():
        if STRATEGY_FILTER and strat_name != STRATEGY_FILTER:
            continue
        print(f"\n--- {strat_name} ---")
        for symbol in strat_cfg["symbols"]:
            try:
                df = get_bars(symbol, 250)
                if len(df) < 60:
                    print(f"  {symbol}: only {len(df)} bars, skipping")
                    continue
                price = df["close"].iloc[-1]
                atr   = calc_atr(df).iloc[-1]
                signal, inds = SIGNAL_FNS[strat_name](df, strat_cfg["params"])
                print(f"  {symbol}: signal={signal} price={price:.2f} atr={atr:.3f}")
                vix_mult, vix_reason = vix_size_multiplier(strat_cfg, vix or 0.0)
                executed = False
                skip_reason = None
                qty = 0
                if signal == "hold":
                    skip_reason = "no_signal"
                elif vix_mult == 0.0:
                    skip_reason = vix_reason
                    print(f"    SKIPPED: {vix_reason}")
                elif signal == "buy" and symbol in positions:
                    skip_reason = "already_in_position"
                elif signal == "sell" and symbol not in positions:
                    skip_reason = "no_position_to_sell"
                else:
                    qty = atr_position_size(equity, price, atr, vix_mult)
                    if qty < 1:
                        skip_reason = "qty_too_small"
                    elif price*qty > buying_power*0.95:
                        skip_reason = "insufficient_buying_power"
                    else:
                        stop = price*(1-ATR_STOP_MULT*atr/price) if signal=="buy" else price*(1+ATR_STOP_MULT*atr/price)
                        tp   = price*(1+ATR_TP_MULT*atr/price)   if signal=="buy" else price*(1-ATR_TP_MULT*atr/price)
                        payload = {"symbol":symbol,"qty":str(qty),"side":signal,"type":"market",
                                   "time_in_force":"day","order_class":"bracket",
                                   "stop_loss":{"stop_price":str(round(stop,2))},
                                   "take_profit":{"limit_price":str(round(tp,2))}}
                        try:
                            r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=alpaca_headers(), json=payload, timeout=10)
                            r.raise_for_status()
                            order_id = r.json().get("id")
                            executed = True
                            orders_placed.append(f"{signal.upper()} {qty} {symbol} via {strat_name}")
                            print(f"    ORDER: {signal} {qty} {symbol} | stop={stop:.2f} tp={tp:.2f} | id={order_id}")
                        except Exception as oe:
                            skip_reason = f"order_error: {oe}"
                            print(f"    ORDER FAILED: {oe}")
                b44_post("signal_log", {"timestamp":datetime.now(ET).isoformat(),"strategy_name":strat_name,
                    "symbol":symbol,"signal":signal,"vix_at_signal":vix,"size_multiplier":vix_mult,
                    "suggested_qty":qty,"atr_value":round(atr,4),"price_at_signal":round(price,2),
                    "executed":executed,"skip_reason":skip_reason,"indicator_values":inds})
                if executed:
                    b44_post("trade_log", {"symbol":symbol,"strategy_name":strat_name,"side":signal,
                        "qty":qty,"price":round(price,2),"timestamp":datetime.now(ET).isoformat(),
                        "mode":MODE,"status":"open","vix_at_entry":vix,"atr_at_entry":round(atr,4)})
            except Exception as e:
                print(f"  {symbol}: ERROR — {e}")
                traceback.print_exc()

    b44_post("portfolio_state", {"timestamp":datetime.now(ET).isoformat(),"equity":equity,
        "buying_power":buying_power,"peak_equity":peak_equity,
        "current_drawdown_pct":round(drawdown_pct,2),"is_halted":False,"mode":MODE,"vix_current":vix,
        "open_positions_count":len(positions)})

    if vix:
        regime = "low" if vix<15 else "elevated" if vix<25 else "high" if vix<35 else "extreme"
        b44_post("vix_history", {"date":datetime.now(ET).strftime("%Y-%m-%d"),"vix_close":vix,"regime":regime})

    print(f"\n=== Complete — {len(orders_placed)} orders placed ===")
    for o in orders_placed:
        print(f"  {o}")
    print(f"Drawdown: {drawdown_pct:.2f}%")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[UNHANDLED ERROR] {e}")
        traceback.print_exc()
        sys.exit(1)
