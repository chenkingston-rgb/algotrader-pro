# Trend-3 zero-cost cloud candidate

This branch stages the independently tested candidate under `nextgen/` without modifying the currently deployed engine.

Safety defaults:

- Paper account credentials reuse the existing `ALPACA_PAPER_KEY` and
  `ALPACA_PAPER_SECRET` repository secrets.
- `ALPACA_PAPER` is hard-coded true.
- Live authorization variables are blank.
- The strategy allocation is 10%.
- The paper workflow is manual-only until durable Cloudflare state and alerting
  have been deployed and smoke-tested.
- Merge/cutover is blocked until the deployment runbook gates pass.

See `nextgen/DEPLOYMENT_RUNBOOK.md`.
