"""Offline MES indicator screening; never submits to the simulated broker."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import median
from uuid import uuid4

from tvbot_v2.feed.normalize import normalize_bar
from tvbot_v2.replay.topstep import Bar, load_sqlite
from tvbot_v2.strategy.indicator_farm import Candidate, generate_candidates


HORIZONS = (3, 6, 12)
MIN_SAMPLE = 30


def load_export_csv(path: str | Path, timeframe: str) -> tuple[list[Bar], dict[str, int]]:
    """Normalize receipt-time Supabase exports, excluding ambiguous bars."""
    if timeframe not in {"5m", "15m", "30m"}:
        raise ValueError("unsupported timeframe")
    source = f"tv_datafeed_{timeframe}"
    by_time: dict[str, dict | None] = {}
    quality = {"rows_seen": 0, "malformed_or_late": 0, "duplicate_identical": 0,
               "conflicting_timestamps": 0, "bars": 0}
    with open(path, newline="", encoding="utf-8-sig") as stream:
        for raw in csv.DictReader(stream):
            quality["rows_seen"] += 1
            try:
                row = normalize_bar(raw, source)
            except (KeyError, TypeError, ValueError, OverflowError):
                quality["malformed_or_late"] += 1
                continue
            key = row["ts"]
            if key in by_time:
                previous = by_time[key]
                if previous is not None and all(previous[field] == row[field]
                                                for field in ("open", "high", "low", "close", "volume")):
                    quality["duplicate_identical"] += 1
                else:
                    quality["conflicting_timestamps"] += 1
                    by_time[key] = None
            else:
                by_time[key] = row
    bars = [Bar(datetime.fromisoformat(row["ts"]), row["open"], row["high"],
                row["low"], row["close"], row["volume"] or 0)
            for row in by_time.values() if row is not None]
    bars.sort(key=lambda bar: bar.ts)
    quality["bars"] = len(bars)
    gaps = [(right.ts - left.ts).total_seconds() / 60 for left, right in zip(bars, bars[1:])]
    quality["nonconsecutive_intervals"] = sum(delta != int(timeframe[:-1]) for delta in gaps)
    quality["largest_gap_minutes"] = int(max(gaps, default=0))
    if len(bars) < 150:
        raise ValueError("fewer than 150 unambiguous bars")
    return bars, quality


def merge_decision_feed(bars: list[Bar], db_path: str | Path,
                        timeframe: str) -> tuple[list[Bar], dict[str, int | str]]:
    """Add the Pi bridge's durable, closed decision candles without rewriting history."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT bar_ts,bar_json FROM sim_decision_feed WHERE timeframe=? ORDER BY bar_ts",
            (timeframe,),
        ).fetchall()
    merged = {bar.ts.isoformat(): bar for bar in bars}
    metrics: dict[str, int | str] = {"rows_seen": 0, "identical_overlap": 0,
                                      "new_bars": 0, "latest_feed_bar": ""}
    interval = timedelta(minutes=int(timeframe[:-1]))
    for timestamp, payload in rows:
        raw = json.loads(payload)
        ts = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if ts.tzinfo is None or ts.minute % int(timeframe[:-1]) or ts.second or ts.microsecond:
            raise ValueError("decision feed contains unaligned timestamp")
        if datetime.fromisoformat(str(raw["timestamp"]).replace("Z", "+00:00")) != ts:
            raise ValueError("decision feed timestamp mismatch")
        if ts + interval > datetime.now(timezone.utc):
            raise ValueError("decision feed contains an unclosed bar")
        bar = Bar(ts, *[float(raw[field]) for field in ("open", "high", "low", "close")],
                  float(raw.get("volume") or 0))
        if bar.volume < 0:
            raise ValueError("decision feed contains negative volume")
        metrics["rows_seen"] += 1
        metrics["latest_feed_bar"] = ts.isoformat()
        key = ts.isoformat()
        prior = merged.get(key)
        if prior is None:
            merged[key] = bar
            metrics["new_bars"] += 1
        elif (prior.open, prior.high, prior.low, prior.close, prior.volume) == (
                bar.open, bar.high, bar.low, bar.close, bar.volume):
            metrics["identical_overlap"] += 1
        else:
            raise ValueError(f"conflicting broker decision feed bar at {key}")
    result = sorted(merged.values(), key=lambda bar: bar.ts)
    return result, metrics


