from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .alerts import ping_heartbeat, send_alert
from .broker import (
    Instruction,
    Position,
    Quote,
    ReconcileError,
    build_order_plan,
    cap_buy_plan_to_cash,
    submit_and_wait,
)
from .config import (
    ALLOWED_ASSETS,
    CRITICAL_REAUDIT_DRAWDOWN_PCT,
    EXECUTION_POLICY_VERSION,
    FREEZE_RISK_INCREASE_DRAWDOWN_PCT,
    INTERMONTH_DRIFT_ALERT_BPS,
    MAX_CATCHUP_TRADING_DAYS,
    REBALANCE_END_NY,
    REBALANCE_START_NY,
    REVIEW_DRAWDOWN_PCT,
    RISK_ASSETS,
    SCHEMA_VERSION,
    STRATEGY_VERSION,
    TARGET_DRIFT_TOLERANCE_BPS,
    Settings,
)
from .data import (
    assert_exchange_sessions,
    assert_provider_agreement,
    fetch_adjusted_closes,
    fetch_yahoo_adjusted_closes,
    make_signal_snapshot,
    restore_signal_snapshot,
)


from .state import RunStore, create_run_store
from .strategy import decide
from .telemetry import publish_status


logger = logging.getLogger(__name__)
NY = ZoneInfo("America/New_York")
ACTIVE_STATES = {
    "CREATED", "DATA_VALIDATED", "SIGNAL_LOCKED", "RECONCILED",
    "SELLING", "SELLS_CONFIRMED", "BUYING", "VERIFYING",
}


@dataclass(frozen=True)
class ExecutionWindow:
    eligible: bool
    signal_date: date | None
    session_number: int | None
    reason: str


def _clock(value: str) -> time:
    return time.fromisoformat(value)


def execution_context(trading_client, now: datetime) -> ExecutionWindow:
    """Find the prior month-end and allow a bounded three-session catch-up."""
    from alpaca.trading.requests import GetCalendarRequest

    local = now.astimezone(NY)
    if not (_clock(REBALANCE_START_NY) <= local.time().replace(tzinfo=None) <= _clock(REBALANCE_END_NY)):
        return ExecutionWindow(False, None, None, "outside execution window")
    calendars = trading_client.get_calendar(
        GetCalendarRequest(start=local.date() - timedelta(days=45), end=local.date())
    )
    sessions = sorted({c.date for c in calendars})
    if local.date() not in sessions:
        return ExecutionWindow(False, None, None, "not a regular trading session")
    current_month = [d for d in sessions if d.year == local.year and d.month == local.month]
    if local.date() not in current_month:
        return ExecutionWindow(False, None, None, "calendar response is incomplete")
    session_number = current_month.index(local.date()) + 1
    prior_month = [d for d in sessions if d < current_month[0]]
    if not prior_month:
        raise RuntimeError("Market calendar did not include the prior month-end")
    signal_date = prior_month[-1]
    if session_number > MAX_CATCHUP_TRADING_DAYS:
        return ExecutionWindow(False, signal_date, session_number, "catch-up window expired")
    return ExecutionWindow(True, signal_date, session_number, "rebalance due")


def _expected_signal_sessions(trading_client, signal_date: date) -> list[date]:
    from alpaca.trading.requests import GetCalendarRequest

    calendars = trading_client.get_calendar(
        GetCalendarRequest(start=signal_date - timedelta(days=400), end=signal_date)
    )
    sessions = sorted({c.date for c in calendars if c.date <= signal_date})
    if len(sessions) < 200 or sessions[-1] != signal_date:
        raise RuntimeError("Alpaca calendar cannot establish the exact 200-session signal window")
    return sessions[-200:]


def _latest_quotes(settings: Settings) -> dict[str, Quote]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockLatestQuoteRequest

    client = StockHistoricalDataClient(settings.api_key, settings.api_secret)
    if settings.quote_data_feed != "iex":
        raise ReconcileError("The zero-subscription build requires current IEX quotes")
    raw = client.get_stock_latest_quote(
        StockLatestQuoteRequest(symbol_or_symbols=list(ALLOWED_ASSETS), feed=DataFeed.IEX)
    )
    missing = set(ALLOWED_ASSETS) - set(raw)
    if missing:
        raise ReconcileError(f"Missing IEX quotes: {sorted(missing)}")
    return {
        s: Quote(float(raw[s].bid_price), float(raw[s].ask_price), getattr(raw[s], "timestamp", None))
        for s in ALLOWED_ASSETS
    }


