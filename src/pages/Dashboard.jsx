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

const FUNCTION_URL = "https://6a15a02a1ee419a7f5f9b72f.base44.app/api/functions/getLivePortfolio";
const REFRESH_MS   = 30_000;

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
export default function Dashboard() {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);
  const [lastAt,  setLastAt]  = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const r = await fetch(FUNCTION_URL, { method: "GET" });
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
    return () => clearInterval(t);
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
