"""Gates and stands: where each departure boards, and what happens when it cannot.

A departure holds its gate from boarding (``BOARDING`` before off-block) until it leaves. A
flight loses its gate in three ways:

* the gate **closes** (a jet bridge fault, a security sweep): every departure holding it
  during the closure moves;
* the flight before it at the gate **leaves late** and is still there when boarding should
  start;
* the desk **changes** its gate to one another flight already holds.

A displaced departure goes to the first ordinary gate at the station that is free for its
whole boarding window. If none is, it takes the station's emergency gate; if that is taken
too, it boards from a remote stand by bus, which takes ``BUS_EXTRA`` longer, and that delay is
judged against crew duty like any other. Gates are tried in a fixed order and each move is
booked before the next is placed, so the same disruption always gives the same plan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from crewpay.domain import GateKind, Leg
from crewpay.engine.clock import IST
from crewpay.engine.impact import DelayImpact, delay_impact
from crewpay.engine.operation import NotFound, Operation

BOARDING = timedelta(minutes=45)
"""A departure holds its gate from this long before off-block."""
BUS_EXTRA = timedelta(minutes=15)
"""Boarding from a remote stand by bus takes this much longer."""


@dataclass(frozen=True, slots=True)
class Slot:
    leg: Leg
    gate: str
    start: datetime
    end: datetime

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return self.start < end and start < self.end


@dataclass(frozen=True, slots=True)
class Move:
    leg_id: str
    flight_no: str
    trip_id: str | None
    from_gate: str
    to_gate: str
    to_kind: GateKind
    boarding_from: datetime
    departs: datetime
    booked: int
    delay_minutes: int
    why: str
    alternatives: tuple[str, ...] = ()
    """Other ordinary gates free for the whole window, for the desk to choose instead."""


@dataclass(frozen=True, slots=True)
class GatePlan:
    station: str
    what: str
    moves: tuple[Move, ...]
    crew: tuple[DelayImpact, ...] = field(default=())
    """Duty impact of every move that delays a departure."""

    @property
    def passengers(self) -> int:
        return sum(m.booked for m in self.moves)


def gates_at(op: Operation, station: str) -> dict[str, GateKind]:
    found = {g.gate: g.kind for g in op.gates.get(station, ())}
    if not found:
        raise NotFound("station with gates", station)
    return found


def slot_of(leg: Leg, late: timedelta = timedelta(0)) -> Slot:
    if leg.gate is None:
        raise NotFound("gate for", leg.leg_id)
    return Slot(leg, leg.gate, leg.off_block - BOARDING, leg.off_block + late)


def departures(op: Operation, station: str, start: datetime, end: datetime) -> list[Slot]:
    """Every departure at ``station`` holding a gate between ``start`` and ``end``."""
    found = [
        slot_of(leg)
        for leg in op.legs.values()
        if leg.origin == station and leg.gate and leg.leg_id not in op.cancelled
    ]
    return sorted(
        (s for s in found if s.overlaps(start, end)), key=lambda s: (s.start, s.leg.leg_id)
    )


def trip_of(op: Operation, leg_id: str) -> str | None:
    return next((t.trip_id for t in op.trips.values() if leg_id in t.leg_ids), None)


class _Apron:
    """Who holds which gate when, as moves are booked one by one."""

    def __init__(self, op: Operation, station: str, start: datetime, end: datetime) -> None:
        self.kinds = gates_at(op, station)
        self.held: list[Slot] = departures(op, station, start - BOARDING, end + BOARDING)

    def free(self, gate: str, start: datetime, end: datetime, ignore: str) -> bool:
        return not any(
            s.gate == gate and s.leg.leg_id != ignore and s.overlaps(start, end) for s in self.held
        )

    def free_gates(
        self, kind: GateKind, start: datetime, end: datetime, ignore: str, closed: Iterable[str]
    ) -> list[str]:
        shut = set(closed)
        return [
            g
            for g, k in self.kinds.items()
            if k is kind and g not in shut and self.free(g, start, end, ignore)
        ]

    def place(self, slot: Slot, why: str, closed: Iterable[str]) -> Move:
        leg = slot.leg
        closed = tuple(closed)
        ordinary = self.free_gates(GateKind.CONTACT, slot.start, slot.end, leg.leg_id, closed)
        delay = timedelta(0)
        if ordinary:
            gate, kind = ordinary[0], GateKind.CONTACT
        elif spare := self.free_gates(GateKind.EMERGENCY, slot.start, slot.end, leg.leg_id, closed):
            gate, kind = spare[0], GateKind.EMERGENCY
        else:
            delay = BUS_EXTRA
            stands = self.free_gates(
                GateKind.REMOTE, slot.start, slot.end + delay, leg.leg_id, closed
            )
            gate = stands[0] if stands else "remote apron"
            kind = GateKind.REMOTE
        self.held = [s for s in self.held if s.leg.leg_id != leg.leg_id]
        self.held.append(Slot(leg, gate, slot.start, slot.end + delay))
        return Move(
            leg_id=leg.leg_id,
            flight_no=leg.flight_no,
            trip_id=None,
            from_gate=slot.gate,
            to_gate=gate,
            to_kind=kind,
            boarding_from=slot.start,
            departs=leg.off_block + delay,
            booked=leg.booked,
            delay_minutes=int(delay.total_seconds() // 60),
            why=why,
            alternatives=tuple(ordinary[1:4]),
        )


def _plan(op: Operation, station: str, what: str, moves: list[Move]) -> GatePlan:
    moves = [replace(m, trip_id=trip_of(op, m.leg_id)) for m in moves]
    crew = []
    for m in moves:
        if m.delay_minutes and m.trip_id:
            day = op.legs[m.leg_id].day
            crew.append(delay_impact(op, m.trip_id, round(m.delay_minutes / 60, 2), day))
    return GatePlan(station, what, tuple(moves), tuple(crew))


def gate_closure(
    op: Operation, station: str, gates: str | Iterable[str], start: datetime, end: datetime
) -> GatePlan:
    """Move every departure that would hold one of ``gates`` while they are closed."""
    shut = (gates,) if isinstance(gates, str) else tuple(gates)
    apron = _Apron(op, station, start, end)
    for gate in shut:
        if gate not in apron.kinds:
            raise NotFound(f"gate at {station}", gate)
    hit = sorted(
        (s for s in apron.held if s.gate in shut and s.overlaps(start, end)),
        key=lambda s: (s.start, s.leg.leg_id),
    )
    names = ", ".join(shut)
    why = (
        f"gate{'s' if len(shut) > 1 else ''} {names} closed "
        f"{start.astimezone(IST):%d %b %H:%M}-{end.astimezone(IST):%H:%M} IST"
    )
    moves = [apron.place(s, why, closed=shut) for s in hit]
    return _plan(op, station, why, moves)


def late_departure(op: Operation, leg_id: str, minutes: int) -> GatePlan:
    """A flight that leaves ``minutes`` late keeps its gate; whoever boards there next moves."""
    try:
        leg = op.legs[leg_id]
    except KeyError:
        raise NotFound("flight", leg_id) from None
    late = slot_of(leg, timedelta(minutes=minutes))
    apron = _Apron(op, leg.origin, late.start, late.end)
    apron.held = [s for s in apron.held if s.leg.leg_id != leg_id] + [late]
    why = f"{leg.flight_no} still at gate {late.gate} until {late.end.astimezone(IST):%H:%M} IST"
    nxt = [
        s
        for s in apron.held
        if s.gate == late.gate and s.leg.leg_id != leg_id and s.overlaps(late.start, late.end)
    ]
    moves = [apron.place(s, why, closed=()) for s in sorted(nxt, key=lambda s: s.start)]
    return _plan(op, leg.origin, why, moves)


@dataclass(frozen=True, slots=True)
class GateCheck:
    leg_id: str
    flight_no: str
    station: str
    current: str | None
    wanted: str
    free: bool
    clashes: tuple[str, ...]
    """Flights holding ``wanted`` during this departure's boarding window."""
    alternatives: tuple[str, ...]


def check_gate(op: Operation, leg_id: str, wanted: str) -> GateCheck:
    """Can ``leg_id`` board at ``wanted``? If not, who holds it and which gates are free."""
    try:
        leg = op.legs[leg_id]
    except KeyError:
        raise NotFound("flight", leg_id) from None
    slot = slot_of(leg)
    apron = _Apron(op, leg.origin, slot.start, slot.end)
    if wanted not in apron.kinds:
        raise NotFound(f"gate at {leg.origin}", wanted)
    clashes = tuple(
        s.leg.flight_no
        for s in apron.held
        if s.gate == wanted and s.leg.leg_id != leg_id and s.overlaps(slot.start, slot.end)
    )
    free = apron.free_gates(GateKind.CONTACT, slot.start, slot.end, leg_id, closed=())
    return GateCheck(
        leg_id=leg_id,
        flight_no=leg.flight_no,
        station=leg.origin,
        current=leg.gate,
        wanted=wanted,
        free=not clashes,
        clashes=clashes,
        alternatives=tuple(g for g in free if g not in (wanted, leg.gate))[:4],
    )