def _positions(trading_client) -> list[Position]:
    return [Position(str(p.symbol), float(p.qty), float(p.market_value)) for p in trading_client.get_all_positions()]


def _optional_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in {float("inf"), float("-inf")} else None


def _holding_snapshot(trading_client) -> list[dict]:
    """Read-only broker marks for the mobile operator dashboard.

    These values never participate in signal generation or order sizing.  They
    are deliberately optional because Alpaca may omit a mark outside the
    session or for an empty paper account.
    """
    rows: list[dict] = []
    for position in trading_client.get_all_positions():
        rows.append({
            "symbol": str(position.symbol),
            "qty": _optional_float(getattr(position, "qty", None)),
            "market_value": _optional_float(getattr(position, "market_value", None)),
            "current_price": _optional_float(getattr(position, "current_price", None)),
            "avg_entry_price": _optional_float(getattr(position, "avg_entry_price", None)),
            "unrealized_pl": _optional_float(getattr(position, "unrealized_pl", None)),
            "unrealized_plpc": _optional_float(getattr(position, "unrealized_plpc", None)),
            "change_today": _optional_float(getattr(position, "change_today", None)),
        })
    return sorted(rows, key=lambda row: row["symbol"])


def _signal_summary(snapshot: dict | None) -> list[dict]:
    if not snapshot:
        return []
    frame = restore_signal_snapshot(snapshot)
    signal_date = date.fromisoformat(str(snapshot["signal_date"]))
    decision = decide(frame, signal_date)
    return [
        {
            "symbol": signal.symbol,
            "close": signal.close,
            "sma_200": signal.sma,
            "distance_pct": (signal.close / signal.sma - 1.0) * 100.0,
            "risk_on": signal.risk_on,
        }
        for signal in decision.signals
    ]


def _assert_account_identity_and_ready(trading_client, settings: Settings) -> None:
    account = trading_client.get_account()
    actual_id = str(getattr(account, "id", ""))
    if settings.expected_account_id and actual_id != settings.expected_account_id:
        raise ReconcileError(
            f"Broker account mismatch: expected {settings.expected_account_id}, received {actual_id or 'missing'}"
        )
    status = str(getattr(getattr(account, "status", None), "value", getattr(account, "status", ""))).lower()
    if status != "active":
        raise ReconcileError(f"Broker account is not ACTIVE: {status or 'missing status'}")
    if any(
        bool(getattr(account, field, False))
        for field in ("account_blocked", "trading_blocked", "trade_suspended_by_user")
    ):
        raise ReconcileError("Broker account reports a trading/account block or suspension")


def _open_orders(trading_client) -> list[object]:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    return list(trading_client.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN)))


def _recent_rejected_orders(trading_client, now: datetime | None = None) -> list[str]:
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    after = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(days=7)
    orders = trading_client.get_orders(
        filter=GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=after,
            symbols=list(ALLOWED_ASSETS),
            limit=500,
        )
    )
    rejected = []
    for order in orders:
        client_id = str(getattr(order, "client_order_id", ""))
        status = str(getattr(getattr(order, "status", None), "value", getattr(order, "status", ""))).lower()
        if client_id.startswith("T3Q20-") and status == "rejected":
            rejected.append(client_id)
    return rejected


def _assert_no_open_orders(trading_client) -> None:
    orders = _open_orders(trading_client)
    if orders:
        raise ReconcileError(f"Open orders exist before reconciliation: {[str(o.id) for o in orders]}")


def _assert_market_and_assets_tradeable(trading_client) -> None:
    clock = trading_client.get_clock()
    if getattr(clock, "is_open", False) is not True:
        raise ReconcileError("Alpaca reports that the regular market is not open")
    problems = []
    for symbol in ALLOWED_ASSETS:
        asset = trading_client.get_asset(symbol)
        if getattr(asset, "tradable", False) is not True or getattr(asset, "fractionable", False) is not True:
            problems.append(symbol)
    if problems:
        raise ReconcileError(f"Assets are not both tradable and fractionable: {problems}")


def _assert_only_expected_orders(trading_client, expected: list[Instruction]) -> None:
    allowed = {x.client_order_id for x in expected}
    unexpected = [
        str(getattr(o, "client_order_id", getattr(o, "id", "unknown")))
        for o in _open_orders(trading_client)
        if getattr(o, "client_order_id", None) not in allowed
    ]
    if unexpected:
        raise ReconcileError(f"Unrelated open orders detected during recovery: {unexpected}")


