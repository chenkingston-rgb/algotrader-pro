import { useState, useEffect, useCallback } from "react";

// ─────────────────────────────────────────────────────────────────────────────
// AlgoTrader v3.0-ensemble — Monitor
//
// Data is read DIRECTLY from GitHub raw — zero Base44 integration credits,
// no entities, no backend function, no paid plan. Same pattern the v9
// dashboard used, pointed at the new payload.
//
// The trading system (private repo algotrader-pro-v2) publishes the payload
// to THIS public repo on every run. Everything rendered below comes from that
// one file.
//
// Panel order is deliberate: is the machine ALIVE → is it IN SYNC with the
// broker → what does it hold → how is it doing. This strategy trades ~12
// times a year; daily performance is noise and is shown last.
// ─────────────────────────────────────────────────────────────────────────────

const DATA_URL =
  "https://raw.githubusercontent.com/chenkingston-rgb/algotrader-pro/main/logs/v3_dashboard.json";
const REFRESH_MS = 900_000; // 15 min — the engine writes at most once a day

const money = (n) =>
  n == null ? "—" : Number(n).toLocaleString("en-US", { style: "currency", currency: "USD" });
const pct = (n, d = 2) => (n == null ? "—" : `${Number(n).toFixed(d)}%`);

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [loading, setLoading] = useState(true);
  const [fetchedAt, setFetchedAt] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${DATA_URL}?t=${Date.now()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(`GitHub returned ${res.status}`);
      setData(await res.json());
      setErr(null);
      setFetchedAt(new Date());
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  if (loading) return <Shell><p className="text-slate-400">Loading…</p></Shell>;

  if (err && !data)
    return (
      <Shell>
        <Banner tone="bad" title="Cannot reach the data file">
          {err}. The trading system publishes <code>logs/v3_dashboard.json</code> to
          this repository on every run. If this persists, the job may have stopped.
        </Banner>
      </Shell>
    );

  const acct = data.account ?? {};
  const risk = data.risk ?? {};
  const regime = data.regime ?? {};
  const sched = data.schedule ?? {};
  const health = data.health ?? {};
  const positions = data.positions ?? [];
  const sleeves = data.sleeves ?? {};
  const exp = data.expectations ?? {};

  // Staleness is computed HERE, from the payload's own timestamp — the most
  // important signal on the page. A dashboard that looks fine while the engine
  // is dead is worse than no dashboard.
  const ageH = data.generated_at
    ? (Date.now() - new Date(data.generated_at).getTime()) / 3_600_000
    : null;
  const stale = ageH != null && ageH > 26;

  const target = new Set(
    positions.filter((p) => (p.target_weight_pct ?? 0) > 0).map((p) => p.symbol));
  const held = new Set(positions.filter((p) => (p.market_value ?? 0) > 0).map((p) => p.symbol));
  const inSync = [...target].every((s) => held.has(s)) && [...held].every((s) => target.has(s));

  const alerts = [];
  if (stale) alerts.push(`Payload is ${ageH.toFixed(1)} hours old — the trading job may have stopped.`);
  if (health.last_run_ok === false) alerts.push("Last run FAILED — check GitHub Actions.");
  if (!inSync) alerts.push(
    `Book differs from target — holding [${[...held].join(", ") || "nothing"}], target [${[...target].join(", ")}].`);
  if (risk.kill_switch_active) alerts.push("KILL SWITCH ACTIVE — all trading halted, human review required.");
  if (risk.halt_active) alerts.push("DRAWDOWN HALT ACTIVE — awaiting acknowledgement.");
  if (health.alert_webhook_configured === false) alerts.push("ALERT_WEBHOOK not configured — failures reach the log only.");
  if (health.heartbeat_configured === false) alerts.push("HEARTBEAT_URL not configured — no dead-man's switch.");

  const healthy = !stale && health.last_run_ok !== false && inSync && !risk.kill_switch_active;

  return (
    <Shell>
      <div className="flex items-center justify-between flex-wrap gap-3 mb-5">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">AlgoTrader v3 — Ensemble</h1>
          <p className="text-sm text-slate-400">
            {data.strategy_version} · payload {data.generated_at?.slice(0, 19)?.replace("T", " ")} UTC
            {fetchedAt && <> · refreshed {fetchedAt.toLocaleTimeString()}</>}
          </p>
        </div>
        <span className={`px-3 py-1 rounded-full text-sm font-semibold ${
          healthy ? "bg-emerald-600 text-white" : "bg-red-600 text-white"}`}>
          {healthy ? "HEALTHY" : "NEEDS ATTENTION"}
        </span>
      </div>

      {alerts.length > 0 && (
        <Banner tone="bad" title={`${alerts.length} item${alerts.length > 1 ? "s" : ""} need attention`}>
          <ul className="list-disc ml-5 space-y-1">{alerts.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </Banner>
      )}

      {/* 1 ── IS IT ALIVE */}
      <Grid cols={4}>
        <Stat label="Last run" value={health.last_run_ok === false ? "FAILED" : "success"}
              tone={health.last_run_ok === false ? "bad" : "good"} />
        <Stat label="Data age" value={ageH == null ? "—" : `${ageH.toFixed(1)}h`}
              tone={stale ? "bad" : "good"} sub={stale ? "job may have stopped" : "fresh"} />
        <Stat label="Broker in sync" value={inSync ? "yes" : "no"} tone={inSync ? "good" : "bad"} />
        <Stat label="Next rebalance" value={sched.next_rebalance ?? "—"}
              sub={sched.days_to_next_rebalance != null ? `${sched.days_to_next_rebalance} days` : ""} />
      </Grid>

      {/* 2 ── MONEY */}
      <Grid cols={4}>
        <Stat label="Equity" value={money(acct.equity)} big />
        <Stat label="Cash" value={money(acct.cash)} sub={`${pct(acct.invested_pct)} invested`} />
        <Stat label="P&L vs deposits" value={money(data.total_pnl)} big
              tone={(data.total_pnl ?? 0) >= 0 ? "good" : "bad"}
              sub={data.deposit_basis ? `basis ${money(data.deposit_basis)}` : ""} />
        <Stat label="Positions" value={acct.position_count ?? positions.filter(p => p.market_value > 0).length} />
      </Grid>

      {/* 3 ── REGIME + DRAWDOWN */}
      <div className="grid md:grid-cols-2 gap-4 mb-4">
        <Card title="Regime gate">
          <div className="flex items-center gap-3 mb-2">
            <span className={`px-2 py-0.5 rounded text-xs font-semibold ${
              regime.on ? "bg-emerald-600 text-white" : "bg-amber-600 text-white"}`}>
              {regime.label ?? "UNKNOWN"}
            </span>
            <span className="text-sm text-slate-400">
              SPY {regime.close ?? "—"} vs 200-day avg {regime.ma ?? "—"}
            </span>
          </div>
          <div className="text-3xl font-bold text-slate-100">{pct(regime.gap_pct)}</div>
          <p className="text-xs text-slate-400 mt-2">
            How far the S&P 500 sits above its 200-day average. If this turns negative,
            two of the three sleeves move fully to cash at the next month-end.
            {regime.gap_pct != null && regime.gap_pct < 2 && (
              <span className="text-amber-400 font-semibold"> ⚠ Close to flipping.</span>
            )}
          </p>
        </Card>

        <Card title="Drawdown">
          <div className="text-3xl font-bold text-slate-100">{pct(risk.drawdown_pct)}</div>
          <p className="text-xs text-slate-400 mt-2">
            Peak {money(risk.peak_equity)} · {pct(risk.distance_to_halt_pct)} of room before
            the {pct(risk.halt_threshold_pct, 0)} halt · kill at {pct(risk.kill_threshold_pct, 0)}
          </p>
          <p className="text-xs text-slate-500 mt-2">
            Backtested worst case was {pct(risk.backtested_max_drawdown_pct, 1)} — a drawdown
            near that size is expected behaviour, not a malfunction.
          </p>
        </Card>
      </div>

      {/* 4 ── SLEEVES */}
      <Card title="Three sleeves, one third of capital each" className="mb-4">
        <div className="grid md:grid-cols-3 gap-4">
          {Object.entries(sleeves).map(([name, s]) => (
            <div key={name} className="border border-slate-700 rounded-lg p-3">
              <div className="font-semibold capitalize text-slate-200">{name.replace(/_/g, " ")}</div>
              <div className="text-xs text-slate-400 mb-2">{pct(s.capital_share_pct, 1)} of capital</div>
              {Object.entries(s.holdings ?? {}).map(([sym, w]) => (
                <div key={sym} className="flex justify-between text-sm text-slate-300">
                  <span>{sym}</span><span className="text-slate-400">{pct(w, 1)}</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      </Card>

      {/* 5 ── POSITIONS */}
      <Card title="Positions — target vs actual" className="mb-4">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-slate-400 border-b border-slate-700">
              <tr>
                <th className="py-2">Symbol</th><th>Qty</th><th>Value</th>
                <th>Target</th><th>Actual</th><th>Drift</th><th>Sleeves</th>
              </tr>
            </thead>
            <tbody>
              {[...positions]
                .sort((a, b) => (b.target_weight_pct ?? 0) - (a.target_weight_pct ?? 0))
                .map((p) => {
                  const drift = p.drift_pct ?? 0;
                  const sl = Object.keys(p.sleeves ?? {}).join(", ") || "—";
                  return (
                    <tr key={p.symbol}
                        className={`border-b border-slate-800 ${!p.in_target ? "bg-amber-950/40" : ""}`}>
                      <td className="py-2 font-medium text-slate-200">
                        {p.symbol}
                        {!p.in_target && <span className="ml-2 text-xs text-amber-400">to be sold</span>}
                      </td>
                      <td className="text-slate-300">{p.qty ?? 0}</td>
                      <td className="text-slate-300">{money(p.market_value)}</td>
                      <td className="text-slate-300">{pct(p.target_weight_pct)}</td>
                      <td className="text-slate-300">{pct(p.actual_weight_pct)}</td>
                      <td className={Math.abs(drift) > 2 ? "text-red-400 font-semibold" : "text-slate-300"}>
                        {pct(drift)}
                      </td>
                      <td className="text-xs text-slate-500">{sl}</td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-xs text-slate-500 leading-relaxed">
        {exp.note ??
          "This strategy rebalances twelve times a year. Most days the correct status is 'nothing to do'."}
      </p>
      <p className="text-xs text-slate-600 mt-2">
        Source: <code>logs/v3_dashboard.json</code> in this repository, published by
        the private trading repo on every run. Read directly from GitHub — no Base44
        integration credits are consumed by this page.
      </p>
    </Shell>
  );
}

/* ── presentational helpers ──────────────────────────────────────────────── */

function Shell({ children }) {
  return <div className="p-6 max-w-7xl mx-auto min-h-screen bg-slate-950">{children}</div>;
}

function Grid({ cols = 4, children }) {
  return <div className={`grid grid-cols-2 md:grid-cols-${cols} gap-3 mb-4`}>{children}</div>;
}

function Card({ title, children, className = "" }) {
  return (
    <div className={`bg-slate-900 border border-slate-800 rounded-lg p-4 ${className}`}>
      {title && <div className="text-sm font-semibold text-slate-300 mb-3">{title}</div>}
      {children}
    </div>
  );
}

function Stat({ label, value, sub, tone, big }) {
  const colour =
    tone === "good" ? "text-emerald-400" : tone === "bad" ? "text-red-400" : "text-slate-100";
  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`${big ? "text-2xl" : "text-lg"} font-bold ${colour}`}>{value}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function Banner({ tone, title, children }) {
  const cls = tone === "bad"
    ? "bg-red-950 border-red-800 text-red-200"
    : "bg-emerald-950 border-emerald-800 text-emerald-200";
  return (
    <div className={`border rounded-lg p-4 mb-4 ${cls}`}>
      <div className="font-semibold mb-1">{title}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}
