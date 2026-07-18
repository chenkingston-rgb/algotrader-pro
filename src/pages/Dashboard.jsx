import { useState, useEffect, useCallback } from "react";

// ─────────────────────────────────────────────────────────────────────────────
// AlgoTrader Pro — Dashboard v9.0
//
// Changes from v8:
//  • ROW 1 (Status Strip): Bear Block ON/OFF + VIX + Regime + Kill Switch
//    in a compact horizontal status bar — always visible at top
//  • Today's P&L: pulls LIVE from Alpaca equity delta (not stale log).
//    pnl_today = live_equity - last_equity (Alpaca's prev close field).
//    Shows "--" only when market is pre-open AND no live equity yet, never "No runs today".
//  • Removed redundancies:
//    - "Total Equity Move" card removed (duplicate of pnl_today row)
//    - "Peak Equity" removed from Cumulative P&L row (kept in Risk section only)
//    - "Capital Deployed" removed from Engine section (kept in Capital Breakdown only)
//    - "Realized (All-Time)" collapsed into Cumulative P&L card subtitle
//  • Layout: cleaner 3-section structure:
//    1. Status strip (Bear Block / VIX / Regime / Kill Switch)
//    2. Key metrics (Equity / Today's P&L split / Buying Power / Drawdown)
//    3. Cumulative P&L + Positions + Engine + Signals
// ─────────────────────────────────────────────────────────────────────────────

// Data read directly from GitHub raw — zero Base44 integration credits
const DATA_URL = "https://raw.githubusercontent.com/chenkingston-rgb/algotrader-pro/main/logs/dashboard_payload.json";
const REFRESH_MS   = 300_000; // 5 min — engine writes every 15min, polling more often wastes nothing but is pointless

// ── Helpers ───────────────────────────────────────────────────────────────────
const pnlColor = (v) => (v == null || isNaN(v)) ? "text-slate-400" : (v >= 0 ? "text-emerald-400" : "text-red-400");

