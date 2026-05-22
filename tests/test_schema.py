from tvbot_v2.db import init_db, connect_db


def test_schema_initializes(tmp_path):
    db = tmp_path / "feed.sqlite3"
    init_db(db)
    init_db(db)
    with connect_db(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_source_row", "bar", "feed_export_run", "data_quality_check", "replay_run"} <= tables
