from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta


STRATEGY_VERSION = "trend3-qqq20-v1"
EXECUTION_POLICY_VERSION = "t3q20-exec-v1"
SCHEMA_VERSION = 1

RISK_ASSETS = ("SPY", "QQQ", "GLD")
CASH_ASSET = "BIL"
ALLOWED_ASSETS = RISK_ASSETS + (CASH_ASSET,)
SMA_DAYS = 200
CORE_QQQ_WEIGHT = 0.20
DYNAMIC_TOTAL_WEIGHT = 0.80
DYNAMIC_SLEEVE_WEIGHT = DYNAMIC_TOTAL_WEIGHT / len(RISK_ASSETS)
MAX_TARGET_WEIGHTS = {
    "SPY": DYNAMIC_SLEEVE_WEIGHT,
    "QQQ": CORE_QQQ_WEIGHT + DYNAMIC_SLEEVE_WEIGHT,
    "GLD": DYNAMIC_SLEEVE_WEIGHT,
    # The canonical strategy produces at most 80% BIL because of the 20% QQQ
    # core.  Operations may defensively exceed that when the drawdown freeze
    # forbids restoring an underweight risk asset.
    "BIL": 1.0,
}
MAX_GROSS_EXPOSURE = 1.0
MIN_ORDER_DOLLARS = 5.0
LIMIT_BUFFER_BPS = 20.0
TARGET_DRIFT_TOLERANCE_BPS = 50.0
INTERMONTH_DRIFT_ALERT_BPS = 500.0
QUOTE_MAX_AGE_SECONDS = 60
MAX_CATCHUP_TRADING_DAYS = 3
REBALANCE_START_NY = "09:35"
REBALANCE_END_NY = "11:00"
REVIEW_DRAWDOWN_PCT = 0.10
FREEZE_RISK_INCREASE_DRAWDOWN_PCT = 0.15
CRITICAL_REAUDIT_DRAWDOWN_PCT = 0.25


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_secret: str
    paper: bool
    confirm_live: str
    # The strategy signal is calculated from completed, delayed SIP daily bars.
    # Alpaca Basic permits SIP historical queries when the explicit end time is
    # at least 15 minutes old.  Current execution quotes use the free IEX feed.
    signal_data_feed: str = "sip_delayed"
    quote_data_feed: str = "iex"
    state_db: str = "state/trend3_qqq20.sqlite3"
    status_path: str = "state/dashboard.json"
    state_api_url: str = ""
    state_api_write_token: str = ""
    alert_webhook_url: str = ""
    heartbeat_url: str = ""
    initial_peak_equity: float | None = None
    strategy_allocation: float = 1.0
    confirm_scale: str = ""
    cumulative_external_cash_flow: float = 0.0
    expected_account_id: str = ""
    live_authorized_until: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        paper_text = os.getenv("ALPACA_PAPER", "").strip().lower()
        if paper_text not in {"true", "false"}:
            raise RuntimeError("ALPACA_PAPER must be explicitly set to exactly true or false")
        paper = paper_text == "true"
        initial_peak = os.getenv("INITIAL_PEAK_EQUITY", "").strip()
        allocation_text = os.getenv("STRATEGY_ALLOCATION", "").strip()
        if not allocation_text:
            raise RuntimeError("STRATEGY_ALLOCATION must be explicitly configured; there is no live default")
        allocation = float(allocation_text)
        s = cls(
            api_key=os.environ["ALPACA_API_KEY"],
            api_secret=os.environ["ALPACA_SECRET_KEY"],
            paper=paper,
            confirm_live=os.getenv("CONFIRM_LIVE", ""),
            signal_data_feed=os.getenv("ALPACA_SIGNAL_FEED", "sip_delayed").lower(),
            quote_data_feed=os.getenv("ALPACA_QUOTE_FEED", "iex").lower(),
            state_db=os.getenv("STATE_DB", "state/trend3_qqq20.sqlite3"),
            status_path=os.getenv("STATUS_PATH", "state/dashboard.json"),
            state_api_url=os.getenv("STATE_API_URL", "").strip().rstrip("/"),
            state_api_write_token=os.getenv("STATE_API_WRITE_TOKEN", "").strip(),
            alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
            heartbeat_url=os.getenv("HEARTBEAT_URL", ""),
            initial_peak_equity=float(initial_peak) if initial_peak else None,
            strategy_allocation=allocation,
            confirm_scale=os.getenv("CONFIRM_SCALE", ""),
            cumulative_external_cash_flow=float(os.getenv("CUMULATIVE_EXTERNAL_CASH_FLOW", "0")),
            expected_account_id=os.getenv("EXPECTED_ALPACA_ACCOUNT_ID", "").strip(),
            live_authorized_until=os.getenv("LIVE_AUTHORIZED_UNTIL", "").strip(),
        )
        if s.signal_data_feed != "sip_delayed":
            raise RuntimeError(
                "ALPACA_SIGNAL_FEED must be sip_delayed; signals use only completed free historical SIP bars"
            )
        if s.quote_data_feed != "iex":
            raise RuntimeError(
                "ALPACA_QUOTE_FEED must be iex in the zero-subscription build; no paid SIP dependency"
            )
        if bool(s.state_api_url) != bool(s.state_api_write_token):
            raise RuntimeError("STATE_API_URL and STATE_API_WRITE_TOKEN must be configured together")
        if s.state_api_url and not s.state_api_url.startswith("https://"):
            raise RuntimeError("STATE_API_URL must use HTTPS")
        if not s.paper and s.confirm_live != "I_HAVE_COMPLETED_ALL_DEPLOYMENT_GATES":
            raise RuntimeError("Refusing live mode: deployment-gate confirmation is absent")
        if not s.paper and not s.expected_account_id:
            raise RuntimeError("Refusing live mode: EXPECTED_ALPACA_ACCOUNT_ID is absent")
        if not s.paper and s.initial_peak_equity is None:
            raise RuntimeError("Refusing live mode: INITIAL_PEAK_EQUITY is absent")
        if not s.paper and not s.state_api_url:
            raise RuntimeError("Refusing live mode without durable remote state: STATE_API_URL is absent")
        if not s.paper:
            try:
                expiry = date.fromisoformat(s.live_authorized_until)
            except ValueError as exc:
                raise RuntimeError("LIVE_AUTHORIZED_UNTIL must be an ISO date in live mode") from exc
            if date.today() > expiry:
                raise RuntimeError("Live authorization has expired")
            if expiry > date.today() + timedelta(days=31):
                raise RuntimeError("Live authorization may not be issued more than 31 days ahead")
        if s.initial_peak_equity is not None and s.initial_peak_equity <= 0:
            raise RuntimeError("INITIAL_PEAK_EQUITY must be positive when provided")
        if not (0 < s.strategy_allocation <= 1.0):
            raise RuntimeError("STRATEGY_ALLOCATION must be greater than 0 and no more than 1")
        if not s.paper and s.strategy_allocation > 0.25 and s.confirm_scale != "I_HAVE_APPROVAL_TO_SCALE_ABOVE_25_PERCENT":
            raise RuntimeError("Refusing live allocation above 25%: scale approval confirmation is absent")
        return s
