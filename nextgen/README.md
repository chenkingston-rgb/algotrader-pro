# Trend-3 + QQQ20 automated trader — zero-subscription cloud build

This package is the repaired, standalone reference implementation of the recommended growth model. It is intentionally isolated from the legacy 3,341-line intraday engine.

## Frozen investment model

- A permanent **20% QQQ growth core** remains invested.
- The remaining 80% is divided into three independent 26.6667% sleeves: SPY, QQQ and GLD.
- At each completed month end, each sleeve is invested only when that ETF's split/dividend-adjusted close is at or above its own 200-session simple moving average.
- A failed sleeve moves to BIL. QQQ therefore ranges from 20.00% to 46.67%.
- Execute during the next regular trading session; a bounded three-session catch-up is allowed.
- No ranking, leverage, margin, shorting, options, stops, RSI, MACD, ADX, VIX, LLM signal or machine learning.

Canonical 3 January 2007–31 July 2026 result at 10 bps per traded side, including BIL turnover: **11.34% CAGR, 0.95 Sharpe, 0.83 excess Sharpe over BIL, -19.88% maximum drawdown and 2.63x annual traded notional**. At 35 bps per side, CAGR was 10.61%; at 50 bps it was 10.17%. This is historical evidence, not a promise of profit.

## Repairs implemented

1. Free delayed historical Alpaca SIP adjusted daily bars plus an independent Yahoo adjusted-price/trend-state check.
2. Exact month-end signal and New York market-calendar scheduling with holiday, DST and three-session catch-up handling.
3. Deterministic Alpaca client order IDs; retries query the broker before submission.
4. Durable Cloudflare D1 state machine that resumes each sell/buy phase without blind resubmission; SQLite remains for local tests only.
5. Sell-first execution, post-sale cash reconciliation and cash-capped buys; margin is impossible by construction.
6. Marketable DAY limit orders capped at 20 bps; uncertain orders are canceled and escalated rather than automatically repriced.
7. Rejects external/short positions, stale/crossed quotes, unknown open orders, wrong data feed and invalid targets.
8. Final position drift must be within 50 bps per asset.
9. Drawdown governance at 10% review, 15% risk-increase freeze and 25% independent re-audit; no mechanical panic liquidation.
10. Strict health telemetry, alerts and dead-man heartbeat. Unknown or stale status is red, never green.

## Verification

```text
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock
.venv/bin/ruff check trader tests selftest.py
.venv/bin/pytest
.venv/bin/python selftest.py
```

The delivered build passes 67 automated tests plus the deployment smoke test, security scans and frozen backtest reproduction. See `TEST_REPORT.md`.

## Required environment

Configure `.env.example` values as GitHub Actions secrets or variables. Never commit real values.

```text
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_PAPER=true
ALPACA_SIGNAL_FEED=sip_delayed
ALPACA_QUOTE_FEED=iex
EXPECTED_ALPACA_ACCOUNT_ID=...
STATE_API_URL=https://YOUR-WORKER.workers.dev
STATE_API_WRITE_TOKEN=...
STATE_DB=/tmp/trend3_qqq20.sqlite3
STATUS_PATH=/tmp/dashboard.json
INITIAL_PEAK_EQUITY=...
CUMULATIVE_EXTERNAL_CASH_FLOW=0
STRATEGY_ALLOCATION=1.0
ALERT_WEBHOOK_URL=...
HEARTBEAT_URL=...
CONFIRM_LIVE=
CONFIRM_SCALE=
LIVE_AUTHORIZED_UNTIL=
```

Live mode requires `ALPACA_PAPER=false` and the exact phrase `I_HAVE_COMPLETED_ALL_DEPLOYMENT_GATES`. `STRATEGY_ALLOCATION` has no implicit default. Any live value above 25% also requires `CONFIRM_SCALE=I_HAVE_APPROVAL_TO_SCALE_ABOVE_25_PERCENT`. The code checks are only interlocks; they are not permission to skip paper validation. Update `CUMULATIVE_EXTERNAL_CASH_FLOW` after every deposit (+) or withdrawal (-), or drawdown governance will be wrong.

Live mode also requires the exact Alpaca account ID in `EXPECTED_ALPACA_ACCOUNT_ID`, the imported high-water mark in `INITIAL_PEAK_EQUITY`, a durable HTTPS `STATE_API_URL`, and a `LIVE_AUTHORIZED_UNTIL` date no more than 31 days ahead. The operator must renew that date after each documented review. Alert and heartbeat endpoints must be HTTPS URLs without embedded credentials.

## Production deployment

No Base44 subscription, Vercel project or VPS is used. GitHub Actions is the only component with Alpaca credentials and executes the Python worker. Cloudflare Workers/D1 provides durable state, an independent missed-run dispatch and a password-protected read-only dashboard. Both services run within their published free tiers at the expected workload. The Python engine itself enforces the New York clock and official Alpaca calendar.

Do not merge this into the legacy engine. Migration order:

1. Revoke or disable every legacy trading workflow and confirm zero broker open orders.
2. Close or manually transfer all non-SPY/QQQ/GLD/BIL positions. The engine deliberately refuses a mixed account.
3. Set `INITIAL_PEAK_EQUITY` to the greater of current equity and the agreed starting high-water mark.
4. Deploy in paper mode and complete three clean month-end cycles, including a forced restart drill.
5. Shadow the exact target file and broker fills against the frozen backtest.
6. Start live at 10%–25% using `STRATEGY_ALLOCATION`; the engine holds the undeployed balance in BIL. Do not run both engines.
7. Scale only according to `DEPLOYMENT_RUNBOOK.md`.

## Dashboard

Open the Worker at `/dashboard`. The page independently rechecks the canonical status contract, shows the ten latest native alerts, and sends operator Basic credentials only to `/dashboard/status`. Cloudflare validates them server-side and reads D1 directly. Browser code never receives Alpaca, GitHub, D1 write or D1 read credentials. The `vercel/` directory is retained only as an optional fallback.

Read `BUILD_SPEC.md`, the authoritative `DEPLOYMENT_RUNBOOK.md` and `SAFETY_TRACEABILITY.md` before any deployment. `LEGACY_VPS_RUNBOOK.md` is retained only for provenance and is not the selected architecture.






