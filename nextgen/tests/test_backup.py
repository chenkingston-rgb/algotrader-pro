import os

from trader.backup_state import main
from trader.state import RunStore


def test_sqlite_backup_is_consistent_and_retained(monkeypatch, tmp_path):
    source = tmp_path / "state.sqlite3"
    store = RunStore(str(source))
    store.start("r1", {"signal_date": "2026-01-30"})
    store.conn.close()
    monkeypatch.setenv("STATE_DB", str(source))
    main()
    backups = list((tmp_path / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    assert os.stat(backups[0]).st_size > 0
    restored = RunStore(str(backups[0]))
    assert restored.status("r1") == "CREATED"
