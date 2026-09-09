import base64
import json
from datetime import datetime

import pytest

import trader.rebalance as rebalance
import trader.status_server as status_server
from trader.rebalance import NY


def test_startup_failure_overwrites_prior_green_status(monkeypatch, tmp_path):
    status = tmp_path / "dashboard.json"
    status.write_text('{"health":{"healthy":true}}', encoding="utf-8")
    monkeypatch.setenv("STATUS_PATH", str(status))
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "y")
    monkeypatch.setenv("ALPACA_SIGNAL_FEED", "sip_delayed")
    monkeypatch.setenv("ALPACA_QUOTE_FEED", "iex")
    monkeypatch.delenv("STRATEGY_ALLOCATION", raising=False)
    with pytest.raises(RuntimeError, match="explicitly configured"):
        rebalance.run(datetime(2026, 1, 2, 9, 40, tzinfo=NY))
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["run"]["state"] == "STARTUP_ERROR"
    assert payload["health"]["healthy"] is False


def test_status_server_requires_valid_basic_auth(monkeypatch):
    monkeypatch.setattr(status_server, "USERNAME", "operator")
    monkeypatch.setattr(status_server, "PASSWORD", "a-very-long-secret")
    valid = base64.b64encode(b"operator:a-very-long-secret").decode()
    assert status_server._authorized(f"Basic {valid}")
    assert not status_server._authorized(None)
    assert not status_server._authorized("Basic not-base64")
