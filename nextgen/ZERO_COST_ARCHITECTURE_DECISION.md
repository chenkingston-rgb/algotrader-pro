# Architecture decision — remove Base44 and all recurring subscriptions

**Decision date:** 9 September 2026  
**Decision:** use GitHub Actions + Cloudflare Workers/D1; keep Alpaca Basic as broker/data provider. Vercel is not required.  
**Expected recurring subscription cost:** **$0**, provided published free-tier limits are not exceeded.  
**Investment model:** unchanged `trend3-qqq20-v1`.

## Why this combination

| Responsibility | Selected free service | Reason |
|---|---|---|
| Source, tests, secrets, authoritative Python worker | GitHub Free / Actions | Supports pinned Python runtime and the existing tested engine; 2,000 private-repository minutes monthly. |
| Durable state, authenticated API, independent dead-man schedule | Cloudflare Workers + D1 Free | Does not need an always-on server; D1 supplies transactional SQL state and seven-day point-in-time recovery on the current free tier. |
| Human dashboard | Cloudflare Worker | Password-protected read-only status UI on the same free Worker; no trading key and no additional account. |
| Broker, calendar, delayed signal bars, current quote | Alpaca Basic | Free historical SIP is usable when the explicit end is at least 15 minutes old; current IEX is sufficient for bounded protected-limit pricing subject to paper validation. |
| Independent adjusted-price check | Yahoo Finance chart endpoint | Used only to reject a discrepant signal, never to price an order. |
| Alerting | Cloudflare D1 alert journal; optional Discord/Slack webhook | Built-in durable visible alerts at no additional account; optional true out-of-band push. |

Official constraints checked for this decision:

- GitHub Free currently includes 2,000 Actions minutes per month for private repositories: https://docs.github.com/en/billing/reference/product-usage-included
- GitHub notes that scheduled runs can be delayed; the workflow now uses three attempts plus an independent Cloudflare dispatch: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- Cloudflare Workers Free currently includes 100,000 requests/day; D1 Free includes 5 million rows read/day, 100,000 rows written/day and 500 MB per database: https://developers.cloudflare.com/workers/platform/pricing/ and https://developers.cloudflare.com/d1/platform/limits/
- Alpaca Basic is free, current equities coverage is IEX, and unsubscribed historical SIP requests require an end time at least 15 minutes old: https://docs.alpaca.markets/us/docs/about-market-data-api and https://docs.alpaca.markets/us/docs/market-data-faq

## Estimated usage

- GitHub: 90 scheduled invocations/month; most are fast health/no-action runs. Expected range roughly 90–270 runner minutes plus CI, leaving substantial headroom below 2,000 minutes.
- Cloudflare Worker: hundreds, not tens of thousands, of requests/month.
- D1: normally hundreds of reads/writes/month; signal snapshots are small relative to 500 MB.
- Dashboard: one small Worker request per minute only while the operator console is open.
- Alpaca: far below the Basic plan's documented request-rate ceiling.

The GitHub Actions budget must be set to $0 with stop-on-limit enabled. Free-tier exhaustion is treated as an incident, never as authorization to pay automatically or bypass safeguards.

## Why not the other free options

### Base44 Free

Rejected. It does not provide the backend functions/automations required by the prior design. Keeping it as a decorative front end adds another failure surface and no trading capability.

### Supabase Free

Rejected as the authoritative trading-state store. Supabase documents that low-activity free projects can be paused after a seven-day low-activity period. Daily calls would often prevent this, but the threshold is service-controlled and not a suitable dependency for restart state. Cloudflare D1 has no equivalent inactivity-pause mechanism in its published model.

### Git commits or repository JSON as the database

Rejected. The legacy platform already demonstrated race, caching and concurrent-write risks. Git is source control, not the transaction log for orders.

### Vercel

Not required. Hobby cron is not precise, and Cloudflare can safely serve the read-only dashboard without adding another account or secret boundary. The retained `vercel/` implementation is an optional fallback only.

### GitHub Actions alone

Rejected for live use. GitHub explicitly describes schedule delay risk. The Cloudflare dead-man checks GitHub's workflow credential daily, re-enables a workflow disabled for inactivity, and dispatches one serialized backup run when status is stale or unhealthy.

## Security boundary

```text
                       can submit orders
                              │
                              ▼
                     GitHub Actions only
                     Alpaca key + D1 write token
                         │             │
                  broker │             │ durable transition
                         ▼             ▼
                      Alpaca      Cloudflare D1
                                      ▲
                                      │ direct authenticated read
                                      │
                           Cloudflare dashboard route
```

- Alpaca credentials: GitHub Secrets only.
- D1 write token: GitHub and Cloudflare only.
- D1 read token: optional API clients only; the built-in dashboard reads D1 server-side.
- GitHub dispatch token: Cloudflare only; restricted to one workflow/repository.
- Dashboard credentials: Cloudflare Worker secrets only.
- No credential is committed to source, displayed in telemetry or copied across all platforms.

## What changed in the code

1. Replaced the production-only SQLite assumption with an authenticated `RemoteRunStore` backed by D1. Local SQLite remains available for deterministic tests.
2. Made remote state mandatory in live mode.
3. Split data configuration into `sip_delayed` signal data and `iex` execution quotes.
4. Added a hard guard rejecting historical SIP requests newer than 16 minutes.
5. Added authenticated remote status publication; failure is fatal.
6. Added timezone-aware, serialized GitHub trade scheduling with multiple attempts.
7. Added a Cloudflare D1 schema, state API, daily heartbeat, daily GitHub-token verification and missed-run dispatch.
8. Added a password-protected Cloudflare read-only dashboard route; Vercel is optional.
9. Added native Cloudflare alert ingestion/history plus optional free Discord/Slack support without changing the alert contract.
10. Added tests for feed enforcement, mandatory remote state, remote API mapping and authenticated status publication.

## Approval status

The package is approved as a **zero-subscription paper deployment candidate**. It is not approved for immediate live trading. Remaining gates are external by nature: real Alpaca Basic/Yahoo agreement, three clean paper month ends, one state-restore drill, one dead-man dispatch drill and observed execution slippage within 20 bps per side.

The frozen 11.34% historical CAGR belongs to the investment model, not to the hosting provider. Removing subscriptions avoids approximately $1,668/year of Base44 Builder plus Alpaca Plus fees from the prior proposal, but no cloud architecture can guarantee that the strategy will earn its backtest return.
