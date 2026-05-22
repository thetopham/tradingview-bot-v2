from __future__ import annotations

from pathlib import Path
import sqlite3


def connect_db(path: str | Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path) -> None:
    schema = Path(__file__).with_name("schema.sql").read_text()
    with connect_db(path) as conn:
        conn.executescript(schema)
