# Zero-subscription deployment runbook — `trend3-qqq20-v1`

**Authoritative deployment:** GitHub Actions + Cloudflare Workers/D1 + Alpaca Basic  
**Recurring infrastructure subscriptions:** **$0** within published free-tier limits  
**Base44:** not used  
**VPS:** not used  
**Last verified:** 9 September 2026

This runbook changes the hosting and data-delivery mechanisms only. It does not change the frozen investment rule: 20% permanent QQQ plus three equal SPY/QQQ/GLD trend sleeves, with failed sleeves in BIL.

## 1. Final architecture

```text
GitHub private repository
  ├─ frozen Python strategy and tests
  ├─ GitHub Actions 09:35 / 09:50 / 10:05 New York daily schedule
  └─ Alpaca credentials (GitHub Secrets only)
               │
               │ authenticated state operations
               ▼
Cloudflare Worker + D1 (free)
  ├─ durable state machine, events and equity history
  ├─ latest fail-closed dashboard status
  ├─ daily heartbeat endpoint
  └─ 10:10–10:29 New York dead-man dispatch to GitHub
  └─ password-protected operator dashboard at `/dashboard`; no broker credentials

GitHub Actions ───────────────► Alpaca Basic (free)
  delayed completed SIP bars     account/calendar/orders
  + Yahoo independent check      + current IEX quotes
```

Only GitHub receives the Alpaca keys. Cloudflare cannot trade. The browser dashboard receives only a short-lived Basic authorization header and never receives a state-write, GitHub or broker credential.

## 2. Accounts and free-tier boundaries

Create or retain:

1. GitHub Free account and a **private** repository.
2. Cloudflare Free account with Workers and D1 enabled.
3. Alpaca Trading API Basic account.
4. The built-in Cloudflare alert journal; optionally add a free Discord/Slack webhook for true out-of-band push alerts.

Set GitHub Actions' usage budget to **$0 with “stop usage when the budget limit is reached”**. The design normally consumes a small fraction of the included private-repository minutes. Cloudflare usage is expected to be a few hundred requests and rows per month, far below its free limits. The dashboard reads D1 through the Worker only while it is open.

Free tiers are external dependencies, not service-level agreements. The dual scheduler, durable state, deterministic broker IDs and three-session catch-up are the mitigations; they do not create a guarantee of availability.

## 3. Cloudflare durable-state service

From `cloudflare/`:

```bash
npm install
npx wrangler login
npx wrangler d1 create trend3-qqq20-state
cp wrangler.toml.example wrangler.toml
# Put the returned database ID into wrangler.toml.
npx wrangler d1 execute trend3-qqq20-state --remote --file=schema.sql
```

Generate five unrelated random values of at least 32 bytes:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set Worker secrets, entering the value only at the prompt:

```bash
npx wrangler secret put STATE_API_WRITE_TOKEN
npx wrangler secret put STATE_API_READ_TOKEN
npx wrangler secret put HEARTBEAT_SECRET
npx wrangler secret put DASHBOARD_USERNAME
npx wrangler secret put DASHBOARD_PASSWORD
npx wrangler secret put GH_ACTIONS_DISPATCH_TOKEN
npx wrangler deploy
```

`GH_ACTIONS_DISPATCH_TOKEN` must be a fine-grained GitHub token restricted to the one repository and Actions workflow enable/dispatch permissions. It must not have repository-content write permission and must not be an Alpaca credential.

The deployed token expires on **2026-10-10**. Rotate it before that date using
the same one-repository permission boundary, then replace only the encrypted
Cloudflare secret. Never copy it into GitHub Actions, source control, logs, or
the operator dashboard.

Cloudflare cron contract (all UTC; the Worker re-checks America/New_York):

- `0 12 * * MON-FRI`: credential/workflow verification only;
- `10,25 14 * * MON-FRI`: EDT fallback probes;
- `10,25 15 * * MON-FRI`: EST fallback probes.

Only a probe that lands between 10:10 and 10:29 New York time may dispatch.

Smoke checks:

```bash
curl https://YOUR-WORKER.workers.dev/v1/health
curl -H "Authorization: Bearer READ_TOKEN" https://YOUR-WORKER.workers.dev/v1/status
```

The second request should return 404 until the trader has published its first status. A 200 before any trader run is a deployment error.

For the subscription-free built-in alert journal, set GitHub `ALERT_WEBHOOK_URL`
to the same secret heartbeat URL. `GET` records health heartbeats; `POST` stores
alerts in D1 and the dashboard displays the newest ten. A Discord/Slack webhook
may replace the alert URL later without changing trading logic.

## 4. GitHub repository configuration

Copy this complete package to a dedicated private repository. Do not merge it into the legacy 3,341-line engine.

### Actions secrets

| Secret | Value |
|---|---|
| `ALPACA_API_KEY` | paper key first; live key only after all gates |
| `ALPACA_SECRET_KEY` | matching paper/live secret |
| `EXPECTED_ALPACA_ACCOUNT_ID` | exact dedicated account ID |
| `STATE_API_WRITE_TOKEN` | same write token configured in Cloudflare |
| `ALERT_WEBHOOK_URL` | free Discord or Slack HTTPS webhook |
| `HEARTBEAT_URL` | `https://YOUR-WORKER.workers.dev/v1/heartbeat/HEARTBEAT_SECRET` |

### Actions variables