def _split_bounds(total: int) -> dict[str, tuple[int, int]]:
    first = int(total * 0.70)
    second = int(total * 0.85)
    return {"train": (0, first), "validation": (first, second), "test": (second, total)}


def evaluate_events(bars: list[Bar], candidates: list[Candidate],
                    bar_minutes: int, horizons: tuple[int, ...] = HORIZONS) -> tuple[list[dict], dict]:
    """Score next-open-to-future-close direction without crossing gaps or splits."""
    bounds = _split_bounds(len(bars))
    outcomes: list[dict] = []
    skipped = {"split_boundary": 0, "data_gap": 0, "end_of_data": 0}
    interval = timedelta(minutes=bar_minutes)
    for candidate in candidates:
        if candidate.direction not in (-1, 1) or not 0 <= candidate.index < len(bars):
            raise ValueError("invalid candidate")
        split = next(name for name, (start, end) in bounds.items()
                     if start <= candidate.index < end)
        split_end = bounds[split][1]
        for horizon in horizons:
            final = candidate.index + horizon
            if final >= len(bars):
                skipped["end_of_data"] += 1
                continue
            if final >= split_end:
                skipped["split_boundary"] += 1
                continue
            if any(bars[j].ts - bars[j - 1].ts != interval
                   for j in range(candidate.index + 1, final + 1)):
                skipped["data_gap"] += 1
                continue
            entry = bars[candidate.index + 1].open
            exit_price = bars[final].close
            points = (exit_price - entry) * candidate.direction
            outcomes.append({
                "strategy": candidate.strategy,
                "bar_ts": bars[candidate.index].ts.isoformat(),
                "observed_through": (bars[candidate.index].ts + interval).isoformat(),
                "direction": "long" if candidate.direction == 1 else "short",
                "regime": candidate.regime,
                "volatility": candidate.volatility,
                "split": split,
                "horizon_bars": horizon,
                "entry_ts": bars[candidate.index + 1].ts.isoformat(),
                "entry_open": entry,
                "exit_ts": bars[final].ts.isoformat(),
                "exit_close": exit_price,
                "directional_points": round(points, 4),
                "positive": points > 0,
            })
    return outcomes, {"bounds": {name: {"first_bar": bars[start].ts.isoformat(),
                                        "last_bar": bars[end - 1].ts.isoformat()}
                                  for name, (start, end) in bounds.items()},
                      "skipped": skipped}


def _wilson_lower(wins: int, total: int, z: float = 1.96) -> float | None:
    if not total:
        return None
    p = wins / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    radius = z * ((p * (1 - p) + z * z / (4 * total)) / total) ** 0.5
    return max(0.0, (center - radius) / denominator)


def summarize(outcomes: list[dict], current_regime: str,
              current_volatility: str) -> list[dict]:
    groups: dict[tuple[str, str, int, str], list[dict]] = {}
    for outcome in outcomes:
        for regime_key in ("all", f"{outcome['regime']}/{outcome['volatility']}"):
            key = (outcome["strategy"], outcome["split"], outcome["horizon_bars"], regime_key)
            groups.setdefault(key, []).append(outcome)
    rows = []
    for (strategy, split, horizon, regime), members in sorted(groups.items()):
        count = len(members)
        wins = sum(row["positive"] for row in members)
        rows.append({"strategy": strategy, "split": split,
                     "horizon_bars": horizon, "regime": regime,
                     "current_regime": regime == f"{current_regime}/{current_volatility}",
                     "events": count, "positive": wins,
                     "positive_rate": round(wins / count, 4),
                     "wilson_lower_95": round(_wilson_lower(wins, count), 4),
                     "median_directional_points": round(median(row["directional_points"]
                                                              for row in members), 4),
                     "enough_samples": count >= MIN_SAMPLE})
    return rows


