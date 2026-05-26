"""
AlgoTrader Pro — Daily Pre-Market Symbol Filter  (v2)
FIX: pre-market start uses ET-localised datetime (correct DST handling).
Runs Mon–Fri at 9:15 AM ET via GitHub Actions.

Reads the weekly watchlist and applies pre-market filters to surface
the highest-conviction intraday candidates for today's session.

Scoring logic:
  - Gap % from prior close (absolute value) — price has moved into a fresh range
  - Pre-market volume ratio vs 30-day avg daily volume — institutional positioning

Top 12 intraday picks + core symbols written to logs/watchlist_daily.json.

The GitHub Actions intraday runner (bollinger, momentum_roc, vwap_breakout)
reads this file to prioritise which symbols to analyse each 15-min cycle.

Note: The Render WebSocket engine uses the WEEKLY watchlist at startup.
The daily watchlist is primarily consumed by the GitHub Actions intraday runner.
"""

import os, sys, json, base64, logging, requests
import time as _time
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Credentials ───────────────────────────────────────────────────────────────
IS_PAPER      = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
ALPACA_KEY    = os.environ.get("ALPACA_PAPER_KEY" if IS_PAPER else "ALPACA_LIVE_KEY")
ALPACA_SECRET = os.environ.get("ALPACA_PAPER_SECRET" if IS_PAPER else "ALPACA_LIVE_SECRET")
ALPACA_DATA   = "https://data.alpaca.markets"

GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

CORE_SYMBOLS = ["SPY", "QQQ", "IWM", "GLD", "XLK", "XLE", "XLF"]


# ── Alpaca headers ────────────────────────────────────────────────────────────
def alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


# ── Load weekly watchlist from GitHub raw ─────────────────────────────────────
def load_weekly_watchlist() -> list:
    """
    Pull the symbol list committed by scan_symbols.py last Sunday.
    Falls back to core symbols if the file is missing or unreadable.
    """
    try:
        repo = GITHUB_REPOSITORY or "chenkingston-rgb/algotrader-pro"
        url  = f"https://raw.githubusercontent.com/{repo}/main/logs/watchlist_weekly.json"
        r    = requests.get(url, timeout=10)
        if r.ok:
            symbols = r.json().get("symbols", [])
            if symbols:
                logging.info(f"[DAILY] Loaded {len(symbols)} symbols from weekly watchlist")
                return symbols
    except Exception as e:
        logging.warning(f"[DAILY] Could not load weekly watchlist: {e}")
    logging.info("[DAILY] Falling back to core symbols only")
    return CORE_SYMBOLS


# ── Fetch pre-market snapshot ─────────────────────────────────────────────────
def get_premarket_data(symbols: list) -> dict:
    """
    For each symbol, fetch:
      - Recent 35 daily bars → prior close + 30-day avg volume
      - Today's 1-min bars from 4:00 AM UTC (pre-market open) → pm volume + last price

    Returns {symbol: {prior_close, avg_vol_30d, pm_volume, pm_last}}
    """
    now = datetime.now(timezone.utc)
    # FIX: Use ET-localised pre-market start to handle EDT/EST DST correctly.
    # Old code hardcoded hour=8 UTC which = 3 AM ET in winter (EST, UTC-5).
    # Now we compute 4:00 AM ET and convert to UTC regardless of DST.
    import pytz as _pytz
    ET_TZ   = _pytz.timezone("America/New_York")
    now_et  = now.astimezone(ET_TZ)
    pm_start_et = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    pm_start    = pm_start_et.astimezone(timezone.utc)
    results  = {}
    chunk_size = 40

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i : i + chunk_size]

        # ── Daily bars: prior close + avg volume ──────────────────────────────
        params_daily = {
            "symbols":   ",".join(chunk),
            "timeframe": "1Day",
            "start":     (now - timedelta(days=40)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed":      "iex",
            "sort":      "asc",
            "limit":     10000,
        }
        try:
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/bars",
                headers=alpaca_headers(),
                params=params_daily,
                timeout=20,
            )
            r.raise_for_status()
            for sym, bars in r.json().get("bars", {}).items():
                if len(bars) >= 2:
                    results[sym] = {
                        "prior_close": bars[-2]["c"],   # last completed session close
                        "avg_vol_30d": sum(b["v"] for b in bars[-30:]) / min(30, len(bars)),
                        "pm_volume":   0,
                        "pm_last":     bars[-1]["c"],   # default to last daily close
                    }
        except Exception as e:
            logging.warning(f"[DAILY] Daily bar fetch error: {e}")

        # ── 1-min bars: pre-market volume + latest price ──────────────────────
        params_pm = {
            "symbols":   ",".join(chunk),
            "timeframe": "1Min",
            "start":     pm_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed":      "iex",
            "sort":      "asc",
            "limit":     10000,
        }
        try:
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/bars",
                headers=alpaca_headers(),
                params=params_pm,
                timeout=20,
            )
            if r.ok:
                for sym, bars in r.json().get("bars", {}).items():
                    if sym in results and bars:
                        results[sym]["pm_volume"] = sum(b["v"] for b in bars)
                        results[sym]["pm_last"]   = bars[-1]["c"]
        except Exception as e:
            logging.warning(f"[DAILY] Pre-market bar fetch error: {e}")

        _time.sleep(0.35)

    return results


