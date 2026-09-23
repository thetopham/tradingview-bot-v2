"""Persistent, brokerless MES execution on closed bars.

A decision observed at bar close can fill only at the next contiguous bar open.
The SQLite transaction makes retries harmless and preserves an audit trail.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from typing import Any

from tvbot_v2.replay.topstep import (
    Account, AccountVariant, Bar, CombineRules, Position, TICK_SIZE, CT,
    _check_position, _exit, _finalize_day, entry_allowed, trade_day,
)
from tvbot_v2.simulate.ledger import SimLedger, _json
from tvbot_v2.simulate.portfolio import SimVariant


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires an offset")
    return result.astimezone(timezone.utc)


def normalize_decision(value: dict[str, Any] | None, account: str,
                       timeframe: str, bar_ts: str) -> dict[str, Any]:
    if value is None:
        return {"signal": "HOLD", "size": 1, "source": "no_decision"}
    if not isinstance(value, dict):
        raise ValueError("decision must be an object")
    if value.get("account") not in (None, account) or value.get("timeframe") not in (None, timeframe):
        raise ValueError("decision account or timeframe mismatch")
    if "bar_ts" in value and utc(str(value["bar_ts"])).isoformat() != bar_ts:
        raise ValueError("decision belongs to a different bar")
    signal = str(value.get("signal", "HOLD")).upper()
    size = value.get("size", 1)
    if signal not in {"BUY", "SELL", "HOLD", "FLAT"} or type(size) is not int or size not in (1, 2, 3):
        raise ValueError("decision needs BUY/SELL/HOLD/FLAT and size 1, 2, or 3")
    result = {"signal": signal, "size": size, "source": str(value.get("source", "recorded"))[:80]}
    for key in ("reason", "invalidation", "next_check", "prompt_version", "decision_id"):
        if value.get(key) is not None:
            result[key] = str(value[key])[:1000]
    if len(result.get("next_check", "")) > 120:
        raise ValueError("next_check exceeds 120 characters")
    return result


class SimBroker:
    def __init__(self, ledger: SimLedger):
        self.ledger = ledger

    def process_bar(self, account_name: str, bar: Bar,
                    decision: dict[str, Any] | None = None) -> dict[str, Any]:
        bar_ts = bar.ts.astimezone(timezone.utc).isoformat()
        with closing(self.ledger.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account_name}")
                variant = SimVariant.from_json(row["variant_json"])
                rules = CombineRules(**json.loads(row["rules_json"]))
                if bar.ts + timedelta(minutes=variant.minutes) > datetime.now(timezone.utc):
                    raise ValueError("bar is not closed yet")
                normalized = normalize_decision(decision, account_name, variant.timeframe, bar_ts)
                bar_data = {"timestamp": bar_ts, "open": bar.open, "high": bar.high,
                            "low": bar.low, "close": bar.close, "volume": bar.volume}
                digest = hashlib.sha256(_json({"bar": bar_data, "decision": normalized}).encode()).hexdigest()
                prior = conn.execute(
                    "SELECT input_hash,snapshot_json FROM sim_bar WHERE account=? AND generation=? AND bar_ts=?",
                    (account_name, row["generation"], bar_ts)).fetchone()
                if prior:
                    if prior["input_hash"] != digest:
                        raise ValueError("conflicting resubmission of processed bar")
                    return json.loads(prior["snapshot_json"])
                last_ts = utc(row["last_bar_ts"]) if row["last_bar_ts"] else None
                if last_ts is not None and bar.ts <= last_ts:
                    raise ValueError("out-of-order bar")
                stored = conn.execute("SELECT * FROM sim_position WHERE account=?", (account_name,)).fetchone()
                position = (Position(stored["direction"], stored["quantity"], stored["entry_price"],
                                     utc(stored["entry_ts"]), stored["stop_price"], stored["target_price"])
                            if stored else None)
                account = Account(AccountVariant(variant.name, 1, 1, 1), row["balance"], row["mll"],
                                  row["day_start_balance"], position=position, status=row["status"],
                                  day_pnl=json.loads(row["day_pnl_json"]))
                generation = row["generation"]

                def event(kind: str, data: dict[str, Any]) -> None:
                    self.ledger._event(conn, account_name, generation, bar_ts, kind, data)

                day = trade_day(bar.ts)
                contiguous = last_ts is not None and bar.ts - last_ts == timedelta(minutes=variant.minutes)
                if last_ts is not None and (row["current_day"] != day.isoformat() or not contiguous):
                    if account.position is not None:
                        previous = Bar(last_ts, row["last_bar_close"], row["last_bar_close"],
                                       row["last_bar_close"], row["last_bar_close"])
                        reason = ("data_gap_session_flat" if row["current_day"] != day.isoformat()
                                  else "data_gap_flat")
                        _exit(account, previous, previous.close, reason, rules)
                        event("risk", {"reason": reason, "last_bar_ts": last_ts.isoformat()})
                    if row["current_day"] != day.isoformat():
                        if row["current_day"] not in account.day_pnl:
                            _finalize_day(account, date.fromisoformat(row["current_day"]), rules)
                            event("day_finalized", {"day": row["current_day"], "balance": account.balance,
                                                    "mll": account.mll, "status": account.status})
                        if account.status == "paused_for_day":
                            account.status = "active"
                    else:
                        event("risk", {"reason": "missing_intraday_bars"})
                pending = json.loads(row["pending_json"]) if contiguous and row["pending_json"] else None
                if pending and account.status == "active":
                    signal = pending["signal"]
                    direction = 1 if signal == "BUY" else -1 if signal == "SELL" else 0
                    if account.position is not None and (signal == "FLAT" or account.position.direction != direction):
                        price = bar.open - account.position.direction * rules.slippage_ticks * TICK_SIZE
                        _exit(account, bar, price, "signal_flat" if signal == "FLAT" else "signal_reverse", rules)
                        event("exit_fill", {"signal": signal, "price": price})
                    if (direction and account.position is None and not row["manual_paused"]
                            and entry_allowed(bar.ts, variant.minutes)):
                        bracket = variant.bracket(pending["size"])
                        fee = rules.round_turn_fee * bracket.contracts / 2
                        if account.balance - fee <= account.mll:
                            event("risk", {"reason": "entry_fee_reaches_mll"})
                        else:
                            price = bar.open + direction * rules.slippage_ticks * TICK_SIZE
                            account.balance -= fee
                            event("fee_charged", {"phase": "entry", "amount": fee})
                            account.position = Position(direction, bracket.contracts, price, bar.ts,
                                                        price - direction * bracket.stop_ticks * TICK_SIZE,
                                                        price + direction * bracket.target_ticks * TICK_SIZE)
                            event("entry_fill", {"signal": signal, "size": pending["size"],
                                                 "quantity": bracket.contracts, "price": price,
                                                 "stop": account.position.stop_price,
                                                 "target": account.position.target_price,
                                                 "source_bar_ts": pending["source_bar_ts"]})
                if account.status in {"active", "paused_for_day"}:
                    _check_position(account, bar, rules, variant.minutes)
                for trade in account.trades:
                    conn.execute("INSERT INTO sim_trade(account,generation,entry_ts,exit_ts,direction,quantity,"
                                 "entry_price,exit_price,gross_pnl,net_pnl,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (account_name, generation, trade["entry_ts"], trade["exit_ts"],
                                  trade["direction"], trade["quantity"], trade["entry_price"],
                                  trade["exit_price"], trade["gross_pnl"], trade["net_pnl"], trade["reason"]))
                    event("trade_closed", trade)
                    event("fee_charged", {"phase": "exit", "amount":
                                          rules.round_turn_fee * trade["quantity"] / 2})
                for risk in account.risk_events:
                    event("risk", risk)
                if account.status != row["status"]:
                    event("status_changed", {"from": row["status"], "to": account.status})
                next_pending = None
                if normalized["signal"] != "HOLD" and account.status == "active":
                    if normalized["signal"] == "FLAT":
                        if account.position is not None:
                            next_pending = {"signal": "FLAT", "size": normalized["size"],
                                            "source_bar_ts": bar_ts}
                    elif not row["manual_paused"] and entry_allowed(bar.ts, variant.minutes):
                        variant.bracket(normalized["size"])
                        next_pending = {"signal": normalized["signal"], "size": normalized["size"],
                                        "source_bar_ts": bar_ts}
                event("decision", {**normalized, "queued": next_pending is not None})
                conn.execute("UPDATE sim_account SET balance=?,mll=?,day_start_balance=?,day_pnl_json=?,"
                             "status=?,current_day=?,last_bar_ts=?,last_bar_close=?,pending_json=?,next_check=?,"
                             "updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (account.balance, account.mll, account.day_start_balance, _json(account.day_pnl),
                              account.status, day.isoformat(), bar_ts, bar.close,
                              _json(next_pending) if next_pending else None,
                              normalized.get("next_check", ""), account_name))
                conn.execute("DELETE FROM sim_position WHERE account=?", (account_name,))
                if account.position is not None:
                    p = account.position
                    conn.execute("INSERT INTO sim_position(account,generation,direction,quantity,entry_price,"
                                 "entry_ts,stop_price,target_price) VALUES (?,?,?,?,?,?,?,?)",
                                 (account_name, generation, p.direction, p.quantity, p.entry_price,
                                  p.entry_ts.isoformat(), p.stop_price, p.target_price))
                updated = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                snapshot = self.ledger._snapshot(conn, updated)
                snapshot.update({"bar_ts": bar_ts, "decision": normalized})
                conn.execute("INSERT INTO sim_bar(account,generation,bar_ts,input_hash,decision_json,snapshot_json)"
                             " VALUES (?,?,?,?,?,?)",
                             (account_name, generation, bar_ts, digest, _json(normalized), _json(snapshot)))
                return snapshot

    def settle_day(self, account_name: str, as_of: datetime) -> dict[str, Any]:
        """Finalize a completed trading day without waiting for the next bar."""
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of requires a timezone offset")
        with closing(self.ledger.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account_name}")
                if row["current_day"] is None:
                    raise ValueError("account has no processed bars")
                day = date.fromisoformat(row["current_day"])
                cutoff = datetime.combine(day, time(15, 10), CT)
                if as_of.astimezone(CT) < cutoff:
                    raise ValueError("trading day has not reached session close")
                account_day_pnl = json.loads(row["day_pnl_json"])
                if day.isoformat() in account_day_pnl:
                    return self.ledger._snapshot(conn, row)
                variant = SimVariant.from_json(row["variant_json"])
                rules = CombineRules(**json.loads(row["rules_json"]))
                position_row = conn.execute("SELECT * FROM sim_position WHERE account=?",
                                            (account_name,)).fetchone()
                account = Account(AccountVariant(variant.name, 1, 1, 1), row["balance"], row["mll"],
                                  row["day_start_balance"], status=row["status"],
                                  day_pnl=account_day_pnl)
                if position_row is not None:
                    p = Position(position_row["direction"], position_row["quantity"],
                                 position_row["entry_price"], utc(position_row["entry_ts"]),
                                 position_row["stop_price"], position_row["target_price"])
                    account.position = p
                    last = utc(row["last_bar_ts"])
                    close = row["last_bar_close"]
                    price = close - p.direction * rules.slippage_ticks * TICK_SIZE
                    _exit(account, Bar(last, close, close, close, close), price,
                          "session_settlement_flat", rules)
                    conn.execute("DELETE FROM sim_position WHERE account=?", (account_name,))
                    self.ledger._event(conn, account_name, row["generation"], row["last_bar_ts"],
                                       "risk", {"reason": "position_open_at_settlement"})
                    trade = account.trades[0]
                    conn.execute("INSERT INTO sim_trade(account,generation,entry_ts,exit_ts,direction,quantity,"
                                 "entry_price,exit_price,gross_pnl,net_pnl,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (account_name, row["generation"], trade["entry_ts"], trade["exit_ts"],
                                  trade["direction"], trade["quantity"], trade["entry_price"],
                                  trade["exit_price"], trade["gross_pnl"], trade["net_pnl"], trade["reason"]))
                    self.ledger._event(conn, account_name, row["generation"], row["last_bar_ts"],
                                       "trade_closed", trade)
                _finalize_day(account, day, rules)
                conn.execute("UPDATE sim_account SET balance=?,mll=?,day_start_balance=?,day_pnl_json=?,"
                             "status=?,pending_json=NULL,updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (account.balance, account.mll, account.day_start_balance,
                              _json(account.day_pnl), account.status, account_name))
                self.ledger._event(conn, account_name, row["generation"], row["last_bar_ts"],
                                   "day_finalized", {"day": day.isoformat(), "balance": account.balance,
                                                     "mll": account.mll, "status": account.status})
                if account.status != row["status"]:
                    self.ledger._event(conn, account_name, row["generation"], row["last_bar_ts"],
                                       "status_changed", {"from": row["status"], "to": account.status})
                updated = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                return self.ledger._snapshot(conn, updated)

    def process_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(envelope, dict) or not isinstance(envelope.get("bar"), dict):
            raise ValueError("input requires account and bar object")
        raw = envelope["bar"]
        bar = Bar(utc(str(raw["timestamp"])), float(raw["open"]), float(raw["high"]),
                  float(raw["low"]), float(raw["close"]), float(raw.get("volume", 0)))
        return self.process_bar(str(envelope["account"]), bar, envelope.get("decision"))
