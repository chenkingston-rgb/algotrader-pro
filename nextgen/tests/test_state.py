from datetime import datetime, timezone

import pytest

from trader.state import RunStore


def test_state_machine_is_durable_atomic_and_idempotent(tmp_path):
    path = tmp_path / "state.sqlite3"
    s = RunStore(str(path))
    assert s.start("r1", {"a": 1}) == "CREATED"
    s.transition("r1", "DATA_VALIDATED", {"a": 1})
    assert s.start("r1", {"a": 2}) == "DATA_VALIDATED"
    assert s.payload("r1") == {"a": 1}
    assert s.conn.execute("SELECT COUNT(*) FROM events WHERE event='START'").fetchone()[0] == 1
    with pytest.raises(RuntimeError):
        s.transition("r1", "COMPLETE", {})
    assert s.status("r1") == "DATA_VALIDATED"


def test_peak_floor_preserves_migration_drawdown_history(tmp_path):
    s = RunStore(str(tmp_path / "state.sqlite3"))
    result = s.record_equity(85_000, datetime(2026, 1, 2, tzinfo=timezone.utc), peak_floor=100_000)
    assert result["peak_equity"] == 100_000
    assert result["drawdown_pct"] == pytest.approx(-15.0)


def test_drawdown_is_adjusted_for_external_deposits_and_withdrawals(tmp_path):
    s = RunStore(str(tmp_path / "state.sqlite3"))
    s.record_equity(100_000, datetime(2026, 1, 2, tzinfo=timezone.utc))
    after_deposit = s.record_equity(
        110_000, datetime(2026, 1, 3, tzinfo=timezone.utc), cumulative_external_cash_flow=10_000
    )
    assert after_deposit["adjusted_equity"] == 100_000
    assert after_deposit["drawdown_pct"] == pytest.approx(0)
    after_loss = s.record_equity(
        95_000, datetime(2026, 1, 4, tzinfo=timezone.utc), cumulative_external_cash_flow=10_000
    )
    assert after_loss["drawdown_pct"] == pytest.approx(-15.0)
