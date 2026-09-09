from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests


logger = logging.getLogger(__name__)


TRANSITIONS = {
    "CREATED": {"DATA_VALIDATED", "ATTENTION", "FAILED"},
    "DATA_VALIDATED": {"SIGNAL_LOCKED", "ATTENTION", "FAILED"},
    "SIGNAL_LOCKED": {"RECONCILED", "ATTENTION", "FAILED"},
    "RECONCILED": {"SELLING", "VERIFYING", "ATTENTION", "FAILED"},
    "SELLING": {"SELLS_CONFIRMED", "ATTENTION", "FAILED"},
    "SELLS_CONFIRMED": {"BUYING", "VERIFYING", "ATTENTION", "FAILED"},
    "BUYING": {"VERIFYING", "ATTENTION", "FAILED"},
    "VERIFYING": {"COMPLETE", "ATTENTION", "FAILED"},
    "COMPLETE": set(), "ATTENTION": set(), "FAILED": set(),
}


class RunStore:
    def __init__(self, path: str):
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        os.chmod(path, 0o600)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS runs(
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS equity_snapshots(
            observed_at TEXT PRIMARY KEY, equity REAL NOT NULL,
            adjusted_equity REAL
        )""")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(equity_snapshots)")}
        if "adjusted_equity" not in columns:
            self.conn.execute("ALTER TABLE equity_snapshots ADD COLUMN adjusted_equity REAL")

    def start(self, run_id: str, payload: dict) -> str:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            inserted = self.conn.execute(
                "INSERT OR IGNORE INTO runs(run_id,status,payload,updated_at) VALUES(?,?,?,?)",
                (run_id, "CREATED", json.dumps(payload, sort_keys=True), now),
            ).rowcount
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if inserted:
                self.conn.execute(
                    "INSERT INTO events(run_id,event,payload,created_at) VALUES(?,?,?,?)",
                    (run_id, "START", json.dumps(payload, sort_keys=True), now),
                )
            self.conn.execute("COMMIT")
            return row[0]
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def transition(self, run_id: str, new_status: str, payload: dict) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise RuntimeError("Unknown run")
            old = row[0]
            if new_status not in TRANSITIONS[old]:
                raise RuntimeError(f"Illegal transition {old} -> {new_status}")
            stamp = datetime.now(timezone.utc).isoformat()
            encoded = json.dumps(payload, sort_keys=True)
            self.conn.execute(
                "UPDATE runs SET status=?,payload=?,updated_at=? WHERE run_id=?",
                (new_status, encoded, stamp, run_id),
            )
            self.conn.execute(
                "INSERT INTO events(run_id,event,payload,created_at) VALUES(?,?,?,?)",
                (run_id, f"{old}->{new_status}", encoded, stamp),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def status(self, run_id: str) -> str | None:
        row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return row[0] if row else None

    def payload(self, run_id: str) -> dict:
        row = self.conn.execute("SELECT payload FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise RuntimeError("Unknown run")
        return json.loads(row[0])

    def record_error(self, run_id: str | None, exc: Exception, context: dict | None = None) -> None:
        payload = {"error": repr(exc), **(context or {})}
        self.conn.execute(
            "INSERT INTO events(run_id,event,payload,created_at) VALUES(?,?,?,?)",
            (run_id, "ERROR", json.dumps(payload, sort_keys=True), datetime.now(timezone.utc).isoformat()),
        )

    def record_equity(
        self,
        equity: float,
        observed_at: datetime | None = None,
        peak_floor: float | None = None,
        cumulative_external_cash_flow: float = 0.0,
    ) -> dict:
        if equity <= 0:
            raise ValueError("Equity must be positive")
        observed_at = observed_at or datetime.now(timezone.utc)
        stamp = observed_at.astimezone(timezone.utc).isoformat()
        adjusted_equity = float(equity) - float(cumulative_external_cash_flow)
        if adjusted_equity <= 0:
            raise ValueError("Cash-flow-adjusted equity must be positive")
        self.conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots(observed_at,equity,adjusted_equity) VALUES(?,?,?)",
            (stamp, float(equity), adjusted_equity),
        )
        observed_peak = float(
            self.conn.execute("SELECT MAX(COALESCE(adjusted_equity,equity)) FROM equity_snapshots").fetchone()[0]
        )
        peak = max(observed_peak, float(peak_floor or 0.0))
        drawdown = adjusted_equity / peak - 1.0
        return {
            "equity": float(equity),
            "adjusted_equity": adjusted_equity,
            "cumulative_external_cash_flow": float(cumulative_external_cash_flow),
            "peak_equity": peak,
            "drawdown_pct": drawdown * 100.0,
        }

    def last_complete_signal_date(self) -> str | None:
        rows = self.conn.execute("SELECT payload FROM runs WHERE status='COMPLETE' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not rows:
            return None
        return json.loads(rows[0]).get("signal_date")

    def latest_complete_payload(self) -> dict | None:
        row = self.conn.execute("SELECT payload FROM runs WHERE status='COMPLETE' ORDER BY updated_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def latest_terminal_payload(self) -> dict | None:
        row = self.conn.execute(
            "SELECT status,payload FROM runs WHERE status IN ('COMPLETE','ATTENTION') "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        payload = json.loads(row[1])
        payload["_terminal_status"] = row[0]
        return payload


class RemoteRunStore:
    """Cloudflare Worker/D1 implementation of the RunStore contract.

    GitHub-hosted runners are ephemeral, so production state cannot live in a
    local SQLite file.  Every mutating operation is authenticated and the
    Worker performs state-transition compare-and-swap checks inside D1.
    """

    def __init__(self, base_url: str, token: str, timeout_seconds: int = 20):
        if not base_url.startswith("https://"):
            raise RuntimeError("Remote state API must use HTTPS")
        if not token:
            raise RuntimeError("Remote state API token is missing")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "trend3-qqq20-zero-cost-cloud-v1",
            },
            json=payload,
            timeout=self.timeout_seconds,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            detail = response.text[:500]
            raise RuntimeError(f"State API {method} {path} failed: HTTP {response.status_code}: {detail}")
        data = response.json()
        if data.get("ok") is not True:
            raise RuntimeError(f"State API rejected {method} {path}: {data}")
        return data

    def start(self, run_id: str, payload: dict) -> str:
        return str(self._request("POST", "/v1/runs/start", {"run_id": run_id, "payload": payload})["status"])

    def transition(self, run_id: str, new_status: str, payload: dict) -> None:
        self._request(
            "POST",
            "/v1/runs/transition",
            {"run_id": run_id, "new_status": new_status, "payload": payload},
        )

    def status(self, run_id: str) -> str | None:
        data = self._request("GET", f"/v1/runs/status?run_id={quote(run_id, safe='')}")
        return data.get("status")

    def payload(self, run_id: str) -> dict:
        return dict(
            self._request("GET", f"/v1/runs/payload?run_id={quote(run_id, safe='')}")["payload"]
        )

    def record_error(self, run_id: str | None, exc: Exception, context: dict | None = None) -> None:
        try:
            self._request(
                "POST",
                "/v1/events/error",
                {"run_id": run_id, "error": repr(exc), "context": context or {}},
            )
        except Exception:
            # Logging must not replace the original execution exception.
            logger.exception("Unable to persist remote error event")

    def record_equity(
        self,
        equity: float,
        observed_at: datetime | None = None,
        peak_floor: float | None = None,
        cumulative_external_cash_flow: float = 0.0,
    ) -> dict:
        return dict(
            self._request(
                "POST",
                "/v1/equity",
                {
                    "equity": equity,
                    "observed_at": (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
                    "peak_floor": peak_floor,
                    "cumulative_external_cash_flow": cumulative_external_cash_flow,
                },
            )["risk"]
        )

    def last_complete_signal_date(self) -> str | None:
        payload = self.latest_complete_payload()
        return payload.get("signal_date") if payload else None

    def latest_complete_payload(self) -> dict | None:
        return self._request("GET", "/v1/runs/latest?status=COMPLETE").get("payload")

    def latest_terminal_payload(self) -> dict | None:
        return self._request("GET", "/v1/runs/latest?status=terminal").get("payload")


def create_run_store(settings):
    """Select durable cloud state when configured, SQLite for local tests only."""
    if settings.state_api_url:
        return RemoteRunStore(settings.state_api_url, settings.state_api_write_token)
    return RunStore(settings.state_db)
