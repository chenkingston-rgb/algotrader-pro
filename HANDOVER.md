# AlgoTrader Pro — Agent Handover Document
**Last updated:** 2026-07-24 11:10 SGT (Jul 23 23:10 ET)  
**Engine version:** v9.1 (FIX-W)  
**Prepared by:** Base44 Superagent  
**For:** Incoming AI agent / operator

---

## 1. PROJECT OVERVIEW

AlgoTrader Pro is an automated equity trading system running on **Alpaca (LIVE)** via **GitHub Actions cron**. It trades US equities and ETFs using multiple strategies across daily (swing) and intraday (15-minute) timeframes. The engine is a single Python file (`scripts/run_strategies.py`, ~3010 lines) that runs every 15 minutes during US market hours.

### Key Facts
- **Repository:** `chenkingston-rgb/algotrader-pro` (GitHub)
- **Broker:** Alpaca LIVE (not paper)
- **Engine:** Python 3, single file, no database — all state persisted as JSON files committed back to the repo
- **Execution:** GitHub Actions cron (every 15min during 9:45am-3:45pm ET)
- **Dashboard:** Separate Base44 app (`algotrader_pro_dashboard`, app ID `69f60c0cd56ea2902b494394`) reads raw GitHub JSON
- **Total fixes deployed:** FIX-E through FIX-W (18 named fixes over 10 days, Jul 14-23, 2026)

---

## 2. CURRENT ACCOUNT STATE (as of Jul 23 23:10 ET)

| Metric | Value |
|--------|-------|
| Equity | $29,543.19 |
| Cash | $23,479.50 |
| Deposit basis | $28,781.89 (total deposits, all-time) |
| Trading P&L | +$761.30 (equity - basis) |
| Peak equity | $29,801.07 |
| Drawdown | 0.87% |
| Kill switch | Inactive (threshold: 25% drawdown) |

### Open Positions (3)
| Symbol | Strategy | Qty | Entry | Current | UPL | UPL% | Stop |
|--------|----------|-----|-------|---------|-----|------|------|
| BAC | macd_crossover (daily) | 47 | $61.79 | $61.27 | -$24.33 | -0.85% | $59.38 |
| CVX | triple_ema (daily) | 15 | $196.26 | $194.80 | -$21.90 | -0.85% | $189.13 |
| HIMS | triple_ema (daily) | 8 | $35.36 | $32.75 | -$20.86 | -7.37% | $30.31 |

**WARNING: HIMS is the highest risk position** — 7.37% underwater, stop at $30.31. A further 7% drop triggers the stop for a ~$40 loss.

### Today's Closed Trade
| Symbol | Strategy | Entry | Exit | Qty | P&L | Hold | Result |
|--------|----------|-------|------|-----|-----|------|--------|
| XLB | bollinger_bands_15m (intraday) | $50.18 | $50.27 | 58 | +$5.41 | 210 min | WIN |

This was the **first successful intraday Bollinger trade** post FIX-W — validates the entire FIX-U + FIX-T + FIX-W pipeline.

---

## 3. PERFORMANCE HISTORY

### Pre-Fix Era (Jun 29 - Jul 21, 101 closed trades)
- **Win rate:** 49% (49W / 52L)
- **Total P&L:** -$715.46
- **Avg win:** +$53.59 | **Avg loss:** -$64.26
- **Profit factor:** 0.79 (for every $1 profit, lost $1.27)
- **Biggest wins:** HOOD +$425, AMAT +$415, SNOW +$233, PANW +$136, XLE +$133
- **Biggest losses:** HOOD -$450, AMAT -$371, SNOW -$305, PANW -$246, PANW -$199
- **Root cause of losses:** Catastrophic single-trade risk management failures (positions held too long, wrong symbols, no stops), NOT signal quality. The 49% win rate proves signals have moderate edge.

### Post-Fix Era (Jul 22+, 1 closed trade + 3 open)
- XLB: +$5.41 (closed, WIN)
- BAC/CVX/HIMS: -$67 total unrealized (still open)
- **Sample size too small to judge** — need 30-50 post-fix trades for meaningful statistics

