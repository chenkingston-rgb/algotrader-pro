# Frozen backtest reproduction

Run from this directory:

```text
python reproduce.py
```

It uses the included Yahoo adjusted-open/adjusted-close snapshot, the slow reference engine and an assertion against the reported 10-bps-per-side metrics. It writes:

- `reproduced/metrics.json`
- `reproduced/equity_curve.csv`
- `reproduced/monthly_targets.csv`
- `delay_sensitivity.py` additionally writes `reproduced/delay_sensitivity.json` for 0/1/2-session execution delays.

Data snapshot ends 31 July 2026. BIL did not exist before 30 May 2007; before its first valid observation the simulator treats the residual as zero-yield cash without backfilling a fictitious BIL price. This is conservative relative to positive cash interest but production could not literally buy BIL before inception.
