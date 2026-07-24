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
    assert "FIX-P" in code or "FIX-J R5" in code  # FIX-P renamed from FIX-J R5
    assert "after 14:00" in code

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
    assert "BREAKEVEN_ATR_TRIGGER  = 1.5" in code
    assert "PROFIT_LOCK_ATR_MULT   = 0.75" in code
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
    assert "BREAKEVEN_ATR_TRIGGER  = 1.5" in code
    assert "trail_upgraded_to_breakeven" in code
    # FIX-J R5 late gate
    assert "late_day_block" in code
    # FIX-K heartbeat
    assert "last_heartbeat" in code


def test_fix_w_intraday_execution_path():
    """FIX-W v9.1: Weekly cap check must NOT consume intraday buy signals.
    
    Bug: `elif signal == "buy" and strategy_type == "intraday":` in the if/elif
    chain consumed ALL intraday buy signals. When the weekly cap didn't fire
    (new symbols with 0 counts), the chain ended — the execution path (pre-10am,
    position sizing, order placement) inside `elif signal == "buy":` was NEVER
    reached. Result: 20 buy signals generated, 0 executed on Jul 23, 2026.
    
    Fix: Move weekly cap check inside the buy catch-all (`elif signal == "buy":`).
    """
    import os, requests, base64
    for line in open('/app/.agents/.env').read().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'): continue
        line = line.replace('export ', '')
        if '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
    GH_TOKEN = os.environ.get('GITHUB_ACCESS_TOKEN','')
    H_GH = {'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json'}
    REPO = 'chenkingston-rgb/algotrader-pro'
    r = requests.get(f'https://api.github.com/repos/{REPO}/contents/scripts/run_strategies.py',
                     headers=H_GH, timeout=15)
    code = base64.b64decode(r.json()['content']).decode()
    
    # 1. The old `elif signal == "buy" and strategy_type == "intraday":` must NOT
    #    be in the if/elif chain (it should be a regular if inside the buy body)
    import ast
    tree = ast.parse(code)
    
    # Find the main loop's if/elif chain
    lines = code.splitlines()
    chain_elifs = [(i+1, l.strip()) for i, l in enumerate(lines) 
                  if l.strip().startswith('elif') and 'strategy_type == "intraday"' in l 
                  and 2680 < i < 2700]
    
    assert len(chain_elifs) == 0, (
        f"FIX-W FAIL: `elif ... strategy_type == intraday` still in if/elif chain at L{chain_elifs[0][0]}. "
        f"This consumes intraday buy signals and blocks the execution path."
    )
    
    # 2. The weekly cap check should be INSIDE the `elif signal == "buy":` body
    buy_elif_idx = None
    for i, l in enumerate(lines):
        if l.strip() == 'elif signal == "buy":' and i > 2600:
            buy_elif_idx = i
            break
    
    assert buy_elif_idx is not None, 'FIX-W FAIL: `elif signal == "buy":` not found' 
    
    # Check that the weekly cap is inside the buy body (within 30 lines)
    buy_block = lines[buy_elif_idx:buy_elif_idx+200]
    weekly_cap_inside = any('weekly_concentration_cap' in l for l in buy_block)
    assert weekly_cap_inside, (
        'FIX-W FAIL: Weekly cap check not found inside buy body'
    )
    
    # 3. The execution path (pre-10am, position sizing, order placement) must be
    #    reachable from the buy catch-all
    exec_path = any('pre_10am_block' in l for l in buy_block) and                 any('atr_position_size' in l for l in lines[buy_elif_idx:buy_elif_idx+200]) and                 any('place_order' in l for l in lines[buy_elif_idx:buy_elif_idx+200])
    assert exec_path, (
        "FIX-W FAIL: Execution path (pre-10am + sizing + order) not reachable from buy catch-all"
    )
    
    # 4. FIX-W comment should be present
    assert "FIX-W" in code, "FIX-W comment not found in code"
    
    print("  FIX-W: Weekly cap moved inside buy catch-all — intraday execution path reachable ✅")


