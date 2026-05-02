# AlgoTrader Pro — Setup Guide

## What You've Built

A complete algorithmic trading platform with:
- **Base44 app** (ID: `69f60c0cd56ea2902b494394`) — Dashboard, config, trade log
- **6 backtested strategies** seeded with equity-tuned parameters
- **GitHub Actions** hourly execution engine
- **Alpaca integration** (Paper + Live Cash Account)

---

## Step 1: Create Your GitHub Repository

1. Go to https://github.com/new
2. Create a **public** repo named `algotrader-pro` (public = unlimited Actions minutes)
3. Initialize with a README

---

## Step 2: Add the Files

Create this folder structure in your repo:

```
algotrader-pro/
├── .github/
│   └── workflows/
│       └── trader.yml          ← copy from outputs/github_actions_trader.yml
├── scripts/
│   └── run_strategies.py       ← copy from outputs/run_strategies.py
└── README.md
```

---

## Step 3: Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets:

| Secret Name | Value |
|---|---|
| `ALPACA_PAPER_KEY` | Your Alpaca Paper Trading API Key ID |
| `ALPACA_PAPER_SECRET` | Your Alpaca Paper Trading Secret Key |
| `ALPACA_LIVE_KEY` | Your Alpaca Live Cash Account Key ID |
| `ALPACA_LIVE_SECRET` | Your Alpaca Live Cash Account Secret Key |
| `BASE44_API_KEY` | Your Base44 API key (get from Base44 Settings → API) |

> **Important:** `TRADING_MODE` defaults to `paper`. The live keys are only used when you manually trigger the workflow with `mode: live`.

---

## Step 4: Get Your Base44 API Key

1. Open your Base44 app: https://app.base44.com/apps/69f60c0cd56ea2902b494394/editor/preview
2. Go to **Settings → API / Integrations**
3. Copy your API key → add as `BASE44_API_KEY` GitHub secret

---

## Step 5: Wire Up Alpaca Keys in Base44

1. Open the Base44 app → **Settings page**
2. Enter your Paper Trading API keys
3. Enter your Live Cash Account API keys
4. Toggle should default to **Paper** mode
5. Click **Sync Account** to pull your initial equity

---

## Step 6: Test a Manual Run

1. Go to your GitHub repo → **Actions** tab
2. Click `AlgoTrader Pro — Hourly Signal Runner`
3. Click **Run workflow** → leave mode as `paper` → click **Run workflow**
4. Watch the logs — you should see signal output for all 6 strategies
5. Check your Base44 app → Signal Monitor page for logged signals

---

## Step 7: Verify the Cron Schedule

The workflow runs at these UTC times on weekdays (Mon–Fri):
- `14:00 UTC` = 10:00 AM ET
- `15:00 UTC` = 11:00 AM ET
- `16:00 UTC` = 12:00 PM ET
- `17:00 UTC` = 1:00 PM ET
- `18:00 UTC` = 2:00 PM ET
- `19:00 UTC` = 3:00 PM ET
- `20:00 UTC` = 4:00 PM ET (signals after close, no orders will fill same day)

The script also checks market hours internally (10:00–15:45 ET) as a secondary guard.

---

## Strategy Reference

| Strategy | Sharpe | VIX Type | VIX Block | Key Parameters |
|---|---|---|---|---|
| RSI+MACD Combo | 0.79 | COMBO | >30 | RSI(14) 35/65 gates + MACD 12/26/9 |
| Bollinger Bands | 0.79 | MEAN_REV | >22 | BB(20, 2-sigma) + above 200d MA only |
| MACD Crossover | 0.76 | TREND | >45 | MACD 12/26/9 histogram cross |
| Triple EMA | 0.74 | TREND | >45 | EMA 8/21/55 all aligned |
| EMA Crossover | 0.70 | TREND | >45 | EMA 12/26 golden/death cross |
| Momentum ROC | 0.72 | MOMENTUM | >35 | ROC(10) threshold 1.5% (not 5%) |

### Critical Parameter Notes
- **Momentum ROC**: threshold was re-tuned 5% to 1.5%. Original generated only 42 signals in 6 years vs 97 at 1.5%.
- **Bollinger Bands**: STRICT VIX filter (block >22). Without it Sharpe drops 0.79 to 0.66.
- **EMA/Triple EMA/MACD**: Keep original go-trader params exactly — re-tuning these made them worse.
- **MEAN_REV strategies**: Never trade when VIX > 22 (market panic = mean reversion breaks down).

---

## Risk Management

- **Position sizing**: 1% portfolio risk per trade, size = risk_$ / (1.5 x ATR14). Capped at 10% of equity.
- **Bracket orders**: Every order includes stop loss (1.5x ATR below entry) + take profit (3x ATR above).
- **Kill switch**: If drawdown from peak equity exceeds 25%, all trading halts. Manual reset required in Base44.
- **VIX regime**: Each strategy has a different VIX block threshold based on its type. See table above.

---

## Going Live

When you're satisfied with paper trading results:

1. Open your Base44 app → toggle Paper/Live switch to **Live**
2. When manually triggering GitHub Actions, set `mode: live`
3. Never set `TRADING_MODE: live` in the workflow default — keep it paper-first

---

## Base44 App Link

Open AlgoTrader Pro Dashboard:
https://app.base44.com/apps/69f60c0cd56ea2902b494394/editor/preview
