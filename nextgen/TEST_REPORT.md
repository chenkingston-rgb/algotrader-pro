# Verification report

**Build:** `trend3-qqq20-v1-zero-cost-cloud-rc1`  
**Verification date:** 2026-09-10  
**Status:** code-complete reference implementation; paper/shadow deployment candidate; not activated live

## Automated results

- Python compile check: PASS
- Ruff static analysis: PASS
- Pytest: **70 passed**
- Dependency-light deployment smoke test: PASS
- Frozen slow backtest reproduction: PASS (11.338639% CAGR; 0.954762 Sharpe; -19.877475% max drawdown)
- One-/two-session execution delay reproduction: PASS (11.40% / 11.25% CAGR)
- Bandit Python security scan: PASS (no reported findings)
- `pip-audit` against the production lock file: PASS (no reported vulnerability)
- Cloudflare Worker JavaScript syntax check: PASS
- Cloudflare embedded dashboard JavaScript syntax check: PASS
- Cloudflare D1 schema local execution: PASS (9 statements)
- Cloudflare local API smoke: PASS (health, authenticated start/transition/read/equity/status/heartbeat)
- Cloudflare negative API checks: PASS (unauthorized 401; illegal transition 409)
- Cloudflare authenticated dashboard/status smoke: PASS (public shell 200; unauthorized status 401; authorized status reaches D1)
- Remote Cloudflare deployment smoke (2026-09-10): PASS; dashboard 200, unauthorized status 401, authenticated D1 read path reached, no service-secret names in delivered HTML

## Behaviors covered

- Eight possible trend-state/target combinations, including the permanent QQQ core.
- Causality/future-data mutation and missing-session failure.
- Provider price/state disagreement and incomplete universe.
- Per-asset target caps, cash cap, unauthorized assets, short positions and stale quotes.
- Sell-before-buy ordering and no overselling on liquidation.
- Deterministic order IDs, accepted-order recovery and network-uncertainty no-resubmit behavior.
- Durable/atomic local state transitions, authenticated remote-state client contract and migrated peak-equity drawdown calculation.
- NYSE calendar catch-up, holiday-aware sessions, DST conversion and expired catch-up.
- Drawdown freeze cannot increase any risk sleeve.
- Fail-closed telemetry for stale status, open orders, drift, risk halt and missing heartbeat.
- Strict paper/live parsing, exact account identity, expiring live authorization and scale interlocks.
- HTTPS-only alert/heartbeat endpoints with redirect refusal and no embedded credentials.
- WAL-consistent state backup/restore and startup-failure red-status publication.

## Tests that require real paper infrastructure

The following are deployment gates, not simulated pass claims:

1. Alpaca Basic delayed historical SIP access and adjusted-bar agreement against Yahoo.
2. IEX-quote-based fractional protected-limit fills and measured slippage at 09:35–11:00 New York time.
3. Cloudflare D1 schema/API deployment, transition concurrency and point-in-time restore drill.
4. Kill/restart drill after broker acceptance but before remote state transition.
5. Three actual paper month-end rebalances and one missed-timer catch-up.
6. Discord/Slack alert delivery and Cloudflare dead-man GitHub dispatch.
7. Cloudflare dashboard browser audit confirming that no broker/write/read service credential is embedded.
8. Independent reproduction of the historical backtest from broker-vended data.

The first remote Cloudflare API and Alpaca paper authentication smoke checks are complete. The remaining real-infrastructure gates above remain open until their scheduled or supervised test windows.






