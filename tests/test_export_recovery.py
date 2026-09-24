import csv
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from tvbot_v2.feed.normalize import normalize_bar
from tvbot_v2.feed.recover import recover_signal_rows
from tvbot_v2.replay.topstep import Bar
from tvbot_v2.supabase_local.export import import_rows


def source_row(ts="2026-01-04 23:30:02.108669+00"):
    return {"id": "1", "ts": ts, "o": "6911.25", "h": "6914.5",
            "l": "6899.25", "c": "6908", "v": "14261",
            "symbol": "MES", "timeframe": "30"}


def test_receipt_time_is_normalized_only_when_close_is_unambiguous():
    assert normalize_bar(source_row(), "tv_datafeed_30m")["ts"] == "2026-01-04T23:00:00+00:00"
    with pytest.raises(ValueError, match="too late"):
        normalize_bar(source_row("2026-01-04 23:40:02+00"), "tv_datafeed_30m")


def test_one_minute_feed_receipt_is_a_closed_bar():
    row = {**source_row("2026-09-23 23:59:05+00"), "timeframe": "1"}
    assert normalize_bar(row, "tv_datafeed")["ts"] == "2026-09-23T23:58:00+00:00"
    with pytest.raises(ValueError, match="too late"):
        normalize_bar({**row, "ts": "2026-09-23 23:59:55+00"}, "tv_datafeed")
    with pytest.raises(ValueError, match="1m"):
        normalize_bar({**row, "timeframe": "5"}, "tv_datafeed")


def test_feed_import_is_idempotent_and_keeps_late_raw_row(tmp_path):
    path = tmp_path / "feed.sqlite3"
    rows = [source_row(), source_row("2026-01-04 23:40:02+00")]
    assert import_rows(path, "tv_datafeed_30m", rows)["malformed"] == 1
    import_rows(path, "tv_datafeed_30m", rows)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM raw_source_row").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM bar").fetchone()[0] == 1


def test_conflicting_candles_are_excluded_even_after_reimport(tmp_path):
    path = tmp_path / "feed.sqlite3"
    first = source_row()
    second = {**first, "id": "2", "c": "6909"}
    assert import_rows(path, "tv_datafeed_30m", [first, second])["conflicts"] == 1
    import_rows(path, "tv_datafeed_30m", [first, second])
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM raw_source_row").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM bar").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ambiguous_bar").fetchone()[0] == 1


def test_signal_recovery_requires_matching_closed_bar_and_keeps_old_size_as_metadata(tmp_path):
    first = datetime(2026, 1, 4, 23, tzinfo=timezone.utc)
    bars = [Bar(first + timedelta(minutes=30 * i), 6900, 6901, 6899, 6900) for i in range(3)]
    decisions = tmp_path / "decisions.csv"
    with decisions.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("account", "timeframe", "prompt_version",
                                                  "timestamp", "signal", "size", "ai_decision_id"))
        writer.writeheader()
        writer.writerow({"account": "epsilon", "timeframe": "30m", "prompt_version": "v1",
                         "timestamp": "2026-01-04 23:30:30+00", "signal": "BUY", "size": 3, "ai_decision_id": 1})
        writer.writerow({"account": "epsilon", "timeframe": "30m", "prompt_version": "v1",
                         "timestamp": "2026-01-05 00:10:00+00", "signal": "SELL", "size": 1, "ai_decision_id": 2})
    rows, metrics = recover_signal_rows(decisions, bars, account="epsilon", prompt_version="v1")
    assert metrics["aligned"] == 1
    assert metrics["unmatched"] == 1
    assert rows[0]["bar_ts"] == first.isoformat()
    assert rows[0]["old_bracket_code"] == "3"
