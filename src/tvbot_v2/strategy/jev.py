"""Bounded TypeSafe Jev decision gate for replay research.

Jev judges a candidate produced by deterministic code. It never sizes a
position, changes an account rule, or places an order.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


JEV_URL = "https://api.typesafe.ai/v1/systemone"


@dataclass(frozen=True)
class GateResult:
    approved: bool
    source: str
    reason: str
    response: dict[str, Any] | None = None


class BaselineGate:
    """Reference strategy: take every eligible deterministic candidate."""

    def evaluate(self, state: dict[str, Any]) -> GateResult:
        return GateResult(True, "baseline", "candidate accepted")


class FixtureGate:
    """Replay previously recorded Jev answers; missing answers block entries."""

    def __init__(self, answers: dict[str, dict[str, Any]]):
        self.answers = answers

    @classmethod
    def from_jsonl(cls, path: str) -> "FixtureGate":
        answers: dict[str, dict[str, Any]] = {}
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    row = json.loads(line)
                    key = row["candidate_id"]
                    if key in answers:
                        raise ValueError(f"duplicate Jev candidate: {key}")
                    answers[key] = row["response"]
        return cls(answers)

    def evaluate(self, state: dict[str, Any]) -> GateResult:
        response = self.answers.get(state["candidate_id"])
        if response is None:
            return GateResult(False, "fixture", "no recorded Jev answer")
        return parse_jev_answer(response, "fixture")


def parse_jev_answer(response: dict[str, Any], source: str = "jev") -> GateResult:
    """Fail closed on malformed or uncertain model output."""
    try:
        answer = response["answers"]["entry_quality"]
        choice = answer["choice"]
        probabilities = answer["probabilities"]
        probability = float(probabilities["approve"])
        confidence = float(answer["confidence"])
        if choice not in {"approve", "reject"}:
            raise ValueError("unknown choice")
        if not (0 <= probability <= 1 and 0 <= confidence <= 1):
            raise ValueError("invalid probability or confidence")
        approved = choice == "approve" and probability >= 0.65 and confidence >= 0.5
        reason = f"{choice}; approve_probability={probability:.3f}; confidence={confidence:.3f}"
        return GateResult(approved, source, reason, response)
    except (KeyError, TypeError, ValueError) as exc:
        return GateResult(False, source, f"invalid Jev response: {type(exc).__name__}", response)


class JevGate:
    def __init__(self, api_key: str | None = None, timeout: float = 10.0):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY")
        if not self.api_key:
            raise ValueError("TYPESAFE_API_KEY is required for --gate jev")
        self.timeout = timeout

    def evaluate(self, state: dict[str, Any]) -> GateResult:
        payload = {
            "model": "jev-latest",
            "state": state,
            "questions": {
                "entry_quality": {
                    "type": "choice",
                    "instructions": "Given only the supplied closed-bar market state and candidate direction, classify whether this candidate has a clear short-horizon directional setup. Do not assume future prices. Reject weak or ambiguous setups.",
                    "criteria": {
                        "approve": "A clear directional setup supports the candidate.",
                        "reject": "The setup is weak, contradictory, or unclear.",
                    },
                },
            },
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            JEV_URL,
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as result:
                response = json.load(result)
            return parse_jev_answer(response)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            # No fallback to an unreviewed trade when Jev is down.
            return GateResult(False, "jev", f"Jev unavailable: {type(exc).__name__}")
