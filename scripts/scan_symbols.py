"""
AlgoTrader Pro — Weekly Symbol Scanner  (v1)
Runs every Sunday ~8 PM ET via GitHub Actions.

Scans a curated universe of ~120 liquid US equities.
Ranks candidates by 4-week relative strength, 12-week relative strength,
and ADX(14) trend quality. Filters out illiquid, broken, and sideways stocks.
Writes top 25 picks + core symbols to logs/watchlist_weekly.json.

The Render WebSocket engine reads this file at startup via GitHub raw URL
and subscribes to those symbols for the coming week.

Hard filters applied per symbol:
  - Price $5–$2,000
  - 30-day avg volume > 300,000 shares
  - Within 30% of 52-week high (not a broken-down stock)
  - 12-week return > -15% (no sustained downtrends)
  - ADX(14) > 18 (trending, not sideways — strategies hate chop)

Ranking score:
  0.40 × 4-week return  +  0.40 × 12-week return  +  0.20 × ADX(14)
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
# SPY/QQQ/IWM required by daily strategies; sector ETFs by intraday strategies
CORE_SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF"]

# ── Scan universe: curated liquid US large/mid caps + sector ETFs ─────────────
# Covers S&P 500 + Nasdaq 100 core, skewed toward names with sufficient volume.
# Sector ETFs included so they can surface as ranked picks if trending.
SCAN_UNIVERSE = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "AVGO", "ORCL", "AMD",
    # Large-cap tech
    "INTC", "QCOM", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ADI",
    "CRM", "NOW", "ADBE", "INTU", "PANW", "CRWD", "PLTR", "DDOG", "ZS", "SNOW",
    # Communication / Media
    "NFLX", "DIS", "CMCSA", "TMUS",
    # Consumer discretionary
    "HD", "NKE", "MCD", "CMG", "COST", "TGT", "LOW", "BKNG", "ABNB", "UBER",
    # Financials
    "JPM", "BAC", "GS", "MS", "WFC", "BLK", "SCHW", "V", "MA", "AXP", "PYPL",
    "COIN", "HOOD", "SOFI",
    # Healthcare
    "LLY", "UNH", "ABBV", "MRK", "TMO", "ABT", "ISRG", "REGN",
    # Energy
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY",
    # Industrials
    "CAT", "DE", "BA", "HON", "GE", "RTX", "LMT",
    # High-beta / momentum
    "RIVN", "MSTR", "RKLB", "IONQ", "SMCI", "ARM",
    # Sector ETFs (can rank into watchlist if trending)
    "SMH", "IBB", "ARKK", "XLV", "XLI", "XLY",
    # Broad market (always on via CORE, listed here so bulk fetch includes them)
    "SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF",
]
SCAN_UNIVERSE = list(dict.fromkeys(SCAN_UNIVERSE))  # deduplicate


# ── Alpaca helpers ────────────────────────────────────────────────────────────
def alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def get_bars_bulk(symbols: list, bar_days: int = 110) -> dict:
    """
    Fetch daily OHLCV bars for a list of symbols in batched API calls.
    Returns {symbol: [bar_dict, ...]} where each bar has keys h, l, c, v, t.
    """
    now   = datetime.now(timezone.utc)
    start = (now - timedelta(days=bar_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    results = {}
    chunk_size = 50  # stay within URL length limits

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
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/bars",
                headers=alpaca_headers(),
                params=params,
                timeout=30,
            )
            r.raise_for_status()
            for sym, bars in r.json().get("bars", {}).items():
                if bars:
                    results[sym] = bars
        except Exception as e:
            logging.warning(f"[SCAN] Bulk bar fetch error (chunk {i//chunk_size + 1}): {e}")
        _time.sleep(0.35)  # respect Alpaca rate limits

    return results


# ── Indicator: ADX(14) ────────────────────────────────────────────────────────
def compute_adx14(bars: list) -> float:
    """
    ADX(14) via Wilder's smoothing. Uses raw bar dicts with keys h, l, c.
    Returns 20.0 (neutral default) on insufficient data or any error.
    """
    if len(bars) < 20:
        return 20.0
    try:
        highs  = [b["h"] for b in bars]
        lows   = [b["l"] for b in bars]
        closes = [b["c"] for b in bars]

        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(highs)):
            tr   = max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i]  - closes[i - 1]))
            up   = highs[i]  - highs[i - 1]
            down = lows[i - 1] - lows[i]
            tr_list.append(tr)
            plus_dm.append(up   if up > down   and up > 0   else 0.0)
            minus_dm.append(down if down > up   and down > 0 else 0.0)

        def wilder(data, n=14):
            """Sum-scale Wilder smoothing — correct for TR and DM components."""
            s = [sum(data[:n])]
            for v in data[n:]:
                s.append(s[-1] - s[-1] / n + v)
            return s

        def wilder_avg(data, n=14):
            """Average-scale Wilder smoothing — correct for DX → ADX final step.
            ADX must remain in 0–100 range; initialising with average (not sum)
            is required to keep the output bounded."""
            s = [sum(data[:n]) / n]
            for v in data[n:]:
                s.append((s[-1] * (n - 1) + v) / n)
            return s

        atr14   = wilder(tr_list)
        plus14  = wilder(plus_dm)
        minus14 = wilder(minus_dm)

        dx = []
        for a, p, m in zip(atr14, plus14, minus14):
            if a == 0:
                continue
            pd_ = 100.0 * p / a
            md_ = 100.0 * m / a
            denom = pd_ + md_
            dx.append(0.0 if denom == 0 else 100.0 * abs(pd_ - md_) / denom)

        # Use wilder_avg (not wilder) for the DX→ADX step — DX values are already
        # 0–100, so we need average-scale smoothing to keep ADX in 0–100.
        return round(wilder_avg(dx)[-1], 2) if len(dx) >= 14 else 20.0
    except Exception:
        return 20.0


# ── Scoring ───────────────────────────────────────────────────────────────────
def score_symbol(symbol: str, bars: list) -> dict | None:
    """
    Apply hard filters and compute a ranking score.
    Returns a metadata dict on pass, None if the symbol is filtered out.
    """
    # Need at least 65 bars: 63 for 12-week return + 2 buffer
    if len(bars) < 65:
        return None

    closes  = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]

    price      = closes[-1]
    high_52w   = max(b["h"] for b in bars[-252:]) if len(bars) >= 252 else max(b["h"] for b in bars)
    avg_vol_30 = sum(volumes[-30:]) / 30

    # ── Hard filters ──────────────────────────────────────────────────────────
    if not (5.0 <= price <= 2000.0):
        return None
    if avg_vol_30 < 300_000:
        return None
    if price < high_52w * 0.70:          # > 30% off 52-week high → structurally broken
        return None

    ret_4w  = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 21 else 0.0
    ret_12w = (closes[-1] / closes[-63] - 1) * 100 if len(closes) > 63 else ret_4w

    if ret_12w < -15.0:                  # sustained downtrend — trend strategies lose here
        return None

    adx = compute_adx14(bars)
    if adx < 18.0:                       # sideways chop — kills EMA/MACD strategies
        return None

    # ── Ranking score ─────────────────────────────────────────────────────────
    score = 0.40 * ret_4w + 0.40 * ret_12w + 0.20 * adx

    return {
        "symbol":           symbol,
        "score":            round(score, 4),
        "price":            round(price, 2),
        "ret_4w_pct":       round(ret_4w, 2),
        "ret_12w_pct":      round(ret_12w, 2),
        "adx14":            adx,
        "avg_vol_30d":      int(avg_vol_30),
        "pct_off_52w_high": round((price / high_52w - 1) * 100, 2),
    }


# ── GitHub log writer ─────────────────────────────────────────────────────────
def write_github_log(filepath: str, content_dict: dict) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        # Local fallback for testing
        os.makedirs("logs", exist_ok=True)
        local_path = filepath
        with open(local_path, "w") as f:
            json.dump(content_dict, f, indent=2, default=str)
        logging.info(f"[LOCAL] Wrote {local_path}")
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
        "message": (
            f"[bot] Weekly symbol scan — "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        ),
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    put_r = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_r.ok:
        logging.info(f"[GITHUB] ✓ Wrote {filepath}")
        return True
    else:
        logging.error(f"[GITHUB] Failed to write {filepath}: "
                      f"{put_r.status_code} {put_r.text[:300]}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    logging.info("=" * 60)
    logging.info("  AlgoTrader Pro — Weekly Symbol Scanner")
    logging.info(f"  Universe : {len(SCAN_UNIVERSE)} symbols")
    logging.info(f"  Run time : {datetime.now(timezone.utc).isoformat()}")
    logging.info("=" * 60)

    if not ALPACA_KEY or not ALPACA_SECRET:
        logging.error("Alpaca credentials not set — exiting.")
        sys.exit(1)

    # ── Fetch bars for entire universe ────────────────────────────────────────
    logging.info("Fetching 110-day daily bars for universe...")
    all_bars = get_bars_bulk(SCAN_UNIVERSE, bar_days=110)
    logging.info(f"Received data for {len(all_bars)} / {len(SCAN_UNIVERSE)} symbols")

    # ── Score each non-core symbol ────────────────────────────────────────────
    scored, skipped = [], 0
    for sym in SCAN_UNIVERSE:
        if sym in CORE_SYMBOLS:
            continue  # cores always included, no need to rank them
        bars = all_bars.get(sym)
        if not bars:
            skipped += 1
            continue
        result = score_symbol(sym, bars)
        if result:
            scored.append(result)
        else:
            skipped += 1

    scored.sort(key=lambda x: x["score"], reverse=True)
    logging.info(f"Passed filters: {len(scored)} | Filtered/missing: {skipped}")

    # ── Select top 25 + core ──────────────────────────────────────────────────
    top_picks   = scored[:25]
    top_symbols = [s["symbol"] for s in top_picks]
    all_symbols = list(dict.fromkeys(CORE_SYMBOLS + top_symbols))

    # ── Log results ───────────────────────────────────────────────────────────
    logging.info(f"\n{'─'*55}")
    logging.info(f"  TOP 15 PICKS  (score = 0.4×ret4w + 0.4×ret12w + 0.2×ADX)")
    logging.info(f"{'─'*55}")
    for i, s in enumerate(scored[:15], 1):
        logging.info(
            f"  {i:2}. {s['symbol']:<6} | score={s['score']:7.2f} | "
            f"4w={s['ret_4w_pct']:+6.1f}% | 12w={s['ret_12w_pct']:+6.1f}% | "
            f"ADX={s['adx14']:4.1f} | vol={s['avg_vol_30d']:>10,}"
        )
    logging.info(f"{'─'*55}")
    logging.info(f"  Full watchlist ({len(all_symbols)} symbols): {all_symbols}")

    # ── Write to repo ─────────────────────────────────────────────────────────
    watchlist = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "scan_type":          "weekly",
        "universe_size":      len(SCAN_UNIVERSE),
        "candidates_passed":  len(scored),
        "candidates_filtered": skipped,
        "symbols":            all_symbols,
        "core_symbols":       CORE_SYMBOLS,
        "ranked_picks":       top_picks,
    }

    write_github_log("logs/watchlist_weekly.json", watchlist)
    logging.info("\n✓ Weekly scan complete")


if __name__ == "__main__":
    main()