def _target_hash(weights: dict[str, float], signal_date: date | str) -> str:
    material = json.dumps(
        {"strategy": STRATEGY_VERSION, "execution_policy": EXECUTION_POLICY_VERSION,
         "signal_date": str(signal_date), "weights": weights},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _instruction_list(raw: list[dict]) -> list[Instruction]:
    return [Instruction(**x) for x in raw]


def _fill_records(orders: list[object], instructions: list[Instruction]) -> list[dict]:
    planned = {x.client_order_id: x for x in instructions}
    records = []
    for order in orders:
        client_id = str(getattr(order, "client_order_id", ""))
        instruction = planned.get(client_id)
        filled_price_raw = getattr(order, "filled_avg_price", None)
        filled_price = float(filled_price_raw) if filled_price_raw not in (None, "") else None
        slippage_bps = None
        if instruction and filled_price is not None and instruction.reference_price > 0:
            direction = 1.0 if instruction.side == "buy" else -1.0
            slippage_bps = direction * (filled_price / instruction.reference_price - 1.0) * 10000
        records.append({
            "order_id": str(getattr(order, "id", "")),
            "client_order_id": client_id,
            "symbol": str(getattr(order, "symbol", getattr(instruction, "symbol", ""))),
            "side": str(getattr(getattr(order, "side", None), "value", getattr(order, "side", getattr(instruction, "side", "")))),
            "status": str(getattr(getattr(order, "status", None), "value", getattr(order, "status", ""))),
            "requested_qty": getattr(instruction, "qty", None),
            "filled_qty": float(getattr(order, "filled_qty", 0) or 0),
            "reference_mid": getattr(instruction, "reference_price", None),
            "limit_price": getattr(instruction, "limit_price", None),
            "filled_avg_price": filled_price,
            "slippage_bps": slippage_bps,
            "submitted_at": str(getattr(order, "submitted_at", "")),
            "filled_at": str(getattr(order, "filled_at", "")),
        })
    return records


def _account_snapshot(trading_client, store: RunStore, now: datetime, settings: Settings) -> dict:
    account = trading_client.get_account()
    equity = float(account.equity)
    cash = float(account.cash)
    buying_power = float(account.buying_power)
    if cash < -0.01:
        raise ReconcileError(f"Negative broker cash indicates margin use: {cash}")
    if buying_power < 0:
        raise ReconcileError(f"Negative broker buying power: {buying_power}")
    risk = store.record_equity(
        equity,
        now.astimezone(timezone.utc),
        peak_floor=settings.initial_peak_equity,
        cumulative_external_cash_flow=settings.cumulative_external_cash_flow,
    )
    risk["review_active"] = risk["drawdown_pct"] <= -REVIEW_DRAWDOWN_PCT * 100
    risk["halt_active"] = risk["drawdown_pct"] <= -FREEZE_RISK_INCREASE_DRAWDOWN_PCT * 100
    risk["kill_switch_active"] = risk["drawdown_pct"] <= -CRITICAL_REAUDIT_DRAWDOWN_PCT * 100
    risk["policy"] = "review/freeze/re-audit; never auto-liquidate solely from account drawdown"
    last_equity = _optional_float(getattr(account, "last_equity", None))
    day_pl = equity - last_equity if last_equity and last_equity > 0 else None
    basis = settings.initial_peak_equity
    adjusted_equity = _optional_float(risk.get("adjusted_equity"))
    total_pl = adjusted_equity - basis if adjusted_equity is not None and basis else None
    return {
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "last_equity": last_equity,
        "day_pl": day_pl,
        "day_pl_pct": day_pl / last_equity if day_pl is not None and last_equity else None,
        "configured_basis_equity": basis,
        "total_pl": total_pl,
        "total_pl_pct": total_pl / basis if total_pl is not None and basis else None,
        "long_market_value": _optional_float(getattr(account, "long_market_value", None)),
        "portfolio_value": _optional_float(getattr(account, "portfolio_value", None)),
        "positions": _holding_snapshot(trading_client),
        **risk,
    }


def _actual_weights(equity: float, positions: list[Position], quotes: dict[str, Quote]) -> tuple[dict[str, float], dict[str, float]]:
    if equity <= 0:
        raise ReconcileError("Account equity must be positive")
    external = {p.symbol for p in positions} - set(ALLOWED_ASSETS)
    if external:
        raise ReconcileError(f"Account contains non-strategy positions: {sorted(external)}")
    values = {s: 0.0 for s in ALLOWED_ASSETS}
    for p in positions:
        if p.qty < -1e-12:
            raise ReconcileError(f"Short position detected: {p.symbol}")
        if p.symbol not in quotes:
            raise ReconcileError(f"Missing current quote for held asset {p.symbol}")
        values[p.symbol] = p.qty * quotes[p.symbol].mid
    weights = {s: values[s] / equity for s in ALLOWED_ASSETS}
    return weights, values


def _effective_frozen_target(desired: dict[str, float], actual: dict[str, float]) -> dict[str, float]:
    """At a 15% drawdown, permit only risk reduction and Treasury purchases."""
    result = {s: min(max(actual.get(s, 0.0), 0.0), desired[s]) for s in RISK_ASSETS}
    result["BIL"] = 1.0 - sum(result.values())
    return result


def _deployment_target(canonical: dict[str, float], allocation: float) -> dict[str, float]:
    """Scale a pilot's risk assets while holding all undeployed capital in BIL."""
    if not (0 < allocation <= 1.0):
        raise ValueError("Deployment allocation must be in (0, 1]")
    result = {s: canonical[s] * allocation for s in RISK_ASSETS}
    result["BIL"] = 1.0 - sum(result.values())
    return result


def _reconcile_snapshot(
    trading_client,
    target: dict[str, float],
    settings: Settings,
    tolerance_bps: float = TARGET_DRIFT_TOLERANCE_BPS,
) -> dict:
    account = trading_client.get_account()
    equity = float(account.equity)
    quotes = _latest_quotes(settings)
    actual, values = _actual_weights(equity, _positions(trading_client), quotes)
    drift = {s: (actual[s] - target[s]) * 10000 for s in ALLOWED_ASSETS}
    open_count = len(_open_orders(trading_client))
    rejected_ids = _recent_rejected_orders(trading_client)
    return {
        "ok": open_count == 0 and not rejected_ids and max((abs(x) for x in drift.values()), default=0.0) <= tolerance_bps,
        "equity": equity,
        "cash": float(account.cash),
        "actual_weights": actual,
        "values": values,
        "drift_bps": drift,
        "max_abs_drift_bps": max((abs(x) for x in drift.values()), default=0.0),
        "open_order_count": open_count,
        "rejected_order_count": len(rejected_ids),
        "recent_rejected_client_order_ids": rejected_ids,
    }


def _reconcile_broker_marks(
    trading_client,
    target: dict[str, float],
    tolerance_bps: float = INTERMONTH_DRIFT_ALERT_BPS,
) -> dict:
    """Closed-market health check using broker marks; never used to size orders."""
    account = trading_client.get_account()
    equity = float(account.equity)
    positions = _positions(trading_client)
    external = {p.symbol for p in positions} - set(ALLOWED_ASSETS)
    if external or any(p.qty < -1e-12 or p.market_value < -1e-8 for p in positions):
        raise ReconcileError(f"Unauthorized or short broker position: {sorted(external)}")
    values = {s: 0.0 for s in ALLOWED_ASSETS}
    for position in positions:
        values[position.symbol] = position.market_value
    actual = {s: values[s] / equity for s in ALLOWED_ASSETS}
    drift = {s: (actual[s] - target[s]) * 10000 for s in ALLOWED_ASSETS}
    open_count = len(_open_orders(trading_client))
    rejected_ids = _recent_rejected_orders(trading_client)
    maximum = max((abs(x) for x in drift.values()), default=0.0)
    return {
        "ok": open_count == 0 and not rejected_ids and maximum <= tolerance_bps,
        "equity": equity,
        "cash": float(account.cash),
        "actual_weights": actual,
        "values": values,
        "drift_bps": drift,
        "max_abs_drift_bps": maximum,
        "open_order_count": open_count,
        "rejected_order_count": len(rejected_ids),
        "recent_rejected_client_order_ids": rejected_ids,
        "valuation_source": "broker marks; market closed; not used for execution",
    }


def _safe_alert(settings: Settings, store: RunStore, run_id: str | None, title: str, detail: str) -> bool:
    try:
        send_alert(settings.alert_webhook_url, title, detail)
        return True
    except Exception as exc:
        store.record_error(run_id, exc, {"component": "alert", "title": title})
        return False


def _safe_heartbeat(settings: Settings, store: RunStore, run_id: str | None) -> bool:
    try:
        ping_heartbeat(settings.heartbeat_url)
        return True
    except Exception as exc:
        store.record_error(run_id, exc, {"component": "heartbeat"})
        return False


def _publish(
    settings: Settings,
    *,
    now: datetime,
    mode: str,
    run_id: str | None,
    signal_date: str | None,
    state: str,
    due: bool,
    target_hash: str | None,
    account: dict,
    target: dict,
    actual: dict,
    reconciliation: dict,
    risk: dict,
    heartbeat_ok: bool,
    signals: list[dict] | None = None,
) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "mode": mode,
        "run": {
            "run_id": run_id,
            "signal_date": signal_date,
            "rebalance_due": due,
            "state": state,
            "last_run_ok": state == "COMPLETE",
            "target_hash": target_hash,
            "strategy_allocation": account.get("strategy_allocation"),
        },
        "account": account,
        "target": target,
        "actual": actual,
        "reconciliation": reconciliation,
        "risk": risk,
        "signals": signals or [],
        "services": {
            "alert_webhook_configured": bool(settings.alert_webhook_url),
            "heartbeat_configured": bool(settings.heartbeat_url),
            "heartbeat_last_attempt_ok": heartbeat_ok,
        },
    }
    return publish_status(
        settings.status_path,
        payload,
        now=now.astimezone(timezone.utc),
        api_url=settings.state_api_url,
        api_token=settings.state_api_write_token,
    )


