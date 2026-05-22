CREATE TABLE IF NOT EXISTS raw_source_row (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_table TEXT NOT NULL,
  source_key TEXT NOT NULL,
  row_hash TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(source_table, source_key)
);

CREATE TABLE IF NOT EXISTS bar (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  ts TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL,
  indicators_json TEXT,
  source_table TEXT NOT NULL,
  raw_source_row_id INTEGER,
  UNIQUE(symbol, timeframe, ts),
  FOREIGN KEY(raw_source_row_id) REFERENCES raw_source_row(id)
);

CREATE TABLE IF NOT EXISTS feed_export_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  metrics_json TEXT,
  error TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_check (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL,
  metrics_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  strategy TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  config_json TEXT NOT NULL,
  metrics_json TEXT
);
