"""Offline five-account Topstep-style Trading Combine replay for MES bars.

This is a research approximation. OHLC bars do not reveal the intrabar path,
so an ambiguous stop/target bar is charged as a stop. No broker code is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import json
import math
import sqlite3
from typing import Any, Protocol

from tvbot_v2.strategy.jev import GateResult


CT = ZoneInfo("America/Chicago")
TICK_SIZE = 0.25
POINT_VALUE = 5.0  # MES dollars per index point, per micro contract


@dataclass(frozen=True)
class Bar:
    ts: datetime  # bar open, with timezone
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise ValueError("bar timestamps must include a timezone offset")
        prices = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError("bar prices must be finite and positive")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC range")


def load_csv(path: str | Path) -> list[Bar]:
    """Read timestamp,open,high,low,close[,volume] with ISO offset timestamps."""
    with open(path, newline="", encoding="utf-8-sig") as stream:
        rows = csv.DictReader(stream)
        if not {"timestamp", "open", "high", "low", "close"}.issubset(rows.fieldnames or []):
            raise ValueError("CSV needs timestamp,open,high,low,close columns")
        bars = [
            Bar(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
                float(row["open"]), float(row["high"]), float(row["low"]),
                float(row["close"]), float(row.get("volume") or 0))
            for row in rows
        ]
    return validate_bars(bars)


def load_sqlite(path: str | Path, symbol: str = "MES", timeframe: str = "5m") -> list[Bar]:
    """Read v2's canonical bar table. This function never creates the DB."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT ts, open, high, low, close, COALESCE(volume, 0) FROM bar "
            "WHERE symbol = ? AND timeframe = ? ORDER BY ts", (symbol, timeframe)
        ).fetchall()
    return validate_bars([
        Bar(datetime.fromisoformat(row[0].replace("Z", "+00:00")), *map(float, row[1:]))
        for row in rows
    ])


def validate_bars(bars: list[Bar]) -> list[Bar]:
    if not bars:
        raise ValueError("no bars supplied")
    bars = sorted(bars, key=lambda bar: bar.ts)
    if any(left.ts >= right.ts for left, right in zip(bars, bars[1:])):
        raise ValueError("duplicate or unordered bar timestamps")
    return bars


def trade_day(ts: datetime) -> date:
    local = ts.astimezone(CT)
    return (local + timedelta(days=1)).date() if local.time() >= time(17) else local.date()


def market_open(ts: datetime) -> bool:
    local = ts.astimezone(CT)
    weekday = local.weekday()
    if weekday == 5 or (weekday == 6 and local.time() < time(17)):
        return False
    if weekday == 4 and local.time() >= time(15, 10):
        return False
    return local.time() >= time(17) or local.time() < time(15, 10)


def entry_allowed(ts: datetime, bar_minutes: int) -> bool:
    if not market_open(ts):
        return False
    local = ts.astimezone(CT)
    if local.time() >= time(17):
        return True
    cutoff = datetime.combine(local.date(), time(15, 10), CT)
    return local + timedelta(minutes=bar_minutes) <= cutoff


@dataclass(frozen=True)
class AccountVariant:
    name: str
    contracts: int
    stop_ticks: int
    target_ticks: int

    def __post_init__(self) -> None:
        if not (1 <= self.contracts <= 50):
            raise ValueError("50K Combine allows at most 50 MES micro contracts")
        if self.stop_ticks < 1 or self.target_ticks < 1:
            raise ValueError("stop and target must be positive ticks")


DEFAULT_VARIANTS = (
    AccountVariant("conservative", 1, 32, 64),
    AccountVariant("balanced", 2, 32, 64),
    AccountVariant("higher_size", 4, 32, 64),
    AccountVariant("tight_bracket", 2, 20, 40),
    AccountVariant("wide_bracket", 2, 48, 96),
)


@dataclass(frozen=True)
class CombineRules:
    starting_balance: float = 50_000.0
    maximum_loss: float = 2_000.0
    profit_target: float = 3_000.0
    consistency_fraction: float = 0.55
    round_turn_fee: float = 1.22  # current TopstepX MES, per micro
    slippage_ticks: int = 1
    daily_loss_limit: float | None = None  # optional, not the default Combine rule

    def __post_init__(self) -> None:
        if self.starting_balance <= 0 or self.maximum_loss <= 0 or self.profit_target <= 0:
            raise ValueError("balance, loss limit, and profit target must be positive")
        if not 0 < self.consistency_fraction < 1:
            raise ValueError("consistency fraction must be between 0 and 1")
        if self.round_turn_fee < 0 or self.slippage_ticks < 0:
            raise ValueError("fees and slippage cannot be negative")


