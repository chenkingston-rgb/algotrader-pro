from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from trader.rebalance import (
    NY,
    _assert_market_and_assets_tradeable,
    _assert_account_identity_and_ready,
    _deployment_target,
    _effective_frozen_target,
    _fill_records,
    _target_hash,
    execution_context,
)


class CalendarClient:
    def __init__(self, days):
        self.days = [SimpleNamespace(date=d) for d in days]

    def get_calendar(self, request):
        return self.days


def sessions():
    return [date(2025, 12, 29), date(2025, 12, 30), date(2025, 12, 31),
            date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]


@pytest.mark.parametrize("day,number", [(2, 1), (5, 2), (6, 3)])
def test_first_three_sessions_are_bounded_catchup_days(day, number):
    now = datetime(2026, 1, day, 9, 40, tzinfo=NY)
    result = execution_context(CalendarClient(sessions()), now)
    assert result.eligible and result.signal_date == date(2025, 12, 31) and result.session_number == number


def test_fourth_session_is_blocked():
    result = execution_context(CalendarClient(sessions()), datetime(2026, 1, 7, 9, 40, tzinfo=NY))
    assert not result.eligible and result.reason == "catch-up window expired"


def test_dst_is_evaluated_in_new_york_time():
    summer = [date(2026, 6, 30), date(2026, 7, 1)]
    result = execution_context(CalendarClient(summer), datetime(2026, 7, 1, 13, 40, tzinfo=timezone.utc))
    assert result.eligible and result.signal_date == date(2026, 6, 30)


def test_outside_local_execution_window_is_blocked():
    result = execution_context(CalendarClient(sessions()), datetime(2026, 1, 2, 8, 30, tzinfo=NY))
    assert not result.eligible


def test_drawdown_freeze_never_increases_risk():
    desired = {"SPY": 0.2666666667, "QQQ": 0.4666666666, "GLD": 0.2666666667, "BIL": 0.0}
    actual = {"SPY": 0.10, "QQQ": 0.30, "GLD": 0.30, "BIL": 0.30}
    effective = _effective_frozen_target(desired, actual)
    assert effective["SPY"] == 0.10 and effective["QQQ"] == 0.30
    assert effective["GLD"] == pytest.approx(desired["GLD"])
    assert sum(effective.values()) == pytest.approx(1.0)


def test_pilot_allocation_scales_risk_and_keeps_remainder_in_bil():
    canonical = {"SPY": 0.8/3, "QQQ": 0.2+0.8/3, "GLD": 0.8/3, "BIL": 0.0}
    pilot = _deployment_target(canonical, 0.25)
    assert pilot["SPY"] == pytest.approx(canonical["SPY"] * 0.25)
    assert pilot["QQQ"] == pytest.approx(canonical["QQQ"] * 0.25)
    assert pilot["GLD"] == pytest.approx(canonical["GLD"] * 0.25)
    assert pilot["BIL"] == pytest.approx(0.75)
    assert sum(pilot.values()) == pytest.approx(1.0)


def test_target_hash_is_deterministic_and_signal_date_specific():
    weights = {"SPY": 0.0, "QQQ": 0.2, "GLD": 0.0, "BIL": 0.8}
    assert _target_hash(weights, "2026-01-30") == _target_hash(weights, "2026-01-30")
    assert _target_hash(weights, "2026-01-30") != _target_hash(weights, "2026-02-27")


def test_market_and_fractional_asset_preflight_fails_closed():
    class Client:
        def __init__(self, open_=True, bad=None):
            self.open, self.bad = open_, bad

        def get_clock(self):
            return SimpleNamespace(is_open=self.open)

        def get_asset(self, symbol):
            return SimpleNamespace(tradable=symbol != self.bad, fractionable=symbol != self.bad)

    _assert_market_and_assets_tradeable(Client())
    with pytest.raises(Exception, match="not open"):
        _assert_market_and_assets_tradeable(Client(open_=False))
    with pytest.raises(Exception, match="fractionable"):
        _assert_market_and_assets_tradeable(Client(bad="GLD"))


def test_account_identity_and_block_flags_are_enforced():
    settings = SimpleNamespace(expected_account_id="expected")

    def client(account_id="expected", blocked=False):
        account = SimpleNamespace(
            id=account_id, status="ACTIVE", account_blocked=blocked,
            trading_blocked=False, trade_suspended_by_user=False,
        )
        return SimpleNamespace(get_account=lambda: account)

    _assert_account_identity_and_ready(client(), settings)
    with pytest.raises(Exception, match="mismatch"):
        _assert_account_identity_and_ready(client(account_id="wrong"), settings)
    with pytest.raises(Exception, match="block"):
        _assert_account_identity_and_ready(client(blocked=True), settings)


def test_fill_evidence_calculates_directional_slippage():
    instruction = SimpleNamespace(
        client_order_id="id1", symbol="SPY", side="buy", qty=1.0,
        reference_price=100.0, limit_price=100.2,
    )
    order = SimpleNamespace(
        id="o1", client_order_id="id1", symbol="SPY", side="buy", status="filled",
        filled_qty="1", filled_avg_price="100.10", submitted_at="t1", filled_at="t2",
    )
    record = _fill_records([order], [instruction])[0]
    assert record["slippage_bps"] == pytest.approx(10.0)
    assert record["filled_qty"] == 1.0