def build_study(bars: list[Bar], bar_minutes: int) -> tuple[dict, list[dict]]:
    if len(bars) < 150:
        raise ValueError("at least 150 bars needed for indicator warmup and holdouts")
    candidates = generate_candidates(bars)
    outcomes, evaluation = evaluate_events(bars, candidates, bar_minutes)
    # The latest regime must be derived independently of whether a signal fired.
    from tvbot_v2.strategy.indicator_farm import atr, ema
    closes = [bar.close for bar in bars]
    atr14 = atr(bars)
    ema21, ema50 = ema(closes, 21), ema(closes, 50)
    i = len(bars) - 1
    trend = "up" if ema21[i] > ema50[i] and closes[i] > ema50[i] else (
        "down" if ema21[i] < ema50[i] and closes[i] < ema50[i] else "chop")
    trailing = [value for value in atr14[max(0, i - 99):i + 1] if value is not None]
    ratio = atr14[i] / median(trailing) if trailing and median(trailing) > 0 else 1.0
    volatility = "high" if ratio > 1.3 else "low" if ratio < 0.8 else "normal"
    rows = summarize(outcomes, trend, volatility)
    validation = [row for row in rows if row["split"] == "validation" and
                  row["horizon_bars"] == 6 and row["regime"] == "all" and
                  row["enough_samples"]]
    validation.sort(key=lambda row: (-row["wilson_lower_95"], -row["events"], row["strategy"]))
    observed_through = bars[-1].ts + timedelta(minutes=bar_minutes)
    age_minutes = max(0, int((datetime.now(timezone.utc) - observed_through).total_seconds() / 60))
    return ({"model": "offline_directional_event_study", "symbol": "MES",
             "bar_minutes": bar_minutes, "bar_count": len(bars),
             "first_bar": bars[0].ts.isoformat(), "last_bar": bars[-1].ts.isoformat(),
             "current_regime": {"trend": trend, "volatility": volatility,
                                "observed_through": observed_through.isoformat(),
                                "feed_age_minutes_at_run": age_minutes,
                                "feed_fresh_at_run": age_minutes <= 15},
             "candidate_count": len(candidates), "outcome_count": len(outcomes),
             "evaluation": evaluation, "rows": rows,
             "validation_screen_30m": validation,
             "limitations": ["Directional outcomes exclude fees, slippage, stops, and bracket fills.",
                             "Overlapping signals are correlated; Wilson bounds do not correct for multiple comparisons.",
                             "This study does not establish tradable probability or profitability."]},
            outcomes)


