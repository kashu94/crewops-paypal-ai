"""The operation in memory, shaped for the rules.

Built once from a ``Dataset`` and then read-only. A regional operation is a few hundred crew
and a few hundred legs a week, so holding it in memory is far cheaper than a database read per
question; the store rebuilds it when the roster changes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from crewpay.domain import Gate, Leg, Rank, Rates, Standby, Trip
from crewpay.domain.dataset import Dataset
from crewpay.engine.clock import DutyDay
from crewpay.engine.rules import CrewState, Duty, RuleBook

REPORT_AFTER_POSITIONING = timedelta(minutes=20)
"""How long after a positioning flight lands its passenger can report for duty."""


class NotFound(LookupError):
    """An id that does not exist. Callers offer near matches; the engine never guesses."""

    def __init__(self, kind: str, value: str) -> None:
        super().__init__(f"no {kind} {value!r}")
        self.kind = kind
        self.value = value


@dataclass(frozen=True, slots=True)
class Positioning:
    """The flight that brings a crew member from their base to the trip's first departure."""

    leg_id: str
    origin: str
    destination: str
    lands: datetime
    slip: timedelta
    """How far day one must move so they can report after landing."""
    departs: datetime
    """When the positioning flight leaves: their duty starts before this, not at the trip's
    own report time."""

    @property
    def slip_hours(self) -> float:
        return round(self.slip.total_seconds() / 3600, 2)


@dataclass(frozen=True, slots=True)
class Operation:
    crew: Mapping[str, CrewState]
    trips: Mapping[str, Trip]
    duty_days: Mapping[str, tuple[DutyDay, ...]]
    legs: Mapping[str, Leg]
    standby: Mapping[str, tuple[Standby, ...]]
    rates: Rates
    book: RuleBook
    now: datetime
    minutes_to_airport: Mapping[str, int]
    years_of_service: Mapping[str, int]
    airline_names: Mapping[str, str]
    command_qualified: frozenset[str] = frozenset()
    right_seat_qualified: frozenset[str] = frozenset()
    unavailable: Mapping[str, tuple[tuple[date, str], ...]] = field(default_factory=dict)
    """Crew id -> (date, report id) from open disruptions; empty on the published roster."""
    unavailable_since: Mapping[str, datetime] = field(default_factory=dict)
    """Report id -> the moment someone fell ill part-way through its first day."""
    gates: Mapping[str, tuple[Gate, ...]] = field(default_factory=dict)
    """Station -> its gates and stands, in the order they are offered."""
    cancelled: frozenset[str] = frozenset()
    """Legs cancelled by an approved plan. Nobody flies or positions on them."""

    def state(self, crew_id: str) -> CrewState:
        try:
            return self.crew[crew_id]
        except KeyError:
            raise NotFound("crew member", crew_id) from None

    def trip(self, trip_id: str) -> Trip:
        try:
            return self.trips[trip_id]
        except KeyError:
            raise NotFound("trip", trip_id) from None

    def days_of(self, trip_id: str) -> tuple[DutyDay, ...]:
        self.trip(trip_id)
        return self.duty_days[trip_id]

    def started(self, trip_id: str) -> bool:
        return self.days_of(trip_id)[0].report <= self.now

    def crew_of(self, airline: str, rank: Rank | None = None) -> list[CrewState]:
        return [
            c
            for c in self.crew.values()
            if c.airline == airline and (rank is None or c.rank is rank)
        ]

    def positioning(self, crew: CrewState, trip_id: str) -> Positioning | None:
        """The same-day flight from their base, not yet departed, that delays the trip least.

        None means they cannot get there that day, which fails the base rule. Any airline's
        flight with a free seat will do (positioning crew travel as passengers), except the
        trip's own legs.
        """
        day = self.days_of(trip_id)[0]
        own = set(self.trip(trip_id).leg_ids)
        best: Positioning | None = None
        for leg in self.legs.values():
            if (
                leg.leg_id in own
                or leg.day != day.day
                or leg.origin != crew.base
                or leg.destination != day.origin
                or leg.off_block < self.now
                or leg.leg_id in self.cancelled
                or leg.booked >= leg.seats
            ):
                continue
            slip = max(timedelta(0), leg.on_block + REPORT_AFTER_POSITIONING - day.report)
            if best is None or (slip, leg.on_block) < (best.slip, best.lands):
                best = Positioning(
                    leg.leg_id, leg.origin, leg.destination, leg.on_block, slip, leg.off_block
                )
        return best


