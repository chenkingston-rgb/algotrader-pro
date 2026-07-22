"""
AlgoTrader Pro — Weekly Symbol Scanner  (v2)
Runs every Sunday ~8 PM ET via GitHub Actions.

Scans a curated universe of ~120 liquid US equities.
Ranks candidates by 4-week relative strength, 12-week relative strength,
and ADX(14) trend quality. Writes results to logs/watchlist_weekly.json.

V2 CHANGE — SPLIT WATCHLIST BY STRATEGY TYPE:
  Previously a single ADX > 18 filter was applied to all candidates.
  This was correct for trend strategies (MACD, EMA) but WRONG for Bollinger
  Bands mean reversion, which works best on ranging/low-ADX stocks.

  Now two candidate pools are generated:
    TREND pool    — ADX > 20, strong 4/12-week return. Top 15 picks.
    MEAN_REV pool — ADX 10–22, within 10% of 52-week high, sideways price.
                    Scored by price stability (low volatility). Top 8 picks.

  Both pools + core symbols are merged into watchlist_weekly.json.
  The Render engine and GitHub Actions workflows both read the same file.

Hard filters (shared):
  - Price $5–$2,000
  - 30-day avg volume > 300,000 shares
  - Within 30% of 52-week high
  - 12-week return > -15%

Trend pool additional filters:
  - ADX(14) > 20
  - Ranked by: 0.40×ret_4w + 0.40×ret_12w + 0.20×ADX

Mean-reversion pool additional filters:
  - ADX(14) between 10 and 22 (ranging, not trending)
  - 4-week return between -8% and +8% (not breaking out or crashing)
  - Ranked by: 0.60×(1/realized_vol_5d) + 0.40×(proximity to SMA20)
    i.e. tightest range + closest to its own moving average
"""

import os, sys, json, math, base64, logging, requests
import time as _time
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Credentials ──────────────────────────────────────────────────────────────
IS_PAPER      = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
ALPACA_KEY    = os.environ.get("ALPACA_PAPER_KEY" if IS_PAPER else "ALPACA_LIVE_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_PAPER_SECRET" if IS_PAPER else "ALPACA_LIVE_SECRET")
ALPACA_DATA   = "https://data.alpaca.markets"

GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

# ── Core symbols — always in watchlist, never scored out ─────────────────────
CORE_SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF"]

# ── Scan universe ─────────────────────────────────────────────────────────────
SCAN_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "ORCL", "AMD",
    "INTC", "QCOM", "MU", "AMAT", "LRCX", "KLAC", "ADI",  # FIX-J R6: TXN and MRVL removed (252-trade audit: TXN -$215, MRVL -$289)
    "CRM", "NOW", "ADBE", "INTU", "PANW", "CRWD", "PLTR", "DDOG", "ZS", "SNOW",
    "NFLX", "DIS", "CMCSA", "TMUS",
    "HD", "NKE", "MCD", "CMG", "COST", "TGT", "LOW", "BKNG", "ABNB", "UBER",
    "JPM", "BAC", "GS", "MS", "WFC", "BLK", "SCHW", "V", "MA", "AXP", "PYPL",
    "COIN", "HOOD", "SOFI",
    "LLY", "UNH", "ABBV", "MRK", "TMO", "ABT", "ISRG", "REGN",
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY",
    "CAT", "DE", "BA", "HON", "GE", "RTX", "LMT",
    "RIVN", "MSTR", "RKLB", "IONQ", "SMCI", "ARM",
    "SMH", "IBB", "ARKK", "XLV", "XLI", "XLY",
    "SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF",
]
SCAN_UNIVERSE = list(dict.fromkeys(SCAN_UNIVERSE))


# ── Alpaca helpers ────────────────────────────────────────────────────────────
def alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def get_bars_bulk(symbols: list, bar_days: int = 110) -> dict:
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=bar_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    results = {}
    chunk_size = 50
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]
        params = {
            "symbols":   ",".join(chunk),
            "timeframe": "1Day",
            "start":     start,
            "end":       end,
            "limit":     10000,
            "feed":      "iex",
            "sort":      "asc",
        }
        try:
            r = requests.get(f"{ALPACA_DATA}/v2/stocks/bars",
                             headers=alpaca_headers(), params=params, timeout=30)
            r.raise_for_status()
            for sym, bars in r.json().get("bars", {}).items():
                if bars:
                    results[sym] = bars
        except Exception as e:
            logging.warning(f"[SCAN] Bulk bar fetch error (chunk {i//chunk_size+1}): {e}")
        _time.sleep(0.35)
    return results


