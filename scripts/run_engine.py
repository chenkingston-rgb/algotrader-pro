"""
AlgoTrader Pro — Persistent WebSocket Trading Engine  (v4)
Runs as a Render Background Worker.

Subscribes to 1-min Alpaca bars for all trading symbols, aggregates them
into 5-min candles, then calls run_all_strategies() from run_strategies.py
on each completed candle.

A minimal HTTP health endpoint is served on HEALTH_PORT (default 8099)
so Render / UptimeRobot can verify the process is alive.

Environment variables:
  ALPACA_API_KEY    — paper or live key (set in Render dashboard)
  ALPACA_API_SECRET — matching secret
  ALPACA_IS_PAPER   — "true" / "false"  (default "true")
  HEALTH_PORT       — port for /health endpoint (default 8099)
"""

import os, sys, time, logging, threading
from collections import defaultdict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import pytz

# run_strategies.py lives in the same scripts/ directory
sys.path.insert(0, os.path.dirname(__file__))
from run_strategies import (
    get_trading_symbols,
    run_all_strategies,
    is_market_hours,
    log_engine_status,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
IS_PAPER   = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", 8099))

BAR_INTERVAL_MINUTES = 5   # aggregate 1-min bars → 5-min candles
MAX_CANDLES          = 200  # rolling window per symbol

ET = pytz.timezone("America/New_York")

# ─────────────────────────────────────────────
# CANDLE STATE
# ─────────────────────────────────────────────
raw_bars: dict = defaultdict(list)    # symbol → list of 1-min bar dicts
candles:  dict = defaultdict(list)    # symbol → list of completed 5-min candle dicts


# ─────────────────────────────────────────────
# HEALTH SERVER
# ─────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass   # suppress access logs


def start_health_server():
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logging.info(f"[HEALTH] Listening on :{HEALTH_PORT}/health")


# ─────────────────────────────────────────────
# BAR AGGREGATION
# ─────────────────────────────────────────────
def aggregate_to_candle(bars_1min: list) -> dict:
    """Merge a list of 1-min bar dicts into a single 5-min candle dict."""
    return {
        "timestamp": bars_1min[0]["timestamp"],
        "open":      bars_1min[0]["open"],
        "high":      max(b["high"]   for b in bars_1min),
        "low":       min(b["low"]    for b in bars_1min),
        "close":     bars_1min[-1]["close"],
        "volume":    sum(b["volume"] for b in bars_1min),
    }


# ─────────────────────────────────────────────
# WEBSOCKET BAR HANDLER
# ─────────────────────────────────────────────
async def on_bar(bar) -> None:
    """Called for every 1-min bar tick received from Alpaca WebSocket."""
    symbol   = bar.symbol
    now_et   = datetime.now(ET)

    if not is_market_hours(now_et):
        return

    raw_bars[symbol].append({
        "timestamp": bar.timestamp,
        "open":      float(bar.open),
        "high":      float(bar.high),
        "low":       float(bar.low),
        "close":     float(bar.close),
        "volume":    float(bar.volume),
    })

    # Once we have BAR_INTERVAL_MINUTES worth of 1-min bars, form a candle
    if len(raw_bars[symbol]) >= BAR_INTERVAL_MINUTES:
        candle = aggregate_to_candle(raw_bars[symbol][:BAR_INTERVAL_MINUTES])
        raw_bars[symbol] = raw_bars[symbol][BAR_INTERVAL_MINUTES:]

        candles[symbol].append(candle)
        if len(candles[symbol]) > MAX_CANDLES:
            candles[symbol] = candles[symbol][-MAX_CANDLES:]

        # Need at least 30 candles before strategies are meaningful
        if len(candles[symbol]) >= 30:
            try:
                run_all_strategies(symbol, candles[symbol])
            except Exception as e:
                logging.error(f"[ENGINE] Strategy error for {symbol}: {e}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    start_health_server()

    symbols = get_trading_symbols()
    log_engine_status(f"Starting — subscribing to {len(symbols)} symbols (paper={IS_PAPER})")
    logging.info(f"[ENGINE] Symbols: {symbols}")

    # Import here so startup doesn't fail if alpaca-py isn't installed locally
    from alpaca.data.live import StockDataStream

    stream = StockDataStream(API_KEY, API_SECRET)
    stream.subscribe_bars(on_bar, *symbols)

    log_engine_status("WebSocket connected — waiting for market hours")
    stream.run()   # blocks forever; reconnects on disconnect


if __name__ == "__main__":
    main()
