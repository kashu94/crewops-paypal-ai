"""What the desk can do when an aircraft goes out of service.

Holding every flight until the aircraft is back only makes sense for a short outage. Before
that, the operation looks for another aircraft of the same type that is free for the whole run
of flights it would take over (out and back, so it ends where it started), then for one of the
spare aircraft kept at the hub, and only then holds or cancels. Crew stay with their flights:
a swap to the same type keeps every duty, rating and time the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from crewpay.domain import AircraftType, Leg
from crewpay.engine.clock import IST
from crewpay.engine.operation import NotFound, Operation

TURN = timedelta(minutes=45)
"""Time an aircraft needs on the ground between flights."""
HOLD_AT_MOST = timedelta(hours=6)
"""Longer than this, holding the flights is not offered: passengers would wait all day."""
SPARE_COST = 250_000
"""Calling up a spare aircraft and its ground crew, in rupees."""


@dataclass(frozen=True, slots=True)
class Spare:
    tail: str
    aircraft_type: AircraftType
    station: str


SPARES = (
    Spare("VT-SPA", AircraftType.A320, "HYD"),
    Spare("VT-SPB", AircraftType.ATR72, "HYD"),
)
"""Aircraft kept on the ground at the hub for exactly this, one of each type flown."""


@dataclass(frozen=True, slots=True)
class AircraftOption:
    way: str
    """``swap``, ``spare``, ``hold`` or ``cancel``."""
    summary: str
    cost: int
    tail: str | None = None
    note: str = ""


def taken_legs(op: Operation, tail: str, start: datetime, back: datetime) -> list[Leg]:
    """The flights an outage takes away on its first day, and on to the aircraft's home
    station (out and back), in order. A longer outage brings the same choice again each day;
    the next day's flights are not folded into this one."""
    own = sorted(
        (
            leg
            for leg in op.legs.values()
            if leg.tail == tail and leg.leg_id not in op.cancelled and start <= leg.off_block < back
        ),
        key=lambda leg: leg.off_block,
    )
    if not any(leg.tail == tail for leg in op.legs.values()):
        raise NotFound("aircraft", tail)
    if not own:
        return []
    first_day = own[0].day
    own = [leg for leg in own if leg.day == first_day]
    station = own[0].origin
    # Out and back: whoever takes these flights flies on until the aircraft is home again.
    later = sorted(
        (
            leg
            for leg in op.legs.values()
            if leg.tail == tail
            and leg.leg_id not in op.cancelled
            and leg.off_block > own[-1].off_block
        ),
        key=lambda leg: leg.off_block,
    )
    for leg in later:
        if own[-1].destination == station:
            break
        own.append(leg)
    return own


def aircraft_options(
    op: Operation,
    tail: str,
    start: datetime,
    back: datetime,
    hold_cost: int = 0,
    crew_over: int = 0,
) -> tuple[list[str], tuple[AircraftOption, ...]]:
    """The flights the outage takes away on its first day, and the ways to fly them, best
    first. A longer outage brings the same choice again each day; the next day's flights are
    not folded into this one."""
    own = taken_legs(op, tail, start, back)
    if not own:
        return [], ()
    first_day = own[0].day
    more_days = any(
        leg.tail == tail and leg.leg_id not in op.cancelled and leg.day != first_day
        for leg in op.legs.values()
        if start <= leg.off_block < back
    )
    kind = own[0].aircraft_type
    station = own[0].origin
    window = (own[0].off_block - TURN, own[-1].on_block + TURN)
    flights = list(dict.fromkeys(leg.flight_no for leg in own))
    named = f"{', '.join(flights)} on {first_day:%d %b}"
    again = (
        f"; out until {back.astimezone(IST):%d %b}, so the same choice comes up each day"
        if more_days
        else ""
    )
    passengers = sum(leg.booked for leg in own)

    airline_of = {leg_id: t.airline for t in op.trips.values() for leg_id in t.leg_ids}
    options: list[AircraftOption] = []
    others = sorted({leg.tail for leg in op.legs.values() if leg.aircraft_type is kind} - {tail})
    for other in others:
        legs = [leg for leg in op.legs.values() if leg.tail == other]
        if any(leg.off_block < window[1] and leg.on_block > window[0] for leg in legs):
            continue
        before = [leg for leg in legs if leg.on_block <= window[0]]
        where = max(before, key=lambda leg: leg.on_block).destination if before else None
        if where is not None and where != station:
            continue
        airline = next((airline_of[leg.leg_id] for leg in legs if leg.leg_id in airline_of), "")
        options.append(
            AircraftOption(
                "swap",
                f"Fly {named} on {other} ({kind}), free "
                f"{window[0].astimezone(IST):%d %b %H:%M}-{window[1].astimezone(IST):%H:%M} IST",
                0,
                other,
                f"{airline} aircraft on the ground at {station}; same crew, same times{again}",
            )
        )
    for spare in SPARES:
        if spare.aircraft_type is kind and spare.station == station:
            options.append(
                AircraftOption(
                    "spare",
                    f"Fly {named} on the spare {spare.tail} ({kind}) at {station}",
                    SPARE_COST,
                    spare.tail,
                    f"the spare kept at the hub; same crew, same times{again}",
                )
            )
    late = back - own[0].off_block
    if late <= HOLD_AT_MOST:
        options.append(
            AircraftOption(
                "hold",
                f"Hold {tail}'s flights until it is back ({int(late.total_seconds() // 60)} min "
                "late, and the later ones with it)",
                hold_cost,
                tail,
                f"{crew_over} crew pushed over a limit need cover"
                if crew_over
                else "every crew member stays within their limits",
            )
        )
    options.append(
        AircraftOption(
            "cancel",
            f"Cancel {named}: {passengers} passengers to rebook; later flights keep their times",
            op.rates.cancellation_per_leg * len(own),
            None,
            f"last resort{again}",
        )
    )
    return flights, tuple(options)