# ── ADX(14) ───────────────────────────────────────────────────────────────────
def compute_adx14(bars: list) -> float:
    if len(bars) < 20:
        return 20.0
    try:
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]
        closes = [b["c"] for b in bars]
        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(highs)):
            tr   = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            up   = highs[i]  - highs[i-1]
            down = lows[i-1] - lows[i]
            tr_list.append(tr)
            plus_dm.append(up   if up > down   and up > 0   else 0.0)
            minus_dm.append(down if down > up   and down > 0 else 0.0)

        def wilder(data, n=14):
            s = [sum(data[:n])]
            for v in data[n:]:
                s.append(s[-1] - s[-1] / n + v)
            return s

        def wilder_avg(data, n=14):
            s = [sum(data[:n]) / n]
            for v in data[n:]:
                s.append((s[-1] * (n-1) + v) / n)
            return s

        atr14   = wilder(tr_list)
        plus14  = wilder(plus_dm)
        minus14 = wilder(minus_dm)
        dx = []
        for a, p, m in zip(atr14, plus14, minus14):
            if a == 0: continue
            pd_ = 100.0 * p / a
            md_ = 100.0 * m / a
            denom = pd_ + md_
            dx.append(0.0 if denom == 0 else 100.0 * abs(pd_ - md_) / denom)
        return round(wilder_avg(dx)[-1], 2) if len(dx) >= 14 else 20.0
    except Exception:
        return 20.0


# ── Realized volatility (5-day, close-to-close) ───────────────────────────────
def compute_realized_vol_5d(bars: list) -> float:
    """Annualised 5-day close-to-close vol. Lower = better for mean reversion."""
    if len(bars) < 7:
        return 999.0
    closes = [b["c"] for b in bars[-7:]]
    log_rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
    if len(log_rets) < 2:
        return 999.0
    mean = sum(log_rets) / len(log_rets)
    var  = sum((r - mean)**2 for r in log_rets) / (len(log_rets) - 1)
    return round(math.sqrt(var) * math.sqrt(252) * 100, 2)


# ── Shared hard filters ────────────────────────────────────────────────────────
def apply_hard_filters(symbol: str, bars: list) -> dict | None:
    """Returns base metrics if symbol passes shared hard filters, else None."""
    if len(bars) < 65:
        return None
    closes  = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]
    price      = closes[-1]
    high_52w   = max(b["h"] for b in bars[-252:]) if len(bars) >= 252 else max(b["h"] for b in bars)
    avg_vol_30 = sum(volumes[-30:]) / 30
    if not (5.0 <= price <= 2000.0):       return None
    if avg_vol_30 < 300_000:               return None
    if price < high_52w * 0.70:            return None
    ret_4w  = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 21 else 0.0
    ret_12w = (closes[-1] / closes[-63] - 1) * 100 if len(closes) > 63 else ret_4w
    if ret_12w < -15.0:                    return None
    return {
        "symbol": symbol, "price": round(price,2),
        "ret_4w_pct": round(ret_4w,2), "ret_12w_pct": round(ret_12w,2),
        "avg_vol_30d": int(avg_vol_30),
        "pct_off_52w_high": round((price/high_52w-1)*100, 2),
    }


# ── Trend pool scoring ────────────────────────────────────────────────────────
def compute_illiq_filter(bars: list) -> bool:
    """Return True (PASS) if symbol is liquid enough to trade.
    Amihud illiquidity > 5e-8 → reject (too thin, wide spread risk).  FIX-F v7.9"""
    if len(bars) < 22:
        return True  # not enough data — pass through (don't reject on data gaps)
    closes  = [b["c"] for b in bars[-22:]]
    volumes = [b["v"] for b in bars[-22:]]
    illiq_vals = []
    for i in range(1, len(closes)):
        prev = closes[i-1]
        if prev <= 0 or volumes[i] <= 0 or closes[i] <= 0:
            continue
        ret    = abs(closes[i] / prev - 1)
        dvol   = closes[i] * volumes[i]
        illiq_vals.append(ret / dvol)
    if not illiq_vals:
        return True
    avg_illiq = sum(illiq_vals) / len(illiq_vals)
    return avg_illiq <= 1e-8   # True = liquid enough (calibrated vs US large/mid-cap)