@dataclass
class Position:
    direction: int  # +1 long, -1 short
    quantity: int
    entry_price: float
    entry_ts: datetime
    stop_price: float
    target_price: float


@dataclass
class Account:
    variant: AccountVariant
    balance: float
    mll: float
    day_start_balance: float
    position: Position | None = None
    status: str = "active"
    day_pnl: dict[str, float] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    risk_events: list[dict[str, Any]] = field(default_factory=list)
    pending_day: date | None = None

    def effective_target(self, rules: CombineRules) -> float:
        best = max(self.day_pnl.values(), default=0.0)
        return max(rules.profit_target, best / rules.consistency_fraction)


class DecisionGate(Protocol):
    def evaluate(self, state: dict[str, Any]) -> GateResult: ...


class SignalTape:
    """Previously recorded n8n/Prodex decisions, keyed by closed bar open time.

    A record is {"bar_ts": ISO timestamp, "signal": "BUY|SELL|HOLD|FLAT"}.
    The replay consumes the record at bar close and fills no earlier than the
    next bar open. It never calls an n8n workflow during historical replay.
    """

    def __init__(self, rows: list[dict[str, Any]]):
        self.signals: dict[str, int] = {}
        mapping = {"BUY": 1, "SELL": -1, "FLAT": 2, "HOLD": 0}
        for row in rows:
            key = datetime.fromisoformat(str(row["bar_ts"]).replace("Z", "+00:00"))
            if key.tzinfo is None:
                raise ValueError("signal timestamps must have a timezone")
            key = key.astimezone(ZoneInfo("UTC")).isoformat()
            if key in self.signals:
                raise ValueError(f"duplicate signal at {key}")
            signal = str(row["signal"]).upper()
            if signal not in mapping:
                raise ValueError(f"unknown signal: {signal}")
            self.signals[key] = mapping[signal]

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "SignalTape":
        with open(path, encoding="utf-8") as stream:
            return cls([json.loads(line) for line in stream if line.strip()])

    def at(self, bar: Bar) -> int:
        return self.signals.get(bar.ts.astimezone(ZoneInfo("UTC")).isoformat(), 0)


def _candidate(bars: list[Bar], index: int, fast: int, slow: int) -> int:
    """Closed-bar SMA crossover; returns +1/-1/0, never reads a future bar."""
    if index < slow:
        return 0
    closes = [bar.close for bar in bars[index - slow:index + 1]]
    previous_fast = sum(closes[-fast - 1:-1]) / fast
    previous_slow = sum(closes[:-1]) / slow
    current_fast = sum(closes[-fast:]) / fast
    current_slow = sum(closes[-slow:]) / slow
    if previous_fast <= previous_slow and current_fast > current_slow:
        return 1
    if previous_fast >= previous_slow and current_fast < current_slow:
        return -1
    return 0


def _mark_pnl(position: Position, price: float) -> float:
    return (price - position.entry_price) * position.direction * POINT_VALUE * position.quantity


def _exit(account: Account, bar: Bar, price: float, reason: str, rules: CombineRules) -> None:
    position = account.position
    assert position is not None
    pnl = _mark_pnl(position, price)
    account.balance += pnl - rules.round_turn_fee * position.quantity / 2
    account.trades.append({
        "entry_ts": position.entry_ts.isoformat(), "exit_ts": bar.ts.isoformat(),
        "direction": "long" if position.direction == 1 else "short",
        "quantity": position.quantity, "entry_price": position.entry_price,
        "exit_price": price, "gross_pnl": round(pnl, 2),
        "net_pnl": round(pnl - rules.round_turn_fee * position.quantity, 2),
        "reason": reason,
    })
    account.position = None


