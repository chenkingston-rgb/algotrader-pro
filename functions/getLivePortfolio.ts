// getLivePortfolio.ts — AlgoTrader Pro dashboard backend
// v8.0 — Phase 1+2 P&L clarity update
//
// NEW in v8.0:
//   • realized_today   = (equity - last_equity) - total_unrealized
//                        → closed trades only, true locked-in P&L
//   • unrealized_today = sum of all open position unrealized_pl
//                        → floating MTM, colour as amber on dashboard
//   • pnl_today        = realized + unrealized (same as before, kept for compat)
//   • positions[]      → each position now carries:
//                          - change_today_pl  : how much the position moved TODAY vs prior close
//                          - is_overnight_carry: true if position was opened before today's session
//                          - entry_date_label : "Today" | "Yesterday" | "N days ago"
//                          - strategy_type    : "intraday" | "daily" (from position_details)
//   • ma20_bear_block  : boolean — SPY below MA20, new buys blocked
//   • ma20_spy_close   : last SPY close used for regime check
//   • ma20_value       : the 20-day MA value
//   • regime_label     : "BULL" | "BEAR"
//   • overnight_carry_count : number of positions entered before today
//   • overnight_carry_upl   : total unrealized P&L from overnight carries only
//   • todays_positions_upl  : total unrealized P&L from positions entered today

const REPO          = "chenkingston-rgb/algotrader-pro";
const GH_TOKEN      = Deno.env.get("GITHUB_ACCESS_TOKEN") ?? "";
const ALPACA_KEY    = Deno.env.get("ALPACA_LIVE_KEY")    ?? Deno.env.get("ALPACA_PAPER_KEY")    ?? "";
const ALPACA_SECRET = Deno.env.get("ALPACA_LIVE_SECRET") ?? Deno.env.get("ALPACA_PAPER_SECRET") ?? "";
const ALPACA_BASE   = "https://api.alpaca.markets";
const DATA_BASE     = "https://data.alpaca.markets";

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

