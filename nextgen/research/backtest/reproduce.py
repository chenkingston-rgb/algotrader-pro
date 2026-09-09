"""Reproduce the frozen Trend-3 + QQQ20 result from the included price snapshot."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from engine import run_monthly

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
START = "2007-01-03"
END = "2026-07-31"
EXPECTED = {"cagr": 0.1133863936, "sharpe": 0.9547619709, "maxdd": -0.1987747515}


def load_snapshot():
    frames = {s: pd.read_csv(DATA / f"{s}.csv", index_col=0, parse_dates=True) for s in ("SPY", "QQQ", "GLD", "BIL")}
    idx = frames["SPY"].index
    idx = idx[(idx >= "2000-01-01") & (idx <= END)]
    opens = pd.DataFrame({s: frames[s]["adj_open"].reindex(idx) for s in frames}, index=idx).astype(float)
    closes = pd.DataFrame({s: frames[s]["adj_close"].reindex(idx) for s in frames}, index=idx).astype(float)
    return opens, closes


def target_factory(closes):
    def target(i, _date):
        out = {"QQQ": 0.20}
        for symbol in ("SPY", "QQQ", "GLD"):
            window = closes[symbol].iloc[i-199:i+1]
            if len(window) != 200 or window.isna().any():
                raise RuntimeError(f"Invalid 200-session window for {symbol} at {closes.index[i]}")
            if float(window.iat[-1]) >= float(window.mean()):
                out[symbol] = out.get(symbol, 0.0) + 0.80/3.0
        return out
    return target


def monthly_ledger(opens, closes, fn):
    rows = []
    idx = closes.index
    start_i = max(1, int(idx.searchsorted(pd.Timestamp(START))))
    end_i = int(idx.searchsorted(pd.Timestamp(END), side="right")) - 1
    signals = [start_i - 1] + [i for i in range(start_i, end_i) if idx[i].to_period("M") != idx[i+1].to_period("M")]
    for i in signals:
        execution_i = i + 1
        if execution_i > end_i:
            continue
        risk = fn(i, idx[i])
        weights = {s: risk.get(s, 0.0) for s in ("SPY", "QQQ", "GLD")}
        weights["BIL"] = 1.0 - sum(weights.values())
        row = {"signal_date": idx[i].date(), "execution_date": idx[execution_i].date(), **{f"target_{s}": weights[s] for s in weights}}
        row.update({f"execution_adj_open_{s}": opens[s].iat[execution_i] for s in weights})
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    opens, closes = load_snapshot()
    fn = target_factory(closes)
    result, nav, returns = run_monthly("trend3-qqq20-v1", opens, closes, fn, START, END, cost_bps=10)
    record = result.record()
    for key, expected in EXPECTED.items():
        if abs(record[key] - expected) > 1e-8:
            raise AssertionError(f"{key} mismatch: {record[key]} vs {expected}")
    out = ROOT / "reproduced"
    out.mkdir(exist_ok=True)
    (out / "metrics.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame({"nav": nav, "daily_return": returns}).to_csv(out / "equity_curve.csv")
    monthly_ledger(opens, closes, fn).to_csv(out / "monthly_targets.csv", index=False)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