def duty_days_of(trip: Trip, legs: Mapping[str, Leg]) -> tuple[DutyDay, ...]:
    days = []
    for d in trip.days:
        flown = [legs[leg_id] for leg_id in d.legs]
        days.append(
            DutyDay(
                day=d.day,
                report=d.report,
                release=d.release,
                sectors=len(flown),
                block_hours=round(sum(leg.block_hours for leg in flown), 2),
                aircraft_type=flown[0].aircraft_type,
                origin=flown[0].origin,
                landings=tuple(leg.on_block for leg in flown),
            )
        )
    return tuple(days)


def build_operation(data: Dataset, now: datetime | None = None) -> Operation:
    """The operation as the engine sees it at ``now``.

    ``now`` is the wall clock when deployed. Tests leave it out and get the dataset's own
    moment, and it is never earlier than that, since the histories already count those hours.
    """
    legs = {leg.leg_id: leg for leg in data.legs}
    duty_days: dict[str, tuple[DutyDay, ...]] = {}
    duties: dict[str, list[Duty]] = defaultdict(list)
    for trip in data.trips:
        days = duty_days_of(trip, legs)
        duty_days[trip.trip_id] = days
        for seat in trip.crew:
            duties[seat.crew_id] += [
                Duty(trip.trip_id, d.day, d.report, d.release, d.block_hours) for d in days
            ]

    history = {h.crew_id: h for h in data.histories}
    certs: dict[str, list] = defaultdict(list)
    for c in data.certificates:
        certs[c.crew_id].append((c.kind, c.expires))

    crew: dict[str, CrewState] = {}
    for c in data.crew:
        h = history.get(c.crew_id)
        crew[c.crew_id] = CrewState(
            crew_id=c.crew_id,
            name=c.name,
            rank=c.rank,
            airline=c.airline,
            base=c.base,
            ratings=c.ratings,
            status=c.status,
            rest_until=h.rest_until if h else None,
            history_duty={d.day: d.duty_hours for d in h.days} if h else {},
            history_block={d.day: d.block_hours for d in h.days} if h else {},
            certs=tuple(certs.get(c.crew_id, ())),
            duties=tuple(sorted(duties.get(c.crew_id, ()), key=lambda d: d.report)),
        )

    standby: dict[str, list[Standby]] = defaultdict(list)
    for s in data.standby:
        standby[s.crew_id].append(s)

    return Operation(
        crew=crew,
        trips={t.trip_id: t for t in data.trips},
        duty_days=duty_days,
        legs=legs,
        standby={k: tuple(v) for k, v in standby.items()},
        rates=data.rates,
        book=RuleBook.from_rules(data.rules),
        now=max(data.as_of, now) if now else data.as_of,
        minutes_to_airport={c.crew_id: c.minutes_to_airport for c in data.crew},
        years_of_service={c.crew_id: c.years_of_service for c in data.crew},
        airline_names={a.code: a.name for a in data.airlines},
        command_qualified=frozenset(c.crew_id for c in data.crew if c.command_qualified),
        gates={
            s: tuple(g for g in data.gates if g.station == s)
            for s in {g.station for g in data.gates}
        },
        right_seat_qualified=frozenset(c.crew_id for c in data.crew if c.right_seat_qualified),
        cancelled=frozenset(leg.leg_id for leg in data.legs if leg.cancelled),
    )