# ── Score a symbol for today's intraday watchlist ─────────────────────────────
def score_daily(symbol: str, data: dict) -> dict | None:
    """
    Returns a scored metadata dict, or None if data is insufficient.

    Score = 0.6 × |gap_pct|  +  0.4 × (pm_vol / avg_daily_vol × 100)

    Gap captures fresh price discovery vs prior session.
    Volume ratio captures institutional conviction pre-open.
    """
    if not data:
        return None

    prior_close = data.get("prior_close", 0)
    avg_vol_30d = data.get("avg_vol_30d", 0)
    pm_volume   = data.get("pm_volume", 0)
    pm_last     = data.get("pm_last", prior_close)

    if prior_close <= 0 or avg_vol_30d <= 0:
        return None

    gap_pct      = (pm_last - prior_close) / prior_close * 100
    pm_vol_ratio = pm_volume / avg_vol_30d   # fraction of daily avg moved in pre-market

    # Minimum activity thresholds: some gap or some pre-market volume
    if abs(gap_pct) < 0.10 and pm_vol_ratio < 0.005:
        return None

    score = abs(gap_pct) * 0.6 + pm_vol_ratio * 100 * 0.4

    return {
        "symbol":        symbol,
        "score":         round(score, 4),
        "prior_close":   round(prior_close, 2),
        "pm_last":       round(pm_last, 2),
        "gap_pct":       round(gap_pct, 2),
        "gap_direction": "up" if gap_pct >= 0 else "down",
        "pm_volume":     int(pm_volume),
        "pm_vol_ratio":  round(pm_vol_ratio, 4),
        "avg_vol_30d":   int(avg_vol_30d),
    }


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
        "message": (
            f"[bot] Daily pre-market scan — "
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
        logging.error(
            f"[GITHUB] Failed to write {filepath}: "
            f"{put_r.status_code} {put_r.text[:300]}"
        )
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    run_time = datetime.now(timezone.utc)
    logging.info("=" * 60)
    logging.info("  AlgoTrader Pro — Daily Pre-Market Scanner")
    logging.info(f"  Run time : {run_time.isoformat()}")
    logging.info("=" * 60)

    if not ALPACA_KEY or not ALPACA_SECRET:
        logging.error("Alpaca credentials not set — exiting.")
        sys.exit(1)

    # ── Load weekly base watchlist ────────────────────────────────────────────
    weekly_symbols = load_weekly_watchlist()

    # ── Fetch pre-market snapshot ─────────────────────────────────────────────
    logging.info(f"Fetching pre-market data for {len(weekly_symbols)} symbols...")
    snapshot = get_premarket_data(weekly_symbols)
    logging.info(f"Got pre-market data for {len(snapshot)} symbols")

    # ── Score each non-core symbol ────────────────────────────────────────────
    scored = []
    for sym in weekly_symbols:
        if sym in CORE_SYMBOLS:
            continue
        result = score_daily(sym, snapshot.get(sym))
        if result:
            scored.append(result)

    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── Top 12 intraday picks ─────────────────────────────────────────────────
    top_intraday = [s["symbol"] for s in scored[:12]]
    all_symbols  = list(dict.fromkeys(CORE_SYMBOLS + top_intraday))

    # ── Log results ───────────────────────────────────────────────────────────
    logging.info(f"\n{'─'*60}")
    logging.info(f"  TODAY'S TOP INTRADAY CANDIDATES")
    logging.info(f"{'─'*60}")
    for i, s in enumerate(scored[:12], 1):
        arrow = "↑" if s["gap_pct"] >= 0 else "↓"
        logging.info(
            f"  {i:2}. {s['symbol']:<6} | gap={s['gap_pct']:+.2f}% {arrow} | "
            f"pm_vol={s['pm_volume']:>8,} | ratio={s['pm_vol_ratio']:.4f}x | "
            f"score={s['score']:.3f}"
        )
    logging.info(f"{'─'*60}")
    logging.info(f"  Active symbols today ({len(all_symbols)}): {all_symbols}")

    # ── Write watchlist_daily.json ────────────────────────────────────────────
    watchlist = {
        "generated_at":   run_time.isoformat(),
        "scan_type":      "daily_premarket",
        "symbols":        all_symbols,
        "core_symbols":   CORE_SYMBOLS,
        "intraday_picks": scored[:12],
        "weekly_base":    weekly_symbols,
    }

    write_github_log("logs/watchlist_daily.json", watchlist)
    logging.info(f"\n✓ Daily scan complete — {len(all_symbols)} symbols active today")


if __name__ == "__main__":
    main()
