# Deployment status

Last updated: 2026-09-10

## Completed

- Candidate source merged into `main` under `nextgen/` without replacing the
  existing live engine.
- GitHub candidate CI passed four consecutive runs.
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

## Intentionally not active yet

- GitHub is not yet configured with the Worker write token and heartbeat URL.
- The independent Cloudflare dead-man scheduler has no GitHub dispatch token
  and has no cron trigger yet.
- Vercel is no longer required; its source remains an optional fallback only.
- The paper workflow remains manual-only.
- Live trading authorization remains blank and cannot activate.

These remaining controls must be completed and tested before the scheduled
paper phase begins. Live capital remains gated by the deployment runbook.
