"""Normalize TradingView datafeed rows without importing execution code."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        if number > 1e12:
            number /= 1000
        ts = datetime.fromtimestamp(number, tz=timezone.utc)
    elif isinstance(value, str):
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError("naive timestamp")
    else:
        raise ValueError("missing timestamp")
    return ts.astimezone(timezone.utc).isoformat()


def normalize_bar(row: dict[str, Any], source_table: str) -> dict[str, Any]:
    """Return canonical fields; reject ambiguous rows instead of guessing."""
    candidate = row
    for key in ("data", "payload", "body", "bar", "ohlc"):
        nested = _object(candidate.get(key))
        if nested and (_first(nested, "open", "o") is not None):
            candidate = {**row, **nested}
            break
    timeframe = str(_first(candidate, "timeframe", "tf") or source_table.rsplit("_", 1)[-1])
    if timeframe.isdigit():
        timeframe += "m"
    if source_table == "tv_datafeed" and timeframe != "1m":
        raise ValueError("tv_datafeed contains 1m MES bars only")
    if source_table.startswith("tv_datafeed_") and timeframe != source_table.removeprefix("tv_datafeed_"):
        raise ValueError("source table and row timeframe disagree")
    symbol = str(_first(candidate, "symbol", "ticker", "tickerid", "contract") or "").upper()
    if "MES" in symbol:
        symbol = "MES"
    if symbol != "MES":
        raise ValueError("only MES is supported by the first simulator")
    ts = _timestamp(_first(candidate, "timestamp", "bar_time", "time", "ts", "t"))
    if (source_table == "tv_datafeed" or source_table.startswith("tv_datafeed_")) and "ts" in candidate and not any(
        key in candidate for key in ("timestamp", "bar_time", "time")
    ):
        # v1's ts is the n8n receipt time just after the bar CLOSE. Snap only
        # if it is within two minutes of that close; a late receipt is unsafe
        # to assign to a bar without a TradingView bar timestamp.
        if not timeframe.endswith("m") or not timeframe[:-1].isdigit():
            raise ValueError("unsupported source timeframe")
        minutes = int(timeframe[:-1])
        received = datetime.fromisoformat(ts)
        close_minute = (received.minute // minutes) * minutes
        close_time = received.replace(minute=close_minute, second=0, microsecond=0)
        if not 0 <= (received - close_time).total_seconds() <= 120:
            raise ValueError("feed receipt too late to infer bar close")
        if source_table == "tv_datafeed" and (received - close_time).total_seconds() > 50:
            raise ValueError("1m feed receipt too late to infer bar close")
        ts = (close_time - timedelta(minutes=minutes)).isoformat()
    values = [float(_first(candidate, full, short)) for full, short in
              (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))]
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError("invalid OHLC values")
    op, high, low, close = values
    if low > min(op, close) or high < max(op, close):
        raise ValueError("invalid OHLC range")
    volume = _first(candidate, "volume", "v")
    if volume is not None and (not math.isfinite(float(volume)) or float(volume) < 0):
        raise ValueError("invalid volume")
    return {"symbol": symbol, "timeframe": timeframe, "ts": ts,
            "open": op, "high": high, "low": low, "close": close,
            "volume": float(volume) if volume is not None else None,
            "indicators_json": json.dumps({key: value for key, value in candidate.items()
                                           if key not in {"open", "high", "low", "close", "o", "h", "l", "c"}},
                                          default=str, sort_keys=True)}
