import { Outlet } from "react-router-dom";

// v3 is a SINGLE-PAGE monitor. The v9 sidebar linked to Signals, Trades,
// Strategies, RiskControl, StrategyDocs and AppSettings — all of which
// described the retired intraday engine and would now render dead data.
// One page, one job: tell you whether the machine is healthy.
export default function Layout() {
  return (
    <div className="min-h-screen bg-slate-950">
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-4">
          <span className="font-bold text-slate-100">AlgoTrader v3</span>
          <span className="text-xs text-slate-500">three-sleeve monthly ensemble</span>
          <a href="https://github.com/chenkingston-rgb/algotrader-pro-v2/actions"
             target="_blank" rel="noreferrer"
             className="ml-auto text-xs text-slate-400 hover:text-slate-200 underline">
            GitHub Actions ↗
          </a>
        </div>
      </header>
      <main><Outlet /></main>
    </div>
  );
}
