# Release provenance

- Release candidate: `trend3-qqq20-v1-zero-cost-cloud-rc1`
- Strategy version: `trend3-qqq20-v1`
- Execution-policy version: `t3q20-exec-v1`
- Assembly and final verification date: 2026-09-09
- Historical input snapshot: frozen adjusted Yahoo daily data in `research/backtest/data/`
- Historical evaluation window: 2007-01-03 through 2026-07-31
- Canonical assumed trading cost: 10 bps per traded side

`SOURCE_MANIFEST.sha256` records every release file except itself. Local virtual environments, caches, development secrets, Wrangler state and superseded VPS artifacts are intentionally excluded.

This is a clean standalone implementation. It was not derived by editing the private live execution engine because that repository and live credentials were unavailable. The public monitoring repository was inspected, but it does not contain the private order-routing source.

The package is code-complete and independently reproducible, including a local D1/API smoke test. It is not evidence that remote Cloudflare/Vercel deployment or Alpaca paper/live gates have passed and is not authorization to activate full capital.
