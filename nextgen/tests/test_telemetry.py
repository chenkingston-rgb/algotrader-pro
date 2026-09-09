from datetime import datetime, timedelta, timezone

from trader.config import EXECUTION_POLICY_VERSION, SCHEMA_VERSION, STRATEGY_VERSION
from trader.telemetry import evaluate_health, publish_status, write_status_atomic


def valid_payload(now):
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "execution_policy_version": EXECUTION_POLICY_VERSION,
        "generated_at": now.isoformat(),
        "mode": "paper",
        "run": {"rebalance_due": True, "state": "COMPLETE", "last_run_ok": True, "target_hash": "abc"},
        "account": {"equity": 30000},
        "target": {"desired": {}},
        "actual": {},
        "reconciliation": {"ok": True, "open_order_count": 0, "rejected_order_count": 0, "max_abs_drift_bps": 10},
        "risk": {"halt_active": False, "kill_switch_active": False},
        "services": {"alert_webhook_configured": True, "heartbeat_configured": True, "heartbeat_last_attempt_ok": True},
    }


def test_valid_health_payload_is_healthy(tmp_path):
    now = datetime.now(timezone.utc)
    payload = valid_payload(now)
    assert evaluate_health(payload, now) == (True, [])
    written = write_status_atomic(str(tmp_path / "dashboard.json"), payload)
    assert written["health"]["healthy"] is True


def test_health_fails_closed_for_staleness_drift_orders_and_missing_service():
    now = datetime.now(timezone.utc)
    payload = valid_payload(now - timedelta(hours=30))
    payload["reconciliation"].update({"open_order_count": 1, "max_abs_drift_bps": 51})
    payload["services"]["heartbeat_configured"] = False
    healthy, problems = evaluate_health(payload, now)
    assert not healthy
    joined = " | ".join(problems)
    assert "stale" in joined and "open orders" in joined and "drift" in joined and "heartbeat" in joined


def test_attention_or_unknown_risk_never_renders_green():
    now = datetime.now(timezone.utc)
    payload = valid_payload(now)
    payload["run"].update({"state": "ATTENTION", "last_run_ok": False})
    payload["risk"]["halt_active"] = True
    healthy, _ = evaluate_health(payload, now)
    assert not healthy


def test_cloud_status_publish_is_authenticated(monkeypatch, tmp_path):
    sent = {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"ok": True}

    def put(url, **kwargs):
        sent.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("trader.telemetry.requests.put", put)
    result = publish_status(
        str(tmp_path / "dashboard.json"),
        valid_payload(datetime.now(timezone.utc)),
        api_url="https://state.example.test",
        api_token="secret",
    )
    assert result["health"]["healthy"] is True
    assert sent["headers"]["Authorization"] == "Bearer secret"
    assert sent["url"].endswith("/v1/status")
