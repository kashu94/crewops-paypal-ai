"""Is this crew member legal for this trip, and if not, why not?

``assess`` is the one place the rest of the system gets a legality answer from. Before the
rules run, it applies what changes when the duty actually happens:

* Positioning. Someone based elsewhere flies in first, so day one moves until they can report.
  Positioning is duty: their day starts when they report for the positioning flight, so the
  flight counts toward the duty period and the rest before it. Later days keep their times,
  so a late first release eats into the night-stop, and the rest between the trip's own days
  is checked again.
* Reporting late. Someone called from home who cannot reach the airport by report time moves
  day one the same way.
* A what-if delay. A late departure stretches one duty day; that is how a delay pushes
  someone past a limit.

It also applies what the roster alone does not show: a standby whose window does not cover the
report time is not available, crew only fly for their own airline, and anyone reported
unavailable for a date is out on that date.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from crewpay.engine.clock import DutyDay, hours_between
from crewpay.engine.operation import Operation, Positioning
from crewpay.engine.rules import (
    BASE,
    RATING,
    REST,
    Finding,
    blockers,
    evaluate,
    failed,
    required_rest,
)

UNAVAILABLE = "reported unavailable"
"""How an unavailability finding starts, so cover can tell it from a rule failure."""


@dataclass(frozen=True, slots=True)
class Assessment:
    crew_id: str
    trip_id: str
    findings: tuple[Finding, ...]
    days: tuple[DutyDay, ...]
    """The duty days as judged, after positioning and any delay."""
    positioning: Positioning | None
    on_standby: bool
    """On standby that day with a window covering the report time."""
    delay_hours: float = 0.0
    slip: timedelta = timedelta(0)
    """How far day one moved: positioning, or reporting late, whichever is later."""

    @property
    def legal(self) -> bool:
        return not any(f.failed for f in self.findings)

    @property
    def blockers(self) -> list[Finding]:
        return blockers(self.findings)

    @property
    def slip_hours(self) -> float:
        return round(self.slip.total_seconds() / 3600, 2)


def _rest_within_trip(days: list[DutyDay], op: Operation) -> list[Finding]:
    """Rest between consecutive days of the same trip. The published roster keeps it, so only
    a slip or a delay can break it."""
    out = []
    for prev, nxt in zip(days, days[1:], strict=False):
        rest = hours_between(prev.release, nxt.report)
        need = required_rest(op.book, prev.fdp_hours)
        if rest < need:
            out.append(
                failed(
                    REST,
                    f"only {rest}h rest between {prev.day:%d %b} and {nxt.day:%d %b} of the trip, "
                    f"{need:g}h needed",
                    day=nxt.day,
                    used=rest,
                    limit=need,
                )
            )
    return out


def assess(
    op: Operation,
    crew_id: str,
    trip_id: str,
    delay_hours: float = 0.0,
    delay_day: date | None = None,
    late_by: timedelta = timedelta(0),
) -> Assessment:
    """All nine rules for one crew member on every day of one trip.

    ``late_by`` is how long after the scheduled report they can be there; the first departure
    waits for them.
    """
    if delay_hours < 0:
        raise ValueError("a delay cannot be negative")
    crew = op.state(crew_id)
    trip = op.trip(trip_id)
    days = list(op.days_of(trip_id))

    own = [d for d in crew.duties if d.trip_id == trip_id]
    # For crew already on this trip, a rest that ends after one of its days is rest earned on
    # the trip itself, not rest still owed before it.
    if crew.rest_until and any(d.release <= crew.rest_until for d in own):
        crew = replace(crew, rest_until=None)

    positioning = None
    if crew.base != days[0].origin and not own:
        positioning = op.positioning(crew, trip_id)
    slip = max(positioning.slip if positioning else timedelta(0), late_by)
    if slip:
        days[0] = days[0].shifted(slip)
    if positioning is not None:
        first_leg = op.legs[trip.days[0].legs[0]]
        lead = first_leg.off_block - trip.days[0].report
        days[0] = replace(days[0], report=min(days[0].report, positioning.departs - lead))

    if delay_hours:
        index = 0
        if delay_day is not None:
            index = next((i for i, d in enumerate(days) if d.day == delay_day), -1)
            if index < 0:
                raise ValueError(f"{trip_id} has no duty on {delay_day}")
        days[index] = days[index].delayed(delay_hours)

    findings = evaluate(
        crew, days, op.book, exclude_trip=trip_id, positioned=positioning is not None or bool(own)
    )
    findings += _rest_within_trip(days, op)

    if crew.airline != trip.airline:
        theirs = op.airline_names.get(crew.airline, crew.airline)
        flies = op.airline_names.get(trip.airline, trip.airline)
        findings.append(
            failed(RATING, f"employed by {theirs}; {trip_id} is a {flies} trip", day=days[0].day)
        )

    for when, report_id in op.unavailable.get(crew_id, ()):
        since = op.unavailable_since.get(report_id)
        # Taken ill part-way through a day: duties that end before then are still flown.
        if any(d.day == when and (since is None or d.release > since) for d in days):
            findings.append(failed(RATING, f"{UNAVAILABLE} ({report_id})", day=when))
            break

    rostered = [s for s in op.standby.get(crew_id, ()) if days[0].day in s.dates]
    on_standby = any(s.covers(days[0].report) for s in rostered)
    if rostered and not on_standby and not own:
        s = rostered[0]
        findings.append(
            failed(
                BASE,
                f"standby window {s.window_start:%H:%M}-{s.window_end:%H:%M}Z does not cover "
                f"the {days[0].report:%H:%M}Z report",
                day=days[0].day,
            )
        )

    return Assessment(
        crew_id=crew_id,
        trip_id=trip_id,
        findings=tuple(findings),
        days=tuple(days),
        positioning=positioning,
        on_standby=on_standby,
        delay_hours=delay_hours,
        slip=slip,
    )
