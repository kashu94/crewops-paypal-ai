"""How urgent a problem is: high, medium or low, always with the reasons.

A controller works the most urgent problem first. Urgency here comes from three things the
engine already knows, never from a model's impression:

* **time**: how soon the affected trip reports. A seat that must be filled in the next few
  hours leaves fewer people who can get to the airport;
* **reach**: how many passengers are booked on the trip's legs;
* **room**: whether any crew member can still fill the seat, or only cancelling is left.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from crewpay.engine.operation import Operation
from crewpay.engine.watch import Issue

SOON = timedelta(hours=24)
"""A trip reporting within this is urgent."""
VERY_SOON = timedelta(hours=6)
MANY_PASSENGERS = 300
"""Passengers booked across a trip's legs above which a problem reaches many people."""
CERTIFICATE_DAYS_HIGH = 3


class Level(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return {Level.HIGH: 0, Level.MEDIUM: 1, Level.LOW: 2}[self]


@dataclass(frozen=True, slots=True)
class Severity:
    level: Level
    reasons: tuple[str, ...]


def _hours(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours >= 48:
        return f"{hours / 24:.0f} days"
    return f"{hours:.0f} hours" if hours >= 1.5 else f"{max(1, round(hours * 60))} minutes"


def trip_reach(op: Operation, trip_id: str) -> tuple[timedelta, int]:
    """How long until ``trip_id`` reports, and the passengers booked on its legs."""
    until = op.days_of(trip_id)[0].report - op.now
    booked = sum(op.legs[leg].booked for leg in op.trip(trip_id).leg_ids)
    return until, booked


def for_trip(op: Operation, trip_id: str, *, no_crew_left: bool = False) -> Severity:
    """An empty or illegal seat on ``trip_id``."""
    until, booked = trip_reach(op, trip_id)
    reasons = [f"reports in {_hours(until)}", f"{booked} passengers booked"]
    if no_crew_left:
        reasons.insert(0, "no legal crew left: only cancelling or flying short")
        return Severity(Level.HIGH, tuple(reasons))
    if until <= VERY_SOON or (until <= SOON and booked >= MANY_PASSENGERS):
        return Severity(Level.HIGH, tuple(reasons))
    if until <= SOON or booked >= MANY_PASSENGERS:
        return Severity(Level.MEDIUM, tuple(reasons))
    return Severity(Level.LOW, tuple(reasons))


def for_issue(op: Operation, issue: Issue) -> Severity:
    if issue.kind == "illegal" and issue.trip_id:
        cancel_only = bool(issue.suggestion) and issue.suggestion.startswith("Cancel")
        s = for_trip(op, issue.trip_id, no_crew_left=cancel_only)
        # Someone rostered illegally is never a low priority: the trip cannot fly as it is.
        level = Level.MEDIUM if s.level is Level.LOW else s.level
        return Severity(level, ("rostered crew would break a rule", *s.reasons))
    if issue.kind == "fatigue":
        days = (issue.day - op.now.date()).days
        level = Level.MEDIUM if days <= 2 else Level.LOW
        return Severity(level, (f"high fatigue {days} days from now", "legal, but a risk"))
    days = (issue.day - op.now.date()).days
    level = Level.MEDIUM if days <= CERTIFICATE_DAYS_HIGH else Level.LOW
    return Severity(level, (f"certificate runs out in {days} days",))
