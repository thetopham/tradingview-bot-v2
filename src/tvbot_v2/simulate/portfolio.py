"""Versioned settings for independent, simulated MES Combine accounts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class Bracket:
    contracts: int
    stop_ticks: int
    target_ticks: int

    def __post_init__(self) -> None:
        if not 1 <= self.contracts <= 50 or self.stop_ticks < 1 or self.target_ticks < 1:
            raise ValueError("invalid MES bracket")


@dataclass(frozen=True)
class SimVariant:
    name: str
    timeframe: str
    strategy_id: str
    brackets: dict[int, Bracket]

    def __post_init__(self) -> None:
        if (not self.name or not self.strategy_id or
                not re.fullmatch(r"[1-9][0-9]*m", self.timeframe) or
                int(self.timeframe[:-1]) > 1440):
            raise ValueError("invalid account variant")
        if not self.brackets or not set(self.brackets).issubset({1, 2, 3}):
            raise ValueError("bracket choices must use size codes 1, 2, or 3")

    @property
    def minutes(self) -> int:
        return int(self.timeframe[:-1])

    def bracket(self, size: int) -> Bracket:
        try:
            return self.brackets[size]
        except KeyError as exc:
            raise ValueError(f"size {size} is not enabled for {self.name}") from exc

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SimVariant":
        if not isinstance(data, dict):
            raise ValueError("account variant must be an object")
        brackets = {int(code): Bracket(**details) for code, details in data["brackets"].items()}
        return cls(str(data["name"]), str(data["timeframe"]), str(data["strategy_id"]), brackets)

    @classmethod
    def from_json(cls, value: str) -> "SimVariant":
        return cls.from_dict(json.loads(value))


BASE_BRACKETS = {
    1: Bracket(1, 24, 48),  # MES: gross -$30 / +$60
    2: Bracket(2, 12, 24),
    3: Bracket(3, 8, 16),
}

# Provisional experiment settings, not a reconstruction of old account terms.
DEFAULT_PORTFOLIO = (
    SimVariant("alpha", "5m", "alpha_5m_conservative", {1: BASE_BRACKETS[1]}),
    SimVariant("beta", "5m", "beta_5m_balanced", {1: BASE_BRACKETS[1], 2: BASE_BRACKETS[2]}),
    SimVariant("gamma", "5m", "gamma_5m_full_brackets", BASE_BRACKETS.copy()),
    SimVariant("delta", "15m", "delta_15m_wide", {1: Bracket(1, 36, 72), 2: Bracket(2, 18, 36)}),
    SimVariant("epsilon", "30m", "epsilon_30m_full_brackets", BASE_BRACKETS.copy()),
)


def load_portfolio(path: str | Path) -> tuple[SimVariant, ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, list):
        raise ValueError("portfolio config must be a JSON array")
    variants = tuple(SimVariant.from_dict(item) for item in document)
    validate_portfolio(variants)
    return variants


def validate_portfolio(variants: tuple[SimVariant, ...]) -> None:
    if not variants or len({variant.name for variant in variants}) != len(variants):
        raise ValueError("portfolio must contain uniquely named accounts")
