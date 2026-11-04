"""Checking an answer against the evidence, with no model involved.

Every crew id, trip id, flight number, clock time, hour figure, count and rupee amount in the
answer must be found in what the tools returned, in the question itself, or in the rule
book's own limits. Each is checked in the forms an answer might use: ``20840``, ``20,840``
and ``₹20,840`` are one amount; ``1.75h``, ``1h45m`` and ``1 hour 45 minutes`` are one
duration.

The check is deliberately literal. It cannot judge whether a sentence is wise, only that its
facts were not invented, which is the failure that matters most when a controller acts on it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from crewpay.agent.advisor import Step

IDS = re.compile(r"\b[CT]-\d{4}\b")
FLIGHT = re.compile(r"\b[A-Z]{2}\d{3,4}\b")
CLOCK = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d\b")
MONEY = re.compile(
    r"(?:₹|\bINR\s?|\bRs\.?\s?)(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:INR|rupees)\b",
    re.IGNORECASE,
)
HOURS_MINUTES = re.compile(
    r"\b(\d{1,3})\s?(?:h|hours?|hrs?)\s?(\d{1,2})\s?(?:m|min|mins|minutes)\b", re.IGNORECASE
)
DECIMAL_HOURS = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s?(?:h|hours?|hrs?)\b(?!\s?\d)", re.IGNORECASE)
COUNT = re.compile(
    r"\b(\d[\d,]*)\s+(passengers?|legs?|sectors?|trips?|crew|seats?|landings?|days?)\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _leaves(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _leaves(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _leaves(v)
    else:
        yield value


class Evidence:
    """Everything the tools returned, as text and as numbers."""

    def __init__(self, question: str, steps: Sequence[Step], reference: str = "") -> None:
        texts = [question, reference]
        numbers: set[float] = set()
        for step in steps:
            for leaf in _leaves([step.arguments, step.result]):
                if isinstance(leaf, bool) or leaf is None:
                    continue
                if isinstance(leaf, (int, float)):
                    numbers.add(round(float(leaf), 2))
                texts.append(str(leaf))
        self.text = "\n".join(texts)
        for match in NUMBER.findall(self.text):
            numbers.add(round(_number(match), 2))
        for h, m in HOURS_MINUTES.findall(self.text):
            numbers.add(round(int(h) + int(m) / 60, 2))
        self.numbers = numbers

    def has_text(self, value: str) -> bool:
        return value in self.text

    def has_number(self, value: float, tolerance: float = 0.01) -> bool:
        return any(abs(value - n) <= tolerance for n in self.numbers)


class EvidenceVerifier:
    def __init__(self, reference: str = "") -> None:
        self.reference = reference
        """Text every answer may quote, such as the rule book's limits."""

    def problems(self, answer: str, question: str, steps: Sequence[Step]) -> list[str]:
        evidence = Evidence(question, steps, self.reference)
        found: list[str] = []

        def miss(kind: str, shown: str) -> None:
            problem = f"{kind} {shown} is not in any tool result"
            if problem not in found:
                found.append(problem)

        for value in IDS.findall(answer):
            if not evidence.has_text(value):
                miss("id", value)
        for value in FLIGHT.findall(answer):
            if not evidence.has_text(value):
                miss("flight", value)
        for match in CLOCK.finditer(answer):
            if not evidence.has_text(match.group(0).zfill(5)):
                miss("time", match.group(0))
        for a, b in MONEY.findall(answer):
            if not evidence.has_number(_number(a or b)):
                miss("amount", a or b)

        durations = HOURS_MINUTES.findall(answer)
        for h, m in durations:
            # 1h45m: within half a minute of a figure the tools gave
            if not evidence.has_number(int(h) + int(m) / 60, tolerance=0.009):
                miss("duration", f"{h}h{int(m):02d}m")
        spans = [m.span() for m in HOURS_MINUTES.finditer(answer)]
        for match in DECIMAL_HOURS.finditer(answer):
            if any(start <= match.start() < end for start, end in spans):
                continue
            if not evidence.has_number(_number(match.group(1))):
                miss("hours", match.group(0))
        for number, noun in COUNT.findall(answer):
            if not evidence.has_number(_number(number)):
                miss("count", f"{number} {noun}")
        return found
