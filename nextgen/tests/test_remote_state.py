from datetime import datetime, timezone

import pytest

from trader.state import RemoteRunStore, create_run_store


class Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload


def test_remote_store_maps_contract_to_authenticated_api(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/v1/runs/start"):
            return Response({"ok": True, "status": "CREATED"})
        if "/v1/runs/status" in url:
            return Response({"ok": True, "status": "SIGNAL_LOCKED"})
        if "/v1/runs/payload" in url:
            return Response({"ok": True, "payload": {"signal_date": "2026-08-31"}})
        if url.endswith("/v1/equity"):
            return Response({"ok": True, "risk": {"drawdown_pct": -1.0}})
        if "/v1/runs/latest" in url:
            return Response({"ok": True, "payload": None})
        return Response({"ok": True})

    monkeypatch.setattr("trader.state.requests.request", request)
    store = RemoteRunStore("https://state.example.test", "secret")
    assert store.start("run/one", {"x": 1}) == "CREATED"
    store.transition("run/one", "DATA_VALIDATED", {"x": 2})
    assert store.status("run/one") == "SIGNAL_LOCKED"
    assert store.payload("run/one")["signal_date"] == "2026-08-31"
    assert store.record_equity(10_000, datetime.now(timezone.utc))["drawdown_pct"] == -1.0
    assert all(c[2]["headers"]["Authorization"] == "Bearer secret" for c in calls)
    assert "%2F" in calls[2][1]


def test_remote_store_rejects_redirect_or_api_error(monkeypatch):
    monkeypatch.setattr(
        "trader.state.requests.request",
        lambda *a, **k: Response({"ok": False, "error": "denied"}, status=409),
    )
    with pytest.raises(RuntimeError, match="HTTP 409"):
        RemoteRunStore("https://state.example.test", "secret").start("r1", {})


def test_factory_keeps_sqlite_for_local_test_and_uses_remote_for_cloud(tmp_path):
    class Local:
        state_api_url = ""
        state_api_write_token = ""
        state_db = str(tmp_path / "state.sqlite3")

    assert create_run_store(Local()).__class__.__name__ == "RunStore"

    class Cloud(Local):
        state_api_url = "https://state.example.test"
        state_api_write_token = "secret"

    assert isinstance(create_run_store(Cloud()), RemoteRunStore)
