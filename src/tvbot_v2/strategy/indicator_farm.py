"""Pure, closed-bar MES indicator candidates for offline screening.

Rules here are research definitions, not TradingView/Market Cipher clones.
Every emitted event is timestamped at the bar where all inputs are known.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from tvbot_v2.replay.topstep import Bar


@dataclass(frozen=True)
class Candidate:
    index: int
    strategy: str
    direction: int
    regime: str
    volatility: str


def sma(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= length:
            total -= values[i - length]
        if i >= length - 1:
            out[i] = total / length
    return out


def ema(values: list[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < length:
        return out
    current = sum(values[:length]) / length
    out[length - 1] = current
    alpha = 2 / (length + 1)
    for i in range(length, len(values)):
        current += alpha * (values[i] - current)
        out[i] = current
    return out


def rsi(closes: list[float], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= length:
        return out
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain = sum(max(0.0, x) for x in changes[:length]) / length
    loss = sum(max(0.0, -x) for x in changes[:length]) / length

    def value() -> float:
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        return 100 - 100 / (1 + gain / loss)

    out[length] = value()
    for i in range(length + 1, len(closes)):
        change = changes[i - 1]
        gain = (gain * (length - 1) + max(0.0, change)) / length
        loss = (loss * (length - 1) + max(0.0, -change)) / length
        out[i] = value()
    return out


def atr(bars: list[Bar], length: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) < length:
        return out
    tr = [bar.high - bar.low if i == 0 else max(
        bar.high - bar.low, abs(bar.high - bars[i - 1].close),
        abs(bar.low - bars[i - 1].close)) for i, bar in enumerate(bars)]
    current = sum(tr[:length]) / length
    out[length - 1] = current
    for i in range(length, len(bars)):
        current = (current * (length - 1) + tr[i]) / length
        out[i] = current
    return out


def cci(bars: list[Bar], length: int) -> list[float | None]:
    typical = [(bar.high + bar.low + bar.close) / 3 for bar in bars]
    means = sma(typical, length)
    out: list[float | None] = [None] * len(bars)
    for i in range(length - 1, len(bars)):
        mean = means[i]
        assert mean is not None
        deviation = sum(abs(value - mean) for value in typical[i - length + 1:i + 1]) / length
        out[i] = (typical[i] - mean) / (0.015 * deviation) if deviation else 0.0
    return out


def _cross_up(previous_a: float | None, previous_b: float | None,
              current_a: float | None, current_b: float | None) -> bool:
    return None not in (previous_a, previous_b, current_a, current_b) and (
        previous_a <= previous_b and current_a > current_b)


def _cross_down(previous_a: float | None, previous_b: float | None,
                current_a: float | None, current_b: float | None) -> bool:
    return None not in (previous_a, previous_b, current_a, current_b) and (
        previous_a >= previous_b and current_a < current_b)


def _pivot(bars: list[Bar], index: int, low: bool, width: int = 3) -> bool:
    if index < width or index + width >= len(bars):
        return False
    values = [bar.low if low else bar.high for bar in bars[index - width:index + width + 1]]
    center = values[width]
    return all(center < value for j, value in enumerate(values) if j != width) if low else (
        all(center > value for j, value in enumerate(values) if j != width))


def generate_candidates(bars: list[Bar], *, cooldown_bars: int = 6) -> list[Candidate]:
    """Generate signals using only bars through each returned candidate index."""
    closes = [bar.close for bar in bars]
    fast_ema, trend_ema, slow_ema = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    fast_sma, slow_sma = sma(closes, 9), sma(closes, 21)
    rsi14, atr14 = rsi(closes), atr(bars)
    cci6, cci14 = cci(bars, 6), cci(bars, 14)
    macd_fast, macd_slow = ema(closes, 12), ema(closes, 26)
    macd = [a - b if a is not None and b is not None else None
            for a, b in zip(macd_fast, macd_slow)]
    signal: list[float | None] = [None] * len(bars)
    first_macd = next((i for i, value in enumerate(macd) if value is not None), len(bars))
    for i, value in enumerate(ema([float(x) for x in macd[first_macd:]], 9), first_macd):
        signal[i] = value
    lower: list[float | None] = [None] * len(bars)
    upper: list[float | None] = [None] * len(bars)
    for i in range(19, len(bars)):
        window = closes[i - 19:i + 1]
        mean = sum(window) / 20
        deviation = (sum((x - mean) ** 2 for x in window) / 20) ** 0.5
        lower[i], upper[i] = mean - 2 * deviation, mean + 2 * deviation

    events: list[Candidate] = []
    last_emitted: dict[str, int] = {}
    previous_low: tuple[int, float, float] | None = None
    previous_high: tuple[int, float, float] | None = None

    for i in range(100, len(bars)):
        if None in (fast_ema[i], trend_ema[i], slow_ema[i], atr14[i]):
            continue
        trend = "up" if trend_ema[i] > slow_ema[i] and closes[i] > slow_ema[i] else (
            "down" if trend_ema[i] < slow_ema[i] and closes[i] < slow_ema[i] else "chop")
        trailing_atr = [value for value in atr14[i - 99:i + 1] if value is not None]
        ratio = atr14[i] / median(trailing_atr) if trailing_atr and median(trailing_atr) > 0 else 1.0
        volatility = "high" if ratio > 1.3 else "low" if ratio < 0.8 else "normal"

        def emit(name: str, direction: int) -> None:
            if i - last_emitted.get(name, -cooldown_bars - 1) <= cooldown_bars:
                return
            events.append(Candidate(i, name, direction, trend, volatility))
            last_emitted[name] = i

        if _cross_up(macd[i - 1], signal[i - 1], macd[i], signal[i]):
            emit("macd_cross", 1)
        if _cross_down(macd[i - 1], signal[i - 1], macd[i], signal[i]):
            emit("macd_cross", -1)
        if rsi14[i - 1] is not None and rsi14[i] is not None:
            if rsi14[i - 1] <= 30 < rsi14[i]:
                emit("rsi_reentry", 1)
            if rsi14[i - 1] >= 70 > rsi14[i]:
                emit("rsi_reentry", -1)
        if lower[i - 1] is not None and lower[i] is not None:
            if closes[i - 1] < lower[i - 1] and closes[i] >= lower[i]:
                emit("bollinger_reentry", 1)
            if closes[i - 1] > upper[i - 1] and closes[i] <= upper[i]:
                emit("bollinger_reentry", -1)
        if _cross_up(fast_sma[i - 1], slow_sma[i - 1], fast_sma[i], slow_sma[i]):
            emit("sma_9_21_cross", 1)
        if _cross_down(fast_sma[i - 1], slow_sma[i - 1], fast_sma[i], slow_sma[i]):
            emit("sma_9_21_cross", -1)
        if _cross_up(fast_ema[i - 1], trend_ema[i - 1], fast_ema[i], trend_ema[i]):
            emit("ema_9_21_cross", 1)
        if _cross_down(fast_ema[i - 1], trend_ema[i - 1], fast_ema[i], trend_ema[i]):
            emit("ema_9_21_cross", -1)
        if trend == "up" and fast_ema[i] > trend_ema[i] and bars[i].low <= fast_ema[i] < closes[i] \
                and bars[i - 1].low > fast_ema[i - 1]:
            emit("ema_9_touch", 1)
        if trend == "down" and fast_ema[i] < trend_ema[i] and bars[i].high >= fast_ema[i] > closes[i] \
                and bars[i - 1].high < fast_ema[i - 1]:
            emit("ema_9_touch", -1)
        prior_high = max(bar.high for bar in bars[i - 20:i])
        prior_low = min(bar.low for bar in bars[i - 20:i])
        if closes[i] > prior_high and closes[i - 1] <= max(bar.high for bar in bars[i - 21:i - 1]):
            emit("momentum_20_breakout", 1)
        if closes[i] < prior_low and closes[i - 1] >= min(bar.low for bar in bars[i - 21:i - 1]):
            emit("momentum_20_breakout", -1)
        if cci6[i - 1] is not None and cci14[i] is not None:
            if trend == "up" and cci14[i] > 0 and cci6[i - 1] <= 0 < cci6[i]:
                emit("cci_6_14_pullback_proxy", 1)
            if trend == "down" and cci14[i] < 0 and cci6[i - 1] >= 0 > cci6[i]:
                emit("cci_6_14_pullback_proxy", -1)

        # A pivot at i-3 is first knowable now; never timestamp it at i-3.
        pivot_index = i - 3
        oscillator = rsi14[pivot_index]
        if oscillator is None:
            continue
        if _pivot(bars, pivot_index, low=True):
            current = (pivot_index, bars[pivot_index].low, oscillator)
            if previous_low and pivot_index - previous_low[0] <= 60:
                if current[1] < previous_low[1] and current[2] > previous_low[2]:
                    emit("rsi_regular_bullish_divergence", 1)
                if current[1] > previous_low[1] and current[2] < previous_low[2]:
                    emit("rsi_hidden_bullish_divergence", 1)
            previous_low = current
        if _pivot(bars, pivot_index, low=False):
            current = (pivot_index, bars[pivot_index].high, oscillator)
            if previous_high and pivot_index - previous_high[0] <= 60:
                if current[1] > previous_high[1] and current[2] < previous_high[2]:
                    emit("rsi_regular_bearish_divergence", -1)
                if current[1] < previous_high[1] and current[2] > previous_high[2]:
                    emit("rsi_hidden_bearish_divergence", -1)
            previous_high = current
    return events