def _publish_no_action(
    settings: Settings,
    store: RunStore,
    trading_client,
    now: datetime,
    window: ExecutionWindow,
) -> dict:
    """Refresh operational health on every scheduler invocation.

    Normal market drift is allowed up to the wider inter-month alert boundary;
    the 50-bps requirement remains mandatory immediately after a rebalance.
    """
    latest = store.latest_terminal_payload()
    latest_signal = (latest or {}).get("signal_date")
    signals = _signal_summary((latest or {}).get("primary_signal_snapshot"))
    missed = bool(
        window.reason == "catch-up window expired"
        and window.signal_date is not None
        and latest_signal != window.signal_date.isoformat()
    )
    target = {k: float(v) for k, v in (latest or {}).get("effective_weights", {}).items()}
    desired = {k: float(v) for k, v in (latest or {}).get("desired_weights", {}).items()}
    target_hash = (latest or {}).get("target_hash")
    state = "MISSED" if missed else ((latest or {}).get("_terminal_status", "UNKNOWN"))
    heartbeat_ok = False
    try:
        account = _account_snapshot(trading_client, store, now, settings)
        account["strategy_allocation"] = settings.strategy_allocation
        if set(target) == set(ALLOWED_ASSETS):
            if getattr(trading_client.get_clock(), "is_open", False) is True:
                reconciliation = _reconcile_snapshot(
                    trading_client, target, settings, tolerance_bps=INTERMONTH_DRIFT_ALERT_BPS
                )
            else:
                reconciliation = _reconcile_broker_marks(
                    trading_client, target, tolerance_bps=INTERMONTH_DRIFT_ALERT_BPS
                )
            actual = reconciliation["actual_weights"]
        else:
            reconciliation = {
                "ok": False, "open_order_count": len(_open_orders(trading_client)),
                "rejected_order_count": 0, "max_abs_drift_bps": None, "drift_bps": {},
            }
            actual = {}
    except Exception as exc:
        store.record_error(None, exc, {"component": "daily health reconciliation"})
        account, actual = {}, {}
        reconciliation = {
            "ok": False, "open_order_count": None, "rejected_order_count": None,
            "max_abs_drift_bps": None, "drift_bps": {}, "error": repr(exc),
        }
    risk = {
        "drawdown_pct": account.get("drawdown_pct"),
        "review_active": account.get("review_active"),
        "halt_active": account.get("halt_active"),
        "kill_switch_active": account.get("kill_switch_active"),
    }
    if account.get("review_active"):
        _safe_alert(
            settings,
            store,
            None,
            f"{STRATEGY_VERSION}: drawdown review",
            f"Observed drawdown is {account.get('drawdown_pct')}% during daily health check.",
        )
    if reconciliation.get("ok") is not True:
        _safe_alert(
            settings,
            store,
            None,
            f"{STRATEGY_VERSION}: reconciliation unhealthy",
            json.dumps(reconciliation, sort_keys=True, default=str),
        )
    if reconciliation.get("ok") is True and not account.get("halt_active") and not account.get("kill_switch_active"):
        heartbeat_ok = _safe_heartbeat(settings, store, None)
    published = _publish(
        settings,
        now=now,
        mode="paper" if settings.paper else "live",
        run_id=None,
        signal_date=window.signal_date.isoformat() if window.signal_date else latest_signal,
        state=state,
        due=missed,
        target_hash=target_hash,
        account=account,
        target={"desired": desired, "effective": target},
        actual=actual,
        reconciliation=reconciliation,
        risk=risk,
        heartbeat_ok=heartbeat_ok,
        signals=signals,
    )
    if missed:
        _safe_alert(
            settings,
            store,
            None,
            f"{STRATEGY_VERSION}: rebalance missed",
            f"No completed run for signal date {window.signal_date}; catch-up window expired.",
        )
    return published