def compute_carhart_mom(bars: list) -> float:
    """Carhart (1997) 12m-1m momentum.
    Returns 12-week return minus 1-week return as a percentage delta.
    Proxy for the UMD factor; uses weekly approximations (63d/5d) since
    we operate on daily bars without a full calendar year.  FIX-F v7.9"""
    closes = [b["c"] for b in bars]
    if len(closes) < 64:
        return 0.0
    ret_12w = (closes[-1] / closes[-63] - 1) * 100 if len(closes) >= 63 else 0.0
    ret_1w  = (closes[-1] / closes[-5]  - 1) * 100 if len(closes) >= 5  else 0.0
    return round(ret_12w - ret_1w, 4)


def score_trend(symbol: str, bars: list) -> dict | None:
    """
    Trend candidates: ADX > 20, strong momentum.
    Score = 0.40×ret_4w + 0.40×ret_12w + 0.20×ADX
    """
    base = apply_hard_filters(symbol, bars)
    if base is None:
        return None
    adx = compute_adx14(bars)
    if adx < 20.0:          # must be trending for MACD/EMA strategies
        return None
    # ── Carhart momentum layer (FIX-F v7.9) ────────────────────────────────────
    # Adds 12m-1m momentum signal to the trend score.
    # Weight shift: carhart gets 0.20, adx drops from 0.20→0.15, ret_12w 0.40→0.30, ret_4w 0.40→0.35
    carhart = compute_carhart_mom(bars)
    score = (0.35 * base["ret_4w_pct"]
           + 0.30 * base["ret_12w_pct"]
           + 0.15 * adx
           + 0.20 * carhart)
    return {**base, "adx14": adx, "carhart_mom": round(carhart, 4),
            "score": round(score, 4), "pool": "TREND"}


# ── Mean-reversion pool scoring ───────────────────────────────────────────────
def score_mean_rev(symbol: str, bars: list) -> dict | None:
    """
    Mean-reversion candidates for Bollinger Bands strategy.
    ADX 10–22 (ranging), 4-week return between -8% and +8% (not breaking out).
    Score = 0.60×(1/realized_vol_5d×100) + 0.40×(1 - |price/SMA20 - 1|×10)
    Higher score = tighter range + closer to its own 20-day moving average.
    """
    base = apply_hard_filters(symbol, bars)
    if base is None:
        return None
    adx = compute_adx14(bars)
    if not (10.0 <= adx <= 22.0):                    # must be ranging
        return None
    if not (-8.0 <= base["ret_4w_pct"] <= 8.0):      # no strong directional move
        return None
    # Realized vol component
    rvol = compute_realized_vol_5d(bars)
    if rvol <= 0 or rvol > 200:
        return None
    vol_score = 100.0 / rvol   # inverse: lower vol = higher score

    # SMA20 proximity component
    closes  = [b["c"] for b in bars]
    sma20   = sum(closes[-20:]) / 20
    sma_dist = abs(base["price"] / sma20 - 1.0) * 10   # 0 = perfect, 1 = 10% away
    sma_score = max(0.0, 1.0 - sma_dist)

    score = 0.60 * vol_score + 0.40 * sma_score
    return {**base, "adx14": adx, "realized_vol_5d": rvol, "score": round(score, 4), "pool": "MEAN_REV"}


