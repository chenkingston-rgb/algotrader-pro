"""
AlgoTrader Pro — Paper → Live Migration Script
Run this ONCE manually to:
  1. Archive all paper trading logs with a timestamp prefix
  2. Reset live log files to empty/fresh state
  3. Write the live_baseline.json to track deposits separately
  4. Zero out the Base44 dashboard entities for a clean live start

Usage:
  GITHUB_TOKEN=... GITHUB_REPOSITORY=... ALPACA_LIVE_KEY=... ALPACA_LIVE_SECRET=... \
  BASE44_SERVICE_TOKEN=... python3 scripts/migrate_to_live.py
"""

import os, sys, json, base64, requests
from datetime import datetime
import pytz

ET = pytz.timezone("America/New_York")

GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "chenkingston-rgb/algotrader-pro")
ALPACA_LIVE_KEY   = os.environ.get("ALPACA_LIVE_KEY", "")
ALPACA_LIVE_SECRET= os.environ.get("ALPACA_LIVE_SECRET", "")
BASE44_TOKEN      = os.environ.get("BASE44_SERVICE_TOKEN", "") or os.environ.get("BASE44_API_KEY", "")
BASE44_APP_ID     = os.environ.get("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE44_BASE       = f"https://app.base44.com/api/apps/{BASE44_APP_ID}/entities"
ALPACA_BASE       = "https://api.alpaca.markets"

GH_HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github+json",
}
ALP_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_LIVE_KEY,
    "APCA-API-SECRET-KEY": ALPACA_LIVE_SECRET,
}
B44_HEADERS = {
    "api-key":      BASE44_TOKEN,
    "Content-Type": "application/json",
}

NOW_ET   = datetime.now(ET)
ARCHIVE_PREFIX = f"paper_archive_{NOW_ET.strftime('%Y%m%d')}"


# ─── Helpers ────────────────────────────────────────────────────────────────

def gh_get_file(path):
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    r   = requests.get(url, headers=GH_HEADERS, timeout=10)
    if r.ok:
        data = r.json()
        content = json.loads(base64.b64decode(data["content"]).decode())
        return content, data.get("sha")
    return None, None


def gh_write_file(path, content, sha=None, msg=None):
    url   = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{path}"
    body  = {
        "message": msg or f"[bot] Migration: write {path} [skip render]",
        "content": base64.b64encode(
            json.dumps(content, indent=2, default=str).encode()
        ).decode(),
    }
    if sha:
        body["sha"] = sha
    r = requests.put(url, headers=GH_HEADERS, json=body, timeout=15)
    if r.ok:
        print(f"  ✓ Wrote {path}")
    else:
        print(f"  ✗ Failed {path}: {r.status_code} {r.text[:200]}")
    return r.ok


def b44_get_entity(name):
    r = requests.get(f"{BASE44_BASE}/{name}", headers=B44_HEADERS, timeout=10)
    if r.ok:
        return r.json()
    return []


def b44_delete_all(name):
    records = b44_get_entity(name)
    if not isinstance(records, list):
        records = records.get("records", [])
    print(f"  Deleting {len(records)} records from {name}...")
    for rec in records:
        rid = rec.get("id")
        if rid:
            requests.delete(f"{BASE44_BASE}/{name}/{rid}", headers=B44_HEADERS, timeout=10)
    print(f"  ✓ Cleared {name}")


# ─── Step 1: Read live Alpaca equity ────────────────────────────────────────

print("\n=== STEP 1: Read live Alpaca account ===")
r = requests.get(f"{ALPACA_BASE}/v2/account", headers=ALP_HEADERS, timeout=10)
if not r.ok:
    print(f"  ✗ Cannot reach live Alpaca: {r.status_code} — check ALPACA_LIVE_KEY/SECRET")
    sys.exit(1)
live_acct   = r.json()
live_equity = float(live_acct["equity"])
print(f"  Live equity: ${live_equity:,.2f}")
print(f"  Account status: {live_acct.get('status')}")
if live_acct.get("status") != "ACTIVE":
    print("  ✗ Live account is not ACTIVE — aborting")
    sys.exit(1)


# ─── Step 2: Archive paper logs ─────────────────────────────────────────────

print(f"\n=== STEP 2: Archive paper logs → {ARCHIVE_PREFIX}/ ===")
LOG_FILES = [
    "logs/run_history.json",
    "logs/intraday_latest.json",
    "logs/daily_latest.json",
    "logs/signals_history.json",
    "logs/watchlist_daily.json",
    "logs/watchlist_weekly.json",
]
for fpath in LOG_FILES:
    content, sha = gh_get_file(fpath)
    if content is not None:
        archive_path = fpath.replace("logs/", f"logs/{ARCHIVE_PREFIX}/")
        gh_write_file(archive_path, content,
                      msg=f"[bot] Archive paper log: {fpath} [skip render]")
        print(f"  Archived {fpath} → {archive_path}")
    else:
        print(f"  (skip) {fpath} not found")


