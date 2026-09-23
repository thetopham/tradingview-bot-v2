"""SQLite state and append-only audit events for simulated accounts."""

from __future__ import annotations

from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
from typing import Any

from tvbot_v2.db import connect_db
from tvbot_v2.replay.topstep import CombineRules
from tvbot_v2.simulate.portfolio import DEFAULT_PORTFOLIO, SimVariant, validate_portfolio


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SimLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with closing(connect_db(self.path)) as conn:
            conn.executescript(schema)
            conn.commit()

    def connection(self) -> sqlite3.Connection:
        conn = connect_db(self.path)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def register(self, variants: tuple[SimVariant, ...] = DEFAULT_PORTFOLIO,
                 rules: CombineRules = CombineRules()) -> list[dict[str, Any]]:
        validate_portfolio(variants)
        rules_json = _json(asdict(rules))
        with closing(self.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                existing = {row["name"]: row for row in conn.execute("SELECT * FROM sim_account")}
                for variant in variants:
                    if variant.name in existing:
                        if (existing[variant.name]["variant_json"] != variant.to_json()
                                or existing[variant.name]["rules_json"] != rules_json):
                            raise ValueError(f"existing account {variant.name} has different settings")
                    else:
                        conn.execute(
                            "INSERT INTO sim_account(name,variant_json,rules_json,balance,mll,"
                            "day_start_balance,status) VALUES (?,?,?,?,?,?,?)",
                            (variant.name, variant.to_json(), rules_json,
                             rules.starting_balance, rules.starting_balance - rules.maximum_loss,
                             rules.starting_balance, "active"))
                        self._event(conn, variant.name, 1, None, "account_created",
                                    {"variant": json.loads(variant.to_json()), "rules": asdict(rules)})
        return self.status()

    @staticmethod
    def _event(conn: sqlite3.Connection, account: str, generation: int,
               bar_ts: str | None, event_type: str, payload: dict[str, Any]) -> None:
        conn.execute("INSERT INTO sim_event(account,generation,bar_ts,event_type,payload_json) "
                     "VALUES (?,?,?,?,?)", (account, generation, bar_ts, event_type, _json(payload)))

    @staticmethod
    def _snapshot(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        variant = SimVariant.from_json(row["variant_json"])
        rules = CombineRules(**json.loads(row["rules_json"]))
        day_pnl = json.loads(row["day_pnl_json"])
        best_day = max(day_pnl.values(), default=0.0)
        position = conn.execute("SELECT * FROM sim_position WHERE account=?", (row["name"],)).fetchone()
        position_data = dict(position) if position else None
        equity = row["balance"]
        if position is not None and row["last_bar_close"] is not None:
            equity += ((row["last_bar_close"] - position["entry_price"])
                       * position["direction"] * 5.0 * position["quantity"])
        trades = conn.execute("SELECT count(*) FROM sim_trade WHERE account=? AND generation=?",
                              (row["name"], row["generation"])).fetchone()[0]
        return {"account": row["name"], "generation": row["generation"],
                "strategy_id": variant.strategy_id, "timeframe": variant.timeframe,
                "status": row["status"], "manual_paused": bool(row["manual_paused"]),
                "balance": round(row["balance"], 2), "equity": round(equity, 2),
                "mll": round(row["mll"], 2), "mll_remaining": round(equity - row["mll"], 2),
                "net_pnl": round(row["balance"] - rules.starting_balance, 2),
                "effective_profit_target": round(max(rules.profit_target,
                                                      best_day / rules.consistency_fraction), 2),
                "best_day": round(best_day, 2), "day_pnl": day_pnl,
                "position": position_data, "pending": json.loads(row["pending_json"]) if row["pending_json"] else None,
                "next_check": row["next_check"], "last_bar_ts": row["last_bar_ts"],
                "trade_count": trades}

    def status(self, account: str | None = None) -> list[dict[str, Any]]:
        with closing(self.connection()) as conn:
            rows = conn.execute("SELECT * FROM sim_account WHERE (? IS NULL OR name=?) ORDER BY name",
                                (account, account)).fetchall()
            if account and not rows:
                raise ValueError(f"unknown simulated account: {account}")
            return [self._snapshot(conn, row) for row in rows]

    def set_pause(self, account: str, paused: bool) -> dict[str, Any]:
        with closing(self.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account}")
                if bool(row["manual_paused"]) != paused:
                    conn.execute("UPDATE sim_account SET manual_paused=?,updated_at=CURRENT_TIMESTAMP WHERE name=?",
                                 (int(paused), account))
                    self._event(conn, account, row["generation"], None,
                                "manual_pause" if paused else "manual_resume", {})
                updated = conn.execute("SELECT * FROM sim_account WHERE name=?", (account,)).fetchone()
                return self._snapshot(conn, updated)

    def reset(self, account: str, reason: str) -> dict[str, Any]:
        if not reason.strip() or len(reason) > 200:
            raise ValueError("reset requires a short reason")
        with closing(self.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account}")
                if conn.execute("SELECT 1 FROM sim_position WHERE account=?", (account,)).fetchone():
                    raise ValueError("flatten simulated position before reset")
                rules = CombineRules(**json.loads(row["rules_json"]))
                generation = row["generation"] + 1
                conn.execute("UPDATE sim_account SET generation=?,balance=?,mll=?,day_start_balance=?,"
                             "day_pnl_json='{}',status='active',manual_paused=0,current_day=NULL,"
                             "last_bar_ts=NULL,last_bar_close=NULL,pending_json=NULL,next_check='',"
                             "updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (generation, rules.starting_balance,
                              rules.starting_balance - rules.maximum_loss,
                              rules.starting_balance, account))
                self._event(conn, account, generation, None, "account_reset", {"reason": reason})
                updated = conn.execute("SELECT * FROM sim_account WHERE name=?", (account,)).fetchone()
                return self._snapshot(conn, updated)
