"""Dependency-light smoke test for a deployment host."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

from trader.broker import Position, Quote, ReconcileError, build_order_plan, cap_buy_plan_to_cash, deterministic_order_id
from trader.state import RunStore
from trader.strategy import SignalDataError, decide


def prices(spy=101.0, qqq=101.0, gld=101.0, bil=100.0):
    idx = pd.bdate_range("2025-01-01", periods=205)
    base = [100.0] * 204
    return pd.DataFrame({"SPY": base+[spy], "QQQ": base+[qqq], "GLD": base+[gld], "BIL": base+[bil]}, index=idx)


def main():
    x = prices()
    assert decide(x, x.index[-1]).weights == {
        "SPY": 0.8/3, "QQQ": 0.2 + 0.8/3, "GLD": 0.8/3, "BIL": 0.0,
    }
    x = prices(spy=99, qqq=99, gld=99)
    assert decide(x, x.index[-1]).weights == {"SPY": 0.0, "QQQ": 0.2, "GLD": 0.0, "BIL": 0.8}
    x = prices()
    when = x.index[-2]
    before = decide(x, when)
    x.iloc[-1] = 1_000_000
    assert before == decide(x, when)
    x = prices()
    x.loc[x.index[-10], "GLD"] = None
    try:
        decide(x, x.index[-1])
        raise AssertionError("missing window did not fail")
    except SignalDataError:
        pass

    oid = deterministic_order_id("r1", "SPY", "buy", 1000, "target-hash")
    assert oid == deterministic_order_id("r1", "SPY", "buy", 1000, "target-hash") and len(oid) <= 128
    weights = {"SPY": 0.8/3, "QQQ": 0.2+0.8/3, "GLD": 0.0, "BIL": 0.8/3}
    quotes = {s: Quote(99.9, 100.1, datetime.now(timezone.utc)) for s in weights}
    plan = build_order_plan("r1", "0123456789abcdef", 3000, weights, [Position("GLD", 10, 1000), Position("BIL", 10, 1000)], quotes)
    assert [o.side for o in plan] == sorted([o.side for o in plan], key=lambda z: 0 if z == "sell" else 1)
    buys = [x for x in build_order_plan("r1", "0123456789abcdef", 3000, weights, [], quotes) if x.side == "buy"]
    capped = cap_buy_plan_to_cash(buys, cash=1500)
    assert sum(x.qty * x.limit_price for x in capped) <= 1498.50 + 0.01
    try:
        build_order_plan("r1", "0123456789abcdef", 3000, weights, [Position("AAPL", 1, 100)], quotes)
        raise AssertionError("external position allowed")
    except ReconcileError:
        pass

    db = Path(".selftest_state.sqlite3")
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    store = RunStore(str(db))
    assert store.start("r1", {}) == "CREATED"
    store.transition("r1", "DATA_VALIDATED", {})
    assert store.start("r1", {}) == "DATA_VALIDATED"
    store.conn.close()
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db) + suffix)
        if p.exists():
            p.unlink()
    print("TREND3-QQQ20 SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()

