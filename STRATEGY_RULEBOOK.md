# AlgoTrader Pro — Strategy Rulebook

## ⚠️ PERMANENT RULE: Symbol Pool Routing (FIX-T v8.9)

See `docs/SYMBOL_POOL_ROUTING.md` for full details.

**Summary:**
- `scan_symbols.py` produces a TREND pool and a MEAN_REV pool every Sunday.
- `bollinger_bands_15m` and `rsi_mean_reversion_15m` → MEAN_REV pool (`watchlist_meanrev.json`)
- All other strategies → daily/weekly general watchlist
- **NEVER hardcode symbols as primary source** — use the scan pools
- **NEVER merge TREND + MEAN_REV into one list for all strategies**
- Set `vix_type = "MEAN_REV"` on any new mean-reversion strategy to auto-route correctly


> **Living document.** Every logic change to the engine must be recorded here.
> This file is the authoritative source of truth for all strategy rules, parameters,
> and filters. It mirrors the `StrategyRulebook` entity on the Base44 dashboard.
>
> Last updated: 2026-05-28

---

## 1. Global Risk Rules

| Rule | Value | Notes |
|------|-------|-------|
| Risk per trade | **1% of equity** | `RISK_PCT = 0.01` — base dollar risk before multipliers |
| Max position cap | **10% of equity** | `MAX_POSITION_PCT = 0.10` — hard cap regardless of ATR sizing |
| ATR stop-loss | **1.5× ATR(14)** | `ATR_STOP_MULT = 1.5` → `stop = entry − 1.5 × ATR14` |
| Default take-profit | **3.0× ATR(14)** | `ATR_TP_MULT = 3.0` — overridden by VWAP multiplier if available |
| ATR floor | **0.20% of price** | `min_atr = price × 0.002` — prevents penny-width stops on low-vol names |
| Kill switch | **25% drawdown** | `MAX_DRAWDOWN_PCT = 25.0` — halts all new entries for the session |

---

## 2. Market Regime Filters

### 2a. SPY 20-Day MA Regime Filter *(added 2026-05-28)*
- **Rule:** At the start of every run, fetch SPY daily bars. If `SPY_price < SPY_MA20`, regime = BEAR.
- **Effect:** All **BUY** signals blocked across every strategy. SELL/exit signals always pass through.
- **Why:** Momentum strategies fail in downtrends. Backtesting showed regime filter alone is the
  single highest-impact change — stripping phantom returns from a no-filter backtest.

### 2b. ADX Regime Size Scaler (SPY ADX14)
- **Trending** (ADX ≥ 23 + slope > 0): `1.0×` size — full allocation
- **Neutral** (ADX 18–22): `0.75×` size — moderate reduction
- **Sideways** (ADX < 18): `0.50×` size — half allocation
- Cached every 30 minutes. Stacks multiplicatively with VIX and RSI(2) multipliers.

---

## 3. Symbol Routing — Pool-Aware Watchlist *(added 2026-05-28)*

The scanner classifies each symbol as **TREND** or **MEAN_REV**. The engine routes
each symbol to ONLY the strategy designed for that market character.

| Strategy | Pool | Criteria |
|----------|------|----------|
| `momentum_roc_15m` | TREND | ADX > 22 (weekly) or gap score ≥ 3.0 (daily) |
| `bollinger_bands_15m` | MEAN_REV | ADX 10–22 (weekly) or gap score < 3.0 (daily) |
| `macd_crossover` | TREND | same as momentum |
| `ema_crossover` | TREND | same as momentum |
| `triple_ema` | TREND | same as momentum |
| `rsi_macd_combo` | MEAN_REV | same as Bollinger |

**Open positions are always prepended to both pools** to ensure exit signals are
never skipped for symbols that dropped off the current watchlist.

---

## 4. VIX Filters

| Strategy | Block (size=0) | Reduce | Reduced size |
|----------|---------------|--------|-------------|
| `momentum_roc_15m` | VIX ≥ 35 | VIX 25–35 | 50% |
| `bollinger_bands_15m` | VIX ≥ 22 | VIX 18–22 | 40% |
| `macd_crossover` / `triple_ema` / `ema_crossover` | VIX ≥ 45 | VIX 35–45 | 60% |
| `rsi_macd_combo` | VIX ≥ 30 | VIX 22–30 | 50% |

---

## 5. Position Sizing Multipliers

Final size = `atr_position_size(equity, price, atr) × vix_mult × regime_mult × rsi2_mult`

### RSI(2) Multiplier (SPY short-term mean reversion)
| SPY RSI(2) | Multiplier | Rationale |
|-----------|-----------|-----------|
| < 10 | **1.3×** | Extremely oversold — bounce likely, size up |
| 10–30 | **1.1×** | Mildly oversold |
| 30–70 | **1.0×** | Neutral |
| 70–90 | **0.85×** | Mildly overbought — reduce |
| > 90 | **0.70×** | Extremely overbought — reduce significantly |

### VWAP Take-Profit Multiplier
| Price vs VWAP | TP Multiplier |
|--------------|--------------|
| > 0.5% above VWAP | **3.0×** ATR — extend target, room to run |
| Within ±0.5% of VWAP | **2.5×** ATR — normal target |
| > 0.5% below VWAP | **2.0×** ATR — conservative, resistance near |

