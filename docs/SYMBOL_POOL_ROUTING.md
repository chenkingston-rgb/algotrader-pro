# ⚠️ ARCHITECTURE RULE — SYMBOL POOL ROUTING
## Read This Before Modifying Any Strategy Symbol List

**File:** `docs/SYMBOL_POOL_ROUTING.md`
**Applies to:** ALL future FIX versions, strategy additions, and symbol changes
**Established:** FIX-T v8.9 (2026-07-22) — added after repeated hardcoded-symbol mistakes

---

## The Symbol Pipeline (3 Stages)

```
STAGE 1: Weekly Scan (Sunday)          STAGE 2: Daily Pre-mkt Scan          STAGE 3: Engine (every 15min)
scan_symbols.py                        scan_daily.py                         run_strategies.py
  Scores 95-symbol universe              Reads weekly + pre-market filters       Reads correct pool per strategy
  Produces TWO separate pools:           Writes watchlist_daily.json             MEAN_REV vix_type → meanrev pool
    TREND pool   (ADX>20 + momentum)     (top 12 picks + core)                   TREND vix_type   → daily/weekly
    MEAN_REV pool (ADX<22 + low vol)                                             NEVER apply one merged list
  Writes:                                                                          to ALL strategies
    watchlist_weekly.json (merged)
    watchlist_meanrev.json (MR only)  ← NEW in FIX-T v8.9
```

---

## The Two Scanner Pools

### TREND Pool (`score_trend()`)
- **Criteria:** ADX > 20 (trending), strong Carhart 12m-1m momentum, ADV > $5M
- **Strategy consumers:** `momentum_roc_15m`, `ema_crossover`, `triple_ema`, `macd_crossover`, `rsi_macd_combo`
- **Source file:** `watchlist_weekly.json → trend_symbols` / `watchlist_daily.json`
- **Why:** Breakout/momentum strategies need stocks that are ALREADY moving directionally

### MEAN_REV Pool (`score_mean_rev()`)
- **Criteria:** ADX 10–22 (ranging), 4-week return -8% to +8%, realized vol < 30%, price close to SMA20
- **Strategy consumers:** `bollinger_bands_15m`, `rsi_mean_reversion_15m`
- **Source file:** `watchlist_meanrev.json` (dedicated file written by scan_symbols.py)
- **Why:** Mean-reversion strategies need stocks that OSCILLATE, not trend hard

---

## ⛔ THE MISTAKE THAT KEEPS HAPPENING

Every time a new strategy is added, the same mistake is made:
symbols are hardcoded into INTRADAY_STRATEGIES / DAILY_STRATEGIES config blocks.

  BAD:  "bollinger_bands_15m": { "symbols": ["SPY","QQQ","HOOD","SCHW"] }
        ^ human guess, ignores scanner entirely. SPY/QQQ NEVER touch lower Bollinger
          band in a bull market — they are too trend-stable for mean-reversion buys.

  GOOD: Set vix_type = "MEAN_REV" → engine reads watchlist_meanrev.json at runtime
        → scan-qualified, weekly-refreshed, volatility-verified ranging stocks

The hardcoded "symbols" in each strategy config are the FALLBACK ONLY.
They activate only when the watchlist fetch fails.

---

## What the Engine Does at Runtime (FIX-T v8.9)

  meanrev_symbols = load_dynamic_symbols(pool="MEAN_REV")   → watchlist_meanrev.json
  general_symbols = load_dynamic_symbols(pool="ALL")        → daily or weekly watchlist

  For each strategy:
    if vix_type == "MEAN_REV": use meanrev_symbols  (Bollinger + RSI-MR)
    else:                       use general_symbols  (trend/momentum)

---

## When You Add a New Strategy — CHECKLIST

  1. Set vix_type correctly:
       "MEAN_REV"  → will receive scan-qualified ranging stocks (watchlist_meanrev.json)
       "TREND" / "MOMENTUM" / "COMBO" → will receive trending stocks (daily/weekly watchlist)

  2. Put reasonable symbols in the "symbols" field — FALLBACK ONLY, not primary source.
     Use 3-5 symbols that fit the strategy logic for when fetches fail.

  3. DO NOT rely on "symbols" as the production symbol list. The scanner qualifies better.

  4. DO NOT merge TREND + MEAN_REV into one list and feed to all strategies.

---

## Current Strategy Pool Assignments

  bollinger_bands_15m        → MEAN_REV pool (watchlist_meanrev.json)
  rsi_mean_reversion_15m     → MEAN_REV pool (watchlist_meanrev.json)
  momentum_roc_15m           → general pool  (watchlist_daily.json / weekly)
  ema_crossover              → general pool  (watchlist_weekly.json)
  triple_ema                 → general pool  (watchlist_weekly.json)
  macd_crossover             → general pool  (watchlist_weekly.json)
  rsi_macd_combo             → general pool  (watchlist_weekly.json)

---

## Key Files

  logs/watchlist_weekly.json   → Full merged weekly + both pool sublists (trend_symbols, meanrev_symbols)
  logs/watchlist_meanrev.json  → MEAN_REV pool ONLY (NEW: FIX-T v8.9)
  logs/watchlist_daily.json    → Pre-market intraday picks (top 12 + core)

---

## Historical Context — Why This Rule Exists

FIX-N (v8.6): Bollinger symbols changed from [SPY,QQQ] to [SPY,QQQ,XLK,XLF,XLE].
  Still ad-hoc — none of XLK/XLF/XLE were qualified by the mean-rev scanner.

FIX-S (v8.9): Further expanded to [SPY,QQQ,HOOD,SCHW,AMAT,PYPL,BAC] after live
  analysis showed SPY/QQQ never touch lower BB in bull markets.
  Problem: HOOD/SCHW/AMAT/PYPL/BAC are TREND picks per scanner, NOT MEAN_REV picks.
  This week's actual MEAN_REV picks were: XLI, CVX, TSLA, DIS, XOM, COP, AMZN, UBER.

FIX-T (v8.9): Pool routing implemented. Engine reads pool-specific files.
  Hardcoded lists become fallbacks only.
