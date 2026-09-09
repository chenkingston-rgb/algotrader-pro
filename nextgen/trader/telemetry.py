from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import (
    EXECUTION_POLICY_VERSION,
    INTERMONTH_DRIFT_ALERT_BPS,
    SCHEMA_VERSION,
    STRATEGY_VERSION,
    TARGET_DRIFT_TOLERANCE_BPS,
)


REQUIRED_TOP_LEVEL = {
    "schema_version", "strategy_version", "execution_policy_version", "generated_at", "mode", "run",
    "account", "target", "actual", "reconciliation", "risk", "services",
}


def evaluate_health(payload: dict, now: datetime | None = None) -> tuple[bool, list[str]]:
    """Fail-closed health evaluation used by both the trader and dashboard tests."""
    problems: list[str] = []
    missing = REQUIRED_TOP_LEVEL - set(payload)
    if missing:
        return False, [f"missing top-level fields: {sorted(missing)}"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema version mismatch")
    if payload.get("strategy_version") != STRATEGY_VERSION:
        problems.append("strategy version mismatch")
    if payload.get("execution_policy_version") != EXECUTION_POLICY_VERSION:
        problems.append("execution policy version mismatch")
    try:
        generated = datetime.fromisoformat(str(payload["generated_at"]).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
        age_h = ((now or datetime.now(timezone.utc)) - generated.astimezone(timezone.utc)).total_seconds() / 3600
        if age_h < -0.1 or age_h > 26:
            problems.append(f"stale or future telemetry: age={age_h:.1f}h")
    except Exception:
        problems.append("invalid generated_at")

    run = payload.get("run") or {}
    due = run.get("rebalance_due") is True
    if due and run.get("state") != "COMPLETE":
        problems.append("rebalance due but incomplete")
    if run.get("last_run_ok") is not True:
        problems.append("last run not explicitly successful")
    if not run.get("target_hash"):
        problems.append("missing target hash")

    reconciliation = payload.get("reconciliation") or {}
    if reconciliation.get("ok") is not True:
        problems.append("broker reconciliation not explicitly successful")
    if reconciliation.get("open_order_count") != 0:
        problems.append("open orders remain")
    if reconciliation.get("rejected_order_count") != 0:
        problems.append("rejected orders present")
    drift = reconciliation.get("max_abs_drift_bps")
    allowed_drift = TARGET_DRIFT_TOLERANCE_BPS if due else INTERMONTH_DRIFT_ALERT_BPS
    if drift is None or float(drift) > allowed_drift:
        problems.append("target drift missing or above tolerance")

    risk = payload.get("risk") or {}
    if risk.get("halt_active") is not False or risk.get("kill_switch_active") is not False:
        problems.append("risk halt or kill switch active/unknown")

    services = payload.get("services") or {}
    if services.get("alert_webhook_configured") is not True:
        problems.append("alert webhook is not configured")
    if services.get("heartbeat_configured") is not True:
        problems.append("heartbeat is not configured")
    if services.get("heartbeat_last_attempt_ok") is not True:
        problems.append("heartbeat last attempt failed or unknown")
    return not problems, problems


def write_status_atomic(path: str, payload: dict, now: datetime | None = None) -> dict:
    payload = dict(payload)
    healthy, problems = evaluate_health(payload, now=now)
    payload["health"] = {"healthy": healthy, "problems": problems}
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
    return payload


def publish_status(
    path: str,
    payload: dict,
    *,
    now: datetime | None = None,
    api_url: str = "",
    api_token: str = "",
) -> dict:
    """Evaluate once, retain a local copy, then durably publish to D1.

    In GitHub Actions the file is diagnostic only; the authenticated remote copy
    is authoritative.  A remote publish failure is fatal so the workflow cannot
    report green while the operator dashboard remains stale.
    """
    evaluated = write_status_atomic(path, payload, now=now)
    if not api_url:
        return evaluated
    if not api_token:
        raise RuntimeError("STATE_API_WRITE_TOKEN is missing")
    response = requests.put(
        f"{api_url.rstrip('/')}/v1/status",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
            "User-Agent": "trend3-qqq20-zero-cost-cloud-v1",
        },
        json=evaluated,
        timeout=20,
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Status API publish failed: HTTP {response.status_code}: {response.text[:500]}")
    result = response.json()
    if result.get("ok") is not True:
        raise RuntimeError(f"Status API rejected telemetry: {result}")
    return evaluated