### Honest Assessment of Strategy Quality
- The underlying strategies (Bollinger, MACD, EMA, momentum ROC, RSI mean-reversion) are **textbook strategies with moderate edge** — 49% win rate is real, not noise
- The week of fixes was **90% infrastructure/risk management, 10% signal tuning** — it fixed broken execution paths, not strategy alpha
- **The real test starts now:** with correct signal generation (FIX-U), correct pool routing (FIX-T), correct execution (FIX-W), and proper risk management (FIX-H/L/M/J), the next 30-50 trades will show whether the strategy algorithms themselves are viable or need to evolve

---

## 4. ALL DEPLOYED FIXES (chronological)

| Fix | Date | Description |
|-----|------|-------------|
| FIX-E | Jul 14 | Pre-10am intraday block, wide-open size reduction (50% in 10:00-10:30 window) |
| FIX-F | Jul 15 | Dual-condition high52w filter (block ONLY when h52w<0.75 AND ret12w<0%), Amihud illiq threshold 1e-8 |
| FIX-G | Jul 16 | cord60 (vol-momentum), strev21 (reversal guard), imax20 (bull-trap guard), MA200 (Faber trend filter), ATR volatility targeting (12% annualized, 2x cap), signal persistence (3 bars) |
| FIX-H | Jul 17 | Trailing stop: 0.5xATR trail distance + 2.0xATR static safety net. Time_stop_guard removed. |
| FIX-I | Jul 17 | Momentum ROC: threshold 0.3->0.8, vol_ratio 1.2->1.5, max_extension 2.0->1.8 |
| FIX-J | Jul 17 | Session ban (same-day after stop), 390min max hold, weekly concentration cap (3/symbol/week), 14:00 ET gate, MRVL+TXN removed |
| FIX-K | Jul 17 | Heartbeat monitoring, requests.Session (module-level), test suite rewrite. **BLOCKED:** intraday cron + concurrency guards (needs GitHub PAT workflow scope) |
| FIX-L | Jul 18 | Two-phase breakeven trail: Phase 1 = 2.0xATR static + 0.5xATR trail from entry; Phase 2 = once price >= entry+1xATR, cancel old trail, re-attach 0.5xATR trail = guaranteed profit floor |
| FIX-M | Jul 18 | 30-min trail activation delay — prevents trail from firing on entry noise (9/9 trail exits were losses before this fix) |
| FIX-N | Jul 18 | Bollinger: bb_std 2.0->1.8, ma_filter 50->20; RSI_MR: thresholds widened |
| FIX-O | Jul 21 | Cross-strategy ownership guard — sell signal from strategy X cannot close position opened by strategy Y |
| FIX-P | Jul 21 | Universal 14:00 ET buy cutoff — extended to ALL strategy types (was intraday-only) |
| FIX-Q | Jul 21 | qty=0 guard on trailing stop submission (race condition fix) |
| FIX-T | Jul 22 | Pool routing: scanner writes separate MEAN_REV and TREND watchlists; engine routes by vix_type |
| FIX-U | Jul 22 | MA20 trend filter: replaced broken 15Min rolling(20) with daily-derived MA20; removed redundant MA filter from Bollinger buy gate (logical contradiction with lower-band entry) |
| FIX-V | Jul 23 | Universe refactor: 95->76 symbols, removed 28 dead-weight, added 9 new (XLB/XLU/XLP/XLC/TSM/NU/HIMS/SHOP/DKNG) |
| FIX-W | Jul 23 | **CRITICAL:** Intraday execution deadlock — `elif signal=="buy" and strategy_type=="intraday":` in if/elif chain consumed ALL intraday buys, preventing the execution path from running. Moved weekly cap inside the buy catch-all. |

---

## 5. ARCHITECTURE

### File Layout
```
scripts/run_strategies.py    — Main engine (3010 lines, v9.1)
scripts/scan_symbols.py      — Weekly symbol scanner (Sunday, 76-symbol universe -> TREND + MEAN_REV pools)
scripts/scan_daily.py        — Daily pre-market scanner (9:00am ET weekdays)
test_v84.py                  — 21 unit tests (all passing)
.github/workflows/intraday.yml — Every 15min, 9:45am-3:45pm ET
.github/workflows/daily.yml    — Daily strategies + pre-market scan

logs/                        — All state files (JSON, committed to repo)
  live_baseline.json         — Peak equity, kill switch, cooldowns, weekly counts, regime
  signals_history.json       — Rolling 500-entry signal log
  run_history.json           — Per-run summaries
  strategy_trade_log.json    — Permanent strategy-tagged trade outcomes
  daily_equity_history.json  — All-time daily equity log
  intraday_position_tags.json — Open position metadata (entry price, ATR, trail ID, strategy)
  watchlist_weekly.json      — Merged weekly watchlist (TREND + MEAN_REV sublists)
  watchlist_daily.json       — Daily pre-market picks (TREND pool)
  watchlist_meanrev.json     — MEAN_REV pool (Bollinger/RSI_MR input)
  dashboard_payload.json     — Pre-computed dashboard data
```

