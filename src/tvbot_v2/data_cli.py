"""Recover a local feed and recorded decisions from CSV exports, offline."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from tvbot_v2.feed.reconcile import reconcile_exports
from tvbot_v2.feed.recover import recover_signal_rows
from tvbot_v2.replay.topstep import load_sqlite
from tvbot_v2.supabase_local.export import import_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline TradingView export recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import-feed", help="import exported tv_datafeed CSV")
    importer.add_argument("--csv", required=True)
    importer.add_argument("--table", choices=("tv_datafeed_5m", "tv_datafeed_15m", "tv_datafeed_30m"), required=True)
    importer.add_argument("--db", default="feed/tradingview.sqlite3")
    recovery = commands.add_parser("recover-signals", help="align old AI decisions to imported bars")
    recovery.add_argument("--csv", required=True)
    recovery.add_argument("--db", default="feed/tradingview.sqlite3")
    recovery.add_argument("--account", required=True)
    recovery.add_argument("--timeframe", choices=("5m", "15m", "30m"), required=True)
    recovery.add_argument("--prompt-version")
    recovery.add_argument("--output", required=True, help="JSONL signal tape path")
    reconciliation = commands.add_parser("reconcile-results", help="join decision and trade result exports")
    reconciliation.add_argument("--decisions", required=True)
    reconciliation.add_argument("--results", required=True)
    reconciliation.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "import-feed":
        with open(args.csv, newline="", encoding="utf-8-sig") as stream:
            metrics = import_rows(args.db, args.table, csv.DictReader(stream))
        print(json.dumps(metrics, sort_keys=True))
        return 0
    if args.command == "reconcile-results":
        report = reconcile_exports(args.decisions, args.results)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps({key: value for key, value in report.items() if key != "groups"}, sort_keys=True))
        return 0
    bars = load_sqlite(args.db, timeframe=args.timeframe)
    rows, metrics = recover_signal_rows(
        args.csv, bars, account=args.account, timeframe=args.timeframe,
        prompt_version=args.prompt_version, bar_minutes=int(args.timeframe[:-1]),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        parser.error(f"output already exists: {output}")
    with output.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    summary = {**metrics, "bar_count": len(bars), "account": args.account,
               "timeframe": args.timeframe, "prompt_version": args.prompt_version,
               "alignment_max_delay_seconds": 120}
    output.with_suffix(".metrics.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
