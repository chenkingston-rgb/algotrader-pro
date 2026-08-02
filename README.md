# AlgoTrader Pro — RETIRED

This repository held the **v9.x multi-strategy intraday engine**. It is
**retired and no longer trades**. Do not re-enable anything here.

- Final code state: tag **`v9.1-final-sunset`** (commit `09d9a99`, 27 Jul 2026).
- All trading workflows and engine scripts have been removed from `main`.
- Historical logs are preserved in `logs_v9_archive/` for the record.

## Why it was retired

An independent audit (Parts One–Seven) found the system had no statistically
defensible edge and a negative live record: **101 trades between 29 Jun and
21 Jul 2026 — 49% win rate, profit factor 0.79, −$715 realized**, a loss
substantially explained by transaction costs alone at ~1,600 round-trips/year.
Further audit rounds catalogued critical execution defects including a
naked-stop window, an inverted RSI(2) position-size multiplier, a
dimensionally miscalibrated volatility target, and a force-close that raised
a TypeError on every invocation.

## What replaced it

The live system is now **`algotrader-pro-v2`** (private) running
**v3.0-ensemble**: a three-sleeve, monthly-rebalanced ETF portfolio
(momentum rotation / trend-gated QQQ / static 60-40), ~12 trades a year.

## Security note

This repository's Actions secrets previously held **live** Alpaca trading
credentials. Those keys must be rotated in the Alpaca dashboard; the current
system uses separately-issued keys stored only in `algotrader-pro-v2`.