def _finalize_day(account: Account, day: date, rules: CombineRules) -> None:
    pnl = account.balance - account.day_start_balance
    account.day_pnl[day.isoformat()] = round(pnl, 2)
    # Topstep's trailing MLL rises from end-of-day balance, then locks at start.
    account.mll = min(rules.starting_balance,
                      max(account.mll, account.balance - rules.maximum_loss))
    account.day_start_balance = account.balance
    net = account.balance - rules.starting_balance
    best = max(account.day_pnl.values(), default=0)
    if account.status == "active" and net >= rules.profit_target and best <= net * rules.consistency_fraction + 1e-8:
        account.status = "passed"


def _check_position(account: Account, bar: Bar, rules: CombineRules, bar_minutes: int) -> None:
    position = account.position
    if position is None:
        return
    direction = position.direction
    stop_hit = bar.low <= position.stop_price if direction == 1 else bar.high >= position.stop_price
    target_hit = bar.high >= position.target_price if direction == 1 else bar.low <= position.target_price
    if stop_hit:
        # Gap beyond stop is filled at the opening price; otherwise stop plus adverse slippage.
        price = (min(bar.open, position.stop_price) - rules.slippage_ticks * TICK_SIZE) if direction == 1 else (
            max(bar.open, position.stop_price) + rules.slippage_ticks * TICK_SIZE)
        _exit(account, bar, price, "stop_first_if_ambiguous" if target_hit else "stop", rules)
    elif target_hit:
        _exit(account, bar, position.target_price, "target", rules)
    else:
        adverse = bar.low if direction == 1 else bar.high
        if account.balance + _mark_pnl(position, adverse) <= account.mll:
            _exit(account, bar, adverse, "maximum_loss_intrabar", rules)
            account.status = "failed"
        elif rules.daily_loss_limit is not None and (
            account.balance + _mark_pnl(position, adverse) - account.day_start_balance <= -rules.daily_loss_limit
        ):
            _exit(account, bar, adverse, "daily_loss_limit", rules)
            account.status = "paused_for_day"
    local = bar.ts.astimezone(CT)
    bar_close = local + timedelta(minutes=bar_minutes)
    cutoff = datetime.combine(local.date(), time(15, 10), CT)
    if (account.position is not None and local.time() < time(15, 10)
            and bar_close <= cutoff < bar_close + timedelta(minutes=bar_minutes)):
        exit_price = bar.close - direction * rules.slippage_ticks * TICK_SIZE
        _exit(account, bar, exit_price, "session_flat", rules)
    if account.balance <= account.mll and account.status != "failed":
        account.status = "failed"
        account.risk_events.append({"ts": bar.ts.isoformat(), "reason": "maximum_loss", "balance": account.balance})


