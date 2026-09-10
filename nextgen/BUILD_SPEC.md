# AlgoTrader Pro replacement — Trend-3 + QQQ20 growth build specification

**Status:** historically superior challenger; approved for independent reproduction and paper/shadow operation, not a profit guarantee  
**Implementation verification:** 9 September 2026  
**Account basis:** approximately $29,469; no capital-gains tax  
**Broker:** Alpaca  
**Canonical strategy version:** `trend3-qqq20-v1`

## 1. Decision

Build a new, isolated monthly engine with a **20% permanent QQQ growth core plus SPY / QQQ / GLD independent trend sleeves and BIL fallback**. Do not insert it into `scripts/run_strategies.py` and do not reuse the legacy order functions.

The canonical audit tested 1,209 configurations. The selected deployment candidate was also proposed independently by the trader/execution panel before it was forced into validation. It produced:

| Metric | Trend-3 + QQQ20 | Pure Trend-3 |
|---|---:|---:|
| Full-period after-cost CAGR | 11.34% | 9.93% |
| Raw Sharpe | 0.95 | 0.93 |
| Excess Sharpe over BIL | 0.83 | 0.79 |
| Maximum daily drawdown | -19.88% | -15.98% |
| Annual traded notional / equity | 2.63× | 3.24× |
| 35-bps-per-side stress CAGR | 10.61% | 9.04% |

Disjoint results were 9.38% CAGR in 2007–2015, 13.96% in 2016–2020 and 12.45% in 2021–July 2026. The final segment is historical confirmation, not a genuinely untouched holdout.

## 2. Frozen strategy logic

### Assets

- Risk sleeve 1: SPY
- Risk sleeve 2: QQQ
- Risk sleeve 3: GLD
- Defensive asset: BIL

### Signal

On the last completed regular trading session of every month, for each risk ETF independently:

```text
risk_on(symbol) = adjusted_close(symbol) >= mean(last 200 adjusted closes including signal day)
```

### Targets

```text
SPY = 26.6667% if SPY risk_on, otherwise 0%
QQQ = permanent 20.0000% core + 26.6667% if QQQ risk_on
GLD = 26.6667% if GLD risk_on, otherwise 0%
BIL = 100% - SPY - QQQ - GLD
```

### Timing

1. Use only the completed month-end close and earlier data.
2. Form and durably store the signal after month end.
3. Execute during the next regular session, targeted for 09:35–11:00 New York time.
4. The canonical backtest used the next official open. A one-session delayed execution produced 11.40% CAGR and a two-session delay produced 11.25% versus 11.34% with no delay. The 09:35–11:00 intraday fill convention still requires paper measurement.

### Explicit prohibitions

Do not add or optimize:

- 150-day SMA, even though it backtested higher;
- weekly or daily rebalancing;
- ranking among the three sleeves;
- inverse-volatility sizing;
- leverage, shorting or options;
- stop-losses or trailing stops;
- RSI, MACD, ADX, VIX, LLM sentiment or machine learning;
- automatic liquidation triggered only by account drawdown.

Changing any item creates a new strategy and restarts validation.

## 3. Why this model can outperform the current one

1. It can deploy 100% when three independent trends are positive, while a permanent 20% QQQ core preserves some growth participation when trend sleeves turn defensive.
2. QQQ supplies a higher-growth equity core and dynamic sleeve; SPY reduces single-style dependence; GLD supplies a different monetary/inflation return source.
3. Each failed sleeve goes independently to Treasury bills, rather than turning the whole portfolio off because SPY alone crossed one line.
4. The trend rule reduced the full static basket's historical drawdown from roughly -38% to about -20%.
5. Monthly trading and four liquid ETFs sharply reduce implementation friction and failure surface.

The trend rule did **not** create all of the return. A volatility-matched static SPY/QQQ/GLD basket earned about 9.68% with a much worse -28.4% drawdown. The added QQQ20 core raised historical CAGR by about 1.41 percentage points versus pure Trend-3, at the cost of roughly 3.90 points of additional maximum drawdown. The model's defensible value is the combination of similar return and materially better downside control.

## 4. Research safeguards already applied

- Split- and dividend-adjusted histories.
- Actual ETF inception dates; no pre-listing backfill.
- Month-end signal and next-session adjusted-open execution.
- BIL as an actual defensive leg, including its traded turnover.
- 10-bps-per-side base cost and 5/20/35/50-bps stresses.
- Missing execution opens were never forward-filled.
- A held asset with an unresolved data gap fails the simulation.
- Targets were constrained to long-only, nonnegative weights totaling at most 100%.
- A slow reference engine and accelerated engine matched to machine precision on six strategy families.
- 1,209 attempted configurations were retained in the trial count.
- Training, validation, historical confirmation, rolling five-year, crisis, delay, cost, window and replacement-asset tests.

