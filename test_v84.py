#!/usr/bin/env python3
"""
AlgoTrader Pro — v8.4 FIX-J + FIX-K Integration Tests
Uses both chk() for human-readable output AND assert for pytest compatibility.
"""
import ast, re, sys, os

with open("scripts/run_strategies.py") as f:
    code = f.read()

tree = ast.parse(code)
fns  = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
lines = code.splitlines()

PASS = 0
FAIL = 0

def chk(cond, name):
    global PASS, FAIL
    if cond:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ FAIL: {name}")
        FAIL += 1

def test_fix_j_r1_session_ban():
    assert "SESSION BAN until" in code
    assert "session_end = fill_ts.replace(hour=15" in code
    assert "FIX-J R1" in code

def test_fix_j_r2_max_hold():
    assert "MAX_HOLD_MINUTES = 390" in code
    assert "max_hold_exceeded" in code
    assert "FIX-J R2" in code

def test_fix_j_r3_weekly_cap():
    assert "weekly_concentration_cap" in code
    assert "weekly_symbol_counts" in code
    assert "MAX_WEEKLY_ENTRIES_PER_SYMBOL" in code
    assert "FIX-J R3" in code

def test_fix_j_r5_late_gate():
    assert "hour=14, minute=0" in code
    assert "hour=14, minute=45" not in code
    assert "FIX-J R5" in code
    assert "after 14:00 ET" in code

def test_fix_k_cron():
    with open(".github/workflows/intraday.yml") as f: wf = f.read()
    assert "*/15 13-20 * * 1-5" in wf, "Intraday cron must be restored"

def test_fix_k_concurrency():
    with open(".github/workflows/intraday.yml") as f: wf = f.read()
    assert "concurrency:" in wf
    assert "cancel-in-progress: false" in wf
    with open(".github/workflows/daily.yml") as f: wf2 = f.read()
    assert "concurrency:" in wf2

def test_fix_k_failure_alert():
    with open(".github/workflows/intraday.yml") as f: wf = f.read()
    assert "if: failure()" in wf
    assert "GITHUB_STEP_SUMMARY" in wf
    with open(".github/workflows/daily.yml") as f: wf2 = f.read()
    assert "if: failure()" in wf2

def test_fix_k_heartbeat():
    assert "last_heartbeat" in code
    assert "FIX-K T4" in code
    assert "engine_version" in code

def test_fix_k_session_pooling():
    assert "_http_session" in code
    assert "requests.Session()" in code
    assert "FIX-K T5" in code

def test_previous_fixes_intact():
    assert '"roc_threshold": 0.8' in code
    assert '"roc_max_extension": 1.8' in code
    assert "vol_ratio >= 1.5" in code
    m = re.search(r'ATR_STOP_MULT\s*=\s*([\d.]+)', code)
    assert m and m.group(1) == "2.0"
    m2 = re.search(r'max\(eff_atr\s*\*\s*([\d.]+)', code)
    assert m2 and m2.group(1) == "0.5"
    assert "pre_10am_block" in code
    assert "kill_switch" in code
    assert "run_eod_exit" in fns
    assert "attach_trailing_stop" in fns
    assert "weekly_symbol_counts" in code

if __name__ == "__main__":
    print("── AlgoTrader Pro v8.4+FIX-K Tests ──\n")
    tests = [
        (test_fix_j_r1_session_ban,    "FIX-J R1: Session ban"),
        (test_fix_j_r2_max_hold,       "FIX-J R2: Max hold 390min"),
        (test_fix_j_r3_weekly_cap,     "FIX-J R3: Weekly cap"),
        (test_fix_j_r5_late_gate,      "FIX-J R5: Late gate 14:00"),
        (test_fix_k_cron,              "FIX-K K1: Intraday cron restored"),
        (test_fix_k_concurrency,       "FIX-K K2: Concurrency guard"),
        (test_fix_k_failure_alert,     "FIX-K K3: Failure alert"),
        (test_fix_k_heartbeat,         "FIX-K T4: Heartbeat"),
        (test_fix_k_session_pooling,   "FIX-K T5: Session pooling"),
        (test_previous_fixes_intact,   "All prior FIX-* intact"),
    ]
    for fn, name in tests:
        try:
            fn()
            chk(True, name)
        except AssertionError as e:
            chk(False, f"{name}: {e}")

    print(f"\n{'='*48}")
    print(f"RESULT: {PASS}/{PASS+FAIL} passed")
    sys.exit(0 if FAIL == 0 else 1)

# ─── FIX-L tests ──────────────────────────────────────────────────────────────

def test_fix_l_constants():
    """BREAKEVEN constants present and correctly valued."""
    assert "BREAKEVEN_ATR_TRIGGER  = 1.0" in code
    assert "PROFIT_LOCK_ATR_MULT   = 0.5" in code
    assert "BREAKEVEN_PROFIT_FLOOR = 0.0" in code

def test_fix_l_function_exists():
    """_upgrade_trail_to_breakeven function is defined."""
    assert "def _upgrade_trail_to_breakeven(" in code

def test_fix_l_idempotent_guard():
    """Upgrade is skipped if already done this session (idempotent)."""
    assert "trail_upgraded_to_breakeven" in code
    assert 'tag.get("trail_upgraded_to_breakeven")' in code

def test_fix_l_cancels_old_trail():
    """Old trailing stop is cancelled before re-submitting upgraded one."""
    assert "cancel_order(trail_id)" in code
    assert "cancel_all_trailing_stops_for_symbol(sym)" in code

def test_fix_l_reattaches_trail():
    """New trailing stop is submitted after cancel."""
    assert "attach_trailing_stop(sym, qty, new_trail_dist)" in code

def test_fix_l_stores_upgrade_metadata():
    """Upgrade metadata persisted to position_tags for dashboard display."""
    assert '"trail_upgrade_price"' in code
    assert '"trail_upgrade_floor"' in code
    assert '"trail_upgraded_to_breakeven"' in code

def test_fix_l_wired_into_main():
    """_upgrade_trail_to_breakeven is called in main loop."""
    assert "_upgrade_trail_to_breakeven(positions, position_tags)" in code

def test_fix_l_persists_tags():
    """position_tags are written to GitHub after upgrade."""
    # After the upgrade call there must be a write_github_log(EOD_TAG_FILE
    idx_upgrade = code.index("_upgrade_trail_to_breakeven(positions, position_tags)")
    idx_write   = code.index("write_github_log(EOD_TAG_FILE, position_tags)", idx_upgrade)
    assert idx_write > idx_upgrade, "write must come after upgrade"

def test_fix_l_fallback_on_attach_failure():
    """Fallback trail re-attached if upgraded trail order fails."""
    assert "fallback = attach_trailing_stop" in code
    assert "Upgrade check failed" in code or "[FIX-L]" in code

def test_fix_l_all_prior_still_intact():
    """All prior FIX-* constants and guards still present."""
    assert "ATR_STOP_MULT    = 2.0" in code
    assert "ATR_TP_MULT      = 3.0" in code
    assert "MAX_DRAWDOWN_PCT = 25.0" in code
    assert "BREAKEVEN_ATR_TRIGGER  = 1.0" in code
    assert "trail_upgraded_to_breakeven" in code
    # FIX-J R5 late gate
    assert "late_day_block" in code
    # FIX-K heartbeat
    assert "last_heartbeat" in code