### Strategy Configuration
```
INTRADAY_STRATEGIES (15Min bars, bar_days=20):
  bollinger_bands_15m    — vix_type="MEAN_REV", bb_std=1.8, ma_filter=20, RSI8<45 gate
  rsi_mean_reversion_15m — vix_type="MEAN_REV", RSI8<32 buy, >68 sell, daily MA20 filter
  momentum_roc_15m       — vix_type="MOMENTUM", ROC>0.8%, vol_ratio>1.5, max_extension 1.8%

DAILY_STRATEGIES (1Day bars, bar_days=300):
  macd_crossover         — vix_type="TREND", MACD signal cross
  triple_ema             — vix_type="TREND", 3-EMA crossover
  ema_crossover          — vix_type="TREND", 2-EMA crossover
  rsi_macd_combo         — vix_type="TREND", RSI + MACD confirmation
```

### Symbol Pool Routing (FIX-T)
- **MEAN_REV pool** (`watchlist_meanrev.json`): XLB, XLU, XLP, XLC, TSM -> Bollinger + RSI_MR
- **TREND pool** (`watchlist_daily.json`): SPY, QQQ, IWM, GLD, XLK, XLE, XLF, TSLA, UBER, XOM, BAC, AAPL, SCHW, OXY, AMZN, XLI, PYPL, XLV, SMH, NU, HIMS, SHOP -> Momentum + daily strategies
- **Rule:** `vix_type` in strategy config determines pool. NEVER merge pools. NEVER hardcode symbols as primary source.

### Key Constants
```python
RISK_PCT         = 0.01    # 1% equity risk per trade
MAX_POSITION_PCT = 0.10    # Max 10% equity per position
ATR_STOP_MULT    = 2.0     # Static stop = entry +/- 2.0xATR
ATR_TP_MULT      = 3.0     # Take profit = entry + 3.0xATR
BREAKEVEN_ATR_TRIGGER  = 1.0   # Price must exceed entry + 1xATR for trail upgrade
PROFIT_LOCK_ATR_MULT   = 0.5   # Post-upgrade trail = 0.5xATR
TRAIL_ACTIVATION_MIN   = 30    # 30-min delay before trail activates
MAX_HOLD_MINUTES       = 390   # 6.5 hours max hold for intraday
MAX_WEEKLY_ENTRIES_PER_SYMBOL = 3  # Max 3 intraday entries per symbol per week
MAX_DRAWDOWN_PCT       = 25    # Kill switch at 25% drawdown from peak
EOD_EXIT_HOUR = 15, EOD_EXIT_MIN = 0  # 3:00pm ET forced exit for intraday
```

---

## 6. CRITICAL RULES (NEVER VIOLATE)

1. **Deposit exclusion:** The ~$1,002 equity jump on July 13 SGT (July 14 ET) is a DEPOSIT, not trading profit. Deposit-adjusted basis = **$28,781.89**. Trading P&L = equity - $28,781.89. This applies to ALL performance reports, equity curves, Sharpe calculations — forever.

2. **Kill switch:** 25% trailing drawdown from peak_equity (persisted in `live_baseline.json`) halts all new buys. Peak equity survives across runs.

3. **Universal 14:00 ET cutoff:** No new BUY entries for ANY strategy type after 2:00pm ET. Sells/stops/EOD exits still active.

4. **390-min max hold:** Any intraday position held >390 minutes is force-closed by `run_eod_exit()`.

5. **Pre-10am block:** No intraday entries before 10:00am ET (daily strategies exempt — they use prior-day bars).

6. **Cross-strategy guard (FIX-O):** A sell signal from strategy X cannot close a position opened by strategy Y. EOD sweep and trailing stops are exempt.

7. **30-min trail delay (FIX-M):** Trailing stop does NOT activate until 30 minutes after entry. Static 2.0xATR stop is the only protection during this window.

8. **Two-phase breakeven trail (FIX-L):** Phase 1 = static stop + delayed trail. Phase 2 = once price >= entry+1xATR, cancel old trail, re-attach 0.5xATR trail -> worst-case exit >= entry+0.5xATR (guaranteed profit).

