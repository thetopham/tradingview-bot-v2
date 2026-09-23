"""Command line entry point for the simulated broker."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from tvbot_v2.simulate.brokerless_executor import SimBroker
from tvbot_v2.simulate.ledger import SimLedger
from tvbot_v2.simulate.portfolio import DEFAULT_PORTFOLIO, load_portfolio
from tvbot_v2.replay.topstep import load_csv, load_sqlite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent MES Combine simulated broker")
    parser.add_argument("--db", type=Path, default=Path("data/sim_broker.sqlite"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="register starter accounts or profiles from JSON")
    init.add_argument("--portfolio", type=Path)
    step = commands.add_parser("step", help="process one closed bar and its decision")
    step.add_argument("--input", type=Path, help="JSON envelope; default stdin")
    replay = commands.add_parser("replay", help="advance one account over historical bars")
    replay.add_argument("account")
    source = replay.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", type=Path)
    source.add_argument("--feed-db", type=Path)
    replay.add_argument("--signals", type=Path, help="recorded decision JSONL; otherwise HOLD")
    replay.add_argument("--limit", type=int, help="process first N bars for a bounded trial")
    status = commands.add_parser("status", help="show account snapshots")
    status.add_argument("account", nargs="?")
    for action in ("pause", "resume"):
        commands.add_parser(action).add_argument("account")
    reset = commands.add_parser("reset", help="start a new generation; keep old audit trail")
    reset.add_argument("account")
    reset.add_argument("--reason", required=True)
    settle = commands.add_parser("settle", help="finalize a completed trading day")
    settle.add_argument("account")
    settle.add_argument("--as-of", help="ISO timestamp with offset; default now")
    history = commands.add_parser("history", help="show recent events and trades")
    history.add_argument("account")
    history.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    ledger = SimLedger(args.db)
    try:
        if args.command == "init":
            result = ledger.register(load_portfolio(args.portfolio) if args.portfolio else DEFAULT_PORTFOLIO)
        elif args.command == "step":
            payload = (args.input.read_text(encoding="utf-8") if args.input
                       else sys.stdin.read())
            result = SimBroker(ledger).process_envelope(json.loads(payload))
        elif args.command == "replay":
            (account,) = ledger.status(args.account)
            bars = load_csv(args.csv) if args.csv else load_sqlite(
                args.feed_db, timeframe=account["timeframe"])
            if args.limit is not None:
                if args.limit < 1:
                    raise ValueError("replay limit must be positive")
                bars = bars[:args.limit]
            decisions = {}
            if args.signals:
                with args.signals.open(encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        signal = json.loads(line)
                        key = datetime.fromisoformat(str(signal["bar_ts"]).replace("Z", "+00:00"))
                        if key.tzinfo is None:
                            raise ValueError("signal bar_ts requires offset")
                        key = key.astimezone(timezone.utc).isoformat()
                        if key in decisions:
                            raise ValueError(f"duplicate recorded decision at {key}")
                        size = int(signal.get("size", signal.get("old_bracket_code", 1)))
                        decisions[key] = {"signal": signal["signal"],
                                          "size": size,
                                          "source": "recorded_tape",
                                          "prompt_version": signal.get("prompt_version", ""),
                                          "decision_id": signal.get("ai_decision_id", "")}
            broker = SimBroker(ledger)
            latest = None
            for bar in bars:
                latest = broker.process_bar(args.account, bar,
                                            decisions.get(bar.ts.astimezone(timezone.utc).isoformat()))
            result = {"bars": len(bars), "recorded_decisions": len(decisions), "account": latest}
        elif args.command == "status":
            result = ledger.status(args.account)
        elif args.command in ("pause", "resume"):
            result = ledger.set_pause(args.account, args.command == "pause")
        elif args.command == "reset":
            result = ledger.reset(args.account, args.reason)
        elif args.command == "settle":
            as_of = (datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
                     if args.as_of else datetime.now(timezone.utc))
            result = SimBroker(ledger).settle_day(args.account, as_of)
        else:
            if not 1 <= args.limit <= 1000:
                raise ValueError("history limit must be 1..1000")
            with ledger.connection() as conn:
                ledger.status(args.account)
                events = conn.execute("SELECT id,generation,bar_ts,event_type,payload_json,created_at "
                                      "FROM sim_event WHERE account=? ORDER BY id DESC LIMIT ?",
                                      (args.account, args.limit)).fetchall()
                trades = conn.execute("SELECT * FROM sim_trade WHERE account=? ORDER BY id DESC LIMIT ?",
                                      (args.account, args.limit)).fetchall()
                result = {"events": [{**dict(row), "payload": json.loads(row["payload_json"])}
                                     for row in events], "trades": [dict(row) for row in trades]}
                for event in result["events"]:
                    del event["payload_json"]
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"sim broker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
