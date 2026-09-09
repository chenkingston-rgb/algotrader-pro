from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    source = Path(os.environ["STATE_DB"])
    if not source.is_file():
        raise RuntimeError(f"State database does not exist: {source}")
    destination_dir = source.parent / "backups"
    destination_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(destination_dir, 0o700)
    destination = destination_dir / f"{source.stem}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite3"
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    os.chmod(destination, 0o600)
    backups = sorted(destination_dir.glob(f"{source.stem}-*.sqlite3"), reverse=True)
    for old in backups[30:]:
        old.unlink()
    print(destination)


if __name__ == "__main__":
    main()