## 5. Important evidence against overconfidence

1. The project has already inspected the entire 2007–2026 history. No historical segment is now truly untouched.
2. Full-search PBO was approximately 65%, showing that selecting the apparent best of 1,209 variants is unreliable.
3. SPY/QQQ/GLD are surviving, liquid ETFs chosen with hindsight; asset-universe selection bias remains.
4. QQQ buy-and-hold earned materially more in this historical sample, but with a drawdown above 50%.
5. The model lost about 8.4% in the tested GFC interval, 17.4% in the COVID crash interval and 15.7% in 2022. It is defensive, not crash-proof.
6. A 200-day rule can exit after a fast decline and re-enter after part of a V-shaped rebound is missed.
7. Forward institutional return assumptions do not support treating 10% as a guaranteed base case.

## 6. Production architecture

The signal model is versioned `trend3-qqq20-v1`; capital scaling, drawdown governance and broker behavior are separately versioned `t3q20-exec-v1`. Historical KPIs apply to the full-allocation signal model without a triggered drawdown freeze. A 10%–25% pilot is an operationally scaled portfolio and must not be advertised with the full-allocation account CAGR.

```text
GitHub Actions (09:35 / 09:50 / 10:05 New York)
       |
       v
Alpaca market calendar ----> first 3 sessions after month end?
       |                                  |
       | no                               | yes
       v                                  v
 health publication        free delayed SIP adjusted daily bars
                                          |
                                 independent Yahoo check
                                          |
                                   fail-closed validation
                                          |
                                SPY/QQQ/GLD 200-SMA states
                                          |
                                  four target weights
                                          |
                           account/order/position reconciliation
                                          |
                         free IEX quote + protected limit orders
                                          |
                              fills -> buys -> final verification
                                          |
                               Cloudflare D1 durable state
                                          |
                         Cloudflare authenticated dashboard

Cloudflare dead-man cron ----------> enable/dispatch missed GitHub run
```

Base44, Vercel and a VPS are not required. Only GitHub holds broker credentials. Cloudflare holds execution state and serves the authenticated read-only dashboard, but cannot trade.

## 7. Data contract

### Signal data

- Alpaca SIP `1Day` bars requested with an explicit end time more than 15 minutes old, which is available under Alpaca Basic.
- `adjustment=all`.
- At least 200 complete sessions for every ETF.
- End timestamp must exclude the current incomplete session.
- Secondary adjusted provider must agree within 25 bps on the latest adjusted close and produce identical risk-on states.
- Persist bars, response timestamp and SHA-256 hash for every signal.
- Verify the exact 200 dates against the Alpaca exchange calendar and retain both providers' complete 200-session inputs in Cloudflare D1.

### Execution data

- Current unadjusted IEX bid/ask quotes from Alpaca Basic. IEX quotes are for protected order pricing only, never for the trend signal.
- Every asset must be tradable and fractionable.
- Reject crossed, missing or stale quotes.
- Never silently change the requested signal or execution feed.

## 8. Order contract

1. Dedicated account or zero non-strategy positions.
2. Zero open orders before reconciliation.
3. Deterministic `client_order_id` from strategy version, run date, symbol, side and target hash.
4. Query by that ID before every retry.
5. Submit sells first and wait for terminal fills.
6. Recalculate cash and buy quantities after sells in the production implementation.
7. Use fractional regular-hours DAY marketable-limit orders with a maximum 20-bps limit buffer.
8. No automatic reprice after an uncertain timeout. Cancel, alert and resume only through deterministic broker reconciliation.
9. Final target drift no greater than 50 bps per sleeve.
10. Never borrow on margin.

## 9. Durable state machine

```text
CREATED -> DATA_VALIDATED -> SIGNAL_LOCKED -> RECONCILED
        -> SELLING -> SELLS_CONFIRMED -> BUYING
        -> VERIFYING -> COMPLETE
```

Every transition is committed through an authenticated Cloudflare Worker to D1 before the next external action. A restart on a fresh GitHub runner queries D1 and deterministic broker order IDs, permits only expected open orders and resumes the stored phase. It never blindly resubmits. Rejected, canceled, expired or unrelated orders require operator review.

## 10. Risk and operational controls

### Portfolio invariants

