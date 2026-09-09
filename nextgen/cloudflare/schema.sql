PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_status_updated
  ON runs(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  event TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created
  ON events(created_at DESC);

CREATE TABLE IF NOT EXISTS equity_snapshots (
  observed_at TEXT PRIMARY KEY,
  equity REAL NOT NULL,
  adjusted_equity REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_equity_adjusted
  ON equity_snapshots(adjusted_equity DESC);

CREATE TABLE IF NOT EXISTS latest_status (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  payload TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS control (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
