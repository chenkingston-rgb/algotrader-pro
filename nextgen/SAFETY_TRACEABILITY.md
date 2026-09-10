# Safety and requirements traceability

| ID | Requirement | Implementation | Automated evidence |
|---|---|---|---|
| S01 | Frozen 20% QQQ core + 3 x 26.6667% trend sleeves | `trader/config.py`, `trader/strategy.py::decide` | all 8 state combinations in `tests/test_strategy.py` |
| S02 | Exact 200 exchange sessions, no future data | `validate_closes`, `assert_exchange_sessions`, `decide` | no-lookahead, missing/exact-date/calendar tests |
| S03 | Corporate-action-adjusted delayed SIP signal data on Alpaca Basic; current IEX quotes only for order limits | `data.py::fetch_adjusted_closes`, `rebalance.py::_latest_quotes` | free-feed configuration tests, age guard, provider tests; three paper month ends |
| S04 | Independent data agreement and immutable signal record | `assert_provider_agreement`, `make_signal_snapshot`, `restore_signal_snapshot` | close/state/session/universe/hash-round-trip tests |
| S05 | NYSE calendar, DST and three-session catch-up | `rebalance.py::execution_context` | calendar/DST/catch-up tests |
| S06 | Only SPY/QQQ/GLD/BIL, long only; market open and assets tradable/fractionable | `broker.py::build_order_plan`, `rebalance.py::_assert_market_and_assets_tradeable` | external/short-position and asset-preflight tests |
| S07 | Gross <=100%; no margin; pilot scaling | frozen target bounds, `_deployment_target` + `cap_buy_plan_to_cash` | invalid target, pilot-allocation and cash-cap tests |
| S08 | Never oversell a long position | midpoint sell sizing capped to held quantity | full-liquidation test |
| S09 | Stale/crossed quote rejection | `broker.py::validate_quote` | stale quote test |
| S10 | Idempotent broker submission | target-hash deterministic ID + get-by-client-ID-before-submit | existing-order, target-hash and 404-only-submit tests |
| S11 | Sells before buys | sorted plan + state stages | sell-ordering test; paper restart gate |
| S12 | Durable restart across ephemeral runners | transactional local `RunStore` for tests; authenticated `RemoteRunStore` + Cloudflare D1 for production | local atomic tests, remote API contract tests; paper restart gate |
| S13 | No silent automatic reprice | timeout cancel + alert + durable error | code review; paper timeout gate |
| S14 | Final drift <=50 bps | `_reconcile_snapshot` | telemetry/drift test; paper fill gate |
| S15 | Cash-flow-adjusted drawdown governance, no panic liquidation | `_account_snapshot`, `_effective_frozen_target`, `record_equity` | peak-floor, deposit-adjustment and no-risk-increase tests |
| S16 | Live/paper interlock | `Settings.from_env` | manual deployment gate |
| S17 | Fail-closed health/status | `telemetry.py`, `monitor/index.html` | health/stale/orders/risk tests |
| S18 | Dead-man, alerts and an independent scheduling path | GitHub schedule, Cloudflare cron enable/dispatch, heartbeat, native D1 alert journal, optional Discord/Slack alerts, status schema | startup-red-status and alert tests; native delivery passed; real dispatch gate open |
| S19 | Reproducible dependencies | pinned requirement files + CI | install and CI gate |
| S20 | Legacy isolation | standalone package/service | cutover checklist |
| S21 | Private authenticated monitoring | `cloudflare/src/index.js`, Basic authorization, direct server-side D1 read, independent browser checks | JavaScript syntax check; remote unauthorized/authorized dashboard tests |
| S22 | Persistent state recovery | D1 state/event/equity schema and seven-day point-in-time recovery on the current Free plan | schema review, remote contract tests, restore drill |
| S23 | Broker-secret containment | Alpaca credentials only in GitHub Actions; Cloudflare cannot trade; browser dashboard has no service token | secret inventory and cutover gate |
| S24 | Free-tier fail-closed behavior | live mode requires remote state; no automatic paid-feed or wider-limit fallback | configuration tests and paper cost/quote gate |

## Deliberately not automated

- No auto-liquidation based only on account drawdown.
- No automatic retry after rejected/canceled/expired orders.
- No automatic change from delayed SIP signals or IEX execution quotes to another feed.
- No automatic substitution of another ETF or model parameter.
- No live activation and no scaling based solely on a backtest.





