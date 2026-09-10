# Deployment status

Last updated: 2026-09-10

## Completed

- Candidate source merged into `main` under `nextgen/` without replacing the
  existing live engine.
- GitHub candidate CI passed seven consecutive merged-PR runs before this
  deployment update.
- Existing repository secrets `ALPACA_PAPER_KEY` and
  `ALPACA_PAPER_SECRET` are wired into the manual paper workflow.
- GitHub paper smoke run #6 completed successfully and returned the expected
  safe `NO_ACTION` result outside the execution window.
- Cloudflare D1 database `trend3-qqq20-state` was created in APAC and its
  schema was initialized successfully.
- Cloudflare Worker `trend3-qqq20-state` was deployed at:
  `https://trend3-qqq20-state.trend3-qqq20-state-service.workers.dev`
- Worker health, unauthorized-access rejection, authenticated heartbeat,
  durable run creation, legal state transition, and durable readback all
  passed against the remote service.
- Cloudflare write, read, and heartbeat secrets are configured in the Worker.
- The password-protected Cloudflare dashboard is deployed at the Worker
  `/dashboard` route. Its public shell returned 200, unauthorized status access
  returned 401, authorized access reached D1, and no service-secret names were
  present in browser-delivered HTML.
- GitHub repository variable `TREND3_STATE_API_URL` points to the deployed Worker.
- GitHub Actions secrets now contain the Cloudflare write token, heartbeat URL
  and native alert URL. No secret value is committed to the repository.
- Manual paper runs #7 and #8 completed successfully with authenticated Alpaca
  paper access and Cloudflare status publication. Run #8 also proved native
  alert ingestion; the outside-window run correctly remained unhealthy because
  no month-end target exists yet.
- The paper-only workflow now has DST-safe UTC schedules. The engine remains the
  final authority and cannot trade outside the official New York execution window.
- A fine-grained GitHub token restricted to `chenkingston-rgb/algotrader-pro`
  with Actions read/write and mandatory Metadata read-only access is stored only
  as the encrypted Cloudflare Worker secret `GH_ACTIONS_DISPATCH_TOKEN`. It has
  no repository-content write permission and expires on 2026-10-10.
- The restricted token passed an authenticated workflow-read check and a real
  paper-only workflow-dispatch permission drill. GitHub run #34444054580 was
  accepted and completed successfully on the first attempt.
- Cloudflare Worker version `3cba0be7-49c3-4c35-8291-e1ccc2dbc000` is deployed
  with one weekday credential check and DST-safe 10:10/10:25 New York fallback
  schedules. The public health route still returns OK and the dashboard status
  route still rejects unauthenticated requests with HTTP 401.

## Intentionally not active yet

- The first automatic Cloudflare scheduled invocation has not yet occurred;
  Cloudflare notes that new cron triggers can take up to 15 minutes to propagate.
- Vercel is no longer required; its source remains an optional fallback only.
- The first valid month-end signal/order cycle has not yet occurred.
- Three clean paper month ends, the D1 restore drill, and measured live-paper
  execution slippage remain required evidence.
- Live trading authorization remains blank and cannot activate.

The scheduled paper phase is active. These remaining controls must be completed
before any live-capital decision. Live capital remains gated by the deployment
runbook.
