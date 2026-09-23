"""Align historical overseer decisions to closed TradingView bars."""

from __future__ import annotations

import bisect
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tvbot_v2.replay.topstep import Bar


def recover_signal_rows(
    decisions_csv: str | Path, bars: list[Bar], *, account: str,
    timeframe: str = "30m", prompt_version: str | None = None,
    bar_minutes: int = 30, max_delay_seconds: int = 120,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Use only a decision logged shortly AFTER a known bar closed.

    A decision outside that window is excluded; it is never silently snapped
    to the nearest candle. Size is retained as metadata because the old prompt
    used it as a bracket-selection code, not simulator contract quantity.
    """
    closed = [bar.ts.astimezone(timezone.utc) + timedelta(minutes=bar_minutes) for bar in bars]
    if closed != sorted(closed):
        raise ValueError("bars must be chronological")
    metrics = {"seen": 0, "eligible": 0, "aligned": 0, "unmatched": 0,
               "duplicate_bar": 0, "invalid_signal": 0}
    aligned: dict[str, dict[str, Any]] = {}
    with open(decisions_csv, newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            metrics["seen"] += 1
            if row.get("account") != account or row.get("timeframe") != timeframe:
                continue
            if prompt_version and row.get("prompt_version") != prompt_version:
                continue
            metrics["eligible"] += 1
            signal = (row.get("signal") or "").upper()
            if signal == "FLATTEN":
                signal = "FLAT"
            if signal not in {"BUY", "SELL", "HOLD", "FLAT"}:
                metrics["invalid_signal"] += 1
                continue
            try:
                decision_time = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if decision_time.tzinfo is None:
                    raise ValueError("naive timestamp")
                decision_time = decision_time.astimezone(timezone.utc)
            except (KeyError, ValueError):
                metrics["unmatched"] += 1
                continue
            index = bisect.bisect_right(closed, decision_time) - 1
            if index < 0 or not 0 <= (decision_time - closed[index]).total_seconds() <= max_delay_seconds:
                metrics["unmatched"] += 1
                continue
            bar_ts = bars[index].ts.astimezone(timezone.utc).isoformat()
            record = {"bar_ts": bar_ts, "signal": signal,
                      "decision_time": decision_time.isoformat(),
                      "ai_decision_id": row.get("ai_decision_id"),
                      "old_bracket_code": row.get("size"),
                      "prompt_version": row.get("prompt_version")}
            if bar_ts in aligned:
                metrics["duplicate_bar"] += 1
                if record["decision_time"] >= aligned[bar_ts]["decision_time"]:
                    continue
            aligned[bar_ts] = record
    records = sorted(aligned.values(), key=lambda row: row["bar_ts"])
    metrics["aligned"] = len(records)
    return records, metrics
