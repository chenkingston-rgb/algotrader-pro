import { useState, useEffect, useCallback } from "react";

// ─────────────────────────────────────────────────────────────────────────────
// getLivePortfolio backend function — returns Alpaca live data + SPY MA20 + VIX
// Falls back to GitHub log fields when Alpaca is unavailable.
//
// ACTUAL field names from logs (daily_latest.json / intraday_latest.json):
//   equity, last_equity, buying_power, vix, drawdown_pct, peak_equity,
//   kill_switch_active, trading_pnl, total_deposited, deposit_count,
//   mode, strategy_mode, run_timestamp
//
// COMPUTED by getLivePortfolio.ts (always present when function responds):
//   pnl_today, pnl_today_pct, realized_today, unrealized_today,
//   total_unrealized, total_exposure, exposure_pct,
//   cumulative_trading_pnl, cumulative_trading_pct,
//   return_on_total_capital, total_capital_deployed,
//   drawdown_from_peak_pct (= drawdown_pct from log),
//   ma20_bear_block, ma20_spy_close, ma20_value, ma20_gap_pct, regime_label,
//   vix_status, vix_size_label,
//   positions[], recent_signals[], equity_curve[]
// ─────────────────────────────────────────────────────────────────────────────

const FUNCTION_URL = "https://6a15a02a1ee419a7f5f9b72f.base44.app/api/functions/getLivePortfolio";
const REFRESH_MS   = 30_000;

// ── Formatting helpers ────────────────────────────────────────────────────────
const pnlColor = (v) => (v ?? 0) >= 0 ? "text-emerald-400" : "text-red-400";

const fmt$ = (v, alwaysSign = false) => {
  if (v == null || isNaN(v)) return "—";
  const sign = alwaysSign && v > 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const fmtPct = (v, alwaysSign = false) => {
  if (v == null || isNaN(v)) return "—";
  const sign = alwaysSign && v > 0 ? "+" : "";
  return `${sign}${Number(v).toFixed(2)}%`;
};

// ── Derive fields that may be missing (log fallback) ─────────────────────────
// The getLivePortfolio function computes all of these — this is just a safety net
// in case the function is down and we're reading raw log data.
function normalise(raw) {
  if (!raw) return null;
  const d = { ...raw };

  // drawdown — log uses `drawdown_pct`, function uses `drawdown_from_peak_pct`
  if (d.drawdown_from_peak_pct == null && d.drawdown_pct != null)
    d.drawdown_from_peak_pct = d.drawdown_pct;

  // cumulative P&L — log uses `trading_pnl`
  if (d.cumulative_trading_pnl == null && d.trading_pnl != null)
    d.cumulative_trading_pnl = d.trading_pnl;

  // today's P&L — derive from equity delta if not provided
  if (d.pnl_today == null && d.equity != null && d.last_equity != null)
    d.pnl_today = d.equity - d.last_equity;
  if (d.pnl_today_pct == null && d.pnl_today != null && d.last_equity > 0)
    d.pnl_today_pct = (d.pnl_today / d.last_equity) * 100;

  // realized / unrealized — best effort from positions array
  const positions = d.positions ?? d.position_details ?? [];
  if (d.total_unrealized == null)
    d.total_unrealized = positions.reduce((s, p) => s + (p.unrealized_pl ?? 0), 0);
  if (d.total_exposure == null)
    d.total_exposure = positions.reduce((s, p) => s + (p.market_value ?? 0), 0);
  if (d.exposure_pct == null && d.equity > 0)
    d.exposure_pct = (d.total_exposure / d.equity) * 100;

  // cumulative % — use trading_pnl / equity as rough proxy if not computed
  if (d.cumulative_trading_pct == null && d.cumulative_trading_pnl != null && d.equity > 0)
    d.cumulative_trading_pct = (d.cumulative_trading_pnl / d.equity) * 100;

  // total capital deployed
  if (d.total_capital_deployed == null)
    d.total_capital_deployed = (d.equity ?? 0) + (d.total_deposited ?? 0);

  // return on total capital
  if (d.return_on_total_capital == null && d.cumulative_trading_pnl != null && d.total_capital_deployed > 0)
    d.return_on_total_capital = (d.cumulative_trading_pnl / d.total_capital_deployed) * 100;

  // VIX status + size label
  if (d.vix_status == null && d.vix != null) {
    const vix = d.vix;
    d.vix_status     = vix >= 28 ? "blocked" : vix >= 20 ? "reduced" : "clear";
    d.vix_size_label = vix >= 28 ? "blocked" : vix >= 20 ? "half"    : "full";
  }

  return d;
}

// ── Small reusable card ───────────────────────────────────────────────────────
function StatCard({ label, value, sub, valueClass = "text-white", accent }) {
  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-1 ${accent || "bg-slate-800/60 border-slate-700"}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">{label}</span>
      <span className={`text-xl font-bold leading-tight ${valueClass}`}>{value}</span>
      {sub && <span className="text-xs text-slate-500 mt-0.5">{sub}</span>}
    </div>
  );
}

