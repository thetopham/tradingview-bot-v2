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

    def submit_decision(self, account_name: str, bar_ts: str,
                        decision: dict[str, Any], *,
                        available_at: datetime | None = None) -> dict[str, Any]:
        """Queue a closed decision bar for the next execution open after it is available."""
        decision_ts = utc(bar_ts)
        now = available_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("decision availability requires a timezone offset")
        now = now.astimezone(timezone.utc)
        with closing(self.ledger.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account_name}")
                variant = SimVariant.from_json(row["variant_json"])
                if variant.execution_minutes == variant.minutes:
                    raise ValueError("account has no separate execution feed")
                if decision_ts + timedelta(minutes=variant.minutes) > now:
                    raise ValueError("decision bar is not closed yet")
                normalized = normalize_decision(decision, account_name, variant.timeframe,
                                                decision_ts.isoformat())
                digest = hashlib.sha256(_json(normalized).encode()).hexdigest()
                prior = conn.execute(
                    "SELECT input_hash,snapshot_json FROM sim_decision WHERE account=? AND generation=? AND bar_ts=?",
                    (account_name, row["generation"], decision_ts.isoformat())).fetchone()
                if prior:
                    if prior["input_hash"] != digest:
                        raise ValueError("conflicting resubmission of decision")
                    return json.loads(prior["snapshot_json"])
                latest = conn.execute(
                    "SELECT bar_ts FROM sim_decision WHERE account=? AND generation=? ORDER BY bar_ts DESC LIMIT 1",
                    (account_name, row["generation"])).fetchone()
                if latest and decision_ts <= utc(latest["bar_ts"]):
                    raise ValueError("out-of-order decision")
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                elapsed_minutes = now.hour * 60 + now.minute
                next_boundary = ((elapsed_minutes // variant.execution_minutes) + 1) * variant.execution_minutes
                earliest = day_start + timedelta(minutes=next_boundary)
                pending = None
                if normalized["signal"] != "HOLD" and row["status"] == "active":
                    if normalized["signal"] == "FLAT":
                        if conn.execute("SELECT 1 FROM sim_position WHERE account=?",
                                        (account_name,)).fetchone():
                            pending = {"signal": "FLAT", "size": normalized["size"],
                                       "source_bar_ts": decision_ts.isoformat(),
                                       "earliest_fill_ts": earliest.isoformat()}
                    elif not row["manual_paused"]:
                        variant.bracket(normalized["size"])
                        pending = {"signal": normalized["signal"], "size": normalized["size"],
                                   "source_bar_ts": decision_ts.isoformat(),
                                   "earliest_fill_ts": earliest.isoformat()}
                conn.execute("UPDATE sim_account SET pending_json=?,next_check=?,"
                             "updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (_json(pending) if pending else None,
                              normalized.get("next_check", ""), account_name))
                self.ledger._event(conn, account_name, row["generation"],
                                   decision_ts.isoformat(), "decision",
                                   {**normalized, "queued": pending is not None,
                                    "available_at": now.isoformat(),
                                    "earliest_fill_ts": earliest.isoformat()})
                updated = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                snapshot = self.ledger._snapshot(conn, updated)
                snapshot.update({"decision_bar_ts": decision_ts.isoformat(), "decision": normalized})
                conn.execute("INSERT INTO sim_decision(account,generation,bar_ts,input_hash,decision_json,"
                             "available_at,snapshot_json) VALUES (?,?,?,?,?,?,?)",
                             (account_name, row["generation"], decision_ts.isoformat(),
                              digest, _json(normalized), now.isoformat(), _json(snapshot)))
                return snapshot

    def submit_order(self, account_name: str, signal: str, size: int,
                     client_order_id: str, *, available_at: datetime | None = None,
                     metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Accept a broker order after a strategy finishes, independent of its candle size."""
        now = available_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("order availability requires a timezone offset")
        now = now.astimezone(timezone.utc)
        signal = str(signal).upper()
        if signal not in {"BUY", "SELL", "FLAT"} or type(size) is not int or size not in (1, 2, 3):
            raise ValueError("order needs BUY/SELL/FLAT and size 1, 2, or 3")
        if not isinstance(client_order_id, str) or not 1 <= len(client_order_id) <= 160:
            raise ValueError("client_order_id is required and must be at most 160 characters")
        metadata = metadata or {}
        if not isinstance(metadata, dict):
            raise ValueError("order metadata must be an object")
        with closing(self.ledger.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account_name}")
                variant = SimVariant.from_json(row["variant_json"])
                if variant.execution_minutes != 1:
                    raise ValueError("broker orders require a one-minute execution profile")
                prior = conn.execute(
                    "SELECT * FROM sim_order WHERE account=? AND generation=? AND client_order_id=?",
                    (account_name, row["generation"], client_order_id)).fetchone()
                if prior:
                    if (prior["signal"], prior["size"], prior["metadata_json"]) != (
                            signal, size, _json(metadata)):
                        raise ValueError("conflicting resubmission of broker order")
                    return {"orderId": f"SIM-{prior['id']}", "status": prior["status"],
                            "earliestFillTs": prior["earliest_fill_ts"]}
                if row["status"] != "active" or (row["manual_paused"] and signal != "FLAT"):
                    raise ValueError("simulated account is not accepting orders")
                if signal != "FLAT":
                    variant.bracket(size)
                day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                next_minute = now.hour * 60 + now.minute + 1
                earliest = day_start + timedelta(minutes=next_minute)
                old_pending = json.loads(row["pending_json"]) if row["pending_json"] else None
                if old_pending and old_pending.get("order_id"):
                    conn.execute("UPDATE sim_order SET status='replaced' WHERE id=? AND status='queued'",
                                 (old_pending["order_id"],))
                    self.ledger._event(conn, account_name, row["generation"], None,
                                       "order_cancelled", {"order_id": f"SIM-{old_pending['order_id']}",
                                                           "reason": "replaced"})
                cursor = conn.execute(
                    "INSERT INTO sim_order(account,generation,client_order_id,signal,size,available_at,"
                    "earliest_fill_ts,status,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (account_name, row["generation"], client_order_id, signal, size,
                     now.isoformat(), earliest.isoformat(), "queued", _json(metadata)))
                order_id = cursor.lastrowid
                pending = {"signal": signal, "size": size, "source_bar_ts": now.isoformat(),
                           "earliest_fill_ts": earliest.isoformat(), "order_id": order_id}
                conn.execute("UPDATE sim_account SET pending_json=?,updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (_json(pending), account_name))
                self.ledger._event(conn, account_name, row["generation"], None,
                                   "order_submitted", {"order_id": f"SIM-{order_id}",
                                                       "signal": signal, "size": size,
                                                       "available_at": now.isoformat(),
                                                       "earliest_fill_ts": earliest.isoformat(),
                                                       "metadata": metadata})
                return {"orderId": f"SIM-{order_id}", "status": "queued",
                        "earliestFillTs": earliest.isoformat()}

    def cancel_order(self, account_name: str, order_id: str) -> bool:
        """Cancel a pending market intent; filled orders and brackets are immutable."""
        if not isinstance(order_id, str) or not order_id.startswith("SIM-"):
            raise ValueError("invalid simulated order id")
        try:
            numeric_id = int(order_id[4:])
        except ValueError as exc:
            raise ValueError("invalid simulated order id") from exc
        with closing(self.ledger.connection()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM sim_account WHERE name=?", (account_name,)).fetchone()
                if row is None:
                    raise ValueError(f"unknown simulated account: {account_name}")
                order = conn.execute("SELECT status FROM sim_order WHERE id=? AND account=? AND generation=?",
                                     (numeric_id, account_name, row["generation"])).fetchone()
                if order is None:
                    raise ValueError("unknown simulated order")
                if order["status"] != "queued":
                    return False
                pending = json.loads(row["pending_json"]) if row["pending_json"] else None
                if pending and pending.get("order_id") == numeric_id:
                    conn.execute("UPDATE sim_account SET pending_json=NULL,updated_at=CURRENT_TIMESTAMP "
                                 "WHERE name=?", (account_name,))
                conn.execute("UPDATE sim_order SET status='cancelled' WHERE id=?", (numeric_id,))
                self.ledger._event(conn, account_name, row["generation"], None,
                                   "order_cancelled", {"order_id": order_id})
                return True

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
                if bar.ts + timedelta(minutes=variant.execution_minutes) > datetime.now(timezone.utc):
                    raise ValueError("bar is not closed yet")
                if variant.execution_minutes != variant.minutes and decision is not None:
                    raise ValueError("submit decisions separately for this account")
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
                contiguous = last_ts is not None and bar.ts - last_ts == timedelta(minutes=variant.execution_minutes)
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
                if account.balance <= account.mll and account.status != "failed":
                    account.status = "failed"
                    event("risk", {"reason": "maximum_loss_realized", "balance": account.balance})
                pending_before = json.loads(row["pending_json"]) if row["pending_json"] else None
                first_gap = (last_ts is None and pending_before is not None and
                             pending_before.get("earliest_fill_ts") is not None and
                             bar.ts > utc(pending_before["earliest_fill_ts"]))
                if pending_before and ((last_ts is not None and not contiguous) or first_gap) and pending_before.get("order_id"):
                    conn.execute("UPDATE sim_order SET status='cancelled' WHERE id=? AND status='queued'",
                                 (pending_before["order_id"],))
                pending = pending_before if (last_ts is None or contiguous) and not first_gap else None
                if pending and pending.get("earliest_fill_ts") and bar.ts < utc(pending["earliest_fill_ts"]):
                    pending = None
                order_filled = False
                if pending and account.status == "active":
                    signal = pending["signal"]
                    direction = 1 if signal == "BUY" else -1 if signal == "SELL" else 0
                    if account.position is not None and (signal == "FLAT" or account.position.direction != direction):
                        price = bar.open - account.position.direction * rules.slippage_ticks * TICK_SIZE
                        _exit(account, bar, price, "signal_flat" if signal == "FLAT" else "signal_reverse", rules)
                        event("exit_fill", {"signal": signal, "price": price})
                        order_filled = True
                    if (direction and account.position is None and not row["manual_paused"]
                            and entry_allowed(bar.ts, variant.execution_minutes)):
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
                                                 "source_bar_ts": pending["source_bar_ts"],
                                                 "order_id": (f"SIM-{pending['order_id']}"
                                                              if pending.get("order_id") else None)})
                            order_filled = True
                if pending and pending.get("order_id"):
                    conn.execute("UPDATE sim_order SET status=? WHERE id=? AND status='queued'",
                                 ("filled" if order_filled else "cancelled", pending["order_id"]))
                    event("order_filled" if order_filled else "order_cancelled",
                          {"order_id": f"SIM-{pending['order_id']}",
                           "signal": pending["signal"],
                           "price": bar.open if order_filled else None})
                if account.status in {"active", "paused_for_day"}:
                    _check_position(account, bar, rules, variant.execution_minutes)
                if account.balance <= account.mll and account.status != "failed":
                    account.status = "failed"
                    event("risk", {"reason": "maximum_loss_realized", "balance": account.balance})
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
                next_pending = (json.loads(row["pending_json"])
                                if variant.execution_minutes != variant.minutes and
                                (last_ts is None or contiguous) and not first_gap and
                                row["pending_json"] and pending is None else None)
                if normalized["signal"] != "HOLD" and account.status == "active":
                    if normalized["signal"] == "FLAT":
                        if account.position is not None:
                            next_pending = {"signal": "FLAT", "size": normalized["size"],
                                            "source_bar_ts": bar_ts}
                    elif not row["manual_paused"] and entry_allowed(bar.ts, variant.execution_minutes):
                        variant.bracket(normalized["size"])
                        next_pending = {"signal": normalized["signal"], "size": normalized["size"],
                                        "source_bar_ts": bar_ts}
                if variant.execution_minutes == variant.minutes:
                    event("decision", {**normalized, "queued": next_pending is not None})
                conn.execute("UPDATE sim_account SET balance=?,mll=?,day_start_balance=?,day_pnl_json=?,"
                             "status=?,current_day=?,last_bar_ts=?,last_bar_close=?,pending_json=?,next_check=?,"
                             "updated_at=CURRENT_TIMESTAMP WHERE name=?",
                             (account.balance, account.mll, account.day_start_balance, _json(account.day_pnl),
                              account.status, day.isoformat(), bar_ts, bar.close,
                              _json(next_pending) if next_pending else None,
                              (row["next_check"] if variant.execution_minutes != variant.minutes
                               else normalized.get("next_check", "")), account_name))
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
                if account.balance <= account.mll and account.status != "failed":
                    account.status = "failed"
                    self.ledger._event(conn, account_name, row["generation"], row["last_bar_ts"],
                                       "risk", {"reason": "maximum_loss_realized",
                                                "balance": account.balance})
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