# ─── Step 3: Reset live log files ───────────────────────────────────────────

print(f"\n=== STEP 3: Reset log files for live trading ===")

# run_history.json → empty array
_, sha_hist = gh_get_file("logs/run_history.json")
gh_write_file("logs/run_history.json", [],
              sha=sha_hist,
              msg="[bot] LIVE MIGRATION: reset run_history.json [skip render]")

# intraday_latest.json → empty shell
_, sha_intra = gh_get_file("logs/intraday_latest.json")
empty_run = {
    "run_timestamp": NOW_ET.isoformat(),
    "mode": "live", "strategy_mode": "intraday",
    "equity": live_equity, "buying_power": 0, "vix": None,
    "drawdown_pct": 0, "positions": [], "position_details": [],
    "signals": [], "orders_placed": [],
    "trading_pnl": 0, "total_deposited": 0, "deposit_count": 0,
    "_note": "LIVE TRADING START — paper logs archived"
}
gh_write_file("logs/intraday_latest.json", empty_run,
              sha=sha_intra,
              msg="[bot] LIVE MIGRATION: reset intraday_latest.json [skip render]")

# daily_latest.json → empty shell
_, sha_daily = gh_get_file("logs/daily_latest.json")
gh_write_file("logs/daily_latest.json", {**empty_run, "strategy_mode": "daily"},
              sha=sha_daily,
              msg="[bot] LIVE MIGRATION: reset daily_latest.json [skip render]")

# signals_history.json → empty array
_, sha_sigs = gh_get_file("logs/signals_history.json")
gh_write_file("logs/signals_history.json", [],
              sha=sha_sigs,
              msg="[bot] LIVE MIGRATION: reset signals_history.json [skip render]")

# intraday_position_tags.json → empty dict (no open positions to track yet)
_, sha_tags = gh_get_file("logs/intraday_position_tags.json")
gh_write_file("logs/intraday_position_tags.json", {},
              sha=sha_tags,
              msg="[bot] LIVE MIGRATION: reset position tags [skip render]")


# ─── Step 4: Write live_baseline.json ───────────────────────────────────────

print(f"\n=== STEP 4: Write live_baseline.json ===")
live_baseline = {
    "start_equity":      live_equity,
    "last_known_equity": live_equity,
    "total_deposited":   0.0,
    "total_trading_pnl": 0.0,
    "deposits":          [],
    "initialized_at":    NOW_ET.isoformat(),
    "_note": (
        "Deposit detection: equity jumps > $500 AND > 1% in a single engine run "
        "are classified as cash deposits, not trading P&L."
    ),
}
_, sha_baseline = gh_get_file("logs/live_baseline.json")
gh_write_file("logs/live_baseline.json", live_baseline,
              sha=sha_baseline,
              msg="[bot] LIVE MIGRATION: initialize live_baseline.json [skip render]")
print(f"  Live baseline set at ${live_equity:,.2f}")


# ─── Step 5: Clear Base44 dashboard entities ────────────────────────────────

print(f"\n=== STEP 5: Reset Base44 dashboard entities ===")
if BASE44_TOKEN:
    for entity in ["PortfolioState", "TradeLog", "SignalHistory", "RunHistory"]:
        try:
            b44_delete_all(entity)
        except Exception as e:
            print(f"  (skip) {entity}: {e}")
    # Seed a fresh PortfolioState record for live start
    seed = {
        "equity":           live_equity,
        "buying_power":     float(live_acct.get("buying_power", 0)),
        "trading_pnl":      0,
        "total_deposited":  0,
        "deposit_count":    0,
        "mode":             "live",
        "start_equity":     live_equity,
        "positions":        json.dumps([]),
        "last_updated":     NOW_ET.isoformat(),
        "_note":            "Live trading start",
    }
    r2 = requests.post(f"{BASE44_BASE}/PortfolioState", headers=B44_HEADERS,
                       json=seed, timeout=10)
    print(f"  PortfolioState seed: {r2.status_code}")
else:
    print("  (skip) BASE44_SERVICE_TOKEN not set — skipping entity reset")

print(f"""
{'='*60}
MIGRATION COMPLETE
{'='*60}
  Live equity baseline:   ${live_equity:,.2f}
  Paper logs archived to: logs/{ARCHIVE_PREFIX}/
  Live logs reset:        run_history, intraday/daily_latest, signals_history
  Baseline file:          logs/live_baseline.json
  Dashboard entities:     cleared + seeded with live start

NEXT STEPS:
  1. Update ALPACA_IS_PAPER=false in Render env vars
  2. Confirm ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET are set in Render
  3. Deploy updated run_strategies.py (v7) to Render
  4. Run daily.yml workflow_dispatch once to confirm live connectivity
{'='*60}
""")