# ── GitHub log writer ─────────────────────────────────────────────────────────
def write_github_log(filepath: str, content_dict: dict) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        os.makedirs("logs", exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(content_dict, f, indent=2, default=str)
        logging.info(f"[LOCAL] Wrote {filepath}")
        return True
    content_b64 = base64.b64encode(
        json.dumps(content_dict, indent=2, default=str).encode()
    ).decode()
    headers = {
        "Authorization":        f"token {GITHUB_TOKEN}",
        "Content-Type":         "application/json",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{filepath}"
    get_r   = requests.get(api_url, headers=headers, timeout=10)
    sha     = get_r.json().get("sha") if get_r.ok else None
    payload = {
        "message": f"[bot] Weekly scan v2 — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha
    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        logging.info(f"[GITHUB] ✓ Wrote {filepath}")
        return True
    else:
        logging.error(f"[GITHUB] Failed: {put_r.status_code} {put_r.text[:300]}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    run_time = datetime.now(timezone.utc)
    logging.info("=" * 60)
    logging.info("  AlgoTrader Pro — Weekly Symbol Scanner v2")
    logging.info(f"  Run time : {run_time.isoformat()}")
    logging.info("=" * 60)

    if not ALPACA_KEY or not ALPACA_SECRET:
        logging.error("Alpaca credentials not set — exiting.")
        sys.exit(1)

    logging.info(f"Fetching bars for {len(SCAN_UNIVERSE)} symbols...")
    all_bars = get_bars_bulk(SCAN_UNIVERSE, bar_days=110)
    logging.info(f"Got bars for {len(all_bars)} symbols")

    trend_picks  = []
    meanrev_picks = []

    for sym, bars in all_bars.items():
        if sym in CORE_SYMBOLS:
            continue
        # ── FIX-F v7.9: Pre-screen for liquidity (Amihud illiq > 5e-8 → skip) ──
        if not compute_illiq_filter(bars):
            logging.info(f"[ILLIQ_FILTER] {sym}: rejected (Amihud illiq > 1e-8 — thin liquidity)")
            continue
        t = score_trend(sym, bars)
        if t:
            trend_picks.append(t)
        m = score_mean_rev(sym, bars)
        if m:
            meanrev_picks.append(m)

    trend_picks.sort(key=lambda x: x["score"], reverse=True)
    meanrev_picks.sort(key=lambda x: x["score"], reverse=True)

    top_trend   = trend_picks[:15]
    top_meanrev = meanrev_picks[:8]

    # Merge: core first, then trend, then mean-rev (deduplicated)
    trend_syms   = [s["symbol"] for s in top_trend]
    meanrev_syms = [s["symbol"] for s in top_meanrev]
    all_symbols  = list(dict.fromkeys(CORE_SYMBOLS + trend_syms + meanrev_syms))

    # ── FIX-T v8.9: Pool-specific symbol lists now included in output ────────────
    # This allows run_strategies.py to route each strategy to its correct pool.
    # TREND pool:    stocks with ADX>20, strong Carhart momentum (use for breakout/momentum strategies)
    # MEAN_REV pool: stocks with ADX 10-22, ranging, tight realized vol (use for Bollinger/RSI-MR)
    # CORE symbols:  always included in ALL strategies regardless of pool
    # DO NOT flatten these pools into a single symbol list for strategy routing.
    output = {
        "generated_at":       run_time.isoformat(),
        "scan_type":          "weekly_v3_carhart",  # FIX-F v7.9: Carhart momentum + illiq filter
        "universe_size":      len(SCAN_UNIVERSE),
        "trend_candidates":   len(trend_picks),
        "meanrev_candidates": len(meanrev_picks),
        "symbols":            all_symbols,           # merged: for backward compat + daily scan input
        "core_symbols":       CORE_SYMBOLS,
        "trend_symbols":      trend_syms,            # FIX-T v8.9: TREND pool symbols only
        "meanrev_symbols":    meanrev_syms,          # FIX-T v8.9: MEAN_REV pool symbols only
        "trend_picks":        top_trend,
        "meanrev_picks":      top_meanrev,
    }

    write_github_log("logs/watchlist_weekly.json", output)

    # FIX-T v8.9: Write a dedicated mean-reversion watchlist consumed exclusively
    # by bollinger_bands_15m and rsi_mean_reversion_15m strategies.
    # This prevents the pool-routing loss that occurs when the merged 'symbols'
    # list is applied uniformly to all strategies.
    meanrev_output = {
        "generated_at":    run_time.isoformat(),
        "scan_type":       "weekly_meanrev",
        "symbols":         list(dict.fromkeys(CORE_SYMBOLS + meanrev_syms)),
        "core_symbols":    CORE_SYMBOLS,
        "meanrev_symbols": meanrev_syms,
        "meanrev_picks":   top_meanrev,
        "note":            "Used by bollinger_bands_15m and rsi_mean_reversion_15m strategies only.",
    }
    write_github_log("logs/watchlist_meanrev.json", meanrev_output)
    logging.info(
        f"Done: {len(all_symbols)} symbols total "
        f"({len(top_trend)} trend + {len(top_meanrev)} mean-rev + {len(CORE_SYMBOLS)} core)"
    )
    logging.info(f"Trend: {trend_syms}")
    logging.info(f"MeanRev: {meanrev_syms}")


if __name__ == "__main__":
    main()
