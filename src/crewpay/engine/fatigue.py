"""Fatigue: legal is the floor, not the target.

The nine rules already stop the worst rosters: two clear days in every seven caps a working
block at four days, and rest of at least 12 hours (and at least the duty before it) stops
quick returns. What they allow can still wear someone out, and that is what is flagged here:

* ``early_starts``: four early starts in a row, the longest block the rules allow (caution);
* ``tight_rest``: rest that clears the minimum by less than an hour (caution);
* ``night_then_early``: an early report within 36 hours of a duty through the small hours,
  flipping the body clock from night to morning (high);
* ``repeated_nights``: a third duty through the small hours within seven days (caution), a
  fourth or more (high).

"The small hours" is 02:00 to 05:00 local, when alertness is lowest. It is narrower than the
rules' night window on purpose: an early start reporting at 05:00 is an early start, not a
night duty.

Flags never block a choice; that is the rules' job. They help a controller prefer someone
fresher, and the high ones are raised by the watch. All of it is arithmetic on the roster in
local (IST) time, with no model involved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from crewpay.engine.clock import IST, hours_between, spoken_hours
from crewpay.engine.operation import Operation
from crewpay.engine.rules import required_rest

EARLY_BEFORE = time(7, 0)
"""A report before 07:00 local is an early start."""
EARLY_STREAK = 4
REST_CUSHION_HOURS = 1.0
LOW_START, LOW_END = time(2, 0), time(5, 0)
NIGHT_TO_EARLY_HOURS = 36
NIGHTS_IN_A_WEEK = 3


class Severity(StrEnum):
    CAUTION = "caution"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class Flag:
    kind: str
    severity: Severity
    day: date
    detail: str
    days: tuple[date, ...]
    """Every day the pattern covers."""


@dataclass(frozen=True, slots=True)
class _Duty:
    trip_id: str
    day: date
    report: datetime
    release: datetime

    @property
    def early(self) -> bool:
        return self.report.astimezone(IST).time() < EARLY_BEFORE

    @property
    def small_hours(self) -> bool:
        local = self.report.astimezone(IST).date() - timedelta(days=1)
        while local <= self.release.astimezone(IST).date():
            start = datetime.combine(local, LOW_START, IST)
            end = datetime.combine(local, LOW_END, IST)
            if self.report < end and self.release > start:
                return True
            local += timedelta(days=1)
        return False


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _runs(days: list[date]) -> list[list[date]]:
    runs: list[list[date]] = []
    for day in sorted(set(days)):
        if runs and day - runs[-1][-1] == timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    return runs


def fatigue_flags(op: Operation, crew_id: str, trip_id: str | None = None) -> list[Flag]:
    """Fatigue patterns in ``crew_id``'s roster, with ``trip_id`` added if given.

    With a trip, only patterns that touch the trip's days are returned: what taking it would
    do to them. Without one, every pattern from today on.
    """
    crew = op.state(crew_id)
    duties = [_Duty(d.trip_id, d.day, d.report, d.release) for d in crew.rostered(trip_id)]
    trip_days: set[date] = set()
    if trip_id is not None:
        for d in op.days_of(trip_id):
            duties.append(_Duty(trip_id, d.day, d.report, d.release))
            trip_days.add(d.day)
    duties.sort(key=lambda d: d.report)
    flags: list[Flag] = []

    for run in _runs([d.day for d in duties if d.early]):
        if len(run) >= EARLY_STREAK:
            flags.append(
                Flag(
                    "early_starts",
                    Severity.CAUTION,
                    run[-1],
                    f"{_ordinal(len(run))} early start in a row (report before 07:00 IST)",
                    tuple(run),
                )
            )

    nights: list[_Duty] = []
    for before, after in zip(duties, duties[1:], strict=False):
        if before.day == after.day:
            continue
        rest = hours_between(before.release, after.report)
        need = required_rest(op.book, hours_between(before.report, before.release))
        if need <= rest < need + REST_CUSHION_HOURS:
            flags.append(
                Flag(
                    "tight_rest",
                    Severity.CAUTION,
                    after.day,
                    f"rest {spoken_hours(rest)} before {after.day:%d %b}, "
                    f"{spoken_hours(rest - need)} above the {spoken_hours(need)} minimum",
                    (before.day, after.day),
                )
            )
        if before.small_hours and after.early and rest < NIGHT_TO_EARLY_HOURS:
            flags.append(
                Flag(
                    "night_then_early",
                    Severity.HIGH,
                    after.day,
                    f"early report on {after.day:%d %b}, {spoken_hours(rest)} after a duty "
                    "through the small hours",
                    (before.day, after.day),
                )
            )

    worst_nights: dict[date, Flag] = {}
    for duty in duties:
        if not duty.small_hours:
            continue
        nights = [n for n in nights if (duty.day - n.day).days < 7]
        nights.append(duty)
        if len(nights) >= NIGHTS_IN_A_WEEK:
            # One flag per block of nights, at its worst point.
            worst_nights[nights[0].day] = Flag(
                "repeated_nights",
                Severity.HIGH if len(nights) > NIGHTS_IN_A_WEEK else Severity.CAUTION,
                duty.day,
                f"{_ordinal(len(nights))} duty through the small hours (02:00-05:00 IST) in 7 days",
                tuple(n.day for n in nights),
            )
    flags.extend(worst_nights.values())

    if trip_id is not None:
        return [f for f in flags if trip_days & set(f.days)]
    today = op.now.date()
    return [f for f in flags if f.day >= today]
