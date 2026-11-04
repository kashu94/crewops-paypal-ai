"""Disruptions that hold flights back: a station that closes, an aircraft out of service.

Both come down to departures leaving later than planned. Each trip is held by its first
affected leg, and that delay is judged against every rostered crew member's duty limits by
``delay_impact``, the same check a reported delay gets. Nothing is cancelled here: the result
says who would break a limit and what the delay costs, and the desk decides.

* **Station closure** (weather, a runway inspection): nothing departs the station inside the
  window, and nothing lands there, so a flight due to arrive inside it leaves its origin late
  enough to land when the station reopens.
* **Aircraft out of service** (a technical fault): every flight the tail was to fly before it
  is back leaves when it is back. Later flights that day slip by the same amount, less no
  slack: the rotation is tight.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from crewpay.engine.clock import IST
from crewpay.engine.impact import DelayImpact, delay_impact
from crewpay.engine.operation import NotFound, Operation


@dataclass(frozen=True, slots=True)
class HeldFlight:
    leg_id: str
    flight_no: str
    route: str
    planned: datetime
    leaves: datetime
    booked: int

    @property
    def minutes(self) -> int:
        return int((self.leaves - self.planned).total_seconds() // 60)


@dataclass(frozen=True, slots=True)
class Disruption:
    what: str
    flights: tuple[HeldFlight, ...]
    trips: tuple[DelayImpact, ...]
    """Each affected trip day, with the delay its first held flight causes."""

    @property
    def passengers(self) -> int:
        return sum(f.booked for f in self.flights)

    @property
    def crew_over_limits(self) -> int:
        return sum(t.crew_affected for t in self.trips)


def _trip_and_day(op: Operation, leg_id: str) -> tuple[str, date] | None:
    for trip in op.trips.values():
        for day in trip.days:
            if leg_id in day.legs:
                return trip.trip_id, day.day
    return None


def _judge(op: Operation, what: str, held: list[HeldFlight]) -> Disruption:
    held.sort(key=lambda f: (f.planned, f.leg_id))
    worst: dict[tuple[str, date], float] = defaultdict(float)
    for f in held:
        found = _trip_and_day(op, f.leg_id)
        if found and not op.started(found[0]):
            worst[found] = max(worst[found], round(f.minutes / 60, 2))
    trips = tuple(
        delay_impact(op, trip_id, hours, day)
        for (trip_id, day), hours in sorted(worst.items())
        if hours > 0
    )
    return Disruption(what, tuple(held), trips)


def station_closure(op: Operation, station: str, start: datetime, end: datetime) -> Disruption:
    if end <= start:
        raise ValueError("the closure must end after it starts")
    if not any(leg.origin == station or leg.destination == station for leg in op.legs.values()):
        raise NotFound("station", station)
    held = []
    for leg in op.legs.values():
        if leg.leg_id in op.cancelled:
            continue
        if leg.origin == station and start <= leg.off_block < end:
            leaves = end
        elif leg.destination == station and start <= leg.on_block < end:
            leaves = leg.off_block + (end - leg.on_block)
        else:
            continue
        held.append(
            HeldFlight(
                leg.leg_id,
                leg.flight_no,
                f"{leg.origin}-{leg.destination}",
                leg.off_block,
                leaves,
                leg.booked,
            )
        )
    what = f"{station} closed {start.astimezone(IST):%d %b %H:%M}-{end.astimezone(IST):%H:%M} IST"
    return _judge(op, what, held)


def aircraft_out(op: Operation, tail: str, start: datetime, back: datetime) -> Disruption:
    if back <= start:
        raise ValueError("the aircraft must be back after it goes out of service")
    legs = sorted(
        (leg for leg in op.legs.values() if leg.tail == tail and leg.leg_id not in op.cancelled),
        key=lambda leg: leg.off_block,
    )
    if not legs:
        raise NotFound("aircraft", tail)
    held = []
    slip = None
    for leg in legs:
        if leg.off_block < start:
            continue
        if slip is None:
            if leg.off_block >= back:
                break
            slip = back - leg.off_block
        if slip.total_seconds() <= 0:
            break
        if held and leg.day != held[0].planned.date():
            break  # the overnight stop absorbs the slip
        held.append(
            HeldFlight(
                leg.leg_id,
                leg.flight_no,
                f"{leg.origin}-{leg.destination}",
                leg.off_block,
                leg.off_block + slip,
                leg.booked,
            )
        )
    what = f"{tail} out of service until {back.astimezone(IST):%d %b %H:%M} IST"
    return _judge(op, what, held)
