import { Outlet, Link, useLocation } from "react-router-dom";
import { LayoutDashboard } from "lucide-react";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
];

export default function Layout() {
  const { pathname } = useLocation();
  return (
    <div className="flex h-screen bg-slate-950 text-slate-100">
      {/* Sidebar */}
      <aside className="w-52 flex-shrink-0 border-r border-slate-800 bg-slate-900/80 flex flex-col">
        <div className="px-5 py-5 border-b border-slate-800">
          <span className="text-sm font-bold text-white tracking-tight">AlgoTrader Pro</span>
          <span className="block text-[10px] text-slate-500 mt-0.5">v8.0 · Live</span>
        </div>
        <nav className="flex-1 px-3 py-4 flex flex-col gap-1">
          {navItems.map(({ to, icon: Icon, label }) => (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors
                ${pathname === to
                  ? "bg-blue-600/20 text-blue-400 border border-blue-500/20"
                  : "text-slate-400 hover:text-white hover:bg-slate-800"}`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </Link>
          ))}
        </nav>
        <div className="px-5 py-4 border-t border-slate-800">
          <span className="text-[10px] text-slate-600">GitHub-synced · Base44</span>
        </div>
      </aside>
      {/* Main */}
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  );
}