def _publish_failure(
    settings: Settings,
    store: RunStore,
    now: datetime,
    run_id: str,
    signal_date: str,
    exc: Exception,
) -> None:
    """Immediately replace any prior green dashboard with a red failure state."""
    try:
        payload = store.payload(run_id)
        _publish(
            settings,
            now=now,
            mode="paper" if settings.paper else "live",
            run_id=run_id,
            signal_date=signal_date,
            state="ERROR",
            due=True,
            target_hash=payload.get("target_hash"),
            account={},
            target={
                "desired": payload.get("desired_weights", {}),
                "effective": payload.get("effective_weights", {}),
            },
            actual={},
            reconciliation={
                "ok": False, "open_order_count": None, "rejected_order_count": None,
                "max_abs_drift_bps": None, "drift_bps": {}, "error": repr(exc),
            },
            risk={
                "drawdown_pct": None, "review_active": None,
                "halt_active": None, "kill_switch_active": None,
            },
            heartbeat_ok=False,
        )
    except Exception as publish_exc:
        store.record_error(run_id, publish_exc, {"component": "failure status publication"})


def _run_once(now: datetime | None = None) -> dict:
    """Run or safely resume one canonical month-end rebalance."""
    from alpaca.trading.client import TradingClient

    settings = Settings.from_env()
    trading = TradingClient(settings.api_key, settings.api_secret, paper=settings.paper)
    _assert_account_identity_and_ready(trading, settings)
    now = now or datetime.now(tz=NY)
    store = create_run_store(settings)
    window = execution_context(trading, now)
    if not window.eligible or window.signal_date is None:
        published = _publish_no_action(settings, store, trading, now, window)
        return {
            "status": "NO_ACTION", "reason": window.reason,
            "signal_date": window.signal_date.isoformat() if window.signal_date else None,
            "time": now.isoformat(), "dashboard_health": published["health"],
        }

    signal_date_text = window.signal_date.isoformat()
    run_id = f"{STRATEGY_VERSION}-{EXECUTION_POLICY_VERSION}-{signal_date_text}"
    mode = "paper" if settings.paper else "live"
    existing = store.start(
        run_id,
        {"strategy_version": STRATEGY_VERSION, "signal_date": signal_date_text,
         "execution_session_number": window.session_number, "paper": settings.paper},
    )
    if existing in {"COMPLETE", "ATTENTION"}:
        published = _publish_no_action(settings, store, trading, now, window)
        return {
            "status": f"ALREADY_{existing}", "run_id": run_id,
            "dashboard_health": published["health"],
        }
    if existing == "FAILED" or existing not in ACTIVE_STATES:
        raise RuntimeError(f"Run {run_id} cannot resume from state {existing}")

    try:
        state = store.status(run_id)
        payload = store.payload(run_id)
        primary = None

        if state == "CREATED":
            end = datetime.combine(window.signal_date + timedelta(days=1), time.min, tzinfo=NY)
            primary = fetch_adjusted_closes(settings, end)
            secondary = fetch_yahoo_adjusted_closes(end)
            assert_provider_agreement(primary, secondary, window.signal_date)
            expected_sessions = _expected_signal_sessions(trading, window.signal_date)
            assert_exchange_sessions(primary, expected_sessions, window.signal_date)
            assert_exchange_sessions(secondary, expected_sessions, window.signal_date)
            payload.update({
                "data_rows": len(primary),
                "data_validated_at": now.isoformat(),
                "primary_signal_snapshot": make_signal_snapshot(
                    primary, window.signal_date, "alpaca-sip-delayed-adjustment-all", now
                ),
                "secondary_signal_snapshot": make_signal_snapshot(
                    secondary, window.signal_date, "yahoo-adjusted-close", now
                ),
            })
            store.transition(run_id, "DATA_VALIDATED", payload)
            state = "DATA_VALIDATED"

        if state == "DATA_VALIDATED":
            if primary is None:
                primary = restore_signal_snapshot(payload["primary_signal_snapshot"])
                # Verify both durable inputs before relying on the primary.
                restore_signal_snapshot(payload["secondary_signal_snapshot"])
            decision = decide(primary, window.signal_date)
            canonical = decision.weights
            desired = _deployment_target(canonical, settings.strategy_allocation)
            payload.update({
                "canonical_weights": canonical,
                "desired_weights": desired,
                "strategy_allocation": settings.strategy_allocation,
                "signals": [asdict(x) for x in decision.signals],
                "target_hash": _target_hash(desired, signal_date_text),
            })
            store.transition(run_id, "SIGNAL_LOCKED", payload)
            state = "SIGNAL_LOCKED"

        payload = store.payload(run_id)
        desired = {k: float(v) for k, v in payload["desired_weights"].items()}
        target_hash = str(payload["target_hash"])

        if state == "SIGNAL_LOCKED":
            _assert_market_and_assets_tradeable(trading)
            _assert_no_open_orders(trading)
            account = _account_snapshot(trading, store, now, settings)
            account["strategy_allocation"] = settings.strategy_allocation
            quotes = _latest_quotes(settings)
            positions = _positions(trading)
            actual, _ = _actual_weights(account["equity"], positions, quotes)
            effective = _effective_frozen_target(desired, actual) if account["halt_active"] else desired
            plan = build_order_plan(run_id, target_hash, account["equity"], effective, positions, quotes)
            payload.update({
                "account_at_reconcile": account,
                "effective_weights": effective,
                "risk_freeze_applied": bool(account["halt_active"]),
                "sell_orders": [asdict(x) for x in plan if x.side == "sell"],
            })
            store.transition(run_id, "RECONCILED", payload)
            state = "RECONCILED"

        payload = store.payload(run_id)
        effective = {k: float(v) for k, v in payload["effective_weights"].items()}
        sells = _instruction_list(payload.get("sell_orders", []))
        if state == "RECONCILED":
            store.transition(run_id, "SELLING", payload)
            state = "SELLING"

        if state == "SELLING":
            _assert_only_expected_orders(trading, sells)
            if sells:
                sell_fills = submit_and_wait(trading, sells)
                payload["sell_fill_evidence"] = _fill_records(sell_fills, sells)
            _assert_no_open_orders(trading)
            store.transition(run_id, "SELLS_CONFIRMED", payload)
            state = "SELLS_CONFIRMED"

        if state == "SELLS_CONFIRMED":
            _assert_no_open_orders(trading)
            account_after_sells = trading.get_account()
            equity = float(account_after_sells.equity)
            cash = float(account_after_sells.cash)
            rebuilt = build_order_plan(
                run_id, target_hash, equity, effective, _positions(trading), _latest_quotes(settings)
            )
            buys = cap_buy_plan_to_cash([x for x in rebuilt if x.side == "buy"], cash)
            payload.update({"post_sell_equity": equity, "post_sell_cash": cash,
                            "buy_orders": [asdict(x) for x in buys]})
            store.transition(run_id, "BUYING", payload)
            state = "BUYING"

        payload = store.payload(run_id)
        buys = _instruction_list(payload.get("buy_orders", []))
        if state == "BUYING":
            _assert_only_expected_orders(trading, buys)
            if buys:
                buy_fills = submit_and_wait(trading, buys)
                payload["buy_fill_evidence"] = _fill_records(buy_fills, buys)
            _assert_no_open_orders(trading)
            store.transition(run_id, "VERIFYING", payload)
            state = "VERIFYING"

        if state == "VERIFYING":
            final = _reconcile_snapshot(trading, effective, settings)
            if not final["ok"]:
                raise ReconcileError(
                    f"Final reconciliation failed: max drift={final['max_abs_drift_bps']:.1f} bps, "
                    f"open orders={final['open_order_count']}"
                )
            account = _account_snapshot(trading, store, now, settings)
            account["strategy_allocation"] = settings.strategy_allocation
            payload.update({"final": final, "completed_at": now.isoformat()})
            if payload.get("risk_freeze_applied"):
                payload["attention_reason"] = "15% drawdown freeze applied; operator review required"
                store.transition(run_id, "ATTENTION", payload)
                terminal = "ATTENTION"
            else:
                store.transition(run_id, "COMPLETE", payload)
                terminal = "COMPLETE"
            heartbeat_ok = _safe_heartbeat(settings, store, run_id)
            if account["review_active"]:
                _safe_alert(settings, store, run_id, f"{STRATEGY_VERSION}: drawdown review",
                            f"Drawdown is {account['drawdown_pct']:.2f}% for run {run_id}.")
            published = _publish(
                settings, now=now, mode=mode, run_id=run_id, signal_date=signal_date_text,
                state=terminal, due=True, target_hash=target_hash, account=account,
                target={"desired": desired, "effective": effective}, actual=final["actual_weights"],
                reconciliation=final,
                risk={"drawdown_pct": account["drawdown_pct"],
                      "review_active": account["review_active"],
                      "halt_active": account["halt_active"],
                      "kill_switch_active": account["kill_switch_active"]},
                heartbeat_ok=heartbeat_ok,
                signals=[{
                    "symbol": row["symbol"],
                    "close": row["close"],
                    "sma_200": row["sma"],
                    "distance_pct": (row["close"] / row["sma"] - 1.0) * 100.0,
                    "risk_on": row["risk_on"],
                } for row in payload.get("signals", [])],
            )
            return {
                "status": terminal, "run_id": run_id, "signal_date": signal_date_text,
                "weights": effective, "equity": final["equity"],
                "max_abs_drift_bps": final["max_abs_drift_bps"],
                "dashboard_health": published["health"],
            }

        raise RuntimeError(f"Unhandled resumable state: {state}")
    except Exception as exc:
        store.record_error(run_id, exc, {"state": store.status(run_id), "signal_date": signal_date_text})
        _safe_alert(settings, store, run_id, f"{STRATEGY_VERSION}: execution failed", repr(exc))
        _publish_failure(settings, store, now, run_id, signal_date_text, exc)
        raise