- Only SPY, QQQ, GLD and BIL.
- Long only; gross exposure <=100%.
- SPY and GLD <=26.67%; QQQ <=46.67%; BIL may reach 100% only under a defensive drawdown freeze.
- Weights sum to 100% within one basis point.
- No target above its frozen per-asset cap; every buy is cash-capped and margin is prohibited.
- No live run when paper/live confirmation is inconsistent.

### Drawdown governance

- 10% from peak: alert and incident review.
- 15%: pause new risk increases until operator review; protective sells remain allowed.
- 25%: critical incident and independent re-audit.
- Plan capital for a possible 30% future drawdown despite the -19.88% backtest.
- Do not automatically liquidate solely because a threshold was crossed.
- Drawdown equity must be adjusted for cumulative deposits and withdrawals; `CUMULATIVE_EXTERNAL_CASH_FLOW` is reconciled to broker funding records.

### Operations

- One serialized GitHub Actions worker plus one authenticated Cloudflare D1 database. Local SQLite is test-only.
- External dead-man heartbeat.
- Independent Cloudflare cron enables and dispatches the GitHub workflow if the primary run is missing or unhealthy.
- Pinned dependencies and monthly security review.
- Structured logs; no silent exceptions.
- The Cloudflare dashboard is read-only and never receives broker credentials or the state-write token in browser code.

## 11. Acceptance tests the implementation must deliver

### Strategy tests

- Future-price mutation does not change an earlier decision.
- Exactly 200 completed sessions are used.
- Every on/off combination maps to the correct BIL residual.
- Missing or stale data fails closed.
- 150/200/250 windows are not runtime-configurable in production.

### Broker tests

- Duplicate invocation produces zero duplicate orders.
- Network timeout after broker acceptance resumes by client ID.
- Sells finish before buys.
- Partial fill, rejection, timeout cancellation and operator-controlled retry. The production build intentionally does not auto-reprice after uncertainty.
- Unauthorized symbol halts.
- Margin disabled and gross never exceeds 100%.
- Final drift verification.

### Calendar/restart tests

- Holidays, early closes and both DST offsets.
- Two scheduler invocations on the same day.
- Restart in every state-machine stage.
- Missed timer and delayed execution.

### Research reproduction

- Independent engine CAGR within 0.10 percentage points.
- Maximum drawdown within 0.50 points.
- Identical monthly signals.
- Yahoo and Alpaca adjusted histories agree.
- 10/20/35-bps cost results reproduced.
- 09:35 execution sensitivity completed.

The package includes `research/backtest/reproduce.py`, a slow reference engine, the frozen four-ETF adjusted-price snapshot, monthly target output, equity curve and delay sensitivity. The 09:35 intraday comparison remains a real-paper gate because the historical test uses official daily opens.

## 12. Deployment authorization ladder

1. **Code complete:** all tests pass; no unresolved Critical/High audit item.
2. **Paper:** three clean monthly rebalances; no duplicates; measured slippage within stress assumptions.
3. **Shadow:** compare intended targets and fills to the frozen backtest daily.
4. **Small live:** 25% of account only after three clean paper rebalances; legacy engine disabled.
5. **Scale to 50%:** after three clean live rebalances and independent reconciliation.
6. **Scale beyond 50%:** only after at least 12 months of untouched operation; 24 months is the statistical panel's preferred evidence standard.

Any code, parameter, universe or timing change returns to step 1.

## 13. Earnings interpretation

The exact historical 11.34% CAGR would compound $29,469 to about **$32,811 after one year, $50,422 after five years and $86,274 after ten years**. These are mechanical backtest extrapolations, not forecasts.

For planning, use a **7%–9% central range**, approximately 4% in a low-return case, and the historical 11.34% only as a favorable case. At 9%, the same starting capital becomes about $32,121 / $45,342 / $69,764 at 1 / 5 / 10 years. The platform can lose money over one or multiple years.

## 14. Implementation definition of done

The zero-subscription deployment is finished only when it supplies:

- a separate private repository matching this package;
- a line-by-line trace from every requirement above to code and test;
- test logs;
- a second-provider backtest report;
- paper-order evidence for all on/off combinations;
- screenshots/API records showing paper mode;
- an unresolved-risk register;
- an explicit statement that the legacy workflow is disabled.
- Cloudflare D1 schema and authenticated write/read smoke tests;
- a Cloudflare dead-man dispatch drill;
- a Cloudflare dashboard check proving browser code has no Alpaca, GitHub-dispatch or state-write credential.

Do not accept a prose claim that the strategy was built. Verify the repository, tests, broker state and paper fills directly.






