CREATE TABLE IF NOT EXISTS sim_account (
  name TEXT PRIMARY KEY,
  generation INTEGER NOT NULL DEFAULT 1,
  variant_json TEXT NOT NULL,
  rules_json TEXT NOT NULL,
  balance REAL NOT NULL,
  mll REAL NOT NULL,
  day_start_balance REAL NOT NULL,
  day_pnl_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  manual_paused INTEGER NOT NULL DEFAULT 0,
  current_day TEXT,
  last_bar_ts TEXT,
  last_bar_close REAL,
  pending_json TEXT,
  next_check TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sim_position (
  account TEXT PRIMARY KEY REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  direction INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  entry_price REAL NOT NULL,
  entry_ts TEXT NOT NULL,
  stop_price REAL NOT NULL,
  target_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_bar (
  account TEXT NOT NULL REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  bar_ts TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  decision_json TEXT,
  snapshot_json TEXT NOT NULL,
  processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(account, generation, bar_ts)
);

CREATE TABLE IF NOT EXISTS sim_decision (
  account TEXT NOT NULL REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  bar_ts TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  decision_json TEXT NOT NULL,
  available_at TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  PRIMARY KEY(account, generation, bar_ts)
);

CREATE TABLE IF NOT EXISTS sim_order (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  client_order_id TEXT NOT NULL,
  signal TEXT NOT NULL,
  size INTEGER NOT NULL,
  available_at TEXT NOT NULL,
  earliest_fill_ts TEXT NOT NULL,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(account, generation, client_order_id)
);

CREATE TABLE IF NOT EXISTS sim_trade (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  entry_ts TEXT NOT NULL,
  exit_ts TEXT NOT NULL,
  direction TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL NOT NULL,
  gross_pnl REAL NOT NULL,
  net_pnl REAL NOT NULL,
  reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL REFERENCES sim_account(name),
  generation INTEGER NOT NULL,
  bar_ts TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS sim_event_account_generation
  ON sim_event(account, generation, id);