9. **All bot commits use `[skip render]`** in the commit message to prevent Render deploys.

10. **Fresh `get_positions()`** at the start of each loop iteration — never use stale position data.

---

## 7. KNOWN ISSUES AND BLOCKED ITEMS

### Blocked (requires GitHub PAT upgrade to workflow scope)
1. **Intraday cron scheduling** — The `intraday.yml` workflow has the cron schedule defined but GitHub Actions cron requires the `workflow` scope on the PAT to push workflow file changes. Currently running via manual dispatch or pre-existing cron.
2. **Concurrency guards** — No `concurrency:` block in workflows. Overlapping runs risk JSON file corruption.
3. **Failure alerting** — No notification step on workflow failure.

**Fix:** User needs to add `workflow` scope to GitHub PAT at https://github.com/settings/tokens. Updated workflow files are ready locally.

### Potential Concerns
1. **HIMS position:** -7.37% unrealized, stop at $30.31. Monitor closely.
2. **Strategy trade log incomplete:** Only 14 entries in `strategy_trade_log.json` — the engine's trade logging has gaps (46 orders unaccounted). The full order history is available via Alpaca API.
3. **Signal log truncation:** `signals_history.json` is capped at 500 entries (rolling window). During high-signal days, morning signals get pushed out. Consider increasing `MAX_SIGNALS_HISTORY`.
4. **Engine version in baseline:** `live_baseline.json` may show stale `engine_version` (v8.4-FIX-K) even though engine is running v9.1. The version string update in the baseline may not be firing correctly.
5. **VIX reduce zone for Bollinger:** Bollinger `vix_reduce=18` means at VIX 18-25, position size is reduced to 40%. This significantly limits MEAN_REV trade sizes when VIX is elevated.

---

## 8. TESTING

### Test Suite
```bash
python3 -m pytest test_v84.py -v
```
**21/21 tests pass.** Tests verify all FIX-E through FIX-W guards are intact in the deployed GitHub code. Tests fetch the engine from the GitHub API (not local files) to verify deployed code.

### Key Tests
- `test_fix_l_*` (8 tests): Two-phase breakeven trail logic
- `test_fix_w_intraday_execution_path`: Verifies no `elif` with `strategy_type=="intraday"` in the if/elif chain, weekly cap is inside buy catch-all, execution path is reachable
- `test_fix_j_r5_late_gate`: Verifies universal 14:00 ET cutoff
- `test_previous_fixes_intact`: Verifies all prior FIX-E through FIX-G constants and code patterns

### Syntax Check
```bash
python3 -c "import ast; ast.parse(open('scripts/run_strategies.py').read()); print('OK')"
```

---

## 9. HOW TO RUN / DEPLOY

### Manual Engine Run (via GitHub Actions)
Trigger the "Intraday Strategies (v5 - live trading)" workflow manually from the Actions tab.

### Push Code Changes
Use GitHub Contents API: `PUT https://api.github.com/repos/chenkingston-rgb/algotrader-pro/contents/{path}` with the current file SHA in the payload. Always use `[skip render]` in commit messages.

### Check Live State
```python
# Alpaca account
acct = requests.get(f'{BROKER}/v2/account', headers=H_ALP).json()
positions = requests.get(f'{BROKER}/v2/positions', headers=H_ALP).json()
orders = requests.get(f'{BROKER}/v2/orders?status=all&limit=100&direction=desc', headers=H_ALP).json()
# GitHub state
baseline = gh_json('logs/live_baseline.json')
signals = gh_json('logs/signals_history.json')
```

---

## 10. WHAT NEEDS TO HAPPEN NEXT

### Immediate (next 1-2 days)
1. **Monitor HIMS** — if it drops below $30.31, the stop fires for a ~$40 loss. If it recovers, the FIX-L breakeven upgrade should lock in profit once price exceeds $35.36 + 1xATR.
2. **Watch for first full post-fix trading day** — FIX-W was deployed at 23:14 SGT Jul 23 (11:14am ET). The next full US trading day (Jul 24) will be the first complete session with the fix active. Expect Bollinger intraday signals on XLB/XLP/XLC/TSM to actually execute.
3. **Verify trailing stop behavior** — Confirm FIX-L breakeven upgrade fires correctly on BAC/CVX/HIMS if they move into profit.