const fmt$ = (v, alwaysSign = false) => {
  if (v == null || isNaN(v)) return "—";
  const sign = alwaysSign && v > 0 ? "+" : v < 0 ? "" : "";
  const abs  = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${alwaysSign && v > 0 ? "+" : ""}${v < 0 ? "-" : ""}$${abs}`;
};

const fmtPct = (v, alwaysSign = false) => {
  if (v == null || isNaN(v)) return "—";
  return `${alwaysSign && v > 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
};

// ── Normalise raw API response ────────────────────────────────────────────────
function normalise(raw) {
  if (!raw) return null;
  const d = { ...raw };

  if (d.drawdown_from_peak_pct == null && d.drawdown_pct != null)
    d.drawdown_from_peak_pct = d.drawdown_pct;
  if (d.cumulative_trading_pnl == null && d.trading_pnl != null)
    d.cumulative_trading_pnl = d.trading_pnl;

  // Today's P&L — use Alpaca live equity vs prev close (most accurate)
  if (d.pnl_today == null && d.equity != null && d.last_equity != null && d.last_equity > 0)
    d.pnl_today = d.equity - d.last_equity;
  if (d.pnl_today_pct == null && d.pnl_today != null && d.last_equity > 0)
    d.pnl_today_pct = (d.pnl_today / d.last_equity) * 100;

  const positions = d.positions ?? d.position_details ?? [];
  if (d.total_unrealized == null)
    d.total_unrealized = positions.reduce((s, p) => s + (p.unrealized_pl ?? 0), 0);
  if (d.total_exposure == null)
    d.total_exposure = positions.reduce((s, p) => s + (p.market_value ?? 0), 0);
  if (d.exposure_pct == null && d.equity > 0)
    d.exposure_pct = (d.total_exposure / d.equity) * 100;

  if (d.cumulative_trading_pct == null && d.cumulative_trading_pnl != null && d.equity > 0)
    d.cumulative_trading_pct = (d.cumulative_trading_pnl / d.equity) * 100;
  if (d.total_capital_deployed == null)
    d.total_capital_deployed = (d.equity ?? 0) + (d.total_deposited ?? 0);
  if (d.return_on_total_capital == null && d.cumulative_trading_pnl != null && d.total_capital_deployed > 0)
    d.return_on_total_capital = (d.cumulative_trading_pnl / d.total_capital_deployed) * 100;

  if (d.vix_status == null && d.vix != null) {
    const vix = d.vix;
    d.vix_status     = vix >= 28 ? "blocked" : vix >= 20 ? "reduced" : "clear";
    d.vix_size_label = vix >= 28 ? "BLOCKED"  : vix >= 20 ? "HALF"    : "FULL";
  }
  return d;
}

// ── Status Strip — Bear Block / VIX / Regime / Kill Switch ───────────────────
function StatusStrip({ d }) {
  if (!d) return null;

  const bearOn   = d?.ma20_bear_block;
  const ksOn     = d?.kill_switch_active;
  const vix      = d?.vix;
  const vixStat  = d?.vix_status ?? "unknown";
  const vixLabel = d?.vix_size_label ?? "—";
  const regime   = d?.regime_label ?? (bearOn ? "BEAR" : "BULL");
  const gap      = d?.ma20_gap_pct;
  const ddPct    = d?.drawdown_from_peak_pct ?? d?.drawdown_pct ?? 0;

  const vixCol = vixStat === "blocked" ? "text-red-400"
               : vixStat === "reduced" ? "text-amber-400"
               : "text-emerald-400";

  return (
    <div className="w-full rounded-xl border border-slate-700 bg-slate-800/60 px-4 py-3
                    flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">

      {/* Bear Block */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase tracking-wider">Bear Block</span>
        <span className={`font-bold text-base px-2 py-0.5 rounded-md
          ${bearOn
            ? "bg-red-500/15 text-red-400 border border-red-500/30"
            : "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"}`}>
          {bearOn ? "ON" : "OFF"}
        </span>
        <span className={`text-xs font-semibold ${bearOn ? "text-red-500" : "text-emerald-600"}`}>
          {regime}
        </span>
        {gap != null && (
          <span className="text-xs text-slate-500">
            SPY {gap > 0 ? "+" : ""}{Number(gap).toFixed(2)}% vs MA20
          </span>
        )}
      </div>

      <div className="w-px h-4 bg-slate-700 hidden md:block" />

      {/* VIX */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase tracking-wider">VIX</span>
        <span className={`font-bold text-base ${vixCol}`}>
          {vix != null ? Number(vix).toFixed(2) : "—"}
        </span>
        <span className={`text-xs font-bold uppercase tracking-widest ${vixCol}`}>
          {vixLabel}
        </span>
        <span className="text-xs text-slate-500">size</span>
      </div>

      <div className="w-px h-4 bg-slate-700 hidden md:block" />

      {/* Drawdown */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase tracking-wider">Drawdown</span>
        <span className={`font-bold text-base
          ${ddPct >= 20 ? "text-red-400" : ddPct >= 10 ? "text-amber-400" : "text-emerald-400"}`}>
          {Number(ddPct).toFixed(2)}%
        </span>
        <span className="text-xs text-slate-500">from peak {fmt$(d?.peak_equity)}</span>
      </div>

      {/* Kill Switch — only show if active */}
      {ksOn && (
        <>
          <div className="w-px h-4 bg-slate-700 hidden md:block" />
          <div className="flex items-center gap-2">
            <span className="text-red-400 font-bold text-sm">⚠️ KILL SWITCH ACTIVE</span>
            <span className="text-red-500/70 text-xs">All buys blocked</span>
          </div>
        </>
      )}

      {/* Overnight carry warning */}
      {(d?.overnight_carry_count ?? 0) > 0 && (
        <>
          <div className="w-px h-4 bg-slate-700 hidden md:block" />
          <div className="flex items-center gap-2">
            <span className="text-amber-400 text-xs">
              🌙 {d.overnight_carry_count} carry position{d.overnight_carry_count > 1 ? "s" : ""}
            </span>
            <span className={`text-xs font-medium ${(d.overnight_carry_upl ?? 0) >= 0 ? "text-emerald-400" : "text-amber-400"}`}>
              {fmt$(d.overnight_carry_upl, true)} UPL
            </span>
          </div>
        </>
      )}

    </div>
  );
}

// ── Today's P&L breakdown card (realized + unrealized split) ─────────────────
function TodayPnlCard({ d }) {
  const realized   = d?.realized_today;
  const unrealized = d?.unrealized_today ?? d?.total_unrealized;
  const total      = d?.pnl_today;
  const pct        = d?.pnl_today_pct;

  // If equity == last_equity AND it's pre-market, show informative state
  const isStale = total != null && Math.abs(total) < 0.01 && (d?.equity === d?.last_equity);

  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-2
      ${(total ?? 0) >= 0 ? "bg-emerald-500/8 border-emerald-500/25" : "bg-red-500/8 border-red-500/25"}`}>

      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Today's P&L</span>
        {pct != null && !isStale && (
          <span className={`text-xs font-semibold ${pnlColor(total)}`}>{fmtPct(pct, true)}</span>
        )}
      </div>

      {isStale ? (
        <div>
          <span className="text-lg font-bold text-slate-400">$0.00</span>
          <p className="text-[10px] text-slate-500 mt-1">Pre-market — no moves yet (equity unchanged from prior close)</p>
        </div>
      ) : (
        <span className={`text-2xl font-bold leading-tight ${pnlColor(total)}`}>
          {fmt$(total, true)}
        </span>
      )}

      {/* Realized / Unrealized split */}
      <div className="flex gap-3 mt-1 pt-2 border-t border-slate-700/40">
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-slate-500 uppercase">Realized ✓</span>
          <span className={`text-sm font-semibold ${pnlColor(realized)}`}>{fmt$(realized, true)}</span>
        </div>
        <div className="w-px bg-slate-700/50" />
        <div className="flex flex-col gap-0.5">
          <span className="text-[10px] text-amber-500/70 uppercase">Unrealized ↻</span>
          <span className="text-sm font-semibold text-amber-400">{fmt$(unrealized, true)}</span>
        </div>
      </div>
    </div>
  );
}

// ── Generic stat card ─────────────────────────────────────────────────────────
function StatCard({ label, value, sub, valueClass = "text-white", accent }) {
  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-1 ${accent || "bg-slate-800/60 border-slate-700"}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">{label}</span>
      <span className={`text-xl font-bold leading-tight ${valueClass}`}>{value}</span>
      {sub && <span className="text-xs text-slate-500 mt-0.5">{sub}</span>}
    </div>
  );
}

// ── Position table row ────────────────────────────────────────────────────────
function PositionRow({ p }) {
  const isCarry  = p.is_overnight_carry;
  const uplColor = (p.unrealized_pl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400";
  const ctdColor = (p.change_today_pl ?? 0) >= 0 ? "text-emerald-400" : "text-red-400";
  return (
    <tr className="border-t border-slate-700/50 hover:bg-slate-700/20 transition-colors">
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white">{p.symbol}</span>
          <span className={`text-[9px] font-semibold rounded px-1.5 py-0.5 uppercase border
            ${isCarry
              ? "bg-amber-500/20 text-amber-400 border-amber-500/30"
              : "bg-sky-500/20 text-sky-400 border-sky-500/30"}`}>
            {isCarry ? "CARRY" : "TODAY"}
          </span>
        </div>
        {p.entry_date_label && (
          <div className="text-[9px] text-slate-500 mt-0.5">Entered: {p.entry_date_label}</div>
        )}
      </td>
      <td className="py-2.5 px-3 text-right text-slate-300">{p.qty}</td>
      <td className="py-2.5 px-3 text-right text-slate-400">${Number(p.avg_entry_price ?? p.entry_price ?? 0).toFixed(2)}</td>
      <td className="py-2.5 px-3 text-right text-white font-medium">${Number(p.current_price ?? 0).toFixed(2)}</td>
      <td className="py-2.5 px-3 text-right">
        <span className={`font-semibold ${uplColor}`}>{fmt$(p.unrealized_pl, true)}</span>
        <span className="block text-[9px] text-slate-500">{fmtPct(p.unrealized_plpc != null ? p.unrealized_plpc * 100 : null, true)}</span>
      </td>
      <td className="py-2.5 px-3 text-right">
        <span className={`font-semibold ${ctdColor}`}>{p.change_today_pl != null ? fmt$(p.change_today_pl, true) : "—"}</span>
      </td>
      <td className="py-2.5 px-3 text-right text-slate-400">{p.market_value != null ? fmt$(p.market_value) : "—"}</td>
    </tr>
  );
}

// ── Main Dashboard component ──────────────────────────────────────────────────

// ── StrategyDocs lucide imports (merged) ────────────────────────────────────
} from 'lucide-react';

// ═══════════════════════════════════════════════════════════════════════════════
// AlgoTrader Pro — Dashboard v10.0
// Merged Strategy Docs into a second tab. StrategyDocs.jsx deleted.
// Tabs: Portfolio (live data) | Strategy (engine documentation)
// ═══════════════════════════════════════════════════════════════════════════════

// ── StrategyDocs helper components ──────────────────────────────────────────
  BookOpen, Zap, Shield, Clock, TrendingUp, TrendingDown, BarChart2,
  Activity, AlertTriangle, CheckCircle, Info, ChevronDown, ChevronRight,
  GitCommit, Settings, Target, Filter, Layers
} from 'lucide-react';

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

// ── Strategy tab constants ───────────────────────────────────────────────────
const STRATEGY_TABS = ['Strategies', 'Entry Gates', 'Risk & Exits', 'Signals', 'Changelog'];



export default function Dashboard() {
  const [data,    setData]    = useState(null);
  const [activeTab,    setActiveTab]    = useState('portfolio'); // 'portfolio' | 'strategy'
  const [stratTab,     setStratTab]     = useState('Strategies'); // inner strategy tab
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [lastAt,  setLastAt]  = useState(null);

  const fetchData = useCallback(async () => {
    try {
      // Fetch pre-built payload from GitHub raw — no Base44 function call
      const r = await fetch(`${DATA_URL}?t=${Date.now()}`, {
        method: "GET",
        headers: { "Cache-Control": "no-cache" },
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const raw = await r.json();
      if (raw.error) throw new Error(raw.error);
      setData(normalise(raw));
      setLastAt(new Date());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, REFRESH_MS);

  return (
    <div className="flex flex-col gap-0 pb-10">

      {/* ── Top-level Tab Nav ── */}
      <div className="flex gap-0 border-b border-slate-700 mb-5 sticky top-0 bg-slate-950 z-10">
        {['portfolio', 'strategy'].map(t => (
          <button
            key={t}
            onClick={() => setActiveTab(t)}
            className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === t
                ? 'border-blue-500 text-white'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            {t === 'portfolio' ? 'Portfolio' : 'Strategy'}
          </button>
        ))}
      </div>

      {/* ── PORTFOLIO TAB ── */}
      {activeTab === 'portfolio' && (
        <div className="flex flex-col gap-5">
  }, [fetchData]);

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <div className="w-7 h-7 border-2 border-slate-700 border-t-blue-500 rounded-full animate-spin" />
    </div>
  );

  if (error) return (
    <div className="text-red-400 bg-red-500/10 border border-red-500/30 rounded-xl p-6 text-center">
      <p className="font-bold">Failed to load portfolio data</p>
      <p className="text-sm text-red-500/70 mt-1">{error}</p>
      <button onClick={fetchData} className="mt-3 text-xs text-red-400 underline">Retry</button>
    </div>
  );

  const d     = data;
  const ddPct = d?.drawdown_from_peak_pct ?? d?.drawdown_pct ?? 0;

  return (
    <div className="flex flex-col gap-5 pb-10">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Dashboard</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Last run:{" "}
            {d?.last_run
              ? new Date(d.last_run).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
              : "—"}
            {" · "}VIX: {d?.vix != null ? Number(d.vix).toFixed(2) : "—"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-3 py-1 rounded-full text-xs font-semibold border
            ${d?.mode === "live"
              ? "bg-red-500/10 text-red-400 border-red-500/20"
              : "bg-slate-700 text-slate-400 border-slate-600"}`}>
            {d?.mode === "live" ? "🔴 LIVE" : "📄 PAPER"}
          </span>
          <button onClick={fetchData}
            className="px-3 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500 transition-colors">
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* ══════════════════════════════════════════════════
          STATUS STRIP — Bear Block / VIX / Drawdown / Kill Switch
          ══════════════════════════════════════════════════ */}
      <StatusStrip d={d} />

      {/* ══════════════════════════════════════════════════
          ROW 1 — Key Metrics
          ══════════════════════════════════════════════════ */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">

        {/* Equity */}
        <StatCard
          label="Portfolio Equity"
          value={fmt$(d?.equity)}
          sub={`Prev close: ${fmt$(d?.last_equity ?? d?.prev_close_equity)}`}
        />

        {/* Today's P&L — split card */}
        <TodayPnlCard d={d} />

        {/* Buying Power */}
        <StatCard
          label="Buying Power"
          value={fmt$(d?.buying_power)}
          sub="Available for new orders"
        />

        {/* Total Exposure */}
        <StatCard
          label="Total Exposure"
          value={fmt$(d?.total_exposure)}
          sub={`${Number(d?.exposure_pct ?? 0).toFixed(1)}% of equity in ${d?.position_count ?? d?.positions?.length ?? 0} positions`}
        />

      </div>

      {/* ══════════════════════════════════════════════════
          ROW 2 — Cumulative P&L
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">
          Cumulative P&L
          <span className="text-slate-600 normal-case font-normal"> (deposit-adjusted — trading returns only)</span>
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">

          {/* Total trading P&L */}
          <div className={`rounded-xl border p-4 flex flex-col gap-1
            ${(d?.cumulative_trading_pnl ?? 0) >= 0 ? "bg-emerald-500/8 border-emerald-500/25" : "bg-red-500/8 border-red-500/25"}`}>
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Total Trading P&L</span>
            <span className={`text-2xl font-bold ${pnlColor(d?.cumulative_trading_pnl)}`}>
              {fmt$(d?.cumulative_trading_pnl, true)}
            </span>
            <span className="text-[10px] text-slate-500">
              {fmtPct(d?.cumulative_trading_pct, true)} on starting capital
            </span>
            <div className="flex gap-3 mt-1 pt-2 border-t border-slate-700/40 text-xs">
              <span className="text-slate-500">Closed: <span className={pnlColor(d?.closed_pnl_only ?? d?.trading_pnl)}>{fmt$(d?.closed_pnl_only ?? d?.trading_pnl, true)}</span></span>
              <span className="text-slate-500">Open: <span className="text-amber-400">{fmt$(d?.total_unrealized, true)}</span></span>
            </div>
          </div>

          {/* Return on Capital */}
          <StatCard
            label="Return on Capital"
            value={fmtPct(d?.return_on_total_capital, true)}
            sub={`On ${fmt$(d?.total_capital_deployed)} total deployed`}
            valueClass={pnlColor(d?.return_on_total_capital)}
          />

          {/* Capital Breakdown */}
          <div className="rounded-xl border border-slate-700 bg-slate-800/60 p-4 flex flex-col gap-2">
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Capital Breakdown</span>
            <div className="flex flex-col gap-1 text-xs">
              <div className="flex justify-between">
                <span className="text-slate-500">Start equity</span>
                <span className="text-slate-300">{fmt$(d?.start_equity)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Deposits ({d?.deposit_count ?? 0})</span>
                <span className="text-slate-300">{fmt$(d?.total_deposited)}</span>
              </div>
              <div className="flex justify-between border-t border-slate-700/40 pt-1 mt-0.5">
                <span className="text-slate-400 font-medium">Total deployed</span>
                <span className="text-white font-semibold">{fmt$(d?.total_capital_deployed)}</span>
              </div>
              <div className="flex justify-between text-[10px] mt-1">
                <span className="text-slate-600">Cumulative P&L excludes deposits</span>
              </div>
            </div>
          </div>

        </div>
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 3 — Risk Monitor
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Risk Monitor</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">

          <StatCard
            label="Peak Equity (watermark)"
            value={fmt$(d?.peak_equity)}
            sub="Trailing high — kill switch anchors here"
          />
          <StatCard
            label="Drawdown from Peak"
            value={`${Number(ddPct).toFixed(2)}%`}
            sub={`Kill switch at ${d?.kill_switch_threshold ?? 25}%`}
            valueClass={ddPct >= 20 ? "text-red-400" : ddPct >= 10 ? "text-amber-400" : "text-emerald-400"}
          />
          <StatCard
            label="Kill Switch Progress"
            value={`${Number(ddPct).toFixed(2)} / ${d?.kill_switch_threshold ?? 25}%`}
            sub={d?.kill_switch_active ? "⚠️ ACTIVE — buys blocked" : "Inactive"}
            valueClass={d?.kill_switch_active ? "text-red-400" : "text-slate-300"}
          />
          <StatCard
            label="Runs Today"
            value={d?.todays_runs ?? "—"}
            sub={`${d?.todays_orders ?? "—"} orders filled · ${d?.total_runs ?? "—"} all-time runs`}
          />

        </div>
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 4 — Open Positions
          ══════════════════════════════════════════════════ */}
      <section>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">
            Open Positions ({d?.position_count ?? d?.positions?.length ?? 0})
          </h2>
          {(d?.overnight_carry_count ?? 0) > 0 && (
            <span className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-0.5">
              🌙 {d.overnight_carry_count} overnight — UPL ≠ today's P&L
            </span>
          )}
        </div>

        {(d?.positions?.length ?? 0) > 0 ? (
          <div className="rounded-xl border border-slate-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-800/80">
                  <th className="py-2 px-3 text-left   text-xs text-slate-400 font-medium">Symbol</th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">Qty</th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">Entry</th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">Price</th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">
                    UPL (from entry)
                    <span className="block text-[9px] text-amber-500/70 font-normal">floating</span>
                  </th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">
                    Today's Move
                    <span className="block text-[9px] text-slate-500 font-normal">vs prior close</span>
                  </th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">Mkt Val</th>
                </tr>
              </thead>
              <tbody>
                {d.positions.map(p => <PositionRow key={p.symbol} p={p} />)}
              </tbody>
            </table>
            <div className="bg-slate-800/40 border-t border-slate-700/50 px-4 py-2 flex flex-wrap gap-4 text-xs text-slate-500">
              <span>🟠 UPL = floating from entry, not locked in</span>
              <span>🌙 CARRY = position entered before today</span>
              <span>📊 Today's Move = what contributed to today's equity change</span>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700 bg-slate-800/30 p-8 text-center text-slate-500 text-sm">
            Flat — no open positions
          </div>
        )}
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 5 — Recent Signals
          ══════════════════════════════════════════════════ */}
      {(d?.recent_signals?.length ?? 0) > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Recent Signals</h2>
          <div className="rounded-xl border border-slate-700 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-slate-800/80">
                  {["Time", "Strategy", "Symbol", "Signal", "Qty", "Price", "Stop", "TP"].map(h => (
                    <th key={h} className="py-2 px-3 text-left text-slate-400 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.recent_signals.slice(-10).map((s, i) => (
                  <tr key={i} className="border-t border-slate-700/40 hover:bg-slate-700/20">
                    <td className="py-2 px-3 text-slate-400">
                      {s.timestamp
                        ? new Date(s.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                        : "—"}
                    </td>
                    <td className="py-2 px-3 text-slate-400 max-w-[140px] truncate">{s.strategy ?? "—"}</td>
                    <td className="py-2 px-3 text-white font-semibold">{s.symbol ?? "—"}</td>
                    <td className="py-2 px-3">
                      <span className={`font-bold ${s.signal === "buy" ? "text-emerald-400" : "text-red-400"}`}>
                        {s.signal?.toUpperCase() ?? "—"}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-slate-300">{s.qty ?? "—"}</td>
                    <td className="py-2 px-3 text-slate-300">{s.price ? `$${Number(s.price).toFixed(2)}`  : "—"}</td>
                    <td className="py-2 px-3 text-red-400/70">{s.stop  ? `$${Number(s.stop).toFixed(2)}`  : "—"}</td>
                    <td className="py-2 px-3 text-emerald-400/70">{s.tp  ? `$${Number(s.tp).toFixed(2)}`  : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

    </div>
  );
}
        </div>
      )}

      {/* ── STRATEGY TAB ── */}
      {activeTab === 'strategy' && (
        <div className="space-y-6 max-w-5xl">

          {/* Header */}
          <div className="flex items-start justify-between flex-wrap gap-3">
            <div>
              <h1 className="text-xl font-bold text-white flex items-center gap-2">
                Strategy Documentation
              </h1>
              <p className="text-sm text-slate-400 mt-1">
                Live strategy parameters · entry gates · risk rules ·
                <span className="font-mono ml-1">scripts/run_strategies.py</span>
              </p>
            </div>
            <div className="flex gap-2 flex-wrap">
              <Badge variant="success">Live Trading</Badge>
              <Badge variant="primary">Alpaca Broker</Badge>
              <Badge variant="default">GitHub Actions CI</Badge>
            </div>
          </div>

          {/* Inner Strategy Tab Nav */}
          <div className="flex gap-1 bg-slate-800 p-1 rounded-lg w-fit flex-wrap">
            {STRATEGY_TABS.map(t => (
              <button
                key={t}
                onClick={() => setStratTab(t)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  stratTab === t ? 'bg-slate-950 text-white shadow-sm' : 'text-slate-400 hover:text-slate-300'
                }`}
              >
                {t}
              </button>
            ))}
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

export default Dashboard;