| Variable | Paper value |
|---|---|
| `ALPACA_PAPER` | `true` |
| `STATE_API_URL` | deployed Worker origin |
| `INITIAL_PEAK_EQUITY` | approved starting high-water mark |
| `CUMULATIVE_EXTERNAL_CASH_FLOW` | `0` initially |
| `STRATEGY_ALLOCATION` | `1.0` for paper; `0.10`–`0.25` for first live stage |
| `CONFIRM_LIVE` | blank in paper |
| `CONFIRM_SCALE` | blank |
| `LIVE_AUTHORIZED_UNTIL` | blank in paper |

Keep the repository's default branch in `cloudflare/wrangler.toml`. Run the CI workflow. It must pass Python tests, lint, security checks, frozen-backtest reproduction and JavaScript syntax checks.

The trade workflow uses timezone-aware New York schedules at 09:35, 09:50 and 10:05 every day. Weekend and holiday invocations refresh health but cannot trade because the Python engine—not the cron expression—requires an official regular session and one of the first three sessions after month end. Daily health runs prevent a misleading stale dashboard over weekends. Duplicate invocations serialize through GitHub `concurrency`, then stop through the durable monthly `run_id` and deterministic Alpaca order IDs.

## 5. Free market-data contract

The no-subscription build deliberately separates signals from execution:

1. **Primary signal:** corporate-action-adjusted Alpaca SIP daily bars for the completed prior month-end, requested with an explicit end time more than 15 minutes old. Alpaca Basic permits this delayed historical request.
2. **Independent signal check:** Yahoo adjusted closes must contain the same 200 exchange sessions, agree within 25 bps on the final closes and produce identical trend states.
3. **Execution quotes:** current Alpaca IEX bid/ask quotes only. They are never used to calculate the 200-day signal.
4. **Order protection:** regular-hours DAY limit orders no worse than 20 bps beyond the observed IEX bid/ask. No market order and no automatic repricing after uncertainty.

The change from current SIP quotes to IEX quotes is an implementation change, not a new investment rule. It must pass three paper month ends. If IEX quote quality causes rejects, stale quotes, drift over 50 bps or all-in cost over 20 bps per side, the system remains paper-only; it does not silently subscribe or widen limits.

## 6. Cloudflare read-only dashboard

Open `https://YOUR-WORKER.workers.dev/dashboard`, sign in with the two dashboard secrets, and confirm it shows **UNHEALTHY** before the first valid status exists. The Worker checks the credentials server-side and reads D1 directly. The HTML/JavaScript contains no Alpaca key, GitHub token, D1 write token or D1 read token. The old `vercel/` directory is retained only as an optional fallback and is not part of the authoritative deployment.

## 7. Paper acceptance — minimum three completed month ends

For every paper cycle retain:

1. exact signal date and 200-session snapshot hashes;
2. Alpaca/Yahoo agreement and each SPY/QQQ/GLD trend state;
3. target hash and durable D1 transition history;
4. zero unrelated positions/orders before execution;
5. deterministic client order IDs, fills and slippage;
6. sell completion before any buy;
7. final maximum drift no greater than 50 bps;
8. visible Discord/Slack test alert and Cloudflare heartbeat;
9. a forced retry after simulated network uncertainty with zero duplicate orders;
10. one Worker-originated Cloudflare dead-man dispatch drill after temporarily
    disabling the primary schedule (the restricted-token workflow-read and
    direct paper-dispatch permission checks are already complete);
11. one session-two or session-three catch-up drill;
12. restored D1 state using Cloudflare's point-in-time recovery procedure.

Any wrong symbol, margin use, duplicate, stale/crossed quote, data disagreement, unexplained alert failure, remote-state failure or false-green dashboard resets the paper count to zero.

## 8. Live cutover

1. Disable every legacy trading workflow and revoke its Alpaca key.
2. Confirm directly in Alpaca that there are zero open orders and no position outside SPY/QQQ/GLD/BIL.
3. Use a fresh key dedicated to this engine.
4. Reconcile `INITIAL_PEAK_EQUITY` and all deposits/withdrawals.
5. Set `STRATEGY_ALLOCATION=0.10` to `0.25`.
6. Set `ALPACA_PAPER=false`.
7. Set `CONFIRM_LIVE=I_HAVE_COMPLETED_ALL_DEPLOYMENT_GATES`.
8. Set `LIVE_AUTHORIZED_UNTIL` no more than 31 days ahead.
9. Leave `CONFIRM_SCALE` blank.
10. Supervise the first three live rebalances in the Alpaca console.

Scaling rules are unchanged: three clean live rebalances before 50%; at least 12 months frozen operation (24 preferred) and a new independent audit before exceeding 50%.

## 9. Failure behavior

- GitHub unavailable: Cloudflare attempts one enable-and-dispatch during the 10:10–10:29 New York window.
- Cloudflare unavailable: the trader refuses live startup because durable state is mandatory; it does not trade from an empty local file.
- Dashboard route unavailable: trading can continue; view broker and GitHub logs directly.
- Yahoo or delayed SIP unavailable/disagreeing: no new orders.
- IEX quote stale/crossed/missing: no new orders.
- Order state uncertain: cancel, reconcile by deterministic client ID and require review; no blind retry.
- Missed first session: use the same locked prior-month signal on sessions two or three; after that, alert and do not trade.
- Any status older than 26 hours is red even if its last stored value said healthy.

## 10. What remains free—and what is not promised

No Base44, Vercel, paid GitHub, VPS or Alpaca SIP subscription is required for this design at the expected traffic level. Domain registration is optional and not included. Free-tier quotas and provider terms can change; review them quarterly.

The historical 11.34% CAGR is unchanged as a research result because the investment signal is unchanged. It is not a forward guarantee. The free execution path adds IEX quote-basis risk, which is bounded by limits and must be measured in paper trading before any live authorization.