---

## 6. Strategy Signal Logic

### 6a. `bollinger_bands_15m` — Mean Reversion (15-min bars)
**Entry:** `price < BB_lower(20, 2σ)` **AND** `price > MA50` **AND** MA50 is valid  
**Exit:** `price > BB_upper(20, 2σ)`  
**Params:** `bb_period=20, bb_std=2.0, ma_filter=50`

> **FIX-1 (2026-05-26):** Added MA50 filter to entry — prevents entering falling knives
> in downtrends. Original code also had a NaN-MA bug that triggered phantom entries.
>
> **FIX-2 (2026-05-26):** Exit raised from midband (MA20) to upper band — original
> code cut winners the moment price recovered to its mean, leaving half the profit on the table.

---

### 6b. `momentum_roc_15m` — Momentum (15-min bars)
**Entry:** `ROC(10) > 0.3%` **AND** `ROC > prev_ROC` (accelerating)  
    + `price > MA50` AND `MA50 sloping up` (trend filter)  
    + `current_volume ≥ 1.0 × avg_volume(20)` (volume confirmation)  
**Exit:** `ROC(10) < -0.3%` AND `ROC < prev_ROC` AND `MA50 sloping down`  
**Params:** `roc_period=10, roc_threshold=0.3, trend_ma=50, vol_ma=20, vol_threshold=1.0`

> **v6 ROC logic (2026-05-26):** Changed from zero-crossing to sustained-momentum.
> Old: buy only when ROC crosses threshold from below. New: buy when ROC is above
> threshold AND accelerating — catches continuation moves, not just initial crosses.
>
> **FIX-A (2026-05-27):** Trend MA filter — blocks counter-trend entries. RKLB
> triggered on a single bar spike while the trend was flat/down — this blocks it.
>
> **FIX-B (2026-05-27):** Volume confirmation — filters one-bar spikes with no
> institutional participation. SMCI and RKLB both triggered on below-average volume
> and immediately reversed. Sells are NOT volume-gated.

---

### 6c. `macd_crossover` — Daily Trend (1Day bars)
**Entry:** MACD histogram crosses from negative → positive  
**Exit:** Histogram crosses from positive → negative  
**Params:** `macd_fast=12, macd_slow=26, macd_sig=9`

---

### 6d. `triple_ema` — Daily Trend (1Day bars)
**Entry:** First bar where EMA(8) > EMA(21) > EMA(55) — all three aligned bullish  
**Exit:** First bar where EMA(8) < EMA(21) < EMA(55) — all three aligned bearish  
**Params:** `ema_fast=8, ema_mid=21, ema_slow=55`  
Note: Only fires on the FIRST alignment bar — not on continuation.

---

### 6e. `ema_crossover` — Daily Trend (1Day bars)
**Entry:** EMA(12) crosses above EMA(26) (diff goes negative → positive)  
**Exit:** EMA(12) crosses below EMA(26)  
**Params:** `ema_fast=12, ema_slow=26`

---

### 6f. `rsi_macd_combo` — Daily Mean Reversion (1Day bars)
**Entry:** `RSI(14) < 35` AND MACD histogram just turned positive  
**Exit:** `RSI(14) > 65` AND histogram just turned negative  
**Params:** `rsi_period=14, rsi_os=35, rsi_ob=65, macd 12/26/9`  
Note: Both RSI and MACD conditions required together — RSI alone stays oversold too long.

---

## 7. Exit Logic — Critical Asymmetry

**Sell signals are NEVER blocked by regime, VIX, volume, or trend filters.**

This is intentional. If the regime turns bearish, the system stops entering new longs
but does NOT prevent exiting existing positions. The only partial exception is
`momentum_roc_15m` sell signal, which requires `trend_ok_short` (MA50 sloping down)
— but this is a quality filter on the exit, not a block.

Being trapped in a position is always worse than a missed exit.

---

## 8. Weekly Scanner — Scan Universe

The scanner runs every Sunday ~8PM ET across ~120 liquid US equities.

**Hard filters (all symbols):**
- Price: $5 – $2,000
- 30-day avg volume: > 300,000 shares
- Within 30% of 52-week high
- 12-week return > -15%

**TREND pool** (top 15): ADX > 20, score = 0.40×ret_4w + 0.40×ret_12w + 0.20×ADX  
**MEAN_REV pool** (top 8): ADX 10–22, ret_4w −8% to +8%, score by price stability + SMA20 proximity

**Core symbols** (always included, never scored out): `SPY, QQQ, IWM, GLD, XLK, XLE, XLF`

---

## 9. Change Log

| Date | Change | Commit |
|------|--------|--------|
| 2026-05-26 | v6 engine: Bollinger exit at upper band, ROC sustained logic, ATR floor | multiple |
| 2026-05-27 | momentum_roc: trend MA filter + volume confirmation (FIX-A, FIX-B) | f326ae3 |
| 2026-05-28 | Pool-aware symbol routing (scanner pools → strategy routing) | b2d67f0 |
| 2026-05-28 | Market regime filter: SPY 20-day MA blocks all buys in bear market | f12687b |
| 2026-05-28 | Strategy Rulebook entity created on Base44 dashboard | — |
