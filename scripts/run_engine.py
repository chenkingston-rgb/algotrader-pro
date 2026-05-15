"""
AlgoTrader Pro — Persistent WebSocket Trading Engine  (v5 FINAL)
Deploys as a Render Web Service (free tier).

Subscribes to Alpaca WebSocket for real-time 1-minute bars.
Aggregates to 5-minute candles internally.
Runs all 7 strategies on each new candle during market hours.

Health endpoint on Render's PORT keeps the service alive via UptimeRobot.
UptimeRobot pings /health every 5 minutes — prevents Render's free tier
from spinning down during market hours.

Environment variables (set in Render dashboard):
  ALPACA_PAPER_KEY    — paper API key
  ALPACA_PAPER_SECRET — paper API secret
  ALPACA_LIVE_KEY     — live API key
  ALPACA_LIVE_SECRET  — live API secret
  ALPACA_IS_PAPER     — "true" / "false"  (default "true")
  PORT                — injected automatically by Render (do NOT set manually)
"""

import os
import sys
import threading
import logging
from datetime import datetime
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytz

# Both files are in scripts/ — add that directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_strategies import (
    get_trading_symbols,
    run_all_strategies,
    is_market_hours,
    get_cached_adx_slope,
    compute_vwap,
    compute_rsi_n,
    get_tp_multiplier,
    get_rsi2_size_multiplier,
    get_regime_size_multiplier,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── Credentials: paper/live key split ───────────────────────────────────
IS_PAPER = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
if IS_PAPER:
    API_KEY    = os.environ["ALPACA_PAPER_KEY"]
    API_SECRET = os.environ["ALPACA_PAPER_SECRET"]
else:
    API_KEY    = os.environ["ALPACA_LIVE_KEY"]
    API_SECRET = os.environ["ALPACA_LIVE_SECRET"]

# ── Render Web Service port — injected by Render automatically ───────────
# NEVER hardcode this. Render assigns PORT at runtime for Web Services.
HEALTH_PORT = int(os.environ.get("PORT", "10000"))

BAR_INTERVAL_MINUTES = 5    # aggregate 1-min bars → 5-min candles
MAX_CANDLES          = 60   # reduced from 200 — strategies need 60 bars max, saves ~70% candle buffer RAM  # rolling window per symbol

ET = pytz.timezone("America/New_York")

# ── Per-symbol bar buffers ───────────────────────────────────────────────
raw_bars_buffer: dict = defaultdict(list)   # symbol → pending 1-min bars
candle_history:  dict = defaultdict(list)   # symbol → completed 5-min candles


# ── Health / keep-alive HTTP server ─────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    """
    Minimal HTTP server so Render and UptimeRobot can verify the service is alive.
    UptimeRobot pings /health every 5 minutes — prevents Render free tier spin-down.
    """
    def do_GET(self):
        if self.path in ("/", "/health"):
            now_et = datetime.now(ET)
            mode_str = "true" if IS_PAPER else "false"
            body = (
                f'{{"status":"ok","paper":{mode_str},'
                f'"time_et":"{now_et.strftime("%H:%M:%S")}"}}'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass   # suppress per-request HTTP logs to keep Render logs clean


def start_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"[ENGINE] Health server listening on :{HEALTH_PORT}")
    logger.info(f"[ENGINE] Ping: https://<your-render-url>/health")


# ── 1-min → 5-min candle aggregation ────────────────────────────────────
def aggregate_candle(bars_1min: list) -> dict:
    return {
        "timestamp": bars_1min[0]["timestamp"],
        "open":      bars_1min[0]["open"],
        "high":      max(b["high"]   for b in bars_1min),
        "low":       min(b["low"]    for b in bars_1min),
        "close":     bars_1min[-1]["close"],
        "volume":    sum(b["volume"] for b in bars_1min),
    }


# ── WebSocket bar handler ────────────────────────────────────────────────
async def on_bar(bar) -> None:
    """Called for every 1-min bar tick received from Alpaca WebSocket."""
    symbol = bar.symbol
    now_et = datetime.now(ET)

    if not is_market_hours(now_et):
        return

    raw_bars_buffer[symbol].append({
        "timestamp": bar.timestamp,
        "open":      float(bar.open),
        "high":      float(bar.high),
        "low":       float(bar.low),
        "close":     float(bar.close),
        "volume":    float(bar.volume),
    })

    if len(raw_bars_buffer[symbol]) >= BAR_INTERVAL_MINUTES:
        candle = aggregate_candle(raw_bars_buffer[symbol][:BAR_INTERVAL_MINUTES])
        raw_bars_buffer[symbol] = raw_bars_buffer[symbol][BAR_INTERVAL_MINUTES:]

        candle_history[symbol].append(candle)
        if len(candle_history[symbol]) > MAX_CANDLES:
            candle_history[symbol] = candle_history[symbol][-MAX_CANDLES:]

        if len(candle_history[symbol]) >= 30:
            logger.info(
                f"[ENGINE] {symbol} 5-min candle | "
                f"close={candle['close']:.2f} | bars_stored={len(candle_history[symbol])}"
            )
            try:
                run_all_strategies(symbol, candle_history[symbol])
            except Exception as e:
                logger.error(f"[ENGINE] Strategy error for {symbol}: {e}", exc_info=True)


# ── Startup banner ───────────────────────────────────────────────────────
def print_startup_banner(symbols: list):
    mode_label = "PAPER" if IS_PAPER else "*** LIVE TRADING ***"
    logger.info("=" * 60)
    logger.info("  AlgoTrader Pro — Engine Starting")
    logger.info(f"  Mode         : {mode_label}")
    logger.info(f"  Symbols      : {len(symbols)} symbols")
    logger.info(f"  Candle size  : {BAR_INTERVAL_MINUTES} min (from 1-min bars)")
    logger.info(f"  Health port  : {HEALTH_PORT}  (Render PORT env var)")
    logger.info(f"  Candle buffer: {MAX_CANDLES} bars max per symbol")
    logger.info(f"  ADX cache    : refreshes every 30 min")
    logger.info(f"  VIX cache    : refreshes every 30 min")
    logger.info("=" * 60)


# ── Main ─────────────────────────────────────────────────────────────────
def main():
    symbols = get_trading_symbols()
    print_startup_banner(symbols)
    start_health_server()

    # Import here so startup doesn't fail if alpaca-py not installed locally
    from alpaca.data.live import StockDataStream

    stream = StockDataStream(API_KEY, API_SECRET)  # paper= not accepted in alpaca-py >= 0.30.0
    stream.subscribe_bars(on_bar, *symbols)

    logger.info(f"[ENGINE] WebSocket connected — streaming {len(symbols)} symbols")
    stream.run()   # blocks forever; reconnects on disconnect


if __name__ == "__main__":
    main()

