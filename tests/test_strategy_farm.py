"""Check the event study's time boundary, not indicator profitability."""

from datetime import datetime, timedelta, timezone
import csv

from tvbot_v2.replay.strategy_farm import evaluate_events, load_export_csv
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
