"""The watch: problems in the coming days that nobody has reported yet.

Run on a schedule, it re-judges everyone rostered on every trip that has not started in the
next few days, and reports:

* ``illegal``: someone who would break a rule on a trip they are rostered on, such as a
  medical that lapses before the trip ends. Each comes with the cheapest legal cover.
* ``fatigue``: a high-severity fatigue pattern on a legal roster.
* ``certificate``: a certificate that runs out soon, before it makes anyone illegal.

The same issue keeps the same id on every run, so a scheduler can alert once and stay quiet
until it is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from crewpay.engine.cover import TripStarted, find_cover
from crewpay.engine.fatigue import Severity, fatigue_flags
from crewpay.engine.legality import assess
from crewpay.engine.operation import Operation

HORIZON_DAYS = 7
CERTIFICATE_WARNING_DAYS = 14
ORDER = {"illegal": 0, "fatigue": 1, "certificate": 2}


@dataclass(frozen=True, slots=True)
class Issue:
    issue_id: str
    kind: str
    crew_id: str
    name: str
    day: date
    detail: str
    trip_id: str | None = None
    rule_ids: tuple[str, ...] = ()
    suggestion: str | None = None
    suggestion_cost: int | None = None


def scan(
    op: Operation,
    horizon_days: int = HORIZON_DAYS,
    certificate_days: int = CERTIFICATE_WARNING_DAYS,
) -> list[Issue]:
    today = op.now.date()
    last = today + timedelta(days=horizon_days)
    issues: list[Issue] = []
    illegal_crew: set[str] = set()
    watched: set[str] = set()

    upcoming = sorted(
        (t for t in op.trips.values() if not op.started(t.trip_id)),
        key=lambda t: (op.days_of(t.trip_id)[0].report, t.trip_id),
    )
    for trip in upcoming:
        if op.days_of(trip.trip_id)[0].day > last:
            continue
        for seat in trip.crew:
            crew = op.state(seat.crew_id)
            result = assess(op, seat.crew_id, trip.trip_id)
            if not result.legal:
                illegal_crew.add(seat.crew_id)
                blockers = result.blockers
                suggestion = cost = None
                try:
                    best = find_cover(op, trip.trip_id, seat.rank, seat.crew_id).best
                    suggestion, cost = best.summary, best.cost
                except TripStarted:
                    pass
                issues.append(
                    Issue(
                        issue_id=f"illegal:{seat.crew_id}:{trip.trip_id}",
                        kind="illegal",
                        crew_id=seat.crew_id,
                        name=crew.name,
                        day=blockers[0].day or op.days_of(trip.trip_id)[0].day,
                        detail="; ".join(f"{b.rule_id} {b.detail}" for b in blockers),
                        trip_id=trip.trip_id,
                        rule_ids=tuple(dict.fromkeys(b.rule_id for b in blockers)),
                        suggestion=suggestion,
                        suggestion_cost=cost,
                    )
                )
                continue
            watched.add(seat.crew_id)

    # Fatigue is a pattern across a person's roster, so it is reported once per person and
    # pattern, against the trip on the day it peaks.
    for crew_id in sorted(watched):
        for flag in fatigue_flags(op, crew_id):
            if flag.severity is not Severity.HIGH or flag.day > last:
                continue
            on_day = next(
                (d.trip_id for d in op.state(crew_id).rostered() if d.day == flag.day), None
            )
            issues.append(
                Issue(
                    issue_id=f"fatigue:{crew_id}:{flag.kind}:{flag.days[0].isoformat()}",
                    kind="fatigue",
                    crew_id=crew_id,
                    name=op.state(crew_id).name,
                    day=flag.day,
                    detail=flag.detail,
                    trip_id=on_day,
                )
            )

    for crew in op.crew.values():
        if crew.crew_id in illegal_crew:
            continue
        for kind, expires in crew.certs:
            if today <= expires <= today + timedelta(days=certificate_days):
                issues.append(
                    Issue(
                        issue_id=f"certificate:{crew.crew_id}:{kind}",
                        kind="certificate",
                        crew_id=crew.crew_id,
                        name=crew.name,
                        day=expires,
                        detail=f"{kind.value.replace('_', ' ')} expires {expires:%d %b %Y}, "
                        f"{(expires - today).days} "
                        "days from now",
                    )
                )

    unique = {i.issue_id: i for i in issues}
    return sorted(unique.values(), key=lambda i: (ORDER[i.kind], i.day, i.issue_id))
