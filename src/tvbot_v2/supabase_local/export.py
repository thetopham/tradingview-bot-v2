"""Idempotent append-only raw feed import into canonical SQLite bars."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from tvbot_v2.db import connect_db, init_db
from tvbot_v2.feed.normalize import normalize_bar
from tvbot_v2.supabase_local.client import LocalSupabaseClient


def import_rows(db_path: str | Path, source_table: str, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    init_db(db_path)
    metrics = {"rows_seen": 0, "bars_upserted": 0, "malformed": 0}
    with connect_db(db_path) as conn:
        for row in rows:
            metrics["rows_seen"] += 1
            raw = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            source_id = str(row.get("id") or row.get("timestamp") or row.get("time") or digest[:20])
            # A changed source row gets a new raw revision. Re-imports do not duplicate it.
            source_key = f"{source_id}:{digest[:16]}"
            conn.execute("INSERT OR IGNORE INTO raw_source_row(source_table,source_key,row_hash,raw_json) "
                         "VALUES (?,?,?,?)", (source_table, source_key, digest, raw))
            raw_id = conn.execute("SELECT id FROM raw_source_row WHERE source_table=? AND source_key=?",
                                  (source_table, source_key)).fetchone()[0]
            try:
                bar = normalize_bar(row, source_table)
            except (KeyError, TypeError, ValueError, OverflowError):
                metrics["malformed"] += 1
                continue
            conn.execute("""INSERT INTO bar(symbol,timeframe,ts,open,high,low,close,volume,
                                            indicators_json,source_table,raw_source_row_id)
                            VALUES (:symbol,:timeframe,:ts,:open,:high,:low,:close,:volume,
                                    :indicators_json,:source_table,:raw_source_row_id)
                            ON CONFLICT(symbol,timeframe,ts) DO UPDATE SET
                              open=excluded.open, high=excluded.high, low=excluded.low,
                              close=excluded.close, volume=excluded.volume,
                              indicators_json=excluded.indicators_json,
                              source_table=excluded.source_table,
                              raw_source_row_id=excluded.raw_source_row_id""",
                         {**bar, "source_table": source_table, "raw_source_row_id": raw_id})
            metrics["bars_upserted"] += 1
    return metrics


def export_local_feed(db_path: str | Path, *, max_rows_per_table: int = 100_000,
                      client: LocalSupabaseClient | None = None) -> dict[str, Any]:
    client = client or LocalSupabaseClient()
    result: dict[str, Any] = {"source": "local_supabase", "tables": {}}
    for table in ("tv_datafeed_5m", "tv_datafeed_15m", "tv_datafeed_30m"):
        first, count = client.get_rows(table, limit=1)
        if count is not None and count > max_rows_per_table:
            raise ValueError(f"{table} has {count} rows; increase max_rows_per_table explicitly")
        columns = set(first[0]) if first else set()
        order = next((candidate for candidate in ("id.asc", "timestamp.asc", "created_at.asc", "time.asc")
                      if candidate.split(".")[0] in columns), None)
        if first and order is None:
            raise ValueError(f"{table} has no stable pagination column")
        metrics = {"rows_seen": 0, "bars_upserted": 0, "malformed": 0}
        for offset in range(0, count if count is not None else max_rows_per_table, 1000):
            rows, _ = client.get_rows(table, offset=offset, limit=1000, order=order)
            if not rows:
                break
            batch = import_rows(db_path, table, rows)
            for key, value in batch.items():
                metrics[key] += value
            if len(rows) < 1000:
                break
        result["tables"][table] = metrics
    return result