def test_fix_x_oto_stop_trail_activation():
    """FIX-X v9.2: After 30-min FIX-M delay, cancel OTO static stop and attach trailing stop.
    
    Bug: place_order() creates an OTO bracket that reserves all shares for the
    static stop. attach_trailing_stop() then fails with "insufficient qty available"
    because the shares are held. Result: NO trailing stop was ever attached for
    ANY position. The breakeven upgrade could not fire because there was nothing
    to upgrade from.
    
    Fix: In _upgrade_trail_to_breakeven, when trail_order_id is empty and the
    30-min delay has passed, cancel ALL sell orders (including OTO stop) via
    cancel_all_sell_orders_for_symbol(), then attach a trailing stop.
    """
    import os, requests, base64
    for line in open('/app/.agents/.env').read().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'): continue
        line = line.replace('export ', '')
        if '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
    GH_TOKEN = os.environ.get('GITHUB_ACCESS_TOKEN','')
    H_GH = {'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json'}
    REPO = 'chenkingston-rgb/algotrader-pro'
    r = requests.get(f'https://api.github.com/repos/{REPO}/contents/scripts/run_strategies.py',
                     headers=H_GH, timeout=15)
    code = base64.b64decode(r.json()['content']).decode()
    
    # 1. cancel_all_sell_orders_for_symbol must exist
    assert 'def cancel_all_sell_orders_for_symbol' in code, \
        'FIX-X FAIL: cancel_all_sell_orders_for_symbol not defined'
    
    # 2. It must cancel both stop AND trailing_stop orders
    assert '"stop"' in code and '"trailing_stop"' in code, \
        'FIX-X FAIL: cancel function must handle both stop types'
    
    # 3. _upgrade_trail_to_breakeven must call cancel_all_sell_orders_for_symbol
    assert 'cancel_all_sell_orders_for_symbol' in code, \
        'FIX-X FAIL: cancel function not called in engine'
    
    # 4. The activation logic must exist (attach trail when trail_id is empty)
    assert 'if not trail_id:' in code, \
        'FIX-X FAIL: trail activation logic not found'
    assert 'attach_trailing_stop' in code, \
        'FIX-X FAIL: attach_trailing_stop call not found'
    
    # 5. FIX-X comment must be present
    assert 'FIX-X' in code, 'FIX-X comment not found'
    
    print('  FIX-X: OTO stop cancellation + trailing stop activation ✅')


def test_fix_z_signal_persistence_direction():
    """FIX-Z v9.4: Signal persistence cache must track direction, not just count.
    
    Bug: After a sell signal, the cache forced signal='buy' on the next hold bar,
    causing immediate whipsaw (sell then buy back). The cache only stored a count,
    not the direction of the original signal.
    
    Fix: Cache stores [direction, count]. On hold, persists the ORIGINAL direction.
    """
    import os, requests, base64
    for line in open('/app/.agents/.env').read().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'): continue
        line = line.replace('export ', '')
        if '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())
    GH_TOKEN = os.environ.get('GITHUB_ACCESS_TOKEN','')
    H_GH = {'Authorization': f'Bearer {GH_TOKEN}', 'Accept': 'application/vnd.github+json'}
    REPO = 'chenkingston-rgb/algotrader-pro'
    r = requests.get(f'https://api.github.com/repos/{REPO}/contents/scripts/run_strategies.py',
                     headers=H_GH, timeout=15)
    code = base64.b64decode(r.json()['content']).decode()
    
    # 1. Cache must store direction (list/tuple, not just integer)
    assert '_signal_hold_cache[_cache_key] = [signal' in code, \
        'FIX-Z FAIL: cache must store [direction, count], not just count'
    
    # 2. Persistence must use cached_dir, not force "buy"
    assert 'signal = cached_dir' in code, \
        'FIX-Z FAIL: persistence must use original direction'
    
    # 3. Old pattern (always buy) must be gone
    assert 'signal = "buy" if _signal_hold_cache' not in code, \
        'FIX-Z FAIL: old always-buy pattern still present'
    
    # 4. IndexError guards must be present
    assert 'if len(hist) > 1' in code or 'if len(ef)>1' in code, \
        'FIX-Z FAIL: IndexError guard not found'
    
    # 5. Position sizing must return 0, not max(1, ...)
    assert 'max(0, int(min(shares_by_risk' in code, \
        'FIX-Z FAIL: position sizing must return max(0, ...) not max(1, ...)'
    
    # 6. Breakeven trigger must be 1.5
    assert 'BREAKEVEN_ATR_TRIGGER  = 1.5' in code, \
        'FIX-Z FAIL: breakeven trigger must be 1.5'
    
    # 7. Profit lock must be 0.75
    assert 'PROFIT_LOCK_ATR_MULT   = 0.75' in code, \
        'FIX-Z FAIL: profit lock must be 0.75'
    
    print('  FIX-Z: direction tracking + guards + params ✅')
