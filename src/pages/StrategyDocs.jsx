import { useState } from 'react';
import {
  BookOpen, Zap, Shield, Clock, TrendingUp, TrendingDown, BarChart2,
  Activity, AlertTriangle, CheckCircle, Info, ChevronDown, ChevronRight,
  GitCommit, Settings, Target, Filter, Layers
} from 'lucide-react';
import { cn } from '@/lib/utils';

/* ─── tiny primitives ───────────────────────────────────────────────────── */

function Badge({ children, variant = 'default' }) {
  const cls = {
    default:  'bg-muted text-muted-foreground',
    primary:  'bg-primary/15 text-primary border border-primary/30',
    success:  'bg-emerald-500/15 text-emerald-500 border border-emerald-500/30',
    warning:  'bg-yellow-500/15 text-yellow-600 dark:text-yellow-400 border border-yellow-500/30',
    danger:   'bg-destructive/15 text-destructive border border-destructive/30',
    purple:   'bg-purple-500/15 text-purple-500 border border-purple-500/30',
    blue:     'bg-blue-500/15 text-blue-500 border border-blue-500/30',
  };
  return (
    <span className={cn('inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-semibold', cls[variant])}>
      {children}
    </span>
  );
}

function SectionHeader({ icon: Icon, title, subtitle, color = 'text-primary' }) {
  return (
    <div className="flex items-start gap-3 mb-4">
      <div className={cn('mt-0.5 p-2 rounded-lg bg-card border', color)}>
        <Icon className="w-4 h-4" />
      </div>
      <div>
        <h2 className="text-base font-bold text-foreground">{title}</h2>
        {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
      </div>
    </div>
  );
}

function Collapsible({ title, badge, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-border rounded-lg overflow-hidden mb-3">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-3 bg-card hover:bg-accent/50 transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-foreground font-mono">{title}</span>
          {badge && badge}
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-4 pb-4 pt-2 bg-card/50 border-t border-border">{children}</div>}
    </div>
  );
}

function ParamRow({ label, value, note }) {
  return (
    <div className="flex items-start justify-between py-1.5 border-b border-border/50 last:border-0">
      <span className="text-xs font-mono text-muted-foreground w-44 shrink-0">{label}</span>
      <span className="text-xs font-mono text-foreground font-semibold text-right">{value}</span>
      {note && <span className="text-xs text-muted-foreground ml-3 text-right max-w-xs hidden md:block">{note}</span>}
    </div>
  );
}

function GateRow({ order, gate, trigger, reason, source }) {
  return (
    <div className="flex items-start gap-3 py-2 border-b border-border/50 last:border-0">
      <span className="text-xs font-mono text-muted-foreground w-5 shrink-0">{order}.</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono font-bold text-foreground">{gate}</span>
          {source && <Badge variant="purple">{source}</Badge>}
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">{trigger}</p>
        <p className="text-xs text-muted-foreground/70 mt-0.5 italic">{reason}</p>
      </div>
    </div>
  );
}

function ChangelogRow({ version, fix, date, summary, commits = [] }) {
  return (
    <div className="flex gap-3 pb-4 relative">
      <div className="flex flex-col items-center">
        <div className="w-2 h-2 rounded-full bg-primary mt-1.5 shrink-0" />
        <div className="w-px flex-1 bg-border/50 mt-1" />
      </div>
      <div className="pb-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono font-bold text-foreground">{version}</span>
          <Badge variant="primary">{fix}</Badge>
          <span className="text-xs text-muted-foreground">{date}</span>
        </div>
        <p className="text-xs text-muted-foreground mt-1">{summary}</p>
        {commits.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {commits.map(c => (
              <span key={c} className="text-xs font-mono bg-muted px-1.5 py-0.5 rounded text-muted-foreground">
                <GitCommit className="inline w-3 h-3 mr-0.5" />{c}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── Main ──────────────────────────────────────────────────────────────── */

const TABS = ['Strategies', 'Entry Gates', 'Risk & Exits', 'Factors', 'Changelog'];

export default function StrategyDocs() {
  const [tab, setTab] = useState('Strategies');

  return (
    <div className="min-h-screen p-4 md:p-6 max-w-5xl mx-auto space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-primary" />
            AlgoTrader Pro — Strategy & Logic Documentation
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Engine <span className="font-mono">v8.4-FIX-K</span> · Live since Jun 5, 2026 ·
            <span className="font-mono ml-1">scripts/run_strategies.py</span>
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Badge variant="success">Live Trading</Badge>
          <Badge variant="primary">Alpaca Broker</Badge>
          <Badge variant="default">GitHub Actions CI</Badge>
        </div>
      </div>

      {/* Tab nav */}
      <div className="flex gap-1 bg-muted p-1 rounded-lg w-fit flex-wrap">
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              'px-3 py-1.5 rounded-md text-xs font-semibold font-mono transition-colors',
              tab === t ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {/* ── STRATEGIES TAB ────────────────────────────────────────────── */}
      {tab === 'Strategies' && (
        <div className="space-y-4">
          <SectionHeader icon={Layers} title="Active Strategy Universe" subtitle="4 daily strategies + 2 intraday strategies running in parallel" />

          {/* DAILY */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-mono font-bold text-muted-foreground uppercase tracking-wider">Daily Strategies (1-Day bars, 300-day lookback)</span>
              <Badge variant="blue">Swing / Positional</Badge>
            </div>

            <Collapsible title="rsi_macd_combo" badge={<Badge variant="success">SPY · QQQ · IWM</Badge>} defaultOpen>
              <p className="text-xs text-muted-foreground mb-3">
                Combines RSI oversold confirmation with MACD bullish crossover. Requires both signals to agree — RSI below 35 (oversold zone) AND MACD histogram turning positive. Designed to catch mean-reversion bounces in broad market ETFs after pullbacks.
              </p>
              <div className="grid grid-cols-2 gap-x-6">
                <div>
                  <ParamRow label="RSI period" value="14" note="Wilder smoothing" />
                  <ParamRow label="RSI oversold" value="35" note="Entry threshold" />
                  <ParamRow label="RSI overbought" value="65" note="Exit signal" />
                </div>
                <div>
                  <ParamRow label="MACD fast" value="12" />
                  <ParamRow label="MACD slow" value="26" />
                  <ParamRow label="MACD signal" value="9" />
                </div>
              </div>
              <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
                <strong className="text-foreground">Guard:</strong> <code className="font-mono">strev_overbought</code> blocks entry when 21-day return &gt; +10% (Jegadeesh 1990 short-term reversal). VIX block at 28, size reduction at VIX 20 (50% size).
              </div>
            </Collapsible>

            <Collapsible title="macd_crossover" badge={<Badge variant="success">SPY · QQQ · IWM</Badge>}>
              <p className="text-xs text-muted-foreground mb-3">
                Pure MACD bullish crossover — MACD line crosses above the signal line from below. Trend-following, does not require RSI confirmation. Signal must persist for 3 consecutive bars before entry to avoid cron-timing false fires.
              </p>
              <ParamRow label="MACD fast / slow / sig" value="12 / 26 / 9" />
              <ParamRow label="Signal persistence" value="3 bars" note="FIX-G: prevents 15min cron misses" />
              <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
                <strong className="text-foreground">Guards:</strong> <code className="font-mono">MA200</code> (price must be above 200-day MA), <code className="font-mono">imax20_exhaustion</code> (20-bar high recency &gt; 75% → bull-trap block). VIX block at 28.
              </div>
            </Collapsible>

            <Collapsible title="triple_ema" badge={<Badge variant="success">SPY · QQQ</Badge>}>
              <p className="text-xs text-muted-foreground mb-3">
                Triple EMA alignment strategy — all three EMAs must be in bullish stack: EMA-8 &gt; EMA-21 &gt; EMA-55. Designed for trend continuation. Only fires when all three agree, reducing false positives during choppy markets.
              </p>
              <ParamRow label="EMA fast" value="8" />
              <ParamRow label="EMA mid" value="21" />
              <ParamRow label="EMA slow" value="55" />
              <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
                <strong className="text-foreground">Guards:</strong> <code className="font-mono">MA200</code> (price above 200-day MA required), <code className="font-mono">imax20_exhaustion</code> (bull-trap guard), <code className="font-mono">cord60</code> (volume-price correlation advisory). VIX block at 28.
              </div>
            </Collapsible>

            <Collapsible title="ema_crossover" badge={<Badge variant="success">SPY · QQQ · IWM</Badge>}>
              <p className="text-xs text-muted-foreground mb-3">
                Dual EMA crossover — EMA-12 crosses above EMA-26. Faster and more responsive than triple_ema, suited for shorter-term trend changes. Signal persistence of 3 bars applied.
              </p>
              <ParamRow label="EMA fast" value="12" />
              <ParamRow label="EMA slow" value="26" />
              <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
                <strong className="text-foreground">Guards:</strong> <code className="font-mono">MA200</code>, <code className="font-mono">imax20_exhaustion</code>. VIX block at 28.
              </div>
            </Collapsible>
          </div>

          {/* INTRADAY */}
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-mono font-bold text-muted-foreground uppercase tracking-wider">Intraday Strategies (15-Min bars, 20-day lookback)</span>
              <Badge variant="warning">Day-trade · EOD force-close 15:00 ET</Badge>
            </div>

            <Collapsible title="bollinger_bands_15m" badge={<Badge variant="success">SPY · QQQ</Badge>} defaultOpen>
              <p className="text-xs text-muted-foreground mb-3">
                Mean-reversion on Bollinger Band extremes. Enters long when price drops below the lower band while remaining above the 50-bar MA (trend filter confirms underlying uptrend, not broken downtrend). Exits on band re-entry or EOD sweep.
              </p>
              <ParamRow label="BB period" value="20" />
              <ParamRow label="BB std devs" value="2.0σ" />
              <ParamRow label="MA trend filter" value="MA-50 (15min bars)" note="price must be above MA-50" />
              <ParamRow label="VIX type" value="MEAN_REV" />
              <ParamRow label="VIX block" value="≥ 25" note="High-vol mean-rev is unreliable" />
              <ParamRow label="VIX reduce" value="≥ 18 (40% size)" />
            </Collapsible>

            <Collapsible title="momentum_roc_15m" badge={<Badge variant="success">SPY · QQQ · XLK · XLE · XLF</Badge>}>
              <p className="text-xs text-muted-foreground mb-3">
                Rate-of-change momentum entry on 15-min bars. Requires ROC to be above threshold, accelerating from prior bar, above MA-50, with confirmed volume surge, and not yet extended into blow-off territory. All five conditions must be met simultaneously.
              </p>
              <ParamRow label="ROC period" value="10 bars" />
              <ParamRow label="ROC threshold" value="0.8%" note="FIX-I v8.3: raised from 0.3% (0.3-0.8% band was 100% losses)" />
              <ParamRow label="ROC max extension" value="1.8%" note="FIX-I: tightened from 2.0%. Valid window: 0.8–1.8%" />
              <ParamRow label="Volume ratio" value="≥ 1.5× 20-bar avg" note="FIX-I: raised from 1.2× (1.2-1.9× band was 91% losses)" />
              <ParamRow label="ATR extension guard" value="1.0×ATR" note="FIX-C: block if bar moved > 1.0×ATR from open" />
              <ParamRow label="MA50 filter" value="Price above MA-50" note="FIX-A: prevents counter-trend longs" />
              <ParamRow label="VIX type" value="MOMENTUM" />
              <ParamRow label="VIX block" value="≥ 28" />
              <ParamRow label="VIX reduce" value="≥ 20 (50% size)" />
              <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
                <strong className="text-foreground">Key finding:</strong> 91.4% of all logged signals were "no_signal" before FIX-I. The ROC + volume + MA50 + direction joint probability produced noise-only entries. Raising thresholds achieved ~1-2 high-conviction trades/day.
              </div>
            </Collapsible>
          </div>
        </div>
      )}

      {/* ── ENTRY GATES TAB ───────────────────────────────────────────── */}
      {tab === 'Entry Gates' && (
        <div className="space-y-4">
          <SectionHeader icon={Filter} title="Entry Gate Waterfall" subtitle="Gates evaluated in order — first match skips the trade. All conditions must pass for execution." />

          <div className="bg-card border border-border rounded-lg p-4">
            <p className="text-xs text-muted-foreground mb-4">
              Every buy signal passes through this waterfall before reaching the order router. Gates are evaluated top-to-bottom. The <code className="font-mono">skip_reason</code> is logged in signals_history.json for every blocked trade.
            </p>

            <GateRow order="1" gate="no_signal" trigger="Signal function returned 'hold' (not buy/sell)" reason="Strategy conditions not met — normal non-event." source="Engine" />
            <GateRow order="2" gate="vix_block" trigger="VIX ≥ 28 for MOMENTUM, ≥ 28 for TREND, ≥ 25 for MEAN_REV" reason="Extreme volatility makes intraday entries unreliable. Position size halved at VIX 20." source="Risk" />
            <GateRow order="3" gate="already_in_position" trigger="Open position in this symbol already exists" reason="Engine is long-only; no pyramiding." source="Engine" />
            <GateRow order="4" gate="sold_this_run" trigger="Symbol already sold in this 15-min cycle" reason="BUG-001: prevents sell→rebuy churn in one run." source="BUG-001" />
            <GateRow order="5" gate="stop_cooldown" trigger="Symbol was stopped out — ban active until 15:00 ET" reason="FIX-J R1: GOOGL stopped 12:10 → re-entered 13:17 → -$34.70. Same-day re-entry after stop-loss now impossible." source="FIX-J R1" />
            <GateRow order="6" gate="weekly_concentration_cap" trigger="Symbol already traded 3× this week (intraday only)" reason="FIX-J R3: HOOD was 42.9% of all gross gains; MRVL 98% of loss magnitude. Max 3 intraday entries/symbol/week." source="FIX-J R3" />
            <GateRow order="7" gate="high52w_block" trigger="52-week proximity ratio < 0.75 AND 12-week return < 0%" reason="FIX-F: dual-condition — single h52w blocked HOOD (h52w=0.744, ret12w=+43%). Both conditions required." source="FIX-F" />
            <GateRow order="8" gate="illiq_block" trigger="Amihud illiquidity score > 1×10⁻⁸" reason="FIX-F: thin stocks have wide effective spreads that destroy theoretical R:R." source="FIX-F" />
            <GateRow order="9" gate="imax20_exhaustion" trigger="imax20 > 0.75 (recent high was in last 75% of lookback)" reason="FIX-G: qlib158 factor. Price near 20-bar high = bull-trap zone. Applied to macd_crossover, triple_ema, ema_crossover, momentum_roc_15m." source="FIX-G" />
            <GateRow order="10" gate="ma200_bear_block" trigger="Price / MA-200 < 1.0 (trading below 200-day)" reason="FIX-G: Faber (2007) trend filter. Only long breakout signals above the 200-day MA. Applied to macd_crossover, triple_ema." source="FIX-G" />
            <GateRow order="11" gate="strev_overbought" trigger="21-day return > +10% (Jegadeesh 1990)" reason="FIX-G: short-term reversal factor. Stocks that ran >10% in 21 days are overbought and likely to mean-revert against long entries." source="FIX-G" />
            <GateRow order="12" gate="pre_10am_block" trigger="Time < 10:00 ET (intraday only)" reason="FIX-E: first 30 minutes are noise (opening volatility, gap fills). 10:00–10:15 window has 64% win rate — gate is correctly placed." source="FIX-E" />
            <GateRow order="13" gate="late_day_block" trigger="Time > 14:00 ET (intraday only)" reason="FIX-J R5: 14:00–15:00 window was worst across 252 trades: 26 trades, 38% win rate, -$304.74 total, avg -$11.72." source="FIX-J R5" />
            <GateRow order="14" gate="kill_switch_active" trigger="Portfolio drawdown ≥ 25% from peak watermark" reason="Trailing high-watermark kill switch. Blocks ALL new buys. Watermark persists in live_baseline.json." source="Risk" />
            <GateRow order="15" gate="ma20_bear_block" trigger="SPY below 20-day MA by > 1% (bear) or < 0.5% above (bull hysteresis)" reason="SPY MA20 regime filter. Bear market: no new longs. Bull: full size. Regime label logged each run." source="Risk" />
            <GateRow order="16" gate="qty_too_small" trigger="Calculated position size < 1 share" reason="ATR-based sizing produces < 1 share — skip rather than place fractional order." source="Risk" />
            <GateRow order="17" gate="insufficient_buying_power" trigger="Order value > available buying power" reason="Broker-side guardrail before order submission." source="Broker" />
          </div>
        </div>
      )}

      {/* ── RISK & EXITS TAB ──────────────────────────────────────────── */}
      {tab === 'Risk & Exits' && (
        <div className="space-y-4">
          <SectionHeader icon={Shield} title="Risk Management & Exit Logic" subtitle="Position sizing, stop-loss, trailing stop, kill switch, and EOD sweep" />

          {/* Position Sizing */}
          <Collapsible title="Position Sizing — ATR Volatility Targeting" badge={<Badge variant="primary">FIX-G R5</Badge>} defaultOpen>
            <p className="text-xs text-muted-foreground mb-3">
              Each position size is derived from ATR-based risk budgeting with volatility targeting (Harvey 2018). The engine scales risk per trade to target 12% annualized portfolio volatility, adjusting dynamically to current market conditions.
            </p>
            <ParamRow label="Base risk per trade" value="1% of equity" note="RISK_PCT = 0.01" />
            <ParamRow label="Max single position" value="10% of equity" note="MAX_POSITION_PCT = 0.10" />
            <ParamRow label="Vol target (annual)" value="12%" note="VOL_TARGET = 0.12 / √252 daily" />
            <ParamRow label="Vol scalar cap" value="2.0×" note="Never more than 2× base size" />
            <ParamRow label="VIX multiplier" value="0.5–1.0×" note="50% size at VIX ≥ 20 (MOMENTUM)" />
            <ParamRow label="Sizing formula" value="equity × risk_pct × vol_scalar × vix_mult / (ATR_STOP_MULT × ATR)" />
          </Collapsible>

          {/* Stop Loss — Two-Phase FIX-L */}
          <Collapsible title="Stop-Loss Architecture — Two-Phase System" badge={<Badge variant="danger">FIX-H v8.2 + FIX-L v8.5</Badge>} defaultOpen>
            <p className="text-xs text-muted-foreground mb-3">
              Two-phase stop system. <strong className="text-foreground">Phase 1</strong> (entry to price &lt; entry + 1×ATR): wide static stop at 2.0×ATR provides breathing room through normal intrabar noise. <strong className="text-foreground">Phase 2</strong> (price clears entry + 1×ATR): engine cancels existing trail and re-attaches a 0.5×ATR trail whose worst-case execution is above the entry price — guaranteeing no loss.
            </p>
            <div className="mb-3 p-2 bg-primary/10 border border-primary/20 rounded text-xs font-mono">
              <div className="text-primary font-bold mb-1">Example — $100 entry, $1.00 ATR</div>
              <div className="text-muted-foreground space-y-0.5">
                <div>Phase 1:  static stop @$98.00 (−2×ATR) · trail @$99.50 (−0.5×ATR) active</div>
                <div>Upgrade trigger:  price hits $101.00 ≥ entry + 1×ATR</div>
                <div>Phase 2:  new trail distance $0.50 · stop floor = $100.50</div>
                <div>Worst case after upgrade: exit @$100.50 → +$0.50/share — never a loss ✅</div>
              </div>
            </div>
            <ParamRow label="Phase 1 static stop" value="entry − 2.0×ATR" note="FIX-H: safety net, wide to allow entry breathing room" />
            <ParamRow label="Phase 1 trail (immediate)" value="0.5×ATR from current" note="Active from T=0 — protects against large reversals only" />
            <ParamRow label="Upgrade trigger" value="price ≥ entry + 1.0×ATR" note="FIX-L: BREAKEVEN_ATR_TRIGGER = 1.0 (configurable)" />
            <ParamRow label="Phase 2 trail distance" value="0.5×ATR" note="FIX-L: PROFIT_LOCK_ATR_MULT = 0.5. Floor always above entry" />
            <ParamRow label="Worst case after upgrade" value="Breakeven or profit" note="Trail floor is always ≥ entry once Phase 2 activates" />
            <ParamRow label="Upgrade cadence" value="Every 15-min cron cycle" note="Checked in main loop between EOD sweep and signal scan" />
            <ParamRow label="Idempotent guard" value="trail_upgraded_to_breakeven flag" note="Upgrade runs once per position — skips if already set" />
            <ParamRow label="Fallback" value="Re-attach original trail" note="If Phase 2 order submission fails, original trail re-submitted" />
            <ParamRow label="Take-profit target" value="3.0×ATR" note="ATR_TP_MULT = 3.0 (bracket limit order)" />
            <div className="mt-3 p-2 bg-muted/50 rounded text-xs text-muted-foreground">
              <strong className="text-foreground">Metadata persisted:</strong> <code className="font-mono">trail_upgrade_price</code>, <code className="font-mono">trail_upgrade_floor</code>, <code className="font-mono">trail_upgraded_to_breakeven</code> stored in <code className="font-mono">eod_position_tags.json</code> — survives restarts.
            </div>
          </Collapsible>

          {/* Kill Switch */}
          <Collapsible title="Kill Switch — Trailing High-Watermark" badge={<Badge variant="danger">Always On</Badge>}>
            <p className="text-xs text-muted-foreground mb-3">
              The kill switch uses a trailing high-watermark approach. Peak equity is tracked in <code className="font-mono">live_baseline.json</code> and persists across all runs. When current equity drops 25% or more below the watermark, all new buy signals are blocked until equity recovers.
            </p>
            <ParamRow label="Threshold" value="25% drawdown from peak" note="MAX_DRAWDOWN_PCT = 25.0 (configurable)" />
            <ParamRow label="Watermark storage" value="live_baseline.json → peak_equity" note="Survives restarts and cron gaps" />
            <ParamRow label="Logged fields" value="peak_equity, drawdown_pct, kill_switch_active" />
            <ParamRow label="Override" value="DISABLE_KILL_SWITCH=true" note="Environment variable override (emergency)" />
          </Collapsible>

          {/* EOD */}
          <Collapsible title="EOD Forced Exit — 15:00 ET" badge={<Badge variant="warning">Intraday Only</Badge>}>
            <p className="text-xs text-muted-foreground mb-3">
              All intraday-tagged positions are force-closed at or after 15:00 ET. Trailing stops are cancelled before market orders are placed. Daily/swing positions are explicitly exempted.
            </p>
            <ParamRow label="EOD exit time" value="15:00 ET" note="BUG-011: was 15:30; corrected to match 19:00 UTC cron" />
            <ParamRow label="Position scope" value="strategy_type == 'intraday' only" />
            <ParamRow label="Max hold guard" value="390 minutes from entry" note="FIX-J R2: Jun 8 MRVL held 1350min overnight → -$282.73" />
            <ParamRow label="Trail stop cancel" value="Before market sell order" note="cancel_all_trailing_stops_for_symbol() called first" />
          </Collapsible>

          {/* Session ban */}
          <Collapsible title="Session Ban After Stop-Loss" badge={<Badge variant="warning">FIX-J R1</Badge>}>
            <p className="text-xs text-muted-foreground mb-3">
              When a stop-loss fills, the symbol is banned from re-entry for the remainder of the trading session (until 15:00 ET). Replaces the old 30-minute cooldown, which proved ineffective.
            </p>
            <ParamRow label="Old cooldown" value="30 minutes" note="Was bypassed on same-session trend continuation" />
            <ParamRow label="New ban expiry" value="15:00 ET same day" note="Session end — full day ban after stop-out" />
            <ParamRow label="Evidence" value="GOOGL: stopped 12:10 → re-entered 13:17 → -$34.70" note="Repeat loss, same session, same direction" />
          </Collapsible>
        </div>
      )}

      {/* ── FACTORS TAB ───────────────────────────────────────────────── */}
      {tab === 'Factors' && (
        <div className="space-y-4">
          <SectionHeader icon={Activity} title="Academic Factor Library" subtitle="Integrated from HKU vibe-trading-ai and Microsoft qlib158. Source: Hong Kong University DS factor zoo + published literature." />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">

            {/* cord60 */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">cord60</span>
                <div className="flex gap-1">
                  <Badge variant="purple">qlib158</Badge>
                  <Badge variant="blue">Advisory</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                60-period rolling Pearson correlation between daily returns and daily volume changes. Positive cord60 confirms that volume is amplifying price moves (institutional participation). Negative cord60 means volume diverges — caution signal.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="Threshold" value="cord60 > 0.20" note="Below 0.20 = advisory warning only, not a hard block" />
                <ParamRow label="Applied to" value="Daily breakout signals" />
                <ParamRow label="Effect" value="Soft gate (logged warning)" />
              </div>
            </div>

            {/* strev21 */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">strev21</span>
                <div className="flex gap-1">
                  <Badge variant="purple">Jegadeesh 1990</Badge>
                  <Badge variant="danger">Hard Block</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Short-term reversal factor. Negative of the 21-day (1-month) raw return. Stocks that have risen significantly in the past month tend to mean-revert. Blocks long entries when recent returns are too high.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="Threshold" value="21d return > +10%" note="Block if stock ran > 10% in last 21 trading days" />
                <ParamRow label="Applied to" value="rsi_macd_combo (daily)" />
                <ParamRow label="Effect" value="Hard block → skip_reason: strev_overbought" />
              </div>
            </div>

            {/* imax20 */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">imax20</span>
                <div className="flex gap-1">
                  <Badge variant="purple">qlib158</Badge>
                  <Badge variant="danger">Hard Block</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Relative time-step position of the 20-bar high. If the highest price in the last 20 bars occurred recently (imax20 &gt; 0.75), it suggests the stock is in a recent-high zone — classic bull-trap territory where new longs get trapped as the high is tested and fails.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="Threshold" value="imax20 > 0.75" note="Recent 20-bar high in last 75% of lookback" />
                <ParamRow label="Applied to" value="macd_crossover, triple_ema, ema_crossover, momentum_roc_15m" />
                <ParamRow label="Effect" value="Hard block → skip_reason: imax20_exhaustion" />
              </div>
            </div>

            {/* MA200 */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">MA200</span>
                <div className="flex gap-1">
                  <Badge variant="purple">Faber 2007</Badge>
                  <Badge variant="danger">Hard Block</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                200-day simple moving average trend filter. Faber (2007): buying when price is above its 200-day MA improves Sharpe by 0.6–1.0 vs buy-and-hold. Price below MA200 signals a structural bear phase where breakout strategies underperform.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="Threshold" value="price / MA200 < 1.0" note="Below MA200 = bear structural regime" />
                <ParamRow label="Applied to" value="macd_crossover, triple_ema, ema_crossover" />
                <ParamRow label="Effect" value="Hard block → skip_reason: ma200_bear_block" />
              </div>
            </div>

            {/* high52w */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">high52w (h52w)</span>
                <div className="flex gap-1">
                  <Badge variant="purple">George & Hwang 2004</Badge>
                  <Badge variant="danger">Hard Block</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                52-week proximity ratio: close / 252-day max close. Dual-condition guard — blocks entry only when BOTH h52w &lt; 0.75 (far from annual high) AND 12-week return &lt; 0% (negative momentum). Single condition was a false positive on HOOD.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="h52w threshold" value="< 0.75" note="FIX-F: single condition false-positive on HOOD (h52w=0.744, ret12w=+43%)" />
                <ParamRow label="ret12w threshold" value="< 0%" note="BOTH conditions required — dual gate" />
                <ParamRow label="Applied to" value="All daily strategies" />
              </div>
            </div>

            {/* Amihud illiq */}
            <div className="bg-card border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-mono font-bold text-foreground">illiq (Amihud)</span>
                <div className="flex gap-1">
                  <Badge variant="purple">Amihud 2002</Badge>
                  <Badge variant="danger">Hard Block</Badge>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                Amihud (2002) illiquidity ratio: 21-day average of |daily return| / dollar volume. Higher illiq = price impact per dollar traded is larger = wider effective spread. Blocks entries in thin stocks where transaction costs destroy theoretical edge.
              </p>
              <div className="mt-2 pt-2 border-t border-border/50">
                <ParamRow label="Threshold" value="illiq > 1×10⁻⁸" note="Calibrated to universe effective spread tolerance" />
                <ParamRow label="Applied to" value="All strategies" />
                <ParamRow label="Effect" value="Hard block → skip_reason: illiq_block" />
              </div>
            </div>

          </div>

          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-2">
              <Target className="w-4 h-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">Volatility Targeting (Harvey, Liu & Zhu 2018)</span>
              <Badge variant="primary">FIX-G R5</Badge>
            </div>
            <p className="text-xs text-muted-foreground">
              Position size is dynamically scaled to target 12% annualized portfolio volatility. The vol scalar = 12% / realized_vol, capped at 2.0×. When realized vol is low, size increases; when vol is high, size decreases. VIX multiplier applies on top.
            </p>
            <div className="mt-2 grid grid-cols-2 gap-x-4">
              <ParamRow label="Target annual vol" value="12%" />
              <ParamRow label="Realized vol window" value="ATR-based" />
              <ParamRow label="Vol scalar range" value="0.0× – 2.0×" note="Capped at 2× to prevent over-leverage" />
              <ParamRow label="Stack order" value="base_size × vol_scalar × vix_mult" />
            </div>
          </div>
        </div>
      )}

      {/* ── CHANGELOG TAB ─────────────────────────────────────────────── */}
      {tab === 'Changelog' && (
        <div className="space-y-4">
          <SectionHeader icon={GitCommit} title="Engine Changelog" subtitle="All deployed fixes with rationale and commit references. Deposits excluded from all P&L figures." />

          <div className="bg-card border border-border rounded-lg p-5">
            <ChangelogRow
              version="v8.4-FIX-K"
              fix="Tech Roundtable"
              date="2026-07-17"
              summary="Engine heartbeat (last_heartbeat written to live_baseline every run), requests.Session connection pooling (~20% API latency reduction), test assertions fixed for pytest compatibility (was chk()-only)."
              commits={['93928d2f7e', '8ba1c9b2c6']}
            />
            <ChangelogRow
              version="v8.5"
              fix="FIX-L"
              date="2026-07-18"
              summary="Two-phase stop system: Phase 1 keeps static 2.0×ATR stop for entry breathing room. Once price clears entry + 1×ATR (configurable), _upgrade_trail_to_breakeven() cancels the existing trail and re-attaches a 0.5×ATR trail with a floor above entry — worst-case guaranteed to break even or profit. Runs each 15-min cron cycle. Idempotent (upgrades once, flag persisted in eod_position_tags.json). Fallback on attach failure. 20/20 FIX-L tests pass."
              commits={['28755a5a7d', '9776411081']}
            />
            <ChangelogRow
              version="v8.4"
              fix="FIX-J"
              date="2026-07-17"
              summary="Board roundtable: (J1) same-day session ban after stop-loss, replacing 30min cooldown; (J2) 390min max hold guard preventing overnight drift; (J3) weekly 3-entry concentration cap per symbol; (J5) late-day gate tightened 14:45→14:00 ET (worst time window in 252-trade audit: 38% wr, -$304); (J6) MRVL and TXN permanently removed from scan universe."
              commits={['23f65f10d0', 'f3c84d58e3', '0ba4efb014']}
            />
            <ChangelogRow
              version="v8.3"
              fix="FIX-I"
              date="2026-07-17"
              summary="Signal quality calibration: ROC threshold 0.3→0.8% (0.3-0.8% band was 100% losses), vol_ratio 1.2→1.5× (1.2-1.9× band was 91% losses), roc_max_extension 2.0→1.8% (tighter blow-off guard, matches only-win upper bound)."
              commits={['34902e2ae2']}
            />
            <ChangelogRow
              version="v8.2"
              fix="FIX-H"
              date="2026-07-17"
              summary="Trailing stop architecture: time_stop_guard removed (confounded timing with exit logic), ATR_STOP_MULT 1.5→2.0 (static stop is now safety net), trailing stop 1.0→0.5×ATR (38-trade sweep found optimal: total loss -$292 vs -$760, win rate 39.5% vs 0%)."
              commits={['c5c9d39db9']}
            />
            <ChangelogRow
              version="v8.0"
              fix="FIX-G"
              date="2026-07-16"
              summary="5 academic factors deployed: cord60 (qlib158 vol-correlation, advisory), strev21 (Jegadeesh 1990 reversal, hard block), imax20 (qlib158 bull-trap guard), MA200 (Faber 2007 trend filter), volatility targeting (Harvey 2018, 12% target). Signal persistence 1→3 bars."
              commits={['1e2863e393']}
            />
            <ChangelogRow
              version="v7.9 rev1"
              fix="FIX-F"
              date="2026-07-15"
              summary="Dual-condition high52w filter (h52w < 0.75 AND ret12w < 0.0 — both required). Single condition was false-positive on HOOD (h52w=0.744, ret12w=+43%). Amihud illiq threshold calibrated to 1×10⁻⁸. Weekly scanner upgraded to Carhart momentum ranking."
              commits={[]}
            />
            <ChangelogRow
              version="v7.8b"
              fix="FIX-E"
              date="2026-07-14"
              summary="Time-based rules: pre_10am_block (no entries before 10:00 ET), late_day_block (14:45 ET at the time), wide_open_day 50% size rule. Cron set to 15-minute intervals. BUG-011 EOD exit corrected 15:30→15:00 ET."
              commits={[]}
            />
            <ChangelogRow
              version="v7.7"
              fix="FIX-D"
              date="2026-07-14"
              summary="ROC max-extension guard (originally 2.0%) added to momentum_roc_15m. ATR-extension single-bar guard (FIX-C) extended with rolling blow-off check. MA50 trend filter (FIX-A) and volume confirmation 1.2× (FIX-B) established."
              commits={[]}
            />
          </div>

          {/* Engine constants summary */}
          <div className="bg-card border border-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Settings className="w-4 h-4 text-muted-foreground" />
              <span className="text-sm font-semibold text-foreground">Current Engine Constants</span>
              <Badge variant="default">v8.4-FIX-K</Badge>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-0">
              <ParamRow label="RISK_PCT" value="0.01 (1%)" />
              <ParamRow label="MAX_POSITION_PCT" value="0.10 (10%)" />
              <ParamRow label="ATR_STOP_MULT" value="2.0×" />
              <ParamRow label="ATR_TP_MULT" value="3.0×" />
              <ParamRow label="TRAILING STOP" value="0.5×ATR" />
              <ParamRow label="MAX_DRAWDOWN_PCT" value="25.0%" />
              <ParamRow label="EOD_EXIT" value="15:00 ET" />
              <ParamRow label="MAX_HOLD_MINUTES" value="390 min" />
              <ParamRow label="MAX_WEEKLY_PER_SYMBOL" value="3 trades" />
              <ParamRow label="VOL_TARGET (annual)" value="12%" />
              <ParamRow label="ROC_THRESHOLD" value="0.8%" />
              <ParamRow label="ROC_MAX_EXTENSION" value="1.8%" />
              <ParamRow label="VOL_RATIO" value="≥ 1.5×" />
              <ParamRow label="MAX_SIGNALS_HISTORY" value="500" />
              <ParamRow label="STOP_COOLDOWN" value="Session ban (→15:00)" />
              <ParamRow label="BREAKEVEN_ATR_TRIGGER" value="1.0×ATR" note="FIX-L: price must exceed entry + 1×ATR to upgrade trail" />
              <ParamRow label="PROFIT_LOCK_ATR_MULT" value="0.5×ATR" note="FIX-L: trail distance after breakeven upgrade" />
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
