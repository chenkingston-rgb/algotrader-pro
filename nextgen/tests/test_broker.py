from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trader.broker import (
    Instruction,
    Position,
    Quote,
    ReconcileError,
    build_order_plan,
    cap_buy_plan_to_cash,
    deterministic_order_id,
    submit_and_wait,
)


WEIGHTS = {"SPY": 0.2666666667, "QQQ": 0.4666666666, "GLD": 0.0, "BIL": 0.2666666667}
QUOTES = {s: Quote(99.9, 100.1, datetime.now(timezone.utc)) for s in WEIGHTS}


def test_order_ids_are_deterministic_and_distinct():
    a = deterministic_order_id("run-1", "SPY", "buy", 1000, "hash-a")
    assert a == deterministic_order_id("run-1", "SPY", "buy", 1000, "hash-a")
    assert a != deterministic_order_id("run-1", "QQQ", "buy", 1000, "hash-a")
    assert a != deterministic_order_id("run-1", "SPY", "buy", 1000, "hash-b")
    assert len(a) <= 128


def test_sells_are_before_buys():
    positions = [Position("GLD", 10, 1000), Position("BIL", 10, 1000)]
    plan = build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, positions, QUOTES)
    sides = [x.side for x in plan]
    assert sides == sorted(sides, key=lambda x: 0 if x == "sell" else 1)


def test_full_liquidation_never_requests_more_shares_than_held():
    positions = [Position("GLD", 10, 1000)]
    plan = build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, positions, QUOTES)
    sell = next(x for x in plan if x.symbol == "GLD")
    assert sell.qty <= 10


def test_external_and_short_positions_fail_closed():
    with pytest.raises(ReconcileError):
        build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, [Position("AAPL", 1, 100)], QUOTES)
    with pytest.raises(ReconcileError):
        build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, [Position("SPY", -1, -100)], QUOTES)


def test_leveraged_or_incomplete_target_rejected():
    with pytest.raises(ReconcileError):
        build_order_plan("run-1", "0123456789abcdef", 3000, {"SPY": .5, "QQQ": .5, "GLD": .5, "BIL": 0}, [], QUOTES)


def test_buy_plan_is_capped_to_actual_cash_without_margin():
    plan = build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, [], QUOTES)
    capped = cap_buy_plan_to_cash([x for x in plan if x.side == "buy"], cash=1500, reserve_bps=10)
    assert sum(x.qty * x.limit_price for x in capped) <= 1498.50 + 0.01


def test_stale_quote_is_rejected():
    stale = dict(QUOTES)
    stale["SPY"] = Quote(99.9, 100.1, datetime.now(timezone.utc) - timedelta(minutes=2))
    with pytest.raises(ReconcileError, match="Stale quote"):
        build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, [], stale)


def test_missing_quote_timestamp_is_rejected():
    missing = dict(QUOTES)
    missing["SPY"] = Quote(99.9, 100.1, None)
    with pytest.raises(ReconcileError, match="Missing quote timestamp"):
        build_order_plan("run-1", "0123456789abcdef", 3000, WEIGHTS, [], missing)


class NotFound(Exception):
    status_code = 404


def _instruction():
    return Instruction("SPY", "buy", 1.0, 100.0, 100.0, 100.0, "T3Q20-test-SPY-B-abc")


def test_retry_queries_client_id_and_does_not_duplicate(monkeypatch):
    monkeypatch.setattr("trader.broker.time.sleep", lambda _: None)
    order = SimpleNamespace(id="o1", status="filled")
    client = SimpleNamespace(
        get_order_by_client_id=lambda _: order,
        get_order_by_id=lambda _: order,
        submit_order=lambda **_: pytest.fail("must not submit an existing deterministic order"),
    )
    assert submit_and_wait(client, [_instruction()]) == [order]


def test_only_a_confirmed_404_can_trigger_new_submission(monkeypatch):
    monkeypatch.setattr("trader.broker.time.sleep", lambda _: None)
    order = SimpleNamespace(id="o1", status="filled")
    calls = []

    def missing(_):
        raise NotFound()

    client = SimpleNamespace(
        get_order_by_client_id=missing,
        get_order_by_id=lambda _: order,
        submit_order=lambda order_data: calls.append(order_data) or order,
    )
    submit_and_wait(client, [_instruction()])
    assert len(calls) == 1

    client.get_order_by_client_id = lambda _: (_ for _ in ()).throw(TimeoutError("network uncertain"))
    with pytest.raises(TimeoutError):
        submit_and_wait(client, [_instruction()])
    assert len(calls) == 1


def test_rejection_cancels_other_working_orders(monkeypatch):
    monkeypatch.setattr("trader.broker.time.sleep", lambda _: None)
    rejected = SimpleNamespace(id="bad", status="rejected")
    working = SimpleNamespace(id="working", status="accepted")
    by_client = {"bad-id": rejected, "working-id": working}
    by_id = {"bad": rejected, "working": working}
    canceled = []

    def cancel(oid):
        canceled.append(oid)
        by_id[oid].status = "canceled"

    client = SimpleNamespace(
        get_order_by_client_id=lambda oid: by_client[oid],
        get_order_by_id=lambda oid: by_id[oid],
        cancel_order_by_id=cancel,
        submit_order=lambda **_: pytest.fail("existing orders must not be resubmitted"),
    )
    orders = [
        Instruction("SPY", "buy", 1, 100, 100, 100, "working-id"),
        Instruction("QQQ", "buy", 1, 100, 100, 100, "bad-id"),
    ]
    with pytest.raises(ReconcileError, match="rejected"):
        submit_and_wait(client, orders)
    assert "working" in canceled


def test_timeout_is_canceled_and_terminal_cancellation_confirmed(monkeypatch):
    monkeypatch.setattr("trader.broker.time.sleep", lambda _: None)
    order = SimpleNamespace(id="working", status="accepted")
    canceled = []

    def cancel(oid):
        canceled.append(oid)
        order.status = "canceled"

    client = SimpleNamespace(
        get_order_by_client_id=lambda _: order,
        get_order_by_id=lambda _: order,
        cancel_order_by_id=cancel,
        submit_order=lambda **_: pytest.fail("existing order must not be resubmitted"),
    )
    with pytest.raises(ReconcileError, match="Timed out"):
        submit_and_wait(client, [_instruction()], timeout_seconds=0)
    assert canceled == ["working"]

