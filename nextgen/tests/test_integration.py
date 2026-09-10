from datetime import date, datetime
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import alpaca.trading.client
import trader.rebalance as rebalance
from trader.rebalance import NY


class FakeTrading:
    def __init__(self):
        self.cash = 10_000.0
        self.equity = 10_000.0
        self.positions = {}

    def get_calendar(self, request):
        days = [x.date() for x in closes().index]
        days += [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]
        return [SimpleNamespace(date=x) for x in days]

    def get_account(self):
        return SimpleNamespace(
            id="paper-account", status="ACTIVE", equity=str(self.equity), cash=str(self.cash),
            buying_power=str(self.cash), account_blocked=False, trading_blocked=False,
            trade_suspended_by_user=False, last_equity="10000",
            long_market_value=str(self.equity - self.cash), portfolio_value=str(self.equity),
        )

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q), market_value=str(q * 100.0),
                                current_price="100", avg_entry_price="99",
                                unrealized_pl=str(q), unrealized_plpc="0.010101",
                                change_today="0.002")
                for s, q in self.positions.items() if q > 0]

    def get_orders(self, filter):
        return []

    def get_clock(self):
        return SimpleNamespace(is_open=True)

    def get_asset(self, symbol):
        return SimpleNamespace(symbol=symbol, tradable=True, fractionable=True)


def closes():
    idx = pd.bdate_range(end="2025-12-31", periods=205)
    base = [100.0] * 204
    return pd.DataFrame({"SPY": base+[101], "QQQ": base+[101], "GLD": base+[101], "BIL": base+[100]}, index=idx)


def configure(monkeypatch, tmp_path, broker):
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "paper-secret")
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("ALPACA_SIGNAL_FEED", "sip_delayed")
    monkeypatch.setenv("ALPACA_QUOTE_FEED", "iex")
    monkeypatch.setenv("STRATEGY_ALLOCATION", "1.0")
    monkeypatch.setenv("STATE_DB", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("STATUS_PATH", str(tmp_path / "dashboard.json"))
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://alerts.invalid/test")
    monkeypatch.setenv("HEARTBEAT_URL", "https://heartbeat.invalid/test")
    monkeypatch.setattr(alpaca.trading.client, "TradingClient", lambda *a, **k: broker)
    monkeypatch.setattr(rebalance, "fetch_adjusted_closes", lambda settings, end: closes())
    monkeypatch.setattr(rebalance, "fetch_yahoo_adjusted_closes", lambda end: closes())
    monkeypatch.setattr(rebalance, "_latest_quotes", lambda settings: {
        s: rebalance.Quote(99.9, 100.1, datetime.now(tz=NY))
        for s in ("SPY", "QQQ", "GLD", "BIL")
    })
    monkeypatch.setattr(rebalance, "ping_heartbeat", lambda url: None)
    monkeypatch.setattr(rebalance, "send_alert", lambda *a: None)


def fill(broker, instructions):
    for item in instructions:
        signed = item.qty if item.side == "buy" else -item.qty
        broker.positions[item.symbol] = broker.positions.get(item.symbol, 0.0) + signed
        broker.cash -= signed * 100.0


def test_full_paper_orchestration_and_repeat_is_idempotent(monkeypatch, tmp_path):
    broker = FakeTrading()
    configure(monkeypatch, tmp_path, broker)
    monkeypatch.setattr(rebalance, "submit_and_wait", lambda client, orders: fill(broker, orders) or [])
    now = datetime(2026, 1, 2, 9, 40, tzinfo=NY)
    result = rebalance.run(now)
    assert result["status"] == "COMPLETE"
    assert result["dashboard_health"]["healthy"] is True
    dashboard = json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))
    assert {row["symbol"] for row in dashboard["signals"]} == {"SPY", "QQQ", "GLD"}
    assert dashboard["account"]["positions"]
    assert dashboard["account"]["day_pl"] == 0
    assert "total_pl" in dashboard["account"]
    before = dict(broker.positions)
    again = rebalance.run(datetime(2026, 1, 5, 9, 40, tzinfo=NY))
    assert again["status"] == "ALREADY_COMPLETE"
    assert broker.positions == before


def test_restart_from_buying_resumes_stored_phase(monkeypatch, tmp_path):
    broker = FakeTrading()
    configure(monkeypatch, tmp_path, broker)
    calls = {"n": 0}

    def crash_after_acceptance(client, orders):
        calls["n"] += 1
        if calls["n"] == 1:
            fill(broker, orders)
            raise TimeoutError("simulated process loss after broker acceptance")
        return []

    monkeypatch.setattr(rebalance, "submit_and_wait", crash_after_acceptance)
    now = datetime(2026, 1, 2, 9, 40, tzinfo=NY)
    with pytest.raises(TimeoutError):
        rebalance.run(now)
    result = rebalance.run(now)
    assert result["status"] == "COMPLETE"
    assert calls["n"] == 2
