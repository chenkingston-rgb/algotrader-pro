# Trend-3 zero-cost cloud candidate

This branch stages the independently tested candidate under `nextgen/` without modifying the currently deployed engine.

Safety defaults:

- Paper account credentials use separate `TREND3_PAPER_*` secrets.
- `ALPACA_PAPER` is hard-coded true.
- Live authorization variables are blank.
- The strategy allocation is 10%.
- Merge/cutover is blocked until the deployment runbook gates pass.

See `nextgen/DEPLOYMENT_RUNBOOK.md`.
