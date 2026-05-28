"""
seed_rulebook.py — Creates StrategyRulebook entity schema in AlgoTrader Pro
and seeds all 31 strategy rules via the Base44 REST API.
"""
import os, requests, time, sys

TOKEN  = os.environ.get("BASE44_API_KEY") or os.environ.get("BASE44_SERVICE_TOKEN") or ""
APP_ID = os.environ.get("BASE44_APP_ID", "69f60c0cd56ea2902b494394")
BASE   = f"https://app.base44.com/api/apps/{APP_ID}/entities/StrategyRuleBook"

hdrs = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type":  "application/json",
}

RULES = [
    {"category":"Global Risk","rule_name":"Risk per trade","status":"Active","version_added":"v5",
     "description":"Risk exactly 1% of total portfolio equity per trade. Base dollar risk before any multipliers (VIX, regime, RSI2) are applied.",
     "implementation":"RISK_PCT = 0.01. dollar_risk = equity x 0.01 x vix_mult",
     "rationale":"Fixed fractional risk ensures no single trade can materially damage the portfolio regardless of position size or price."},
    {"category":"Global Risk","rule_name":"Max position cap","status":"Active","version_added":"v5",
     "description":"No single position may exceed 10% of total portfolio equity, regardless of ATR sizing.",
     "implementation":"MAX_POSITION_PCT = 0.10. max_by_cap = (equity x 0.10) / price. Final qty = min(shares_by_risk, max_by_cap)",
     "rationale":"Hard cap prevents concentration risk in any one name, especially when ATR is very low."},
    {"category":"Global Risk","rule_name":"ATR stop-loss multiplier","status":"Active","version_added":"v5",
     "description":"Stop-loss placed at 1.5x ATR(14) below entry on every buy.",
     "implementation":"ATR_STOP_MULT = 1.5. stop_price = entry - (1.5 x ATR14)",
     "rationale":"ATR-based stops self-adjust to each symbol's volatility. Fixed stops would be too tight on volatile names and too loose on calm ones."},
    {"category":"Global Risk","rule_name":"Default take-profit multiplier","status":"Active","version_added":"v5",
     "description":"Default take-profit at 3.0x ATR(14) above entry. Overridden dynamically by the VWAP TP multiplier if VWAP is available.",
     "implementation":"ATR_TP_MULT = 3.0. tp_price = entry + (3.0 x ATR14). Overridden by get_tp_multiplier() if VWAP available.",
     "rationale":"3:1 reward/risk ratio as baseline. VWAP override provides context-aware targets."},
    {"category":"Global Risk","rule_name":"ATR floor (minimum stop distance)","status":"Active","version_added":"v6 / 2026-05-26",
     "description":"ATR floored at 0.20% of price before computing stop/TP distances. Prevents absurdly tight stops on low-volatility windows.",
     "implementation":"min_atr = price x 0.0020. eff_atr = max(atr, min_atr). Applied in both main loop and _run_streaming_strategy.",
     "rationale":"FIX-4: 15-min ATR on low-vol periods (e.g. GLD at open) could compress to <$1 on a $400 stock. The 0.20% floor prevents penny-width stops."},
    {"category":"Global Risk","rule_name":"Kill switch — max drawdown","status":"Active","version_added":"v5",
     "description":"If portfolio drawdown from peak reaches 25%, engine halts all new entries for the session.",
     "implementation":"MAX_DRAWDOWN_PCT = 25.0. drawdown_pct = (equity - peak_equity) / peak_equity x 100. Checked each run.",
     "rationale":"Catastrophic loss prevention. Losses capped at 25% before the engine can dig deeper if something goes systematically wrong."},
    {"category":"Market Regime","rule_name":"SPY 20-day MA regime filter","status":"Active","version_added":"2026-05-28",
     "description":"At the start of every run, SPY is checked against its 20-day MA. If SPY price < MA20, regime = BEAR. ALL new BUY signals blocked. Sell/exit signals always pass through.",
     "implementation":"spy_ma20 = SPY.rolling(20).mean(). regime_ok = spy_price > spy_ma20. Sets REGIME_OK env var to 1 or 0.",
     "rationale":"Backtest showed regime filter is the single highest-impact change. Momentum strategies fail in downtrends. Exits are never blocked to avoid being trapped in losing positions."},
    {"category":"Market Regime","rule_name":"ADX regime size scaler (SPY)","status":"Active","version_added":"v5",
     "description":"Position size scaled by SPY ADX(14). Trending (ADX>=23 + rising) = 1.0x. Neutral = 0.75x. Sideways (ADX<18) = 0.50x. Stacks with VIX and RSI(2) multipliers.",
     "implementation":"get_regime_size_multiplier(): ADX>=23 + slope>0 -> TRENDING -> 1.0x. ADX<18 -> SIDEWAYS -> 0.5x. else -> NEUTRAL -> 0.75x. Cached every 30 min.",
     "rationale":"Reduces exposure automatically during choppy conditions where momentum signals have lower hit rates."},
    {"category":"Symbol Routing","rule_name":"Pool-aware watchlist routing","status":"Active","version_added":"2026-05-28",
     "description":"Scanner tags each symbol TREND or MEAN_REV. momentum_roc_15m only sees TREND symbols; bollinger_bands_15m only sees MEAN_REV symbols. Dynamic — changes every day/week.",
     "implementation":"POOL_MAP = {momentum_roc_15m: trend, bollinger_bands_15m: mean_rev}. Intraday: score>=3.0 -> TREND. Weekly: ADX>22 -> TREND, ADX 10-22 -> MEAN_REV.",
     "rationale":"Without routing, both strategies received the same list. Momentum entered ranging stocks; Bollinger tried to mean-revert trending ones."},
    {"category":"Symbol Routing","rule_name":"Open position exit priority","status":"Active","version_added":"v5",
     "description":"Symbols in open positions are ALWAYS prepended to every strategy's symbol list, even if dropped from the current watchlist.",
     "implementation":"position_symbols = list(positions.keys()). merged = list(dict.fromkeys(position_symbols + pool_syms)). Applied to every strategy pool.",
     "rationale":"A weekly scan might drop a symbol. Without this rule, the engine would never generate an exit signal, leaving a losing position open indefinitely."},
    {"category":"Watchlist / Scanner","rule_name":"Weekly scanner — TREND pool criteria","status":"Active","version_added":"v2 scanner",
     "description":"Symbols qualify for TREND pool if: hard filters passed AND ADX(14) > 20. Ranked by composite score. Top 15 picks.",
     "implementation":"score_trend(): price $5-$2000, avg_vol_30d > 300K, within 30pct of 52w high, ret_12w > -15pct, ADX14 > 20. Score = 0.40xret_4w + 0.40xret_12w + 0.20xADX.",
     "rationale":"ADX > 20 confirms a genuine directional trend. 4w and 12w returns confirm sustained relative strength, not just a one-day spike."},
    {"category":"Watchlist / Scanner","rule_name":"Weekly scanner — MEAN_REV pool criteria","status":"Active","version_added":"v2 scanner",
     "description":"Symbols qualify for MEAN_REV pool if: ADX 10-22, 4-week return between -8% and +8%. Ranked by price stability + proximity to SMA20. Top 8 picks.",
     "implementation":"score_mean_rev(): hard filters + ADX 10-22 + ret_4w between -8pct and +8pct. Score = 0.60x(100/realized_vol_5d) + 0.40x(1 - abs(price/SMA20 - 1)x10).",
     "rationale":"Bollinger Bands mean reversion works best on stocks in a defined range. ADX < 22 and flat 4-week return confirms oscillation rather than breakout."},
    {"category":"Watchlist / Scanner","rule_name":"Daily scanner — intraday gap scoring","status":"Active","version_added":"v2 scanner",
     "description":"Each morning, symbols scored by pre-market gap and volume ratio. Score >= 3.0 -> TREND pool (momentum). Score < 3.0 -> MEAN_REV pool (Bollinger).",
     "implementation":"score_daily(): score = abs(gap_pct)x0.6 + (pm_vol/avg_daily_vol x 100)x0.4. Minimum: abs(gap_pct) >= 0.10 OR pm_vol_ratio >= 0.005. TREND threshold: score >= 3.0.",
     "rationale":"Pre-market gap + elevated volume is the strongest intraday signal of institutional-driven momentum. Low-activity symbols suit mean reversion."},
    {"category":"VIX Filters","rule_name":"VIX block — MOMENTUM strategies","status":"Active","version_added":"v5",
     "description":"momentum_roc_15m blocked entirely at VIX >= 35. Size reduced to 50% when VIX 25-35.",
     "implementation":"vix_block=35, vix_reduce=25, vix_reduce_pct=0.50.",
     "rationale":"Momentum signals in VIX > 35 environments are dominated by noise — directional bias is unreliable in extreme market fear."},
    {"category":"VIX Filters","rule_name":"VIX block — MEAN_REV strategies","status":"Active","version_added":"v5",
     "description":"bollinger_bands_15m blocked entirely at VIX >= 22. Size reduced to 40% when VIX 18-22.",
     "implementation":"vix_block=22, vix_reduce=18, vix_reduce_pct=0.40.",
     "rationale":"Mean reversion assumes price returns to a stable mean. At VIX >= 22, bands expand dramatically and the mean-reversion assumption breaks down."},
    {"category":"VIX Filters","rule_name":"VIX block — TREND/DAILY strategies","status":"Active","version_added":"v5",
     "description":"Daily trend strategies block at VIX >= 45, reduce at VIX >= 35 (60% size). RSI+MACD combo blocks at VIX >= 30, reduces at VIX >= 22.",
     "implementation":"macd_crossover/triple_ema/ema_crossover: vix_block=45, vix_reduce=35, vix_reduce_pct=0.60. rsi_macd_combo: vix_block=30, vix_reduce=22, vix_reduce_pct=0.50.",
     "rationale":"Daily trend strategies are slower and more forgiving of short-term volatility, so they use higher VIX thresholds than intraday strategies."},
    {"category":"Position Sizing","rule_name":"RSI(2) position size multiplier","status":"Active","version_added":"v5",
     "description":"Position size scaled by SPY RSI(2). RSI2 < 10 = 1.3x (oversold). RSI2 > 90 = 0.7x (overbought). Mid-range = 1.0x.",
     "implementation":"get_rsi2_size_multiplier(): RSI2<10->1.3x. RSI2<30->1.1x. RSI2>90->0.7x. RSI2>70->0.85x. else->1.0x. Stacks with VIX and regime multipliers.",
     "rationale":"RSI(2) on SPY is a fast short-term mean reversion indicator. Extremely oversold = size up. Extremely overbought = reduce before pullback."},
    {"category":"Position Sizing","rule_name":"VWAP take-profit multiplier","status":"Active","version_added":"v5",
     "description":"Take-profit dynamically scaled by distance from intraday VWAP. Far above VWAP = 3.0x ATR. Near VWAP = 2.5x. Below VWAP = 2.0x.",
     "implementation":"get_tp_multiplier(price, vwap): price/vwap > 1.005 -> 3.0x. 0.995-1.005 -> 2.5x. < 0.995 -> 2.0x. Returns 3.0 if VWAP unavailable.",
     "rationale":"Entries well above VWAP have more room to run. Entries near or below VWAP face resistance sooner — take profits earlier."},
    {"category":"Strategy: bollinger_bands_15m","rule_name":"Entry condition","status":"Active","version_added":"v6 / 2026-05-26",
     "description":"Buy when: price < lower Bollinger Band (20-period, 2-sigma) AND price is above the 50-bar MA. Both conditions required simultaneously.",
     "implementation":"signal_bollinger_bands_15m(): price < bb_lower AND price > MA50 AND MA50 is valid (not NaN). Params: bb_period=20, bb_std=2.0, ma_filter=50.",
     "rationale":"FIX-1: Added MA50 filter to ensure we only buy oversold dips within an uptrend — not falling knives. Also guards against NaN MA triggering phantom entries."},
    {"category":"Strategy: bollinger_bands_15m","rule_name":"Exit condition","status":"Active","version_added":"v6 / 2026-05-26",
     "description":"Sell when price crosses above the UPPER Bollinger Band (not the midband).",
     "implementation":"price > bb_upper -> sell. bb_upper = MA20 + 2.0 x std(20).",
     "rationale":"FIX-2: Original logic exited at the midband (MA20), cutting winners in half. Full mean-reversion target is the upper band — changed to let winners run to completion."},
    {"category":"Strategy: bollinger_bands_15m","rule_name":"Symbol universe","status":"Active","version_added":"2026-05-28",
     "description":"Only trades symbols tagged MEAN_REV by scanner (ADX 10-22, flat 4-week return). Core ETFs IWM, GLD, XLK, XLE, XLF always included.",
     "implementation":"POOL_MAP[bollinger_bands_15m] = mean_rev. Intraday: scanner score < 3.0. Weekly: ADX 10-22.",
     "rationale":"Bollinger mean reversion breaks down on trending stocks. Restricting to ranging/low-ADX symbols improves signal quality."},
    {"category":"Strategy: momentum_roc_15m","rule_name":"Core ROC signal — sustained momentum","status":"Active","version_added":"v6 / 2026-05-26",
     "description":"Buy when ROC(10) > 0.3% AND accelerating (ROC > prev ROC). Sell when ROC < -0.3% AND decelerating. Both direction AND acceleration must agree.",
     "implementation":"roc = (close / close.shift(10) - 1) x 100. Buy: r > 0.3 AND r > prev_r. Sell: r < -0.3 AND r < prev_r.",
     "rationale":"v6: Changed from zero-crossing to sustained-momentum. Old logic missed continuation moves on volatile names (IONQ, AMD) where ROC stays above threshold across bars."},
    {"category":"Strategy: momentum_roc_15m","rule_name":"Trend MA entry filter","status":"Active","version_added":"2026-05-27",
     "description":"Buy signals only execute if price > 50-bar MA AND the MA is sloping upward. Sell signals require MA sloping downward.",
     "implementation":"trend_ok_long = (price > MA50) AND (MA50_now > MA50_prev). Buy blocked if not trend_ok_long -> skip_reason=trend_filter_long.",
     "rationale":"FIX-A: Prevents counter-trend entries. RKLB spiked one bar while trend was flat/down on 2026-05-27 — direct cause of that day's losses."},
    {"category":"Strategy: momentum_roc_15m","rule_name":"Volume confirmation filter","status":"Active","version_added":"2026-05-27",
     "description":"Buy signals require current bar volume >= 1.0x the 20-bar average volume. Exits (sells) are never volume-gated.",
     "implementation":"vol_ok = cur_vol >= 1.0 x avg_vol(20). Buy blocked if not vol_ok -> skip_reason=vol_filter_long. Params: vol_ma=20, vol_threshold=1.0.",
     "rationale":"FIX-B: SMCI and RKLB triggered on below-average volume and immediately reversed on 2026-05-27. Volume >= average confirms genuine institutional participation."},
    {"category":"Strategy: momentum_roc_15m","rule_name":"Symbol universe","status":"Active","version_added":"2026-05-28",
     "description":"Only trades TREND-pool symbols (ADX > 22 weekly, or gap score >= 3.0 intraday). Core ETFs SPY, QQQ always included.",
     "implementation":"POOL_MAP[momentum_roc_15m] = trend. Intraday: scanner score >= 3.0. Weekly: ADX > 22.",
     "rationale":"Momentum requires a confirmed trend. Low-ADX ranging symbols produce false ROC signals — price oscillates without directional follow-through."},
    {"category":"Strategy: macd_crossover","rule_name":"Signal logic","status":"Active","version_added":"v5",
     "description":"Buy when MACD histogram crosses from negative to positive. Sell when crosses from positive to negative. Timeframe: 1Day.",
     "implementation":"hist > 0 AND prev_hist <= 0 -> buy. hist < 0 AND prev_hist >= 0 -> sell. Params: macd_fast=12, macd_slow=26, macd_sig=9.",
     "rationale":"Histogram zero-cross (not the MACD line cross) is faster and reduces lag. One of the most reliable daily momentum signals."},
    {"category":"Strategy: triple_ema","rule_name":"Signal logic","status":"Active","version_added":"v5",
     "description":"Buy on FIRST bar where EMA(8) > EMA(21) > EMA(55) all aligned bullish. Sell on first full bearish alignment. Not on continuation bars.",
     "implementation":"buy if f > m > s AND NOT (pf > pm > ps). Sell if f < m < s AND NOT (pf < pm < ps). Params: ema_fast=8, ema_mid=21, ema_slow=55. Timeframe: 1Day.",
     "rationale":"Three-EMA alignment is high-conviction trend confirmation. First-bar-only constraint avoids re-entering an already-running trend mid-move."},
    {"category":"Strategy: ema_crossover","rule_name":"Signal logic","status":"Active","version_added":"v5",
     "description":"Buy when EMA(12) crosses above EMA(26). Sell on reverse cross. Timeframe: 1Day.",
     "implementation":"diff = EMA12 - EMA26. Buy: diff > 0 AND prev_diff <= 0. Sell: diff < 0 AND prev_diff >= 0.",
     "rationale":"Simple dual-EMA crossover on daily bars. Classic trend-following entry used as daily regime confirmation."},
    {"category":"Strategy: rsi_macd_combo","rule_name":"Signal logic","status":"Active","version_added":"v5",
     "description":"Buy when RSI(14) < 35 AND MACD histogram just turned positive. Sell when RSI > 65 AND histogram just turned negative. BOTH conditions required simultaneously.",
     "implementation":"buy: rsi < 35 AND hist > 0 AND prev_hist < 0. sell: rsi > 65 AND hist < 0 AND prev_hist > 0. Params: rsi_period=14, macd 12/26/9. Timeframe: 1Day.",
     "rationale":"RSI alone stays oversold for weeks in downtrends. MACD histogram cross as timing trigger ensures momentum has actually reversed before entering."},
    {"category":"Exit Logic","rule_name":"Sell signals bypass all buy-side gates","status":"Active","version_added":"2026-05-28",
     "description":"Sell/exit signals are NEVER blocked by regime filter, volume filter, or trend filter. Being trapped in a position is always worse than a missed exit.",
     "implementation":"REGIME_OK=0 only blocks signal==buy. Volume filter only applied to buys. Bollinger exit fires regardless of regime.",
     "rationale":"Intentional asymmetry. All exit gates are one-sided. The regime turns off new entries — it never traps us in existing losing positions."},
    {"category":"Exit Logic","rule_name":"Position exit when dropped from watchlist","status":"Active","version_added":"v5",
     "description":"If a symbol is held but no longer on the current watchlist, it is still processed for exit signals every run.",
     "implementation":"dropped = [s for s in position_symbols if s not in pools_all]. Prepended to all strategy symbol lists.",
     "rationale":"Weekly scanner refreshes could drop underperforming symbols. Without this rule, the position would be held forever with no exit signal generated."},
]

print(f"Seeding {len(RULES)} rules into AlgoTrader Pro StrategyRulebook...")
print(f"App ID: {APP_ID}")
print(f"Token present: {bool(TOKEN)}")
print()

# Check if entity already has records
check = requests.get(BASE, headers=hdrs, timeout=10)
print(f"Entity check: {check.status_code}")
if check.ok:
    existing = check.json()
    if isinstance(existing, list) and len(existing) > 0:
        print(f"Entity already has {len(existing)} records — skipping seed.")
        sys.exit(0)

created = 0
failed  = 0
for rule in RULES:
    r = requests.post(BASE, headers=hdrs, json=rule, timeout=10)
    name = rule["rule_name"]
    if r.ok:
        created += 1
        print(f"  OK  {name[:55]}")
    else:
        failed += 1
        print(f"  ERR {name[:55]} — {r.status_code} {r.text[:80]}")
    time.sleep(0.05)

print()
print(f"Done: {created} created, {failed} failed")
if failed > 0:
    sys.exit(1)
