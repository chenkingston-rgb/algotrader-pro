"""
AlgoTrader Pro — Emergency Stop-Loss Recovery
Fetches all open positions that have NO standing stop-loss order,
then places a GTC stop sell at 2% below current price for each one.

Run via GitHub Actions (recovery workflow) or locally with env vars set.
"""

import os, sys, requests, json
from datetime import datetime

IS_PAPER = os.environ.get("ALPACA_IS_PAPER", "true").lower() == "true"
if IS_PAPER:
    KEY    = os.environ["ALPACA_PAPER_KEY"]
    SECRET = os.environ["ALPACA_PAPER_SECRET"]
    BASE   = "https://paper-api.alpaca.markets"
else:
    KEY    = os.environ["ALPACA_LIVE_KEY"]
    SECRET = os.environ["ALPACA_LIVE_SECRET"]
    BASE   = "https://api.alpaca.markets"

STOP_PCT = 0.02   # 2% below current price

def headers():
    return {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}

def get_positions():
    r = requests.get(f"{BASE}/v2/positions", headers=headers(), timeout=10)
    r.raise_for_status()
    return r.json()

def get_open_orders():
    r = requests.get(f"{BASE}/v2/orders", headers=headers(),
                     params={"status": "open", "limit": 500}, timeout=10)
    r.raise_for_status()
    return r.json()

def place_stop_order(symbol: str, qty: int, stop_price: float) -> dict:
    """GTC stop sell — no bracket, no take-profit. Pure protection."""
    payload = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "stop",
        "time_in_force": "gtc",
        "stop_price":    str(round(stop_price, 2)),
    }
    r = requests.post(f"{BASE}/v2/orders", headers=headers(),
                      json=payload, timeout=10)
    r.raise_for_status()
    return r.json()

def main():
    print(f"\n{'='*60}")
    print(f"Stop-Loss Recovery — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'PAPER' if IS_PAPER else 'LIVE'}")
    print(f"{'='*60}\n")

    positions = get_positions()
    if not positions:
        print("No open positions found. Nothing to do.")
        return

    # Build set of symbols that already have a standing sell order
    open_orders = get_open_orders()
    protected_symbols = set()
    for o in open_orders:
        if o.get("side") == "sell" and o.get("status") in ("new", "accepted", "pending_new"):
            protected_symbols.add(o["symbol"])

    print(f"Open positions  : {[p['symbol'] for p in positions]}")
    print(f"Already protected: {list(protected_symbols) or 'none'}")
    print()

    placed   = []
    skipped  = []
    failed   = []

    for pos in positions:
        symbol = pos["symbol"]
        qty    = int(float(pos["qty"]))
        side   = pos.get("side", "long")

        if side != "long":
            skipped.append(f"{symbol} (short — skipping)")
            continue

        if symbol in protected_symbols:
            skipped.append(f"{symbol} (already has open sell order)")
            continue

        current_price = float(pos.get("current_price") or pos.get("avg_entry_price"))
        stop_price    = round(current_price * (1 - STOP_PCT), 2)
        entry_price   = round(float(pos.get("avg_entry_price", current_price)), 2)
        unrl_pl       = round(float(pos.get("unrealized_pl", 0)), 2)
        unrl_plpc     = round(float(pos.get("unrealized_plpc", 0)) * 100, 2)

        print(f"  {symbol}: qty={qty} | entry=${entry_price} | "
              f"current=${current_price:.2f} | P&L={unrl_plpc:+.2f}% (${unrl_pl:+.2f})")
        print(f"    → Placing GTC stop sell at ${stop_price:.2f} (2% below ${current_price:.2f})")

        try:
            order = place_stop_order(symbol, qty, stop_price)
            order_id = order.get("id", "?")
            placed.append(f"{symbol}: stop=${stop_price:.2f} qty={qty} id={order_id}")
            print(f"    ✓ Stop order placed | id={order_id}\n")
        except Exception as e:
            failed.append(f"{symbol}: {e}")
            print(f"    ✗ FAILED: {e}\n")

    print(f"\n{'='*60}")
    print(f"Recovery complete")
    print(f"  Placed : {len(placed)}")
    print(f"  Skipped: {len(skipped)}")
    print(f"  Failed : {len(failed)}")
    if placed:
        print("\nPlaced:")
        for p in placed: print(f"  • {p}")
    if skipped:
        print("\nSkipped:")
        for s in skipped: print(f"  • {s}")
    if failed:
        print("\nFailed:")
        for f in failed: print(f"  • {f}")
        sys.exit(1)
    print(f"{'='*60}\n")

if __name__ == "__main__":
    main()