def _emergency_failure_status(now: datetime, exc: Exception) -> None:
    """Best-effort red status for failures before settings/state initialization."""
    path = os.getenv("STATUS_PATH", "state/dashboard.json")
    paper = os.getenv("ALPACA_PAPER", "").lower()
    mode = "paper" if paper == "true" else "live" if paper == "false" else "unknown"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "mode": mode,
        "run": {
            "run_id": None, "signal_date": None, "rebalance_due": True,
            "state": "STARTUP_ERROR", "last_run_ok": False, "target_hash": None,
            "strategy_allocation": None,
        },
        "account": {}, "target": {"desired": {}, "effective": {}}, "actual": {},
        "reconciliation": {
            "ok": False, "open_order_count": None, "rejected_order_count": None,
            "max_abs_drift_bps": None, "drift_bps": {}, "error": repr(exc),
        },
        "risk": {
            "drawdown_pct": None, "review_active": None,
            "halt_active": None, "kill_switch_active": None,
        },
        "services": {
            "alert_webhook_configured": bool(os.getenv("ALERT_WEBHOOK_URL")),
            "heartbeat_configured": bool(os.getenv("HEARTBEAT_URL")),
            "heartbeat_last_attempt_ok": False,
        },
    }
    try:
        publish_status(
            path,
            payload,
            now=now.astimezone(timezone.utc),
            api_url=os.getenv("STATE_API_URL", "").strip().rstrip("/"),
            api_token=os.getenv("STATE_API_WRITE_TOKEN", "").strip(),
        )
    except Exception as status_exc:
        logger.exception("Unable to publish emergency failure status: %r", status_exc)
    try:
        send_alert(os.getenv("ALERT_WEBHOOK_URL", ""), f"{STRATEGY_VERSION}: startup failure", repr(exc))
    except Exception as alert_exc:
        logger.exception("Unable to send emergency startup-failure alert: %r", alert_exc)


def run(now: datetime | None = None) -> dict:
    moment = now or datetime.now(tz=NY)
    try:
        return _run_once(moment)
    except Exception as exc:
        _emergency_failure_status(moment, exc)
        raise


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