def write_run(output_root: str | Path, source: str | Path, bars: list[Bar], quality: dict,
              summary: dict, outcomes: list[dict],
              decision_feed_db: str | Path | None = None) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    stage = root / ("." + run_id + ".tmp")
    final = root / run_id
    stage.mkdir()
    try:
        digest = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        tape_digest = hashlib.sha256()
        with gzip.open(stage / "bars.jsonl.gz", "wb", compresslevel=6) as stream:
            for bar in bars:
                line = (json.dumps({"timestamp": bar.ts.isoformat(), "open": bar.open,
                                    "high": bar.high, "low": bar.low, "close": bar.close,
                                    "volume": bar.volume}, separators=(",", ":")) + "\n").encode()
                tape_digest.update(line)
                stream.write(line)
        (stage / "config.json").write_text(json.dumps({
            "source": str(Path(source).resolve()), "source_sha256": digest,
            "bar_tape_sha256": tape_digest.hexdigest(),
            "decision_feed_db": str(Path(decision_feed_db).resolve()) if decision_feed_db else None,
            "latest_decision_feed_bar": quality.get("decision_feed", {}).get("latest_feed_bar"),
            "signal_definition": "indicator_farm_v1", "horizons_bars": HORIZONS,
            "min_sample": MIN_SAMPLE, "simulation_only": True}, indent=2) + "\n")
        (stage / "quality.json").write_text(json.dumps(quality, indent=2) + "\n")
        (stage / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        with gzip.open(stage / "outcomes.jsonl.gz", "wt", encoding="utf-8", compresslevel=6) as stream:
            for row in outcomes:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        lines = ["# MES indicator farm: directional event study", "",
                 f"Source bars: {summary['bar_count']:,}; latest bar: {summary['last_bar']}",
                 f"Latest observed regime: {summary['current_regime']['trend']} / "
                 f"{summary['current_regime']['volatility']} through "
                 f"{summary['current_regime']['observed_through']}",
                 f"Feed age at run: {summary['current_regime']['feed_age_minutes_at_run']} minutes "
                 f"({'fresh' if summary['current_regime']['feed_fresh_at_run'] else 'stale'})",
                 f"Candidate events: {summary['candidate_count']:,}", "",
                 f"Source quality: {quality.get('malformed_or_late', 0)} late/malformed, "
                 f"{quality.get('conflicting_timestamps', 0)} conflicting timestamps, "
                 f"{quality.get('nonconsecutive_intervals', 0)} nonconsecutive intervals.",
                 f"Local decision feed: {quality.get('decision_feed', {}).get('new_bars', 0)} new bars, "
                 f"{quality.get('decision_feed', {}).get('identical_overlap', 0)} matching overlap bars.",
                 f"Excluded outcome labels: {summary['evaluation']['skipped']}", "",
                 "## Validation screen: next-open to 30-minute close", "",
                 "| Candidate rule | Events | Positive directional move | Wilson lower 95% |",
                 "|---|---:|---:|---:|"]
        for row in summary["validation_screen_30m"]:
            lines.append(f"| {row['strategy']} | {row['events']} | "
                         f"{row['positive_rate']:.1%} | {row['wilson_lower_95']:.1%} |")
        if not summary["validation_screen_30m"]:
            lines.append("| No rule has the required sample count | | | |")
        current_key = (summary["current_regime"]["trend"] + "/" +
                       summary["current_regime"]["volatility"])
        current_rows = [row for row in summary["rows"] if row["split"] == "test" and
                        row["horizon_bars"] == 6 and row["regime"] == current_key]
        current_rows.sort(key=lambda row: (-row["events"], row["strategy"]))
        lines += ["", f"## Latest observed regime: {current_key}", "",
                  "Test-window 30-minute directional outcomes for historical bars with the same regime:",
                  "", "| Candidate rule | Events | Positive move | Wilson lower 95% | Sample |",
                  "|---|---:|---:|---:|---|"]
        for row in current_rows:
            lines.append(f"| {row['strategy']} | {row['events']} | "
                         f"{row['positive_rate']:.1%} | {row['wilson_lower_95']:.1%} | "
                         f"{'sufficient for a descriptive rate' if row['enough_samples'] else 'sparse'} |")
        if not current_rows:
            lines.append("| No historical test events in this regime | | | | |")
        lines += ["", "## Interpretation", "",
                  "This ranks descriptive directional outcomes, not account profit. Signals are observed "
                  "on closed bars; the comparison enters at the next bar open and exits at a fixed horizon.",
                  "No fees, slippage, brackets, or Topstep account rules are applied here. "
                  "Do not promote a rule from this report alone.",
                  "Divergence pivots appear only after three confirming bars. "
                  "CCI pullback is a documented 6/14 CCI proxy; Market Cipher is not reproduced.",
                  "Inspect summary.json for train, validation, test, and regime-specific counts. "
                  "Small groups are flagged as insufficient."]
        (stage / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        stage.rename(final)
    except Exception:
        # Keep failed staging artifacts for diagnosis; never replace a completed run.
        raise
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline MES indicator event study")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Supabase tv_datafeed_<timeframe> CSV export")
    source.add_argument("--db", help="canonical v2 bar SQLite database")
    parser.add_argument("--decision-feed-db", help="read-only Pi broker cache of closed decision bars")
    parser.add_argument("--timeframe", choices=("5m", "15m", "30m"), default="5m")
    parser.add_argument("--output", default="runs/strategy-farm")
    parser.add_argument("--max-bars", type=int, default=150_000)
    parser.add_argument("--max-feed-age-minutes", type=int,
                        help="fail before writing if the newest completed bar is older")
    args = parser.parse_args(argv)
    if args.max_bars < 150 or (args.max_feed_age_minutes is not None and
                               args.max_feed_age_minutes < 0):
        parser.error("invalid resource/freshness limit")
    if args.csv:
        bars, quality = load_export_csv(args.csv, args.timeframe)
    else:
        bars = load_sqlite(args.db, timeframe=args.timeframe)
        quality = {"bars": len(bars), "source": "canonical_sqlite"}
    if args.decision_feed_db:
        bars, feed_quality = merge_decision_feed(bars, args.decision_feed_db, args.timeframe)
        quality["decision_feed"] = feed_quality
        quality["bars_after_merge"] = len(bars)
    if len(bars) > args.max_bars:
        parser.error(f"{len(bars)} bars exceed --max-bars={args.max_bars}")
    summary, outcomes = build_study(bars, int(args.timeframe[:-1]))
    if (args.max_feed_age_minutes is not None and
            summary["current_regime"]["feed_age_minutes_at_run"] > args.max_feed_age_minutes):
        parser.error("newest completed bar is stale; no run written")
    run = write_run(args.output, args.csv or args.db, bars, quality, summary, outcomes,
                    args.decision_feed_db)
    print(json.dumps({"run": str(run), "bars": len(bars),
                      "candidates": summary["candidate_count"],
                      "outcomes": summary["outcome_count"],
                      "current_regime": summary["current_regime"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
