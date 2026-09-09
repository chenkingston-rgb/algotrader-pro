"""Execution-delay sensitivity using the same frozen target function."""
from __future__ import annotations

import json
from pathlib import Path

from engine_fast import run_monthly_fast
from reproduce import END, START, load_snapshot, target_factory


def main():
    opens, closes = load_snapshot()
    fn = target_factory(closes)
    rows = []
    for delay in (0, 1, 2):
        result, _, _ = run_monthly_fast(
            f"trend3-qqq20-v1-delay-{delay}", opens, closes, fn,
            START, END, cost_bps=10, delay_sessions=delay,
        )
        row = result.record()
        row["delay_sessions"] = delay
        rows.append(row)
    path = Path(__file__).resolve().parent / "reproduced" / "delay_sensitivity.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
