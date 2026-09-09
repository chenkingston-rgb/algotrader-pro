from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from .config import (
    ALLOWED_ASSETS,
    LIMIT_BUFFER_BPS,
    MAX_GROSS_EXPOSURE,
    MAX_TARGET_WEIGHTS,
    MIN_ORDER_DOLLARS,
    QUOTE_MAX_AGE_SECONDS,
    STRATEGY_VERSION,
)


logger = logging.getLogger(__name__)


class ReconcileError(RuntimeError):
    pass


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    market_value: float


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    timestamp: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class Instruction:
    symbol: str
    side: str
    qty: float
    limit_price: float
    reference_price: float
    target_value: float
    client_order_id: str


def deterministic_order_id(
    run_id: str,
    symbol: str,
    side: str,
    target_value: float,
    target_hash: str,
) -> str:
    raw = f"{STRATEGY_VERSION}|{run_id}|{target_hash}|{symbol}|{side}|{target_value:.2f}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:20]
    return f"T3Q20-{run_id[-10:]}-{symbol}-{side[0].upper()}-{digest}"[:128]


def _round_qty(qty: float) -> float:
    return float(Decimal(str(max(qty, 0.0))).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))


def _marketable_limit(quote: Quote, side: str) -> float:
    raw = quote.ask * (1 + LIMIT_BUFFER_BPS / 10000) if side == "buy" else quote.bid * (1 - LIMIT_BUFFER_BPS / 10000)
    mode = ROUND_UP if side == "buy" else ROUND_DOWN
    return float(Decimal(str(raw)).quantize(Decimal("0.01"), rounding=mode))


