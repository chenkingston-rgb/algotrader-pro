# AlgoTrader Pro — Dashboard

Live algorithmic trading dashboard. Built with React + Vite + Base44.

## Architecture

- **Engine** → runs on Render (Python, every 15 min intraday / 9:15am daily)
- **Backend function** → `functions/getLivePortfolio.ts` (Base44, deployed)
- **Dashboard** → this repo (`src/pages/Dashboard.jsx`), synced to Base44 app

## Dashboard Features (v8.0)

- **Realized Today** — locked-in closed trade P&L only (green/red)
- **Unrealized** — floating open position MTM (amber)  
- **Bear Block** — SPY vs MA20 regime indicator (OFF 🟢 / ON 🔴)
- **VIX** — volatility score with size label: `full` / `half` / `blocked`
- Overnight carry banner + CARRY badge on positions entered before today
- Kill switch banner when drawdown ≥ 25% of peak equity

## VIX Thresholds (matches engine logic)

| VIX | Label | Position Size |
|-----|-------|---------------|
| < 20 | `full` 🟢 | 100% |
| 20–27 | `half` 🟡 | ~50% |
| ≥ 28 | `blocked` 🔴 | 0% (no new entries) |

## Bear Block (MA20 Regime)

Engine halts new buy signals when `SPY_close < MA20(20d)`.  
Dashboard shows live gap % so you know how far above/below the threshold is.

---
_Last updated by agent: 2026-06-18_
