"""Check the event study's time boundary, not indicator profitability."""

from datetime import datetime, timedelta, timezone
import csv
import json
import sqlite3

import pytest

from tvbot_v2.replay.strategy_farm import evaluate_events, load_export_csv, merge_decision_feed
from tvbot_v2.replay.topstep import Bar
from tvbot_v2.strategy.indicator_farm import Candidate, generate_candidates


START = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)


def _bars(count: int) -> list[Bar]:
    return [Bar(START + timedelta(minutes=5 * i),
                6000 + i * 0.03, 6002 + i * 0.03, 5998 + i * 0.03,
                6000 + i * 0.03 + (i % 11 - 5) * 0.35, 100)
            for i in range(count)]


def test_indicator_events_do_not_change_when_future_prices_change():
    bars = _bars(210)
    original = [event for event in generate_candidates(bars) if event.index <= 160]
    assert original
    changed = list(bars)
    for i in range(161, len(changed)):
        bar = changed[i]
        changed[i] = Bar(bar.ts, bar.open + 30, bar.high + 30,
                         bar.low + 30, bar.close + 30, bar.volume)
    assert [event for event in generate_candidates(changed) if event.index <= 160] == original


def test_forward_outcomes_exclude_gaps_and_split_crossing():
    bars = _bars(30)
    for i in range(12, len(bars)):
        bar = bars[i]
        bars[i] = Bar(bar.ts + timedelta(minutes=5), bar.open, bar.high,
                      bar.low, bar.close, bar.volume)
    events = [Candidate(index, "example", 1, "up", "normal")
              for index in (10, 19, 26)]
    outcomes, details = evaluate_events(bars, events, 5, horizons=(3,))
    assert len(outcomes) == 1
    assert outcomes[0]["bar_ts"] == bars[26].ts.isoformat()
    assert details["skipped"] == {"split_boundary": 1, "data_gap": 1, "end_of_data": 0}


def test_conflicting_receipt_rows_are_excluded(tmp_path):
    path = tmp_path / "feed.csv"
    fields = ("id", "ts", "o", "h", "l", "c", "v", "symbol", "timeframe")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for i in range(151):
            close = 6000 + i * 0.25
            row = {"id": i, "ts": (START + timedelta(minutes=5 * (i + 1), seconds=1)).isoformat(),
                   "o": close, "h": close + 1, "l": close - 1, "c": close,
                   "v": 100, "symbol": "MES", "timeframe": "5m"}
            writer.writerow(row)
            if i == 10:
                writer.writerow({**row, "id": 999, "c": close + 0.25})
    bars, quality = load_export_csv(path, "5m")
    assert quality["rows_seen"] == 152
    assert quality["conflicting_timestamps"] == 1
    assert len(bars) == 150


def test_broker_decision_feed_merges_matching_bars_and_rejects_conflicts(tmp_path):
    bars = _bars(151)
    path = tmp_path / "broker.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE sim_decision_feed(timeframe TEXT,bar_ts TEXT,bar_json TEXT)")
        for bar in (bars[-1], _bars(152)[-1]):
            conn.execute("INSERT INTO sim_decision_feed VALUES (?,?,?)",
                         ("5m", bar.ts.isoformat(), json.dumps({
                             "timestamp": bar.ts.isoformat(), "open": bar.open,
                             "high": bar.high, "low": bar.low, "close": bar.close,
                             "volume": bar.volume})))
    merged, quality = merge_decision_feed(bars, path, "5m")
    assert len(merged) == 152
    assert quality["identical_overlap"] == 1
    assert quality["new_bars"] == 1
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE sim_decision_feed SET bar_json=? WHERE bar_ts=?",
                     (json.dumps({"timestamp": bars[-1].ts.isoformat(), "open": bars[-1].open,
                                  "high": bars[-1].high, "low": bars[-1].low,
                                  "close": bars[-1].close + 0.25, "volume": bars[-1].volume}),
                      bars[-1].ts.isoformat()))
    with pytest.raises(ValueError, match="conflicting broker decision feed bar"):
        merge_decision_feed(bars, path, "5m")


def test_frozen_splits_do_not_move_when_new_bars_arrive():
    original = _bars(200)
    grown = _bars(220)
    splits = {"train_end": original[140].ts.isoformat(),
              "validation_end": original[170].ts.isoformat(),
              "forward_start": original[190].ts.isoformat()}
    event = Candidate(175, "example", 1, "up", "normal")
    before, bounds_before = evaluate_events(original, [event], 5, horizons=(3,),
                                             frozen_splits=splits)
    after, bounds_after = evaluate_events(grown, [event], 5, horizons=(3,),
                                          frozen_splits=splits)
    assert before == after
    assert before[0]["split"] == "test"
    assert bounds_before["bounds"]["test"] == bounds_after["bounds"]["test"]
    forward, _ = evaluate_events(grown, [Candidate(195, "example", 1, "up", "normal")],
                                 5, horizons=(3,), frozen_splits=splits)
    assert forward[0]["split"] == "forward"
