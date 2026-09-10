# Release notes — `trend3-qqq20-v1-zero-cost-cloud-rc1`

## Delivered

- Exact 20% permanent QQQ core plus three independently gated 26.6667% SPY/QQQ/GLD sleeves; residual in BIL.
- Month-end adjusted-close signal, 200-session SMA, next-session execution and bounded catch-up.
- Standalone Alpaca execution service with sell-first cash rebuilding, no margin, deterministic order recovery and transactional Cloudflare D1 state.
- Alpaca Basic data path: delayed historical adjusted SIP signals, independent Yahoo verification and current IEX quotes for protected limit orders.
- Timezone-aware serialized GitHub Actions execution, plus an independent Cloudflare missed-run enable/dispatch path.
- Password-protected Cloudflare read-only dashboard; broker secrets remain in GitHub only and no Vercel project is required.
- Fail-closed dual-source data validation, account/asset/order preflight, health telemetry, free webhook alerts and heartbeat.
- Reproducible frozen backtest inputs and output ledgers.
- Hashed production and development dependency locks, pinned CI actions, 70 automated tests and clean Python security audits.

## Intentionally excluded

- Legacy intraday and daily indicators.
- Dynamic stock scanning.
- Leverage, margin, shorting, options, stop-loss orders and automatic drawdown liquidation.
- Unvalidated PEAD, machine learning, factor, sentiment or discretionary overlays.

## Activation status

Code complete and suitable for paper/shadow deployment. It is **not live** and is **not approved for immediate full-capital activation**. Follow `DEPLOYMENT_RUNBOOK.md` without skipping gates.
