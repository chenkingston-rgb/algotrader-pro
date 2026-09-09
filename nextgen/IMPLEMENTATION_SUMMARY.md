# Implementation summary — final recommended model

## Decision

The repaired implementation is **Trend-3 + QQQ20 (`trend3-qqq20-v1`)**. It replaces the current multi-strategy engine; it is not an additional signal layered onto it.

### Exact target logic

```text
permanent QQQ core = 20.0000%
dynamic sleeve size = 80% / 3 = 26.6667%

SPY target = 26.6667% when SPY adjusted close >= SPY 200-session SMA, else 0%
QQQ target = 20.0000% + 26.6667% when QQQ adjusted close >= QQQ 200-session SMA
GLD target = 26.6667% when GLD adjusted close >= GLD 200-session SMA, else 0%
BIL target = residual to 100%
```

Signal is formed from the last completed regular session of each month and executed during the next regular session. No other indicator or discretion is permitted.

## Why this model, not a more aggressive backtest winner

The research tested 1,209 configurations and found substantial model-selection risk (estimated PBO about 65%). Higher QQQ core weights produced higher historical CAGR but progressively worse drawdown and lower risk-adjusted efficiency. The 20% core was selected as the growth/risk compromise:

| QQQ permanent core | CAGR | Sharpe | Max drawdown |
|---:|---:|---:|---:|
| 0% (pure Trend-3) | 9.93% | 0.93 | -15.98% |
| 10% | 10.65% | 0.95 | -17.21% |
| **20% selected** | **11.34%** | **0.95** | **-19.88%** |
| 25% | 11.68% | 0.95 | -22.27% |
| 33% | 12.23% | 0.94 | -26.15% |
| 50% | 13.28% | 0.90 | -33.66% |

The selected model historically beat pure Trend-3 by about 1.41 percentage points annually while keeping the tested drawdown below 20%. It did not reliably beat QQQ buy-and-hold; its purpose is materially better downside control and diversification, not maximum bull-market return.

## Historical and planning projection from $29,469

| Horizon | 4% low-return case | 7% planning | 9% planning | 11.34% historical extrapolation |
|---:|---:|---:|---:|---:|
| 1 year | $30,648 | $31,532 | $32,121 | $32,811 |
| 5 years | $35,854 | $41,332 | $45,342 | $50,422 |
| 10 years | $43,621 | $57,970 | $69,764 | $86,274 |

The 11.34% column assumes the past repeats exactly. It is not the operating budget and not a guarantee. A losing year or multi-year lag versus SPY/QQQ remains feasible; the tested maximum drawdown was -19.88%, and a future 30% loss must be financially tolerable.

**Pilot-size warning:** at `STRATEGY_ALLOCATION=0.25`, the historical account-level CAGR was about **3.86%**, because 75% of the account remains in BIL. That is intentional temporary risk control, not a claim that the full model earns only 3.86%. The 11.34% historical figure requires full allocation after the scale gates.

## What was repaired in code

- Replaced the old signal stack with the exact frozen allocation rule.
- Fixed the sell-sizing path so a marketable sell limit can never request more shares than held.
- Added deterministic order identity containing the durable portfolio target hash and 404-only new submission; network uncertainty cannot authorize a duplicate.
- Added transactional restart state and persisted order plans.
- Added sell-first/cash-rebuild execution with a reserve; no margin.
- Added dual-source adjusted-data validation using free delayed historical SIP and strict IEX-only protected execution quotes.
- Added hash-addressed persistence of both providers' exact 200-session signal inputs; restart uses the frozen snapshot rather than refetching mutable history.
- Added official-calendar, DST and bounded catch-up logic.
- Added immediate red failure telemetry and daily health refresh.
- Added drawdown high-water-mark import for safe migration.
- Added a fail-closed private dashboard, alert and dead-man heartbeat contract.
- Added cash-flow-adjusted high-water accounting so deposits and withdrawals do not falsify drawdown controls.
- Added pinned dependency locks, timezone-aware serialized GitHub execution, Cloudflare D1 state/dead-man service and Vercel read-only console.
- Added a runnable slow backtest reproducer, source price snapshot, monthly target ledger and one-/two-session delay study.

## What implementation does not mean

The zero-subscription adaptation is complete as a reference package, but it has not been connected to real GitHub, Cloudflare, Vercel or Alpaca credentials. It must not be described as live-deployed until the D1/API smoke checks, three paper month ends and cutover gates in `DEPLOYMENT_RUNBOOK.md` are completed.






