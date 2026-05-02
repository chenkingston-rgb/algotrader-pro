# AlgoTrader Pro

Algorithmic trading platform built on **GitHub Actions + Alpaca + Base44**.

Executes 6 backtested equity strategies hourly during market hours with a full risk management layer.

## Strategies

| Strategy | Sharpe | VIX Type | VIX Block |
|---|---|---|---|
| RSI+MACD Combo | 0.79 | COMBO | >30 |
| Bollinger Bands Mean Reversion | 0.79 | MEAN_REV | >22 |
| MACD Crossover | 0.76 | TREND | >45 |
| Triple EMA (8/21/55) | 0.74 | TREND | >45 |
| EMA Crossover (12/26) | 0.70 | TREND | >45 |
| Momentum ROC (1.5% threshold) | 0.72 | MOMENTUM | >35 |

## Risk Management

- ATR-based position sizing (1% portfolio risk per trade)
- Per-strategy VIX regime filter (differentiated thresholds by strategy type)
- Bracket orders: stop loss (1.5×ATR) + take profit (3×ATR) on every trade
- Portfolio kill switch at 25% drawdown from peak equity
- Market hours guard: 10:00–15:45 ET only

## Architecture

```
GitHub Actions (hourly cron)
    └── scripts/run_strategies.py
            ├── Alpaca REST API (market data + order execution)
            └── Base44 API (signal log, trade log, portfolio state)
```

## Dashboard

Live dashboard, trade log, signal monitor, and strategy config panel at:
**[Base44 AlgoTrader Pro](https://app.base44.com/apps/69f60c0cd56ea2902b494394/editor/preview)**

## Setup

See [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) for step-by-step wiring instructions.

## Secrets Required

| Secret | Description |
|---|---|
| `ALPACA_PAPER_KEY` | Alpaca Paper Trading API Key |
| `ALPACA_PAPER_SECRET` | Alpaca Paper Trading Secret |
| `ALPACA_LIVE_KEY` | Alpaca Live Cash Account Key |
| `ALPACA_LIVE_SECRET` | Alpaca Live Cash Account Secret |
| `BASE44_API_KEY` | Base44 API Key |

> Trading mode defaults to **paper**. Live mode requires an explicit workflow trigger with `mode: live`.