def simulate(
    bars: list[Bar], gate: DecisionGate, rules: CombineRules = CombineRules(),
    variants: tuple[AccountVariant, ...] = DEFAULT_VARIANTS,
    *, fast: int = 5, slow: int = 20, bar_minutes: int = 5,
    signal_tape: SignalTape | None = None,
) -> dict[str, Any]:
    bars = validate_bars(bars)
    if len(variants) != 5 or len({v.name for v in variants}) != 5:
        raise ValueError("exactly five named account variants are required")
    if not 1 < fast < slow or bar_minutes < 1:
        raise ValueError("invalid signal windows or bar interval")
    accounts = [Account(v, rules.starting_balance, rules.starting_balance - rules.maximum_loss,
                        rules.starting_balance) for v in variants]
    current_day = trade_day(bars[0].ts)
    pending: tuple[int, str] | None = None
    decisions: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        day = trade_day(bar.ts)
        if day != current_day:
            previous = bars[index - 1]
            for account in accounts:
                if account.position is not None:
                    _exit(account, previous, previous.close, "data_gap_session_flat", rules)
                    account.risk_events.append({"ts": previous.ts.isoformat(), "reason": "missing_flat_bar"})
                _finalize_day(account, current_day, rules)
                if account.status == "paused_for_day":
                    account.status = "active"
            current_day = day
            pending = None
        elif index > 0 and bar.ts - bars[index - 1].ts != timedelta(minutes=bar_minutes):
            previous = bars[index - 1]
            for account in accounts:
                if account.position is not None:
                    _exit(account, previous, previous.close, "data_gap_flat", rules)
                    account.risk_events.append({"ts": previous.ts.isoformat(),
                                                "reason": "missing_intraday_bars"})
            pending = None
        if pending and index > 0 and bar.ts - bars[index - 1].ts == timedelta(minutes=bar_minutes) \
                and entry_allowed(bar.ts, bar_minutes):
            direction, candidate_id = pending
            for account in accounts:
                if account.status != "active" or account.position is not None:
                    if account.status != "active":
                        continue
                    if account.position is not None and (direction == 2 or account.position.direction != direction):
                        price = bar.open - account.position.direction * rules.slippage_ticks * TICK_SIZE
                        _exit(account, bar, price, "signal_flat" if direction == 2 else "signal_reverse", rules)
                    if direction == 2 or account.position is not None:
                        continue
                if direction == 2:
                    continue
                v = account.variant
                entry_price = bar.open + direction * rules.slippage_ticks * TICK_SIZE
                stop = entry_price - direction * v.stop_ticks * TICK_SIZE
                target = entry_price + direction * v.target_ticks * TICK_SIZE
                fee = rules.round_turn_fee * v.contracts / 2
                if account.balance - fee <= account.mll:
                    account.risk_events.append({"ts": bar.ts.isoformat(), "reason": "entry_fee_reaches_mll"})
                    continue
                account.balance -= fee
                account.position = Position(direction, v.contracts, entry_price, bar.ts, stop, target)
            pending = None
        elif pending:
            # Missing bars and the 15:10 session cutoff invalidate an entry.
            pending = None
        for account in accounts:
            if account.status in {"active", "paused_for_day"}:
                _check_position(account, bar, rules, bar_minutes)
        if not entry_allowed(bar.ts, bar_minutes):
            continue
        direction = signal_tape.at(bar) if signal_tape is not None else _candidate(bars, index, fast, slow)
        if direction == 0:
            continue
        # The candidate is observed only after this bar closes. The earliest
        # possible fill is the next bar's open.
        candidate_id = f"MES-{bar_minutes}m-{bar.ts.isoformat()}-{direction:+d}"
        recent = bars[max(0, index - 11):index + 1]
        state = {
            "candidate_id": candidate_id, "symbol": "MES", "timeframe": f"{bar_minutes}m",
            "candidate_direction": "long" if direction == 1 else "short" if direction == -1 else "flat",
            "candidate_rule": "recorded signal tape" if signal_tape is not None else f"SMA({fast}) crossed SMA({slow}) on closed bar",
            "observed_through": (bar.ts + timedelta(minutes=bar_minutes)).isoformat(),
            "bars": [{"open": b.open, "high": b.high, "low": b.low,
                      "close": b.close, "volume": b.volume} for b in recent],
        }
        result = GateResult(True, "signal_tape", "flat signal") if direction == 2 else gate.evaluate(state)
        decisions.append({"candidate_id": candidate_id, "bar_ts": bar.ts.isoformat(),
                          "direction": state["candidate_direction"], "approved": result.approved,
                          "source": result.source, "reason": result.reason,
                          "response": result.response})
        if result.approved:
            pending = (direction, candidate_id)
    last = bars[-1]
    for account in accounts:
        if account.position is not None:
            price = last.close - account.position.direction * rules.slippage_ticks * TICK_SIZE
            _exit(account, last, price, "end_of_data", rules)
        _finalize_day(account, current_day, rules)
    summaries = []
    for account in accounts:
        net = round(account.balance - rules.starting_balance, 2)
        summaries.append({"name": account.variant.name, "variant": asdict(account.variant),
                          "status": account.status, "ending_balance": round(account.balance, 2),
                          "net_pnl": net, "mll": round(account.mll, 2),
                          "effective_profit_target": round(account.effective_target(rules), 2),
                          "best_day": max(account.day_pnl.values(), default=0),
                          "trade_count": len(account.trades), "daily_pnl": account.day_pnl,
                          "trades": account.trades, "risk_events": account.risk_events})
    return {"model": "offline_topstep_50k_proxy", "instrument": "MES", "bar_minutes": bar_minutes,
            "first_bar": bars[0].ts.isoformat(), "last_bar": last.ts.isoformat(),
            "bars": len(bars), "rules": asdict(rules),
            "signal": {"source": "recorded_tape" if signal_tape is not None else "sma_crossover",
                       "fast_sma": fast, "slow_sma": slow},
            "fill_policy": "next_bar_open; one-tick adverse slippage; stop before target in ambiguous bar; session flat at 15:10 CT",
            "decisions": decisions, "accounts": summaries,
            "combined_net_pnl": round(sum(row["net_pnl"] for row in summaries), 2)}
