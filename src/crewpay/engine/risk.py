"""How likely a crew member is to drop out of the roster in the coming days: a risk score.

Legal is a yes or no; risk is a matter of degree. Someone legal for a trip can still be
close to their weekly duty limit, on a run of early starts, two days from a lapsed medical,
or an hour's drive from the airport for a callout. Each of those adds points, and the score
says which ones did, so a controller can see why someone scores high.

The score is shown beside cover choices and on the crew record. It never ranks cover and
never rules anyone out: the rules engine alone decides who is legal, and cost alone orders
the legal choices.

==================================  ======  ===============================================
Driver                              Points  When
==================================  ======  ===============================================
fatigue on the coming roster        25/12   a high / a caution fatigue pattern ahead
duty hours in the last 7 days       20/10   at 90% / 75% of the limit or more
block hours in the last 28 days     15/8    at 90% / 75% of the limit or more
reported unavailable                15      each report in the operation, up to two
certificate running out             15/8    within 14 / 30 days
long way from the airport           10      60 minutes or more, for a short-notice callout
==================================  ======  ===============================================

High is 50 points or more, medium 25 or more.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from crewpay.domain import Unavailability
from crewpay.engine.clock import window
from crewpay.engine.fatigue import Severity as FatigueSeverity
from crewpay.engine.fatigue import fatigue_flags
from crewpay.engine.operation import Operation

HIGH = 50
MEDIUM = 25
FAR_FROM_AIRPORT = 60


class RiskLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class Driver:
    factor: str
    points: int
    detail: str


@dataclass(frozen=True, slots=True)
class Risk:
    crew_id: str
    score: int
    drivers: tuple[Driver, ...]

    @property
    def level(self) -> RiskLevel:
        if self.score >= HIGH:
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM if self.score >= MEDIUM else RiskLevel.LOW


def _share(used: float, limit: float) -> float:
    return used / limit if limit else 0.0


def crew_risk(op: Operation, crew_id: str, reports: Iterable[Unavailability] = ()) -> Risk:
    crew = op.state(crew_id)
    today = op.now.date()
    book = op.book
    drivers: list[Driver] = []

    flags = [f for f in fatigue_flags(op, crew_id) if f.day >= today]
    if flags:
        worst = max(flags, key=lambda f: f.severity is FatigueSeverity.HIGH)
        high = worst.severity is FatigueSeverity.HIGH
        drivers.append(Driver("fatigue", 25 if high else 12, worst.detail))

    duty = crew.duty_between(*window(today, book.duty_days))
    share = _share(duty, book.duty_hours)
    if share >= 0.75:
        drivers.append(
            Driver(
                "duty hours",
                20 if share >= 0.9 else 10,
                f"{duty:.1f} of {book.duty_hours:g} duty hours in the last {book.duty_days} days",
            )
        )

    block = crew.block_between(*window(today, book.block_days))
    share = _share(block, book.block_hours)
    if share >= 0.75:
        drivers.append(
            Driver(
                "block hours",
                15 if share >= 0.9 else 8,
                f"{block:.1f} of {book.block_hours:g} block hours in the last "
                f"{book.block_days} days",
            )
        )

    mine = [u for u in reports if u.crew_id == crew_id]
    if mine:
        drivers.append(
            Driver(
                "reported unavailable",
                15 * min(len(mine), 2),
                f"reported unavailable {len(mine)} time{'s' if len(mine) > 1 else ''} "
                f"({', '.join(sorted({u.kind for u in mine}))})",
            )
        )

    soonest = (
        min(((expires - today).days, str(kind)) for kind, expires in crew.certs)
        if crew.certs
        else None
    )
    if soonest is not None and 0 <= soonest[0] <= 30:
        drivers.append(
            Driver(
                "certificate",
                15 if soonest[0] <= 14 else 8,
                f"{soonest[1]} runs out in {soonest[0]} days",
            )
        )

    minutes = op.minutes_to_airport.get(crew_id)
    if minutes is not None and minutes >= FAR_FROM_AIRPORT:
        drivers.append(Driver("distance", 10, f"{minutes} minutes from the airport for a callout"))

    drivers.sort(key=lambda d: -d.points)
    return Risk(crew_id, min(100, sum(d.points for d in drivers)), tuple(drivers))


def risk_json(risk: Risk) -> dict:
    return {
        "score": risk.score,
        "level": str(risk.level),
        "drivers": [
            {"factor": d.factor, "points": d.points, "detail": d.detail} for d in risk.drivers
        ],
    }