// ── Bear Block indicator card ─────────────────────────────────────────────────
function BearBlockCard({ d }) {
  const on    = d?.ma20_bear_block;
  const gap   = d?.ma20_gap_pct;
  const label = d?.regime_label ?? "—";
  return (
    <div className={`rounded-xl border p-4 flex flex-col items-center justify-center gap-0.5 min-w-[110px]
      ${on ? "bg-red-500/10 border-red-500/40" : "bg-emerald-500/10 border-emerald-500/30"}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Bear Block</span>
      <span className={`text-2xl font-bold mt-1 ${on ? "text-red-400" : "text-emerald-400"}`}>
        {on ? "ON" : "OFF"}
      </span>
      <span className={`text-[10px] font-semibold uppercase tracking-wide mt-0.5 ${on ? "text-red-500" : "text-emerald-600"}`}>
        {label}
      </span>
      {gap != null && (
        <span className="text-[10px] text-slate-500 mt-1">
          SPY {gap > 0 ? "+" : ""}{Number(gap).toFixed(2)}% vs MA20
        </span>
      )}
    </div>
  );
}

// ── VIX + size label card ─────────────────────────────────────────────────────
// vix_status:    "clear" | "reduced" | "blocked" | "unknown"
// vix_size_label: "full"  | "half"    | "blocked" | "—"
function VixCard({ d }) {
  const vix    = d?.vix;
  const status = d?.vix_status  ?? "unknown";
  const label  = d?.vix_size_label ?? "—";

  const col    = status === "blocked" ? "text-red-400"
               : status === "reduced" ? "text-amber-400"
               : status === "clear"   ? "text-emerald-400"
               : "text-slate-400";
  const border = status === "blocked" ? "border-red-500/40    bg-red-500/10"
               : status === "reduced" ? "border-amber-500/40  bg-amber-500/10"
               : status === "clear"   ? "border-emerald-500/30 bg-emerald-500/10"
               : "border-slate-700   bg-slate-800/60";

  return (
    <div className={`rounded-xl border p-4 flex flex-col items-center justify-center gap-0.5 min-w-[100px] ${border}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">VIX</span>
      <span className={`text-2xl font-bold mt-1 ${col}`}>
        {vix != null ? Number(vix).toFixed(1) : "—"}
      </span>
      <span className={`text-[11px] font-bold uppercase tracking-widest mt-0.5 ${col}`}>
        {label}
      </span>
    </div>
  );
}

// ── Alert banners ─────────────────────────────────────────────────────────────
function KillSwitchBanner({ d }) {
  if (!d?.kill_switch_active) return null;
  return (
    <div className="w-full rounded-xl border border-red-500 bg-red-500/10 px-4 py-3 flex items-center gap-3">
      <span className="text-lg">⚠️</span>
      <div>
        <span className="text-red-400 font-bold text-sm">KILL SWITCH ACTIVE — All new buy entries blocked</span>
        <span className="text-red-500/70 text-xs ml-2">
          Drawdown {Number(d.drawdown_from_peak_pct ?? d.drawdown_pct ?? 0).toFixed(2)}% ≥ {d.kill_switch_threshold ?? 25}% threshold
        </span>
      </div>
    </div>
  );
}

function BearBanner({ d }) {
  if (!d?.ma20_bear_block) return null;
  return (
    <div className="w-full rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-2.5 flex items-center gap-3">
      <span className="text-base">🐻</span>
      <span className="text-red-400 text-sm font-semibold">
        BEAR REGIME — SPY {fmt$(d.ma20_spy_close)} below MA20 {fmt$(d.ma20_value)}{" "}
        ({fmtPct(d.ma20_gap_pct, true)})
      </span>
      <span className="text-red-500/50 text-xs ml-auto">Buy signals blocked by engine</span>
    </div>
  );
}

function CarryBanner({ d }) {
  const count = d?.overnight_carry_count;
  const upl   = d?.overnight_carry_upl;
  if (!count) return null;
  return (
    <div className="w-full rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-2.5 flex items-center gap-3">
      <span className="text-base">🌙</span>
      <span className="text-amber-400 text-sm font-semibold">
        {count} overnight position{count > 1 ? "s" : ""} carrying into today
      </span>
      <span className={`text-xs ml-auto font-medium ${(upl ?? 0) >= 0 ? "text-emerald-400" : "text-amber-400"}`}>
        UPL from entry: {fmt$(upl, true)}
      </span>
      <span className="text-slate-500 text-xs ml-3">Unrealized — not final P&L</span>
    </div>
  );
}

// ── Position table row ────────────────────────────────────────────────────────
function PositionRow({ p }) {
  const isCarry  = p.is_overnight_carry;
  const uplColor = (p.unrealized_pl ?? 0) >= 0 ? "text-emerald-400" : "text-amber-400";
  const ctdColor = (p.change_today_pl ?? 0) >= 0 ? "text-emerald-400" : "text-amber-400";
  return (
    <tr className="border-t border-slate-700/50 hover:bg-slate-700/20 transition-colors">
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white">{p.symbol}</span>
          {isCarry ? (
            <span className="text-[9px] font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded px-1.5 py-0.5 uppercase">CARRY</span>
          ) : (
            <span className="text-[9px] font-semibold bg-sky-500/20 text-sky-400 border border-sky-500/30 rounded px-1.5 py-0.5 uppercase">TODAY</span>
          )}
        </div>
        <span className="text-[10px] text-slate-500">{p.entry_date_label ?? "—"} · {p.strategy_type ?? "—"}</span>
      </td>
      <td className="py-2.5 px-3 text-right text-slate-300">{p.qty}sh</td>
      <td className="py-2.5 px-3 text-right text-slate-400">{fmt$(p.avg_entry_price)}</td>
      <td className="py-2.5 px-3 text-right text-white font-medium">{fmt$(p.current_price)}</td>
      <td className={`py-2.5 px-3 text-right font-semibold ${uplColor}`}>
        {fmt$(p.unrealized_pl, true)}
        <div className="text-[10px] font-normal opacity-70">{fmtPct(p.unrealized_plpc, true)}</div>
      </td>
      <td className={`py-2.5 px-3 text-right text-sm ${ctdColor}`}>
        {fmt$(p.change_today_pl, true)}
        <div className="text-[10px] opacity-70">{fmtPct(p.change_today_pct, true)} today</div>
      </td>
    </tr>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────
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

  const d = data;
  // Convenience: drawdown uses whichever field is present
  const ddPct = d?.drawdown_from_peak_pct ?? d?.drawdown_pct ?? 0;

  return (
    <div className="flex flex-col gap-5 pb-10">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">AlgoTrader Pro</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {lastAt ? `Updated ${lastAt.toLocaleTimeString()}` : "Loading…"}
            {" · "}{d?.data_source === "alpaca_live" ? "🔴 Live feed" : "📄 Log snapshot"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-3 py-1 rounded-full text-xs font-semibold border
            ${d?.mode === "live"
              ? "bg-red-500/10 text-red-400 border-red-500/20"
              : "bg-slate-700 text-slate-400 border-slate-600"}`}>
            {d?.mode === "live" ? "🔴 LIVE" : "📄 Paper"}
          </span>
          <span className="px-3 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-slate-700">
            Last run:{" "}
            {d?.last_run
              ? new Date(d.last_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
              : "—"}
          </span>
        </div>
      </div>

      {/* ── Alert banners ── */}
      <KillSwitchBanner d={d} />
      <BearBanner       d={d} />
      <CarryBanner      d={d} />

      {/* ══════════════════════════════════════════════════
          ROW 1 — Today's P&L  +  Bear Block  +  VIX
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Today's P&L</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">

          {/* Realized — locked in ✓ */}
          <div className={`rounded-xl border p-4 flex flex-col gap-1
            ${(d?.realized_today ?? 0) >= 0
              ? "bg-emerald-500/8 border-emerald-500/25"
              : "bg-red-500/8 border-red-500/25"}`}>
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Realized Today</span>
            <span className={`text-2xl font-bold ${pnlColor(d?.realized_today)}`}>
              {fmt$(d?.realized_today, true)}
            </span>
            <span className="text-[10px] text-slate-500">Closed trades only — locked in ✓</span>
          </div>

          {/* Unrealized — floating */}
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/8 p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Unrealized (Open)</span>
            <span className="text-2xl font-bold text-amber-400">
              {fmt$(d?.total_unrealized, true)}
            </span>
            <span className="text-[10px] text-slate-500">
              Today's MTM: {fmt$(d?.unrealized_today, true)} · From entry
            </span>
          </div>

          {/* Bear Block */}
          <BearBlockCard d={d} />

          {/* VIX + size label */}
          <VixCard d={d} />

        </div>
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 2 — Account
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Account</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label="Equity"
            value={fmt$(d?.equity)}
            sub={`vs prev close ${fmt$(d?.last_equity ?? d?.prev_close_equity)}`}
          />
          <StatCard
            label="Buying Power"
            value={fmt$(d?.buying_power)}
            sub="Cash available for new orders"
          />
          <StatCard
            label="Total Exposure"
            value={fmt$(d?.total_exposure)}
            sub={`${Number(d?.exposure_pct ?? 0).toFixed(1)}% of equity deployed`}
          />
          <StatCard
            label="Total Equity Move"
            value={fmt$(d?.pnl_today, true)}
            sub={`${fmtPct(d?.pnl_today_pct, true)} vs prev close (includes open MTM)`}
            valueClass={pnlColor(d?.pnl_today)}
          />
        </div>
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 3 — Cumulative P&L (deposit-adjusted)
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">
          Cumulative P&L{" "}
          <span className="text-slate-600 normal-case font-normal">(deposit-adjusted — trading returns only)</span>
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className={`rounded-xl border p-4 flex flex-col gap-1
            ${(d?.cumulative_trading_pnl ?? 0) >= 0
              ? "bg-emerald-500/8 border-emerald-500/25"
              : "bg-red-500/8 border-red-500/25"}`}>
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Total Trading P&L</span>
            <span className={`text-xl font-bold ${pnlColor(d?.cumulative_trading_pnl)}`}>
              {fmt$(d?.cumulative_trading_pnl, true)}
            </span>
            <span className="text-[10px] text-slate-500">
              {fmtPct(d?.cumulative_trading_pct, true)} on starting capital
            </span>
          </div>
          <StatCard
            label="Realized (All-Time)"
            value={fmt$(d?.closed_pnl_only ?? d?.trading_pnl, true)}
            sub="Closed trades, excl. open positions"
            valueClass={pnlColor(d?.closed_pnl_only ?? d?.trading_pnl)}
          />
          <StatCard
            label="Return on Capital"
            value={fmtPct(d?.return_on_total_capital, true)}
            sub={`On ${fmt$(d?.total_capital_deployed)} deployed`}
            valueClass={pnlColor(d?.return_on_total_capital)}
          />
          <StatCard
            label="Peak Equity"
            value={fmt$(d?.peak_equity)}
            sub={`Drawdown: ${Number(ddPct).toFixed(2)}% (kill at ${d?.kill_switch_threshold ?? 25}%)`}
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

        {d?.positions?.length > 0 ? (
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
                    <span className="block text-[9px] text-amber-500/70 font-normal">amber = floating</span>
                  </th>
                  <th className="py-2 px-3 text-right  text-xs text-slate-400 font-medium">
                    Today's Move
                    <span className="block text-[9px] text-slate-500 font-normal">vs prior close</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {d.positions.map(p => <PositionRow key={p.symbol} p={p} />)}
              </tbody>
            </table>
            <div className="bg-slate-800/40 border-t border-slate-700/50 px-4 py-2 flex flex-wrap gap-4 text-xs text-slate-500">
              <span>🟠 Amber UPL = floating, not locked in</span>
              <span>🌙 CARRY = position entered before today</span>
              <span>📊 Today's Move = what hit today's equity</span>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700 bg-slate-800/30 p-8 text-center text-slate-500 text-sm">
            Flat — no open positions
          </div>
        )}
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 5 — Engine stats
          ══════════════════════════════════════════════════ */}
      <section>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Engine</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Runs Today"   value={d?.todays_runs    ?? "—"} sub={`${d?.total_runs ?? "—"} all-time`} />
          <StatCard label="Orders Today" value={d?.todays_orders  ?? "—"} sub="Filled orders this session" />
          <StatCard
            label="Drawdown from Peak"
            value={`${Number(ddPct).toFixed(2)}%`}
            sub={`Peak: ${fmt$(d?.peak_equity)} · Kill at ${d?.kill_switch_threshold ?? 25}%`}
            valueClass={ddPct >= 20 ? "text-red-400" : ddPct >= 10 ? "text-amber-400" : "text-emerald-400"}
          />
          <StatCard
            label="Capital Deployed"
            value={fmt$(d?.total_capital_deployed)}
            sub={`Deposits: ${fmt$(d?.total_deposited)}`}
          />
        </div>
      </section>

      {/* ══════════════════════════════════════════════════
          ROW 6 — Recent signals
          ══════════════════════════════════════════════════ */}
      {d?.recent_signals?.length > 0 && (
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
                    <td className="py-2 px-3 text-slate-300">{s.price  ? `$${Number(s.price).toFixed(2)}`  : "—"}</td>
                    <td className="py-2 px-3 text-red-400/70">{s.stop  ? `$${Number(s.stop).toFixed(2)}`   : "—"}</td>
                    <td className="py-2 px-3 text-emerald-400/70">{s.tp ? `$${Number(s.tp).toFixed(2)}`    : "—"}</td>
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
