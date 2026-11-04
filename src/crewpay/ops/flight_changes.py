"""Changes to flights themselves, made only when an approver approves them.

A controller's plan for a disruption can move flights to another aircraft (a free one of the
same type, or a spare), board them at another gate, or cancel them. Like a crew change, none
of it touches the operation when it is submitted: the plan carries the changes it would make,
the approver sees them, and they are made the moment the approver confirms. From then on the
flights show their new aircraft, gate or cancellation everywhere: the crew's own flights, the
station's operation and gates, and every check the engine makes (a cancelled flight is never
flown or used for positioning). The crew on each changed flight are told.

Holding flights to new times is not made here: a held flight's report and release belong to
its trip, and the desk retimes those as part of the plan's follow-up.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from crewpay.domain import Leg
from crewpay.engine.aircraft import taken_legs
from crewpay.engine.clock import IST
from crewpay.engine.operation import Operation
from crewpay.ops.notify import DESK, Notice, Outbox

KINDS = ("aircraft_technical", "gate_closure", "gate_change")
"""Reports whose resolution can change flights."""


class FlightChangeError(ValueError):
    """A change that cannot be made as asked."""


@dataclass(frozen=True, slots=True)
class FlightChange:
    leg_id: str
    flight: str
    day: str
    """``03 Nov``, for people reading it."""
    tail: str | None = None
    gate: str | None = None
    cancel: bool = False

    def describe(self) -> str:
        if self.cancel:
            return f"{self.flight} on {self.day} cancelled"
        if self.tail:
            return f"{self.flight} on {self.day} flies on {self.tail}"
        return f"{self.flight} on {self.day} boards at gate {self.gate}"


class Legs(Protocol):
    def operation(self, now: datetime | None = None) -> Operation: ...
    def put_records(self, kind: str, records: Any) -> None: ...


def _change(leg: Leg, **what: Any) -> FlightChange:
    return FlightChange(leg.leg_id, leg.flight_no, f"{leg.off_block.astimezone(IST):%d %b}", **what)


def for_resolution(
    op: Operation,
    kind: str,
    details: Mapping[str, Any],
    analysis: Mapping[str, Any],
    option: int | None = None,
    gates: Mapping[str, str] | None = None,
) -> list[FlightChange]:
    """The flight changes a resolution makes, worked out from the report and the choice the
    controller picked: an aircraft option by its place in the list, a gate for each moved
    flight. A plan that changes no flight (holding, or a note) makes none."""
    if kind == "aircraft_technical":
        options = analysis.get("options") or []
        if option is None:
            return []
        if not 0 <= option < len(options):
            raise FlightChangeError("choose one of the aircraft options listed")
        chosen = options[option]
        start = datetime.fromisoformat(str(details["start"]))
        back = datetime.fromisoformat(str(details["end"]))
        if start.tzinfo is None:
            start, back = start.replace(tzinfo=IST), back.replace(tzinfo=IST)
        legs = taken_legs(op, details["tail"], start, back)
        if chosen["way"] in ("swap", "spare"):
            return [_change(leg, tail=chosen["tail"]) for leg in legs]
        if chosen["way"] == "cancel":
            return [_change(leg, cancel=True) for leg in legs]
        return []
    if kind == "gate_closure":
        picked = gates or {}
        out = []
        for m in analysis.get("moves") or []:
            allowed = [m["to_gate"], *(m.get("other_free_gates") or [])]
            gate = picked.get(m["leg_id"], m["to_gate"])
            if gate not in allowed:
                raise FlightChangeError(
                    f"{m['flight']} can board at {', '.join(allowed)}, not {gate}"
                )
            if m["leg_id"] in op.legs:
                out.append(_change(op.legs[m["leg_id"]], gate=gate))
        return out
    if kind == "gate_change":
        leg = op.legs.get(str(details.get("leg_id") or analysis.get("leg_id") or ""))
        gate = details.get("gate") or analysis.get("wanted_gate")
        return [_change(leg, gate=str(gate))] if leg is not None and gate else []
    return []


def cancel_trip(op: Operation, trip_id: str) -> list[FlightChange]:
    """Every flight of a trip not yet gone: what cancelling it for want of crew takes away."""
    return [
        _change(op.legs[leg_id], cancel=True)
        for leg_id in op.trip(trip_id).leg_ids
        if op.legs[leg_id].off_block > op.now and leg_id not in op.cancelled
    ]


def apply(
    store: Legs,
    changes: list[FlightChange] | list[dict[str, Any]],
    outbox: Outbox,
    approved_by: str,
    ref: str,
    clock: Callable[[], datetime],
) -> list[str]:
    """Make approved changes to the flights and tell the crew on them. Returns what changed."""
    found = [c if isinstance(c, FlightChange) else FlightChange(**c) for c in changes]
    if not found:
        return []
    op = store.operation()
    updated = []
    for c in found:
        leg = op.legs.get(c.leg_id)
        if leg is None:
            raise FlightChangeError(f"no flight {c.leg_id}")
        update: dict[str, Any] = {}
        if c.cancel:
            update["cancelled"] = True
        if c.tail:
            update["tail"] = c.tail
        if c.gate:
            update["gate"] = c.gate
        updated.append(leg.model_copy(update=update))
    store.put_records("legs", updated)

    done = [c.describe() for c in found]
    at = clock()
    outbox.send(
        Notice(
            DESK,
            f"Flights changed: {ref}",
            f"Approved by {approved_by}: {'; '.join(done)}.",
            at,
            ref,
        )
    )
    crew_of: dict[str, list[str]] = {}
    for c in found:
        for trip in op.trips.values():
            if c.leg_id in trip.leg_ids:
                for seat in trip.crew:
                    crew_of.setdefault(seat.crew_id, []).append(c.describe())
    for crew_id, lines in sorted(crew_of.items()):
        outbox.send(Notice(crew_id, "Your flights have changed", "; ".join(lines) + ".", at, ref))
    return done


def as_dicts(changes: list[FlightChange]) -> list[dict[str, Any]]:
    return [asdict(c) for c in changes]