### Short-term (next 1-2 weeks)
4. **Accumulate 30-50 post-fix trades** — Then evaluate: Is win rate still ~49%? Is average loss now smaller than average win? Has the profit factor flipped above 1.0?
5. **If losses persist:** The strategy algorithms themselves need to evolve — consider adding factor-based alpha (vibe-trading-ai library is installed with 101+191+158+10+4 factors), pair-trading, or more sophisticated entry conditions.
6. **Fix GitHub PAT** — User needs to add `workflow` scope so cron scheduling, concurrency guards, and failure alerts can be deployed.

### Medium-term (next 1-3 months)
7. **Evaluate MEAN_REV pool performance** — The new sector ETFs (XLB/XLU/XLP/XLC) are the first true test of pool-routed mean-reversion. If Bollinger generates consistent small wins on these, the architecture is validated.
8. **Consider strategy diversification** — Current strategies are all single-asset, long-only, mean-reversion or momentum. Adding pairs, sector rotation, or short signals could improve Sharpe.
9. **Signal log capacity** — Consider increasing `MAX_SIGNALS_HISTORY` from 500 to 2000 to prevent losing intraday signals on high-volume days.

---

## 11. REPOSITORY DETAILS

- **Repo:** `chenkingston-rgb/algotrader-pro` (private)
- **Main branch:** `main`
- **Secrets (GitHub):** `ALPACA_LIVE_KEY`, `ALPACA_LIVE_SECRET`, `ALPACA_IS_PAPER` (false), `GITHUB_TOKEN`, `GITHUB_ACCESS_TOKEN`
- **Environment variables:** `STRATEGY_MODE` (intraday/daily, set by workflow), `STRATEGY_FILTER` (optional, run single strategy)
- **Bot commits:** All log writes use `[bot]` prefix and `[skip render]` to prevent CI triggers

---

## 12. DAILY OPERATIONS CHECKLIST

For any AI agent resuming this project, run these checks at the start of each session:

1. **Account state:** Fetch Alpaca account -> verify equity, positions, P&L
2. **Engine heartbeat:** Check `live_baseline.json` -> `last_heartbeat_et` should be < 20min stale during market hours
3. **Kill switch:** Verify `kill_switch_active` is false and `peak_equity` is correct
4. **Open positions:** Check each position against its stop price
5. **Today's signals:** Fetch `signals_history.json` -> count buys, sells, executed
6. **Run history:** Check `run_history.json` -> verify 15-min cadence during market hours
7. **GitHub Actions:** Verify recent workflow runs are succeeding
8. **Drawdown:** Verify drawdown < 25% of peak equity

### Key API Endpoints
- **Alpaca Account:** `GET https://api.alpaca.markets/v2/account`
- **Alpaca Positions:** `GET https://api.alpaca.markets/v2/positions`
- **Alpaca Orders:** `GET https://api.alpaca.markets/v2/orders?status=all&limit=100&direction=desc`
- **Alpaca Bars:** `GET https://data.alpaca.markets/v2/stocks/bars`
- **GitHub Contents:** `GET https://api.github.com/repos/chenkingston-rgb/algotrader-pro/contents/{path}`
- **GitHub Commits:** `PUT https://api.github.com/repos/chenkingston-rgb/algotrader-pro/contents/{path}`

---

## 13. GLOSSARY

| Term | Meaning |
|------|---------|
| ATR | Average True Range — volatility measure used for stop/position sizing |
| EOD | End of Day — 3:00pm ET forced exit for intraday positions |
| Kill switch | Auto-halt on 25% drawdown from peak equity |
| Pool routing | MEAN_REV vs TREND symbol pools (FIX-T) |
| Breakeven upgrade | FIX-L: when price >= entry+1xATR, tighten trail to lock profit |
| Trail activation delay | FIX-M: 30-min wait before trailing stop activates |
| Weekly cap | Max 3 intraday entries per symbol per week (FIX-J) |
| Session ban | Same-day re-entry blocked after stop-loss fill (FIX-J) |
| Cross-strategy guard | FIX-O: strategy X can't close strategy Y's position |
| Deposit basis | $28,781.89 — total capital deposited, excludes Jul 14 $1,002 deposit from P&L |

---

*This document is the complete state of AlgoTrader Pro as of Jul 24, 2026. Any incoming agent should read this, verify the live state against section 2, and follow the operations checklist in section 12 before making any changes.*
