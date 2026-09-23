"""Offline replay CLI. No order endpoint or broker import exists here."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from tvbot_v2.replay.topstep import CombineRules, SignalTape, load_csv, load_sqlite, simulate
from tvbot_v2.strategy.jev import BaselineGate, FixtureGate, JevGate


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_result(output_root: Path, result: dict, gate_name: str, source: str,
                  signal_source: str | None = None) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    stage = output_root / ("." + run_id + ".tmp")
    final = output_root / run_id
    stage.mkdir(exist_ok=False)
    try:
        config = (
            'simulation_only = true\n'
            'account_profile = "topstep_50k_combine_proxy"\n'
            f'gate = "{gate_name}"\n'
            f'source = {json.dumps(source)}\n'
            f'source_sha256 = "{_sha256(source)}"\n'
            f'signal_source = {json.dumps(signal_source or "")}\n'
            f'signal_sha256 = "{_sha256(signal_source) if signal_source else ""}"\n'
            f'bar_count = {result["bars"]}\n'
            f'first_bar = "{result["first_bar"]}"\n'
            f'last_bar = "{result["last_bar"]}"\n'
        )
        (stage / "config.toml").write_text(config, encoding="utf-8")
        (stage / "metrics.json").write_text(
            json.dumps({"combined_net_pnl": result["combined_net_pnl"],
                        "accounts": [{key: row[key] for key in ("name", "status", "net_pnl", "trade_count", "best_day")}
                                     for row in result["accounts"]]}, indent=2), encoding="utf-8")
        (stage / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        with (stage / "decisions.jsonl").open("w", encoding="utf-8") as stream:
            for row in result["decisions"]:
                stream.write(json.dumps(row, separators=(",", ":")) + "\n")
        with closing(sqlite3.connect(stage / "results.sqlite3")) as conn:
            conn.executescript("""
                CREATE TABLE account (name TEXT PRIMARY KEY, status TEXT NOT NULL,
                                      ending_balance REAL NOT NULL, net_pnl REAL NOT NULL,
                                      trade_count INTEGER NOT NULL);
                CREATE TABLE trade (account TEXT NOT NULL, entry_ts TEXT NOT NULL,
                                    exit_ts TEXT NOT NULL, direction TEXT NOT NULL,
                                    quantity INTEGER NOT NULL, entry_price REAL NOT NULL,
                                    exit_price REAL NOT NULL, net_pnl REAL NOT NULL,
                                    reason TEXT NOT NULL);
                CREATE TABLE decision (candidate_id TEXT PRIMARY KEY, bar_ts TEXT NOT NULL,
                                       direction TEXT NOT NULL, approved INTEGER NOT NULL,
                                       source TEXT NOT NULL, reason TEXT NOT NULL);
            """)
            for row in result["accounts"]:
                conn.execute("INSERT INTO account VALUES (?,?,?,?,?)",
                             (row["name"], row["status"], row["ending_balance"], row["net_pnl"], row["trade_count"]))
                for trade in row["trades"]:
                    conn.execute("INSERT INTO trade VALUES (?,?,?,?,?,?,?,?,?)",
                                 (row["name"], trade["entry_ts"], trade["exit_ts"], trade["direction"],
                                  trade["quantity"], trade["entry_price"], trade["exit_price"],
                                  trade["net_pnl"], trade["reason"]))
            for decision in result["decisions"]:
                conn.execute("INSERT INTO decision VALUES (?,?,?,?,?,?)",
                             (decision["candidate_id"], decision["bar_ts"], decision["direction"],
                              int(decision["approved"]), decision["source"], decision["reason"]))
            conn.commit()
        lines = ["# Offline 50K Combine replay", "",
                 f"Source: `{source}`", f"Decision gate: `{gate_name}`",
                 f"Bars: {result['bars']} ({result['first_bar']} to {result['last_bar']})", "",
                 "| Account variant | Status | Net P&L | Trades | Best day |",
                 "|---|---|---:|---:|---:|"]
        for row in result["accounts"]:
            lines.append(f"| {row['name']} | {row['status']} | ${row['net_pnl']:,.2f} | "
                         f"{row['trade_count']} | ${row['best_day']:,.2f} |")
        lines += ["", f"Combined net P&L: ${result['combined_net_pnl']:,.2f}", "",
                  "Five independent $50K simulated accounts; the $250K total is a buying-power label, not cash capital.",
                  "Rules are today's Topstep 50K Combine proxy, not the cancelled beta account agreement.",
                  "OHLC bars cannot prove fill sequence; stops win ambiguous stop/target bars.",
                  "Historical Jev calls, if selected, are retrospective judgments, not decisions made at that historical time.",
                  "No order was placed."]
        (stage / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        stage.rename(final)
    except Exception:
        # Keep the temp directory for diagnosis; never overwrite a finished run.
        raise
    return final


class _BudgetGate:
    def __init__(self, gate: JevGate, budget: int):
        self.gate = gate
        self.budget = budget
        self.calls = 0

    def evaluate(self, state):
        if self.calls >= self.budget:
            raise RuntimeError(f"Jev call budget of {self.budget} reached; replay aborted")
        self.calls += 1
        return self.gate.evaluate(state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay five offline simulated Topstep-style MES accounts")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="MES 5-minute OHLC CSV with timezone-aware timestamps")
    source.add_argument("--db", help="v2 canonical SQLite feed database")
    parser.add_argument("--timeframe", choices=("5m", "15m", "30m"), default="30m")
    parser.add_argument("--signals", help="recorded n8n/Prodex signal tape JSONL")
    parser.add_argument("--gate", choices=("baseline", "jev", "fixture"), default="baseline")
    parser.add_argument("--jev-fixture", help="recorded Jev decision JSONL for --gate fixture")
    parser.add_argument("--jev-budget", type=int, default=100)
    parser.add_argument("--allow-retrospective-jev", action="store_true")
    parser.add_argument("--round-turn-fee", type=float, default=1.22)
    parser.add_argument("--slippage-ticks", type=int, default=1)
    parser.add_argument("--output", default="runs/topstep-50k")
    args = parser.parse_args(argv)
    bars = load_csv(args.csv) if args.csv else load_sqlite(args.db, timeframe=args.timeframe)
    signal_tape = SignalTape.from_jsonl(args.signals) if args.signals else None
    if args.gate == "jev":
        if not args.allow_retrospective_jev:
            parser.error("--gate jev requires --allow-retrospective-jev")
        if args.jev_budget < 1:
            parser.error("--jev-budget must be positive")
        gate = _BudgetGate(JevGate(), args.jev_budget)
    elif args.gate == "fixture":
        if not args.jev_fixture:
            parser.error("--gate fixture requires --jev-fixture")
        gate = FixtureGate.from_jsonl(args.jev_fixture)
    else:
        gate = BaselineGate()
    result = simulate(bars, gate, CombineRules(round_turn_fee=args.round_turn_fee,
                                                slippage_ticks=args.slippage_ticks),
                      signal_tape=signal_tape, bar_minutes=int(args.timeframe[:-1]))
    run_dir = _write_result(Path(args.output), result, args.gate,
                            str(args.csv or args.db), args.signals)
    print(f"Run saved: {run_dir}")
    print(f"Combined net P&L: ${result['combined_net_pnl']:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