async function alpacaGet(path: string, base = ALPACA_BASE): Promise<any> {
  const res = await fetch(`${base}${path}`, {
    headers: {
      "APCA-API-KEY-ID":     ALPACA_KEY,
      "APCA-API-SECRET-KEY": ALPACA_SECRET,
    },
  });
  if (!res.ok) throw new Error(`Alpaca ${path}: ${res.status}`);
  return res.json();
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

// Returns how many calendar days ago a UTC ISO string was vs today (ET midnight)
function daysAgoLabel(isoStr: string | null | undefined): string {
  if (!isoStr) return "Unknown";
  try {
    const entryMs = new Date(isoStr).getTime();
    // ET = UTC-4 (EDT) during summer
    const nowET    = new Date(Date.now() - 4 * 3600_000);
    const todayET  = new Date(nowET.toISOString().slice(0, 10) + "T00:00:00.000Z");
    const diffMs   = todayET.getTime() - entryMs + 4 * 3600_000 * 1; // normalise
    const diffDays = Math.floor(diffMs / 86_400_000);
    if (diffDays <= 0)  return "Today";
    if (diffDays === 1) return "Yesterday";
    return `${diffDays} days ago`;
  } catch {
    return "Unknown";
  }
}

// Was the entry timestamp BEFORE today's ET midnight?
function isOvernightCarry(isoStr: string | null | undefined): boolean {
  if (!isoStr) return false;
  try {
    const nowET   = new Date(Date.now() - 4 * 3600_000);
    const todayET = nowET.toISOString().slice(0, 10); // "YYYY-MM-DD"
    const entryET = new Date(new Date(isoStr).getTime() - 4 * 3600_000)
                      .toISOString().slice(0, 10);
    return entryET < todayET;
  } catch {
    return false;
  }
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
    // ── Parallel fetches ──────────────────────────────────────────────────
    const [r1, r2, r3, r4, r5, r6, r7, r8] = await Promise.allSettled([
      fetchGithubJson("logs/intraday_latest.json"),        // r1
      fetchGithubJson("logs/daily_latest.json"),           // r2
      fetchGithubJson("logs/run_history.json"),            // r3
      alpacaGet("/v2/account"),                            // r4
      alpacaGet("/v2/positions"),                          // r5
      fetchGithubJson("logs/live_baseline.json"),          // r6
      fetchGithubJson("logs/intraday_position_tags.json"), // r7 — entry timestamps
      alpacaGet(                                           // r8 — SPY daily bars for MA20
        "/v2/stocks/SPY/bars?timeframe=1Day&limit=25&feed=iex",
        DATA_BASE
      ),
    ]);

    const intraday    = r1.status === "fulfilled" ? r1.value : null;
    const daily       = r2.status === "fulfilled" ? r2.value : null;
    const runs: any[] = r3.status === "fulfilled" && Array.isArray(r3.value) ? r3.value : [];
    const alpacaAcct  = r4.status === "fulfilled" ? r4.value : null;
    const alpacaPos   = r5.status === "fulfilled" && Array.isArray(r5.value) ? r5.value : [];
    const baseline    = r6.status === "fulfilled" ? r6.value : null;
    const posTags     = r7.status === "fulfilled" ? r7.value : {};    // { SYMBOL: { entry_time, ... } }
    const spyBarsRaw  = r8.status === "fulfilled" ? r8.value : null;

    const primary = intraday ?? daily;
    if (!primary && !alpacaAcct) {
      return new Response(JSON.stringify({ error: "No data available" }), { status: 503, headers: cors });
    }

    // ── Core account values ───────────────────────────────────────────────
    const liveEquity  = alpacaAcct ? parseFloat(alpacaAcct.equity      ?? "0") : (primary?.equity ?? 0);
    const prevClose   = alpacaAcct ? parseFloat(alpacaAcct.last_equity  ?? "0") : 0;
    const buyingPower = alpacaAcct ? parseFloat(alpacaAcct.buying_power ?? "0") : (primary?.buying_power ?? 0);
    const cash        = alpacaAcct ? parseFloat(alpacaAcct.cash         ?? "0") : 0;

    // ── Position detail map from intraday_latest (entry strategy + type) ──
    const posDetailMap: Record<string, any> = {};
    const posDetails: any[] = intraday?.position_details ?? daily?.position_details ?? [];
    for (const pd of posDetails) {
      posDetailMap[pd.symbol] = pd;
    }

    // ── Enrich positions with carry/entry metadata ────────────────────────
    const positions = alpacaPos.map((p: any) => {
      const sym          = p.symbol;
      const entryPrice   = parseFloat(p.avg_entry_price ?? "0");
      const curPrice     = parseFloat(p.current_price   ?? "0");
      const qty          = parseFloat(p.qty              ?? "0");
      const upl          = parseFloat(p.unrealized_pl    ?? "0");
      const uplPct       = parseFloat(p.unrealized_plpc  ?? "0") * 100;
      const mktVal       = parseFloat(p.market_value     ?? "0");
      // change_today: price change vs prior close × qty = today's MTM move on THIS position
      const changeTodayPct = parseFloat(p.change_today   ?? "0");     // fractional, e.g. 0.02 = 2%
      const changeTodayPl  = round2(entryPrice === 0 ? 0 : changeTodayPct * curPrice * qty);

      // Entry timestamp: prefer position_tags (engine-tagged at order time),
      // fall back to position_details from log, then null
      const tagEntry    = posTags[sym]?.entry_time ?? null;
      const detailEntry = posDetailMap[sym]?.entry_time ?? null;
      const entryTs     = tagEntry ?? detailEntry ?? null;

      const overnight   = isOvernightCarry(entryTs);
      const entryLabel  = daysAgoLabel(entryTs);
      const stratType   = posDetailMap[sym]?.strategy_type ?? (overnight ? "daily" : "intraday");
      const stratName   = posDetailMap[sym]?.entry_strategy ?? posTags[sym]?.strategy ?? "unknown";

      return {
        symbol:            sym,
        qty:               round2(qty),
        avg_entry_price:   round2(entryPrice),
        current_price:     round2(curPrice),
        market_value:      round2(mktVal),
        unrealized_pl:     round2(upl),
        unrealized_plpc:   round2(uplPct),
        // TODAY's move on this position (vs prior close) — NOT vs entry
        // This answers "how much did this position change TODAY specifically?"
        change_today_pl:   changeTodayPl,
        change_today_pct:  round2(changeTodayPct * 100),
        // Carry metadata
        is_overnight_carry: overnight,
        entry_date_label:   entryLabel,
        entry_timestamp:    entryTs,
        strategy_type:      stratType,
        strategy_name:      stratName,
      };
    });

    // ── P&L Decomposition ─────────────────────────────────────────────────
    //
    // pnl_today        = equity - last_equity  (total equity move, includes everything)
    // total_unrealized = sum of open position UPL from entry (may span multiple days)
    // realized_today   = pnl_today - total_unrealized
    //                  = the closed-trade-only P&L for today's session
    //                  This is the "true" day trading result — money locked in.
    //
    // Why this works:
    //   equity = prev_close + realized_today + (open_position_mtm_change_today)
    //   The MTM change today on open positions = sum(change_today_pl)
    //   But unrealized_pl is from ENTRY (not from prior close), so:
    //   realized_today = (equity - prev_close) - total_unrealized is NOT exact
    //   if positions were opened on a prior day (their UPL includes multi-day move).
    //
    //   More accurate: realized_today = pnl_today - sum(change_today_pl)
    //   where change_today_pl = today's MTM on each position vs PRIOR CLOSE.
    //   This removes ONLY today's open-position contribution, not multi-day UPL.
    //
    const pnlToday        = prevClose > 0 ? liveEquity - prevClose : 0;
    const pnlTodPct       = prevClose > 0 ? (pnlToday / prevClose) * 100 : 0;

    // Total unrealized from ENTRY (whole position lifetime)
    const totalUnrealized = positions.reduce((s: number, p: any) => s + p.unrealized_pl, 0);

    // Today's MTM contribution from open positions (vs prior close, not entry)
    const todayOpenMtm    = positions.reduce((s: number, p: any) => s + p.change_today_pl, 0);

    // Realized today = total equity move minus what open positions contributed today
    // This isolates closed trade P&L only
    const realizedToday   = round2(pnlToday - todayOpenMtm);

    // Overnight carries vs fresh today positions
    const overnightPos    = positions.filter((p: any) => p.is_overnight_carry);
    const todayPos        = positions.filter((p: any) => !p.is_overnight_carry);
    const overnightUpl    = round2(overnightPos.reduce((s: number, p: any) => s + p.unrealized_pl, 0));
    const todayPosUpl     = round2(todayPos.reduce((s: number, p: any) => s + p.unrealized_pl, 0));
    const overnightMtm    = round2(overnightPos.reduce((s: number, p: any) => s + p.change_today_pl, 0));

    // ── Exposure ──────────────────────────────────────────────────────────
    const totalExposure   = positions.reduce((s: number, p: any) => s + p.market_value, 0);
    const exposurePct     = liveEquity > 0 ? (totalExposure / liveEquity) * 100 : 0;

    // ── Cumulative P&L (deposit-adjusted, same logic as before) ──────────
    const closedPnlBaseline    = baseline?.total_trading_pnl ?? 0;
    const totalDeposited       = baseline?.total_deposited   ?? 0;
    const startEquity          = baseline?.start_equity      ?? liveEquity;
    const peakEquity           = baseline?.peak_equity       ?? liveEquity;
    const cumulativeTradingPnl = closedPnlBaseline + totalUnrealized;
    const cumulativeTradingPct = startEquity > 0 ? (cumulativeTradingPnl / startEquity) * 100 : 0;
    const totalCapitalDeployed = startEquity + totalDeposited;
    const returnOnTotalCapital = totalCapitalDeployed > 0
                                  ? (cumulativeTradingPnl / totalCapitalDeployed) * 100 : 0;
    const drawdownFromPeak     = peakEquity > 0
                                  ? ((peakEquity - liveEquity) / peakEquity) * 100 : 0;
    const deposits = (baseline?.deposits ?? []).map((d: any) => ({
      timestamp:     d.timestamp,
      amount:        d.amount,
      equity_before: d.equity_before,
      equity_after:  d.equity_after,
    }));

    // ── MA20 BEAR BLOCK — compute from SPY daily bars ─────────────────────
    //
    // Source: fetch last 25 SPY daily bars from Alpaca data API.
    // MA20 = mean of last 20 closes.
    // is_bull = latest close >= MA20.
    // is_bear_block = !is_bull (matches engine logic exactly).
    //
    // If SPY bars unavailable, fall back to kill_switch_active from log
    // (conservative: show bear block if we can't confirm otherwise).
    //
    let ma20BearBlock  = primary?.kill_switch_active ?? false; // safe fallback
    let ma20Value      = 0;
    let ma20SpyClose   = 0;
    let ma20Gap        = 0;
    let regimeLabel    = "UNKNOWN";

    try {
      const spyBars: any[] = spyBarsRaw?.bars ?? [];
      if (spyBars.length >= 20) {
        const closes   = spyBars.map((b: any) => b.c);
        const last20   = closes.slice(-20);
        ma20Value      = round2(last20.reduce((a: number, b: number) => a + b, 0) / 20);
        ma20SpyClose   = round2(closes[closes.length - 1]);
        ma20Gap        = round2(((ma20SpyClose - ma20Value) / ma20Value) * 100);
        ma20BearBlock  = ma20SpyClose < ma20Value;
        regimeLabel    = ma20BearBlock ? "BEAR" : "BULL";
      }
    } catch (_) {
      // keep fallback values
    }

    // ── Equity curve ──────────────────────────────────────────────────────
    const curve = runs.slice(-60).map((r: any) => ({
      timestamp:   r.timestamp,
      equity:      r.equity,
      trading_pnl: r.trading_pnl ?? null,
      mode:        r.mode,
    }));
    curve.push({
      timestamp:   new Date().toISOString(),
      equity:      liveEquity,
      trading_pnl: cumulativeTradingPnl,
      mode:        "live",
    });

    // ── Today's run count ─────────────────────────────────────────────────
    // Use ET date (UTC-4)
    const nowET    = new Date(Date.now() - 4 * 3600_000);
    const todayStr = nowET.toISOString().slice(0, 10);
    const todayRuns = runs.filter((r: any) => (r.timestamp ?? "").startsWith(todayStr));
    const todayOrders = todayRuns.reduce((s: number, r: any) => s + (r.orders_count ?? 0), 0);

    // ── Recent executed signals ────────────────────────────────────────────
    const executed = primary ? (primary.signals ?? []).filter((s: any) => s.executed).slice(-20) : [];
    const signals  = executed.map((s: any) => ({
      timestamp: s.timestamp, symbol: s.symbol, strategy: s.strategy,
      signal: s.signal, price: s.price, qty: s.qty,
      stop: s.stop_price, tp: s.tp_price, order_id: s.order_id,
    }));

    // ── VIX ───────────────────────────────────────────────────────────────
    const vix = primary?.vix ?? null;
    const vixStatus = vix === null ? "unknown"
      : vix >= 28 ? "blocked"
      : vix >= 20 ? "reduced"
      : "clear";

    // ── Final output ───────────────────────────────────────────────────────
    const out = {
      // ── Account snapshot ────────────────────────────────────────────────
      equity:             round2(liveEquity),
      prev_close_equity:  round2(prevClose),
      cash:               round2(cash),
      buying_power:       round2(buyingPower),

      // ── EXPOSURE ────────────────────────────────────────────────────────
      total_exposure:     round2(totalExposure),
      exposure_pct:       round2(exposurePct),

      // ── P&L DECOMPOSITION (Phase 1 — the core fix) ────────────────────
      //
      //  pnl_today      = total equity change vs yesterday's close
      //                   = realized + open-position today-MTM
      //                   INCLUDES floating unrealized — can mislead
      //
      //  realized_today = pnl_today minus today's MTM from open positions
      //                   = only closed trades, money actually locked in
      //                   Show this as the PRIMARY P&L number, in green/red
      //
      //  unrealized_today = sum of open position today-MTM change
      //                   = floating, not final, colour AMBER on dashboard
      //
      //  total_unrealized = all open UPL from entry (may span multiple days)
      //                   = what moves equity when positions mark-to-market
      //
      pnl_today:            round2(pnlToday),
      pnl_today_pct:        round2(pnlTodPct),
      realized_today:       round2(realizedToday),
      unrealized_today:     round2(todayOpenMtm),     // today's MTM only
      total_unrealized:     round2(totalUnrealized),  // full UPL from entry

      // Overnight carry breakdown
      overnight_carry_count: overnightPos.length,
      overnight_carry_upl:   overnightUpl,            // full UPL from entry
      overnight_carry_mtm:   overnightMtm,            // just today's move
      todays_positions_upl:  todayPosUpl,             // fresh entries today

      // ── CUMULATIVE P&L (deposit-adjusted) ────────────────────────────
      cumulative_trading_pnl:  round2(cumulativeTradingPnl),
      cumulative_trading_pct:  round2(cumulativeTradingPct),
      return_on_total_capital: round2(returnOnTotalCapital),
      closed_pnl_only:         round2(closedPnlBaseline),

      // ── Capital breakdown ─────────────────────────────────────────────
      start_equity:           round2(startEquity),
      total_deposited:        round2(totalDeposited),
      total_capital_deployed: round2(totalCapitalDeployed),
      deposits,

      // ── Risk ──────────────────────────────────────────────────────────
      peak_equity:            round2(peakEquity),
      drawdown_from_peak_pct: round2(drawdownFromPeak),
      kill_switch_threshold:  25.0,
      kill_switch_active:     primary?.kill_switch_active ?? false,

      // ── MA20 REGIME (Phase 2 — bear block indicator) ──────────────────
      //
      //  ma20_bear_block = true  → SPY below 20-day MA, engine blocking new buys
      //                           Show RED "BEAR — Buys Blocked" badge on dashboard
      //  ma20_bear_block = false → SPY above MA20, engine trading normally
      //                           Show GREEN "BULL — Active" badge
      //
      //  ma20_spy_close  = latest SPY daily close used for the check
      //  ma20_value      = the 20-day moving average value
      //  ma20_gap_pct    = (spy_close - ma20) / ma20 * 100
      //                    positive = how far ABOVE MA20 (buffer before bear)
      //                    negative = how far BELOW MA20 (bear depth)
      //  regime_label    = "BULL" | "BEAR" | "UNKNOWN"
      //
      ma20_bear_block:  ma20BearBlock,
      ma20_spy_close:   ma20SpyClose,
      ma20_value:       ma20Value,
      ma20_gap_pct:     ma20Gap,
      regime_label:     regimeLabel,

      // ── VIX ───────────────────────────────────────────────────────────
      vix:         vix,
      vix_status:  vixStatus,  // "clear" | "reduced" | "blocked" | "unknown"
      // vix_status drives position sizing:
      //   clear   → full size (100%)
      //   reduced → 40–50% size (VIX 20–27)
      //   blocked → 0% size (VIX ≥ 28, bollinger blocked at 25)

      // ── Positions (enriched with carry metadata) ──────────────────────
      positions,
      position_count: positions.length,

      // ── Engine metadata ───────────────────────────────────────────────
      drawdown_pct:  primary?.drawdown_pct  ?? null,
      mode:          primary?.mode          ?? "live",
      strategy_mode: primary?.strategy_mode ?? "unknown",
      last_run:      primary?.run_timestamp ?? null,

      // ── History ───────────────────────────────────────────────────────
      equity_curve:   curve,
      recent_signals: signals,
      total_runs:     runs.length,
      todays_runs:    todayRuns.length,
      todays_orders:  todayOrders,

      data_source:  alpacaAcct ? "alpaca_live" : "log_snapshot",
      last_updated: new Date().toISOString(),
    };

    return new Response(JSON.stringify(out), { status: 200, headers: cors });

  } catch (err: any) {
    return new Response(
      JSON.stringify({ error: err.message }),
      { status: 500, headers: cors }
    );
  }
}
