const REPO          = "chenkingston-rgb/algotrader-pro";
const GH_TOKEN      = Deno.env.get("GITHUB_ACCESS_TOKEN") ?? "";
const ALPACA_KEY    = Deno.env.get("ALPACA_LIVE_KEY")    ?? Deno.env.get("ALPACA_PAPER_KEY")    ?? "";
const ALPACA_SECRET = Deno.env.get("ALPACA_LIVE_SECRET") ?? Deno.env.get("ALPACA_PAPER_SECRET") ?? "";
const ALPACA_BASE   = "https://api.alpaca.markets";

// ── Helpers ────────────────────────────────────────────────────────────────

async function fetchGithubJson(path: string): Promise<any> {
  const url = `https://api.github.com/repos/${REPO}/contents/${path}`;
  const res = await fetch(url, {
    headers: {
      "Authorization": `Bearer ${GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
    },
  });
  if (!res.ok) throw new Error(`GitHub ${path}: ${res.status}`);
  const meta = await res.json();
  const b64  = (meta.content as string).replace(/\n/g, "");
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

async function alpacaGet(path: string): Promise<any> {
  const res = await fetch(`${ALPACA_BASE}${path}`, {
    headers: {
      "APCA-API-KEY-ID":     ALPACA_KEY,
      "APCA-API-SECRET-KEY": ALPACA_SECRET,
    },
  });
  if (!res.ok) throw new Error(`Alpaca ${path}: ${res.status}`);
  return res.json();
}

const cors = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
  "Content-Type": "application/json",
};

// ── Handler ────────────────────────────────────────────────────────────────

export default async function handler(req: Request): Promise<Response> {
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });

  try {
    const [r1, r2, r3, r4, r5, r6] = await Promise.allSettled([
      fetchGithubJson("logs/intraday_latest.json"),
      fetchGithubJson("logs/daily_latest.json"),
      fetchGithubJson("logs/run_history.json"),
      alpacaGet("/v2/account"),
      alpacaGet("/v2/positions"),
      fetchGithubJson("logs/live_baseline.json"),
    ]);

    const intraday   = r1.status === "fulfilled" ? r1.value : null;
    const daily      = r2.status === "fulfilled" ? r2.value : null;
    const runs: any[] = r3.status === "fulfilled" && Array.isArray(r3.value) ? r3.value : [];
    const alpacaAcct = r4.status === "fulfilled" ? r4.value : null;
    const alpacaPos  = r5.status === "fulfilled" && Array.isArray(r5.value) ? r5.value : [];
    const baseline   = r6.status === "fulfilled" ? r6.value : null;

    const primary = intraday ?? daily;
    if (!primary && !alpacaAcct) {
      return new Response(JSON.stringify({ error: "No data available" }), { status: 503, headers: cors });
    }

    // ── Core account values (live from Alpaca) ─────────────────────────────
    const liveEquity  = alpacaAcct ? parseFloat(alpacaAcct.equity       ?? "0") : (primary?.equity ?? 0);
    const prevClose   = alpacaAcct ? parseFloat(alpacaAcct.last_equity   ?? "0") : 0;

    // ── BUYING POWER ───────────────────────────────────────────────────────
    // Alpaca definition: cash available to place NEW orders RIGHT NOW.
    // = cash balance minus any reserved margin for open positions.
    // For a cash account (no margin): buying_power = cash = equity - position market value.
    // Does NOT include unrealised gains on open positions until they are closed.
    // This is the correct field to gate new orders against.
    const buyingPower = alpacaAcct ? parseFloat(alpacaAcct.buying_power  ?? "0") : (primary?.buying_power ?? 0);
    const cash        = alpacaAcct ? parseFloat(alpacaAcct.cash          ?? "0") : 0;

    // ── P&L TODAY ──────────────────────────────────────────────────────────
    // Alpaca last_equity = official previous-close equity snapshot.
    // pnl_today = current equity minus that snapshot = today's realised + unrealised combined.
    const pnlToday  = prevClose > 0 ? liveEquity - prevClose : 0;
    const pnlTodPct = prevClose > 0 ? (pnlToday / prevClose) * 100 : 0;

    // ── LIVE POSITIONS (real-time prices from Alpaca) ──────────────────────
    const positions = alpacaPos.map((p: any) => ({
      symbol:          p.symbol,
      qty:             parseFloat(p.qty              ?? "0"),
      avg_entry_price: parseFloat(p.avg_entry_price  ?? "0"),
      current_price:   parseFloat(p.current_price    ?? "0"),
      market_value:    parseFloat(p.market_value      ?? "0"),
      unrealized_pl:   parseFloat(p.unrealized_pl     ?? "0"),
      unrealized_plpc: parseFloat(p.unrealized_plpc   ?? "0") * 100,
      change_today:    parseFloat(p.change_today       ?? "0"),
    }));

    // ── TOTAL EXPOSURE ─────────────────────────────────────────────────────
    // = sum of market value of ALL open positions.
    // Measures: how much of the account is currently deployed in the market
    //           and therefore at risk of loss if prices move against us.
    // NOT the same as buying_power (which is what's NOT deployed).
    // exposure_pct = exposure / equity — tells you what % of the account is "in play".
    const totalExposure    = positions.reduce((s: number, p: any) => s + p.market_value, 0);
    const totalUnrealised  = positions.reduce((s: number, p: any) => s + p.unrealized_pl, 0);
    const exposurePct      = liveEquity > 0 ? (totalExposure / liveEquity) * 100 : 0;

    // ── CUMULATIVE P&L — DEPOSIT-ADJUSTED ─────────────────────────────────
    //
    // Problem with naive equity growth: equity = start_equity + deposits + trading_pnl
    // So (equity - start_equity) overstates trading performance whenever capital is added.
    //
    // Solution (stored in live_baseline.json, maintained by detect_deposit() in engine):
    //   total_trading_pnl = cumulative P&L from strategy activity ONLY.
    //                       Each engine run computes: delta = equity - last_known_equity.
    //                       If delta looks like a deposit (>$500 AND >1%) → classified as deposit.
    //                       Otherwise → added to total_trading_pnl.
    //   total_deposited   = sum of all cash deposits detected.
    //   start_equity      = account equity at live trading inception (Jun 2 2026).
    //
    // cumulative_trading_pnl = total_trading_pnl from baseline (closed P&L component)
    //                        + totalUnrealised (open position component, mark-to-market)
    //
    // This gives true trading-only P&L regardless of how many times capital is added.
    const closedPnlBaseline  = baseline?.total_trading_pnl ?? 0;
    const totalDeposited     = baseline?.total_deposited   ?? 0;
    const startEquity        = baseline?.start_equity      ?? liveEquity;
    const peakEquity         = baseline?.peak_equity       ?? liveEquity;

    // Deposit-adjusted cumulative P&L = closed component + open unrealised
    const cumulativeTradingPnl     = closedPnlBaseline + totalUnrealised;
    // % return on starting capital (not inflated by deposits)
    const cumulativeTradingPct     = startEquity > 0 ? (cumulativeTradingPnl / startEquity) * 100 : 0;
    // % return on total capital deployed (start + all deposits)
    const totalCapitalDeployed     = startEquity + totalDeposited;
    const returnOnTotalCapital     = totalCapitalDeployed > 0
                                     ? (cumulativeTradingPnl / totalCapitalDeployed) * 100 : 0;

    // Drawdown from peak (trailing high-watermark — same as kill switch uses)
    const drawdownFromPeak         = peakEquity > 0
                                     ? ((peakEquity - liveEquity) / peakEquity) * 100 : 0;

    // ── Deposit history for dashboard display ─────────────────────────────
    const deposits = (baseline?.deposits ?? []).map((d: any) => ({
      timestamp: d.timestamp,
      amount:    d.amount,
      equity_before: d.equity_before,
      equity_after:  d.equity_after,
    }));

    // ── Equity curve: last 60 run snapshots ───────────────────────────────
    // Strip deposit-inflated jumps so the curve shows trading performance only.
    // For each run: plot (equity - cumulative_deposits_at_that_time).
    // Simple version: just tag each run point with the mode and let the frontend decide.
    const curve = runs.slice(-60).map((r: any) => ({
      timestamp:     r.timestamp,
      equity:        r.equity,
      trading_pnl:   r.trading_pnl ?? null,
      mode:          r.mode,
    }));
    curve.push({
      timestamp:   new Date().toISOString(),
      equity:      liveEquity,
      trading_pnl: cumulativeTradingPnl,
      mode:        "live",
    });

    // ── Today's run count ─────────────────────────────────────────────────
    const todayStr  = new Date().toISOString().slice(0, 10);
    const todayRuns = runs.filter((r: any) => (r.timestamp ?? "").startsWith(todayStr));

    // ── Recent executed signals ────────────────────────────────────────────
    const executed = primary ? (primary.signals ?? []).filter((s: any) => s.executed).slice(-20) : [];
    const signals  = executed.map((s: any) => ({
      timestamp: s.timestamp, symbol: s.symbol, strategy: s.strategy,
      signal: s.signal, price: s.price, qty: s.qty,
      stop: s.stop_price, tp: s.tp_price, order_id: s.order_id,
    }));

    // ── Final output ───────────────────────────────────────────────────────
    const out = {
      // ── Account snapshot ────────────────────────────────────────────────
      equity:             Math.round(liveEquity  * 100) / 100,
      prev_close_equity:  Math.round(prevClose   * 100) / 100,
      cash:               Math.round(cash        * 100) / 100,

      // ── BUYING POWER ────────────────────────────────────────────────────
      // Cash available for new orders right now.
      // = equity minus capital already deployed in open positions.
      buying_power:       Math.round(buyingPower * 100) / 100,

      // ── TOTAL EXPOSURE ───────────────────────────────────────────────────
      // Dollar value of all open positions = capital currently "in the market".
      // exposure_pct = what % of the account is deployed and at market risk.
      total_exposure:     Math.round(totalExposure   * 100) / 100,
      exposure_pct:       Math.round(exposurePct     * 100) / 100,
      unrealized_pl:      Math.round(totalUnrealised * 100) / 100,

      // ── TODAY'S P&L ─────────────────────────────────────────────────────
      pnl_today:          Math.round(pnlToday  * 100) / 100,
      pnl_today_pct:      Math.round(pnlTodPct * 100) / 100,

      // ── CUMULATIVE TRADING P&L (deposit-adjusted) ───────────────────────
      // The only accurate measure of strategy performance.
      // Excludes all cash deposits. Includes closed P&L + current open unrealised.
      cumulative_trading_pnl:  Math.round(cumulativeTradingPnl * 100) / 100,
      cumulative_trading_pct:  Math.round(cumulativeTradingPct * 100) / 100,   // vs start capital
      return_on_total_capital: Math.round(returnOnTotalCapital * 100) / 100,   // vs all capital deployed
      closed_pnl_only:         Math.round(closedPnlBaseline   * 100) / 100,   // realised only (no open)

      // ── Capital breakdown ────────────────────────────────────────────────
      start_equity:           Math.round(startEquity       * 100) / 100,
      total_deposited:        Math.round(totalDeposited    * 100) / 100,
      total_capital_deployed: Math.round(totalCapitalDeployed * 100) / 100,
      deposits,

      // ── Risk metrics ─────────────────────────────────────────────────────
      peak_equity:            Math.round(peakEquity        * 100) / 100,
      drawdown_from_peak_pct: Math.round(drawdownFromPeak  * 100) / 100,
      kill_switch_threshold:  25.0,   // hardcoded — matches MAX_DRAWDOWN_PCT in engine

      // ── Positions ────────────────────────────────────────────────────────
      positions,
      position_count:    positions.length,

      // ── Engine metadata ───────────────────────────────────────────────────
      vix:               primary?.vix            ?? null,
      drawdown_pct:      primary?.drawdown_pct   ?? null,
      kill_switch_active: primary?.kill_switch_active ?? false,
      mode:              primary?.mode            ?? "live",
      strategy_mode:     primary?.strategy_mode   ?? "unknown",
      last_run:          primary?.run_timestamp   ?? null,

      // ── History ───────────────────────────────────────────────────────────
      equity_curve:      curve,
      recent_signals:    signals,
      total_runs:        runs.length,
      todays_runs:       todayRuns.length,

      data_source:       alpacaAcct ? "alpaca_live" : "log_snapshot",
      last_updated:      new Date().toISOString(),
    };

    return new Response(JSON.stringify(out), { status: 200, headers: cors });
  } catch (err: any) {
    return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: cors });
  }
}
