import { useState, useEffect, useCallback } from "react";

const FUNCTION_URL = "https://6a15a02a1ee419a7f5f9b72f.base44.app/api/functions/getLivePortfolio";
const REFRESH_MS   = 30_000; // refresh every 30s

// ── Colour helpers ──────────────────────────────────────────────────────────
const pnlColor = (v) => v >= 0 ? "text-emerald-400" : "text-red-400";
const pnlBg    = (v) => v >= 0 ? "bg-emerald-500/10 border-emerald-500/20" : "bg-red-500/10 border-red-500/20";
const fmt$     = (v, always_sign=false) => {
  if (v == null) return "—";
  const sign = always_sign && v > 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};
const fmtPct   = (v, always_sign=false) => {
  if (v == null) return "—";
  const sign = always_sign && v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
};

// ── Mini stat card ──────────────────────────────────────────────────────────
function StatCard({ label, value, sub, valueClass = "text-white", accent }) {
  return (
    <div className={`rounded-xl border p-4 flex flex-col gap-1 ${accent || "bg-slate-800/60 border-slate-700"}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">{label}</span>
      <span className={`text-xl font-bold leading-tight ${valueClass}`}>{value}</span>
      {sub && <span className="text-xs text-slate-500 mt-0.5">{sub}</span>}
    </div>
  );
}

// ── Bear Block badge ─────────────────────────────────────────────────────────
function BearBlockCard({ data }) {
  const on = data?.ma20_bear_block;
  const gap = data?.ma20_gap_pct;
  const label = data?.regime_label ?? "—";
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
          SPY {gap > 0 ? "+" : ""}{gap.toFixed(2)}% vs MA20
        </span>
      )}
    </div>
  );
}

// ── VIX card ─────────────────────────────────────────────────────────────────
function VixCard({ data }) {
  const vix    = data?.vix;
  const status = data?.vix_status ?? "unknown";
  const label  = data?.vix_size_label ?? "—";
  const col = status === "blocked" ? "text-red-400"
            : status === "reduced" ? "text-amber-400"
            : status === "clear"   ? "text-emerald-400"
            : "text-slate-400";
  const border = status === "blocked" ? "border-red-500/40 bg-red-500/10"
               : status === "reduced" ? "border-amber-500/40 bg-amber-500/10"
               : status === "clear"   ? "border-emerald-500/30 bg-emerald-500/10"
               : "border-slate-700 bg-slate-800/60";
  return (
    <div className={`rounded-xl border p-4 flex flex-col items-center justify-center gap-0.5 min-w-[100px] ${border}`}>
      <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">VIX</span>
      <span className={`text-2xl font-bold mt-1 ${col}`}>
        {vix != null ? vix.toFixed(1) : "—"}
      </span>
      <span className={`text-[11px] font-bold uppercase tracking-widest mt-0.5 ${col}`}>
        {label}
      </span>
    </div>
  );
}

// ── Kill switch banner ────────────────────────────────────────────────────────
function KillSwitchBanner({ data }) {
  if (!data?.kill_switch_active) return null;
  return (
    <div className="w-full rounded-xl border border-red-500 bg-red-500/10 px-4 py-3 flex items-center gap-3">
      <span className="text-red-400 text-lg">⚠️</span>
      <div>
        <span className="text-red-400 font-bold text-sm">KILL SWITCH ACTIVE — New buy entries blocked</span>
        <span className="text-red-500/70 text-xs ml-2">
          Drawdown {data.drawdown_from_peak_pct?.toFixed(2)}% ≥ {data.kill_switch_threshold}% threshold
        </span>
      </div>
    </div>
  );
}

// ── Bear block banner (when ON, show a softer info bar) ───────────────────────
function BearBanner({ data }) {
  if (!data?.ma20_bear_block) return null;
  return (
    <div className="w-full rounded-xl border border-red-500/40 bg-red-500/8 px-4 py-2.5 flex items-center gap-3">
      <span className="text-red-400 text-base">🐻</span>
      <span className="text-red-400 text-sm font-semibold">
        BEAR REGIME — SPY {fmt$(data.ma20_spy_close)} below MA20 {fmt$(data.ma20_value)} ({fmtPct(data.ma20_gap_pct, true)})
      </span>
      <span className="text-red-500/60 text-xs ml-auto">New buy signals blocked by engine</span>
    </div>
  );
}

// ── Overnight carry banner ─────────────────────────────────────────────────────
function CarryBanner({ data }) {
  const count = data?.overnight_carry_count;
  const upl   = data?.overnight_carry_upl;
  if (!count) return null;
  return (
    <div className="w-full rounded-xl border border-amber-500/30 bg-amber-500/8 px-4 py-2.5 flex items-center gap-3">
      <span className="text-amber-400 text-base">🌙</span>
      <span className="text-amber-400 text-sm font-semibold">
        {count} overnight position{count > 1 ? "s" : ""} carrying into today
      </span>
      <span className={`text-xs ml-auto font-medium ${upl >= 0 ? "text-emerald-400" : "text-amber-400"}`}>
        UPL from entry: {fmt$(upl, true)}
      </span>
      <span className="text-slate-500 text-xs ml-3">Unrealized — not final P&L</span>
    </div>
  );
}

// ── Position row ──────────────────────────────────────────────────────────────
function PositionRow({ p }) {
  const isCarry  = p.is_overnight_carry;
  const uplColor = p.unrealized_pl >= 0 ? "text-emerald-400" : "text-amber-400";
  const ctdColor = p.change_today_pl >= 0 ? "text-emerald-400" : "text-amber-400";
  return (
    <tr className="border-t border-slate-700/50 hover:bg-slate-700/20 transition-colors">
      <td className="py-2.5 px-3">
        <div className="flex items-center gap-2">
          <span className="font-bold text-white">{p.symbol}</span>
          {isCarry ? (
            <span className="text-[9px] font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/30 rounded px-1.5 py-0.5 uppercase tracking-wide">
              CARRY
            </span>
          ) : (
            <span className="text-[9px] font-semibold bg-sky-500/20 text-sky-400 border border-sky-500/30 rounded px-1.5 py-0.5 uppercase tracking-wide">
              TODAY
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-500">{p.entry_date_label} · {p.strategy_type}</span>
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
      const d = await r.json();
      if (d.error) throw new Error(d.error);
      setData(d);
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

  return (
    <div className="flex flex-col gap-5 pb-10">

      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">AlgoTrader Pro</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            {lastAt ? `Updated ${lastAt.toLocaleTimeString()}` : "Loading…"}
            {" · "}{d?.data_source === "alpaca_live" ? "🔴 Live" : "📄 Snapshot"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-3 py-1 rounded-full text-xs font-semibold border
            ${d?.mode === "live" ? "bg-red-500/10 text-red-400 border-red-500/20"
                                 : "bg-slate-700 text-slate-400 border-slate-600"}`}>
            {d?.mode === "live" ? "🔴 LIVE" : "📄 Paper"}
          </span>
          <span className="px-3 py-1 rounded-full text-xs bg-slate-800 text-slate-400 border border-slate-700">
            Last run: {d?.last_run ? new Date(d.last_run).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
          </span>
        </div>
      </div>

      {/* ── Alert banners ── */}
      <KillSwitchBanner data={d} />
      <BearBanner       data={d} />
      <CarryBanner      data={d} />

      {/* ══════════════════════════════════════════════════════════
          ROW 1 — Core P&L (the main fix)
          Three columns: Realized | Unrealized + Bear Block | VIX
          ══════════════════════════════════════════════════════════ */}
      <div>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Today's P&L</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">

          {/* Realized today — PRIMARY metric, locked-in */}
          <div className={`rounded-xl border p-4 flex flex-col gap-1
            ${d?.realized_today >= 0 ? "bg-emerald-500/8 border-emerald-500/25"
                                     : "bg-red-500/8 border-red-500/25"}`}>
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Realized Today</span>
            <span className={`text-2xl font-bold ${pnlColor(d?.realized_today)}`}>
              {fmt$(d?.realized_today, true)}
            </span>
            <span className="text-[10px] text-slate-500">Closed trades only — locked in ✓</span>
          </div>

          {/* Unrealized — amber/floating */}
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/8 p-4 flex flex-col gap-1">
            <span className="text-xs text-slate-400 uppercase tracking-wider font-medium">Unrealized (Open)</span>
            <span className="text-2xl font-bold text-amber-400">
              {fmt$(d?.total_unrealized, true)}
            </span>
            <span className="text-[10px] text-slate-500">
              Today's MTM: {fmt$(d?.unrealized_today, true)} · From entry
            </span>
          </div>

          {/* Bear Block — next to unrealized */}
          <BearBlockCard data={d} />

          {/* VIX + size label */}
          <VixCard data={d} />

        </div>
      </div>

      {/* ══════════════════════════════════════════════════════════
          ROW 2 — Account metrics
          ══════════════════════════════════════════════════════════ */}
      <div>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Account</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label="Equity"
            value={fmt$(d?.equity)}
            sub={`vs prev close ${fmt$(d?.prev_close_equity)}`}
          />
          <StatCard
            label="Buying Power"
            value={fmt$(d?.buying_power)}
            sub="Cash available for new orders"
          />
          <StatCard
            label="Total Exposure"
            value={fmt$(d?.total_exposure)}
            sub={`${d?.exposure_pct?.toFixed(1)}% of equity deployed`}
          />
          <StatCard
            label="Total Equity Move"
            value={fmt$(d?.pnl_today, true)}
            sub={`${fmtPct(d?.pnl_today_pct, true)} vs prev close (includes open MTM)`}
            valueClass={pnlColor(d?.pnl_today)}
          />
        </div>
      </div>

      {/* ══════════════════════════════════════════════════════════
          ROW 3 — Cumulative P&L (deposit-adjusted)
          ══════════════════════════════════════════════════════════ */}
      <div>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">
          Cumulative P&L <span className="text-slate-600 normal-case">(deposit-adjusted — strategy returns only)</span>
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard
            label="Total Trading P&L"
            value={fmt$(d?.cumulative_trading_pnl, true)}
            sub={`${fmtPct(d?.cumulative_trading_pct, true)} on starting capital`}
            valueClass={pnlColor(d?.cumulative_trading_pnl)}
            accent={d?.cumulative_trading_pnl >= 0
              ? "bg-emerald-500/8 border-emerald-500/25 border"
              : "bg-red-500/8 border-red-500/25 border"}
          />
          <StatCard
            label="Realized (All-Time)"
            value={fmt$(d?.closed_pnl_only, true)}
            sub="Closed trades only, no open positions"
            valueClass={pnlColor(d?.closed_pnl_only)}
          />
          <StatCard
            label="Return on Capital"
            value={fmtPct(d?.return_on_total_capital, true)}
            sub={`On $${(d?.total_capital_deployed / 1000)?.toFixed(1)}k deployed (incl. deposits)`}
            valueClass={pnlColor(d?.return_on_total_capital)}
          />
          <StatCard
            label="Peak Equity"
            value={fmt$(d?.peak_equity)}
            sub={`Drawdown: ${d?.drawdown_from_peak_pct?.toFixed(2)}% (limit: ${d?.kill_switch_threshold}%)`}
          />
        </div>
      </div>

      {/* ══════════════════════════════════════════════════════════
          ROW 4 — Open Positions table
          ══════════════════════════════════════════════════════════ */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest">
            Open Positions ({d?.position_count ?? 0})
          </h2>
          {d?.overnight_carry_count > 0 && (
            <span className="text-[10px] text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-2 py-0.5">
              🌙 {d.overnight_carry_count} overnight carry — UPL is NOT today's P&L
            </span>
          )}
        </div>

        {d?.positions?.length > 0 ? (
          <div className="rounded-xl border border-slate-700 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-slate-800/80">
                  <th className="py-2 px-3 text-left text-xs text-slate-400 font-medium">Symbol</th>
                  <th className="py-2 px-3 text-right text-xs text-slate-400 font-medium">Qty</th>
                  <th className="py-2 px-3 text-right text-xs text-slate-400 font-medium">Entry</th>
                  <th className="py-2 px-3 text-right text-xs text-slate-400 font-medium">Price</th>
                  <th className="py-2 px-3 text-right text-xs text-slate-400 font-medium">
                    UPL (from entry)
                    <span className="block text-[9px] text-amber-500/70 font-normal">amber = floating</span>
                  </th>
                  <th className="py-2 px-3 text-right text-xs text-slate-400 font-medium">
                    Today's Move
                    <span className="block text-[9px] text-slate-500 font-normal">vs prior close</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {d.positions.map(p => <PositionRow key={p.symbol} p={p} />)}
              </tbody>
            </table>

            {/* P&L note footer */}
            <div className="bg-slate-800/40 border-t border-slate-700/50 px-4 py-2 flex items-center gap-6 text-xs text-slate-500">
              <span>🟠 Amber UPL = floating, not locked in</span>
              <span>🌙 CARRY badge = position entered before today</span>
              <span>📊 "Today's Move" = what affected today's equity</span>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-slate-700 bg-slate-800/30 p-8 text-center text-slate-500 text-sm">
            Flat — no open positions
          </div>
        )}
      </div>

      {/* ══════════════════════════════════════════════════════════
          ROW 5 — Engine stats
          ══════════════════════════════════════════════════════════ */}
      <div>
        <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-2">Engine Status</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatCard label="Runs Today"    value={d?.todays_runs ?? "—"}   sub={`${d?.total_runs ?? "—"} all-time`} />
          <StatCard label="Orders Today"  value={d?.todays_orders ?? "—"} sub="Executed orders this session" />
          <StatCard
            label="Drawdown"
            value={`${d?.drawdown_from_peak_pct?.toFixed(2) ?? "—"}%`}
            sub={`Peak: ${fmt$(d?.peak_equity)} · Limit: ${d?.kill_switch_threshold}%`}
            valueClass={
              (d?.drawdown_from_peak_pct ?? 0) >= 20 ? "text-red-400"
            : (d?.drawdown_from_peak_pct ?? 0) >= 10 ? "text-amber-400"
            : "text-emerald-400"
            }
          />
          <StatCard
            label="Capital Deployed"
            value={fmt$(d?.total_capital_deployed)}
            sub={`Start: ${fmt$(d?.start_equity)} · Deposits: ${fmt$(d?.total_deposited)}`}
          />
        </div>
      </div>

      {/* ══════════════════════════════════════════════════════════
          ROW 6 — Recent signals
          ══════════════════════════════════════════════════════════ */}
      {d?.recent_signals?.length > 0 && (
        <div>
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
                      {s.timestamp ? new Date(s.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}
                    </td>
                    <td className="py-2 px-3 text-slate-400 max-w-[140px] truncate">{s.strategy}</td>
                    <td className="py-2 px-3 text-white font-semibold">{s.symbol}</td>
                    <td className="py-2 px-3">
                      <span className={`font-bold ${s.signal === "buy" ? "text-emerald-400" : "text-red-400"}`}>
                        {s.signal?.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2 px-3 text-slate-300">{s.qty}</td>
                    <td className="py-2 px-3 text-slate-300">{s.price ? `$${s.price.toFixed(2)}` : "—"}</td>
                    <td className="py-2 px-3 text-red-400/70">{s.stop  ? `$${s.stop.toFixed(2)}`  : "—"}</td>
                    <td className="py-2 px-3 text-emerald-400/70">{s.tp ? `$${s.tp.toFixed(2)}`   : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

    </div>
  );
}
