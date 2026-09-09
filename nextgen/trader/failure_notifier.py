from __future__ import annotations

import sys
from datetime import datetime

from .rebalance import NY, _emergency_failure_status


def main() -> None:
    unit = sys.argv[1] if len(sys.argv) > 1 else "unknown-unit"
    _emergency_failure_status(
        datetime.now(tz=NY),
        RuntimeError(f"systemd reported failure for {unit}"),
    )


if __name__ == "__main__":
    main()