def validate_quote(symbol: str, quote: Quote, now: datetime | None = None) -> None:
    if not (quote.bid > 0 and quote.ask >= quote.bid):
        raise ReconcileError(f"Invalid quote for {symbol}")
    if quote.timestamp is None:
        raise ReconcileError(f"Missing quote timestamp for {symbol}")
    now = now or datetime.now(timezone.utc)
    ts = quote.timestamp if quote.timestamp.tzinfo else quote.timestamp.replace(tzinfo=timezone.utc)
    age = (now.astimezone(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
    if age < -5 or age > QUOTE_MAX_AGE_SECONDS:
        raise ReconcileError(f"Stale quote for {symbol}: age={age:.1f}s")


def build_order_plan(
    run_id: str,
    target_hash: str,
    equity: float,
    weights: dict[str, float],
    positions: list[Position],
    quotes: dict[str, Quote],
) -> list[Instruction]:
    if equity <= 0:
        raise ReconcileError("Account equity must be positive")
    if set(weights) != set(ALLOWED_ASSETS) or abs(sum(weights.values()) - 1.0) > 1e-8:
        raise ReconcileError("Target must contain exactly SPY, QQQ, GLD and BIL and sum to 100%")
    if any(weights[s] < -1e-12 or weights[s] > MAX_TARGET_WEIGHTS[s] + 1e-9 for s in ALLOWED_ASSETS):
        raise ReconcileError("Target exceeds the frozen per-asset bounds")
    if sum(weights.values()) > MAX_GROSS_EXPOSURE + 1e-9:
        raise ReconcileError("Gross exposure exceeds 100%")
    external = {p.symbol for p in positions} - set(ALLOWED_ASSETS)
    if external:
        raise ReconcileError(f"Account contains non-strategy positions: {sorted(external)}")
    if any(p.qty < -1e-12 or p.market_value < -1e-8 for p in positions):
        raise ReconcileError("Short or negative-value position detected")
    if set(quotes) != set(ALLOWED_ASSETS):
        raise ReconcileError("A current validated execution quote is required for every target asset")

    if not target_hash or len(target_hash) < 16:
        raise ReconcileError("A durable portfolio target hash is required")
    current = {p.symbol: float(p.market_value) for p in positions}
    instructions: list[Instruction] = []
    for symbol in ALLOWED_ASSETS:
        q = quotes[symbol]
        validate_quote(symbol, q)
        target = equity * weights[symbol]
        held = next((p for p in positions if p.symbol == symbol), None)
        current_value = held.qty * q.mid if held else current.get(symbol, 0.0)
        delta = target - current_value
        if abs(delta) < MIN_ORDER_DOLLARS:
            continue
        side = "buy" if delta > 0 else "sell"
        limit = _marketable_limit(q, side)
        # Quantity tracks the mid-price value delta.  For a sell, dividing by
        # the lower marketable limit could request more shares than are held
        # and accidentally create a short position.
        if side == "sell":
            if held is None:
                raise ReconcileError(f"Cannot sell unheld asset {symbol}")
            qty = min(_round_qty(abs(delta) / q.mid), _round_qty(held.qty))
        else:
            # Buys use the worst-case limit so planned notional is conservative.
            qty = _round_qty(abs(delta) / limit)
        if qty <= 0:
            continue
        instructions.append(Instruction(
            symbol, side, qty, round(limit, 2), q.mid, target,
            deterministic_order_id(run_id, symbol, side, target, target_hash),
        ))
    # Release capital before deploying it.  Stable symbol ordering makes plans reproducible.
    return sorted(instructions, key=lambda x: (0 if x.side == "sell" else 1, x.symbol))


def cap_buy_plan_to_cash(instructions: list[Instruction], cash: float, reserve_bps: float = 10.0) -> list[Instruction]:
    """Scale a freshly rebuilt buy plan so its worst-case limit notional cannot use margin."""
    if cash < 0 or not (0 <= reserve_bps < 10000):
        raise ReconcileError("Cash and reserve must be non-negative")
    if any(x.side != "buy" for x in instructions):
        raise ReconcileError("Cash cap accepts buy instructions only")
    required = sum(x.qty * x.limit_price for x in instructions)
    available = cash * (1.0 - reserve_bps / 10000.0)
    if required <= available + 1e-8:
        return instructions
    if available < MIN_ORDER_DOLLARS or required <= 0:
        return []
    scale = available / required
    adjusted: list[Instruction] = []
    for x in instructions:
        qty = _round_qty(x.qty * scale)
        if qty * x.limit_price >= MIN_ORDER_DOLLARS:
            adjusted.append(replace(x, qty=qty))
    if sum(x.qty * x.limit_price for x in adjusted) > available + 0.01:
        raise ReconcileError("Cash-capped buy plan still exceeds available cash")
    return adjusted


def submit_and_wait(client, instructions: list[Instruction], timeout_seconds: int = 180) -> list[object]:
    """Idempotent submission; a retry always queries the deterministic ID first."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import LimitOrderRequest

    def status_text(order) -> str:
        value = getattr(order.status, "value", order.status)
        return str(value).lower()

    def is_not_found(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        return status == 404

    def cancel_and_confirm(order_ids, confirm_seconds: int = 30) -> None:
        remaining = {str(x) for x in order_ids}
        for oid in list(remaining):
            try:
                client.cancel_order_by_id(oid)
            except Exception as exc:
                logger.warning("Cancel request failed for order %s; confirmation polling will continue: %r", oid, exc)
        deadline = time.time() + confirm_seconds
        while remaining and time.time() < deadline:
            time.sleep(1)
            for oid in list(remaining):
                try:
                    status = status_text(client.get_order_by_id(oid))
                except Exception as exc:
                    logger.warning("Cancellation-status poll failed for order %s: %r", oid, exc)
                    continue
                if status in {"filled", "canceled", "expired", "rejected"}:
                    remaining.remove(oid)
        if remaining:
            raise ReconcileError(f"Cancellation could not be confirmed for orders: {sorted(remaining)}")

    completed = []
    for phase in ("sell", "buy"):
        phase_orders = [x for x in instructions if x.side == phase]
        submitted = []
        for x in phase_orders:
            try:
                order = client.get_order_by_client_id(x.client_order_id)
                status = status_text(order)
                if status in {"canceled", "expired", "rejected"}:
                    cancel_and_confirm(str(o.id) for o in submitted)
                    raise ReconcileError(f"Existing order {x.client_order_id} is {status}; operator review required")
            except Exception as exc:
                if not is_not_found(exc):
                    raise
                request = LimitOrderRequest(
                    symbol=x.symbol, qty=x.qty,
                    side=OrderSide.SELL if phase == "sell" else OrderSide.BUY,
                    time_in_force=TimeInForce.DAY, limit_price=x.limit_price,
                    client_order_id=x.client_order_id,
                )
                order = client.submit_order(order_data=request)
            submitted.append(order)
        deadline = time.time() + timeout_seconds
        pending = {str(o.id): o for o in submitted}
        while pending and time.time() < deadline:
            time.sleep(2)
            for oid in list(pending):
                order = client.get_order_by_id(oid)
                status = status_text(order)
                if status in {"filled", "canceled", "expired", "rejected"}:
                    completed.append(order)
                    pending.pop(oid)
                    if status != "filled":
                        cancel_and_confirm(pending)
                        raise ReconcileError(f"Order {oid} ended as {status}")
        if pending:
            # Never leave a timed-out order working while the state machine
            # reports a failure.  Cancel, record the incident and require a
            # broker reconciliation before another submission.
            cancel_and_confirm(pending)
            raise ReconcileError(f"Timed out waiting for {phase} orders: {list(pending)}")
    return completed
