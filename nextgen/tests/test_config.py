import pytest
from datetime import date, timedelta

from trader.config import Settings


def base(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_SIGNAL_FEED", "sip_delayed")
    monkeypatch.setenv("ALPACA_QUOTE_FEED", "iex")
    monkeypatch.setenv("STATE_API_URL", "https://state.example.test")
    monkeypatch.setenv("STATE_API_WRITE_TOKEN", "test-token")
    monkeypatch.setenv("STRATEGY_ALLOCATION", "1.0")


def test_live_mode_requires_both_activation_and_scale_interlocks(monkeypatch):
    base(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("STRATEGY_ALLOCATION", "0.25")
    with pytest.raises(RuntimeError, match="deployment-gate"):
        Settings.from_env()
    monkeypatch.setenv("CONFIRM_LIVE", "I_HAVE_COMPLETED_ALL_DEPLOYMENT_GATES")
    monkeypatch.setenv("EXPECTED_ALPACA_ACCOUNT_ID", "PA3TEST")
    monkeypatch.setenv("INITIAL_PEAK_EQUITY", "30000")
    monkeypatch.setenv("LIVE_AUTHORIZED_UNTIL", (date.today() + timedelta(days=30)).isoformat())
    assert Settings.from_env().strategy_allocation == 0.25
    monkeypatch.setenv("STRATEGY_ALLOCATION", "0.50")
    with pytest.raises(RuntimeError, match="scale approval"):
        Settings.from_env()
    monkeypatch.setenv("CONFIRM_SCALE", "I_HAVE_APPROVAL_TO_SCALE_ABOVE_25_PERCENT")
    assert Settings.from_env().strategy_allocation == 0.50


def test_non_free_feed_or_invalid_allocation_is_rejected(monkeypatch):
    base(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("ALPACA_SIGNAL_FEED", "iex")
    with pytest.raises(RuntimeError, match="sip_delayed"):
        Settings.from_env()
    monkeypatch.setenv("ALPACA_SIGNAL_FEED", "sip_delayed")
    monkeypatch.setenv("ALPACA_QUOTE_FEED", "sip")
    with pytest.raises(RuntimeError, match="iex"):
        Settings.from_env()
    monkeypatch.setenv("ALPACA_QUOTE_FEED", "iex")
    monkeypatch.setenv("STRATEGY_ALLOCATION", "1.1")
    with pytest.raises(RuntimeError, match="STRATEGY_ALLOCATION"):
        Settings.from_env()


def test_allocation_has_no_implicit_default(monkeypatch):
    base(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.delenv("STRATEGY_ALLOCATION", raising=False)
    with pytest.raises(RuntimeError, match="explicitly configured"):
        Settings.from_env()


def test_paper_flag_is_strict_not_typo_to_live(monkeypatch):
    base(monkeypatch)
    monkeypatch.setenv("ALPACA_PAPER", "flase")
    with pytest.raises(RuntimeError, match="exactly true or false"):
        Settings.from_env()


def test_live_mode_requires_remote_state(monkeypatch):
    base(monkeypatch)
    monkeypatch.delenv("STATE_API_URL", raising=False)
    monkeypatch.delenv("STATE_API_WRITE_TOKEN", raising=False)
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.setenv("STRATEGY_ALLOCATION", "0.25")
    monkeypatch.setenv("CONFIRM_LIVE", "I_HAVE_COMPLETED_ALL_DEPLOYMENT_GATES")
    monkeypatch.setenv("EXPECTED_ALPACA_ACCOUNT_ID", "PA3TEST")
    monkeypatch.setenv("INITIAL_PEAK_EQUITY", "30000")
    monkeypatch.setenv("LIVE_AUTHORIZED_UNTIL", (date.today() + timedelta(days=30)).isoformat())
    with pytest.raises(RuntimeError, match="durable remote state"):
        Settings.from_env()
    monkeypatch.setenv("STATE_API_URL", "https://state.example.test")
    monkeypatch.setenv("STATE_API_WRITE_TOKEN", "test-token")
    assert Settings.from_env().state_api_url == "https://state.example.test"
