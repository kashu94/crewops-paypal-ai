"""Who can cover an empty seat, what each choice costs, and why everyone else is out.

``find_cover`` judges every crew member of the airline who could sit in the seat and returns
the legal ones ranked by cost. A controller works through choices in a fixed order, and the
result keeps it:

1. Own-rank crew: standby callouts, day-off callouts and positioning from another base, all
   ranked together by what they cost.
2. Qualified substitutes, only when fewer than three own-rank crew are legal: a
   command-qualified first officer for a captain's seat, a right-seat-current captain for a
   first officer's.
3. Flying one cabin crew short, when the passengers booked still allow it.
4. Cancelling the trip's legs, always last.

Every figure an answer might read out (how much cheaper than cancelling, how many choices
cost the same, the premium of the next price) is computed here, so no model ever does sums.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum

from crewpay.domain import Rank
from crewpay.engine.clock import IST, spoken_hours
from crewpay.engine.legality import UNAVAILABLE, Assessment, assess
from crewpay.engine.operation import Operation
from crewpay.engine.rules import BASE, BLOCK, CERT, DUTY, FDP, NIGHT, RATING, REST, WEEKLY

SUBSTITUTES_BELOW = 3
"""With fewer legal own-rank crew than this, qualified crew of the other pilot rank are
offered too, after every own-rank choice."""

PASSENGERS_PER_CABIN_CREW = 50

FUNNEL = (
    ("rated", (RATING,), "not rated on the type, not active, or reported unavailable"),
    ("current", (CERT,), "a certificate lapses before the trip ends"),
    ("in position", (BASE,), "cannot get to the departure, or standby window misses it"),
    ("rested", (REST, WEEKLY), "rest too short, weekly rest broken, or already flying"),
    ("within limits", (FDP, NIGHT, DUTY, BLOCK), "would break a duty or flying-hour limit"),
    ("keeps the crew legal", (), "the late departure they cause would break a rule for others"),
)
"""The order a controller thinks in. A candidate leaves at the first stage they fail."""


class TripStarted(ValueError):
    """The trip has already reported; it can no longer be re-crewed."""


class Way(StrEnum):
    STANDBY = "standby"
    DAY_OFF = "day_off"
    POSITIONING = "positioning"
    COMMAND_UPGRADE = "command_upgrade"
    RIGHT_SEAT = "right_seat"
    ONE_SHORT = "one_short"
    CANCEL = "cancel"

    @property
    def label(self) -> str:
        return {
            Way.STANDBY: "Standby callout",
            Way.DAY_OFF: "Day-off callout",
            Way.POSITIONING: "Positioned from another base",
            Way.COMMAND_UPGRADE: "Command-qualified first officer",
            Way.RIGHT_SEAT: "Captain in the right seat",
            Way.ONE_SHORT: "Fly one cabin crew short",
            Way.CANCEL: "Cancel the trip",
        }[self]


@dataclass(frozen=True, slots=True)
class Choice:
    position: int
    way: Way
    cost: int
    breakdown: dict[str, int]
    crew_id: str | None = None
    name: str | None = None
    rank: Rank | None = None
    base: str | None = None
    slip_hours: float = 0.0
    """How late the first departure leaves with this choice."""
    positioning_leg: str | None = None
    minutes_to_airport: int | None = None
    years_of_service: int | None = None
    note: str | None = None

    @property
    def summary(self) -> str:
        if self.crew_id is None:
            return f"{self.way.label}: {self.note}" if self.note else self.way.label
        who = f"{self.rank} {self.name} ({self.crew_id})"
        late = f", departure {self.slip_hours:g}h late" if self.slip_hours else ""
        return f"{who}, {self.way.label.lower()}{late}"


@dataclass(frozen=True, slots=True)
class Excluded:
    crew_id: str
    name: str
    stage: str
    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Someone who would be a choice, but has reported they cannot fly. Kept so a screen can
    show them struck through instead of making them vanish."""

    choice: Choice
    report_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Stage:
    stage: str
    left: int
    dropped: int
    reason: str


@dataclass(frozen=True, slots=True)
class Cover:
    trip_id: str
    seat: Rank
    vacated_by: str | None
    choices: tuple[Choice, ...]
    """Every legal choice in order; the last is always cancelling."""
    excluded: tuple[Excluded, ...]
    funnel: tuple[Stage, ...]
    considered: int
    facts: dict[str, float | int | str] = field(default_factory=dict)
    """Comparisons an answer may quote, worked out here."""
    withdrawn: tuple[Withdrawn, ...] = ()

    @property
    def best(self) -> Choice:
        return self.choices[0]

    def why_not(self, crew_id: str) -> Excluded | None:
        return next((e for e in self.excluded if e.crew_id == crew_id), None)

    def picks(self) -> dict[str, Choice]:
        """The best crew choice by each of the desk's three aims.

        ``cheapest`` is the first choice. ``least_delay`` leaves soonest, then cheapest.
        ``keeps_standby`` is the cheapest choice that does not use a standby, so standby stays
        free for the next problem; it is missing when every crew choice is a standby.
        """
        crewed = [c for c in self.choices if c.crew_id]
        if not crewed:
            return {}
        out = {
            "cheapest": crewed[0],
            "least_delay": min(crewed, key=lambda c: (c.slip_hours, c.cost, c.position)),
        }
        keeps = next((c for c in crewed if c.way is not Way.STANDBY), None)
        if keeps is not None:
            out["keeps_standby"] = keeps
        return out


def _stage_of(a: Assessment) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    blockers = a.blockers
    for stage, rule_ids, _ in FUNNEL:
        hit = [f for f in blockers if f.rule_id in rule_ids]
        if hit:
            return stage, tuple(f.rule_id for f in hit), tuple(f.detail for f in hit)
    return "within limits", tuple(f.rule_id for f in blockers), tuple(f.detail for f in blockers)


def _delay_breaks(
    op: Operation, trip_id: str, staying: list[str], hours: float, memo: dict
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Rules the rest of the crew would break if the trip left ``hours`` late.

    A positioned or late-arriving replacement holds the departure, and everyone already on
    the trip flies the longer day: a choice that is legal for them can still push a colleague
    over a duty limit or cut their rest before the next trip. Only breaks the delay causes
    count; someone already illegal is another seat's problem.
    """
    if hours not in memo:
        rule_ids: list[str] = []
        reasons: list[str] = []
        for crew_id in staying:
            if not assess(op, crew_id, trip_id).legal:
                continue
            for b in assess(op, crew_id, trip_id, delay_hours=hours).blockers:
                rule_ids.append(b.rule_id)
                name = op.state(crew_id).name
                reasons.append(f"{name} ({crew_id}) would fail {b.rule_id}: {b.detail}")
        memo[hours] = (tuple(dict.fromkeys(rule_ids)), tuple(reasons))
    return memo[hours]


def arrangements(op: Operation, trip_id: str, choice: Choice) -> tuple[str, ...]:
    """What the desk must still arrange if this choice is taken: never left implicit."""
    notes = []
    if choice.positioning_leg:
        leg = op.legs[choice.positioning_leg]
        notes.append(
            f"book a positioning seat on {leg.flight_no} ({leg.origin}-{leg.destination}, "
            f"departs {leg.off_block.astimezone(IST):%d %b %H:%M} IST)"
        )
    if choice.slip_hours:
        notes.append(
            f"{trip_id} departs {spoken_hours(choice.slip_hours)} late: operations to retime "
            "and inform passengers"
        )
    return tuple(notes)


def _late_by(op: Operation, crew_id: str, trip_id: str) -> timedelta:
    """How long after the scheduled report someone called now could be at the airport."""
    ready = op.now + timedelta(minutes=op.minutes_to_airport.get(crew_id, 0))
    return max(timedelta(0), ready - op.days_of(trip_id)[0].report)


def _judge(op: Operation, crew_id: str, trip_id: str) -> Assessment:
    return assess(op, crew_id, trip_id, late_by=_late_by(op, crew_id, trip_id))


def _cost(op: Operation, a: Assessment, rank: Rank) -> dict[str, int]:
    rates = op.rates
    years = op.years_of_service.get(a.crew_id, 0)
    parts = {
        "callout": rates.callout(rank, a.on_standby),
        "service allowance": rates.service_allowance(rank, years),
        "travel allowance": rates.travel(op.minutes_to_airport.get(a.crew_id, 0)),
    }
    if a.positioning:
        parts["positioning"] = rates.positioning
    if a.slip:
        parts["delay"] = round(rates.delay_per_hour * a.slip_hours)
    return parts


def _choice(op: Operation, a: Assessment, way: Way) -> Choice:
    crew = op.state(a.crew_id)
    breakdown = _cost(op, a, crew.rank)
    return Choice(
        position=0,
        way=way,
        cost=sum(breakdown.values()),
        breakdown=breakdown,
        crew_id=crew.crew_id,
        name=crew.name,
        rank=crew.rank,
        base=crew.base,
        slip_hours=a.slip_hours,
        positioning_leg=a.positioning.leg_id if a.positioning else None,
        minutes_to_airport=op.minutes_to_airport.get(crew.crew_id),
        years_of_service=op.years_of_service.get(crew.crew_id),
    )


def _own_way(a: Assessment) -> Way:
    if a.positioning:
        return Way.POSITIONING
    return Way.STANDBY if a.on_standby else Way.DAY_OFF


def _order(choices: list[Choice]) -> list[Choice]:
    return sorted(choices, key=lambda c: (c.cost, c.slip_hours, c.crew_id or ""))


def cabin_minimum(op: Operation, trip_id: str) -> int:
    """One cabin crew per 50 passengers booked on the fullest leg, at least one."""
    trip = op.trip(trip_id)
    booked = max(op.legs[leg].booked for leg in trip.leg_ids)
    return max(1, math.ceil(booked / PASSENGERS_PER_CABIN_CREW))


def _one_short(op: Operation, trip_id: str, seat: Rank, vacated_by: str | None) -> Choice | None:
    if seat is not Rank.CABIN_CREW:
        return None
    trip = op.trip(trip_id)
    remaining = sum(1 for s in trip.crew if s.rank.is_cabin and s.crew_id != vacated_by)
    needed = cabin_minimum(op, trip_id)
    if remaining < needed:
        return None
    return Choice(
        position=0,
        way=Way.ONE_SHORT,
        cost=0,
        breakdown={},
        note=f"{remaining} cabin crew still meet the minimum of {needed} for the passengers booked",
    )


def _cancel(op: Operation, trip_id: str) -> Choice:
    legs = op.trip(trip_id).leg_ids
    passengers = sum(op.legs[leg].booked for leg in legs)
    cost = op.rates.cancellation_per_leg * len(legs)
    return Choice(
        position=0,
        way=Way.CANCEL,
        cost=cost,
        breakdown={"cancellation": cost},
        note=f"{len(legs)} legs, {passengers} passengers booked",
    )


def _facts(choices: list[Choice]) -> dict[str, float | int | str]:
    crewed = [c for c in choices if c.crew_id]
    cancel = choices[-1]
    facts: dict[str, float | int | str] = {
        "legal_choices": len(crewed),
        "cancel_cost": cancel.cost,
    }
    if crewed:
        best = crewed[0]
        facts["best_cost"] = best.cost
        facts["same_cost_as_best"] = sum(1 for c in crewed if c.cost == best.cost)
        facts["saving_vs_cancel"] = cancel.cost - best.cost
        if best.cost:
            facts["cancel_times_best"] = round(cancel.cost / best.cost, 1)
        dearer = next((c for c in crewed if c.cost > best.cost), None)
        if dearer:
            facts["next_price_premium"] = dearer.cost - best.cost
            facts["next_price_crew"] = dearer.crew_id or ""
    return facts


def find_cover(op: Operation, trip_id: str, seat: Rank, vacated_by: str | None = None) -> Cover:
    """Every way to fill ``seat`` on ``trip_id``, best first, and why the rest cannot."""
    trip = op.trip(trip_id)
    if op.started(trip_id):
        raise TripStarted(f"{trip_id} reported at {op.days_of(trip_id)[0].report:%d %b %H:%M}Z")
    on_trip = {s.crew_id for s in trip.crew}

    own: list[Choice] = []
    excluded: list[Excluded] = []
    withdrawn: list[Withdrawn] = []
    staying = [s.crew_id for s in trip.crew if s.crew_id != vacated_by]
    memo: dict[float, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def keeps_crew_legal(crew_id: str, name: str, a: Assessment) -> bool:
        if not a.slip:
            return True
        rule_ids, reasons = _delay_breaks(op, trip_id, staying, a.slip_hours, memo)
        if rule_ids:
            late = f"departing {spoken_hours(a.slip_hours)} late: "
            excluded.append(
                Excluded(
                    crew_id,
                    name,
                    "keeps the crew legal",
                    rule_ids,
                    tuple(late + r for r in reasons),
                )
            )
        return not rule_ids

    candidates = [c for c in op.crew_of(trip.airline, seat) if c.crew_id not in on_trip]
    for crew in candidates:
        a = _judge(op, crew.crew_id, trip_id)
        if a.legal:
            if keeps_crew_legal(crew.crew_id, crew.name, a):
                own.append(_choice(op, a, _own_way(a)))
            continue
        stage, rule_ids, reasons = _stage_of(a)
        excluded.append(Excluded(crew.crew_id, crew.name, stage, rule_ids, reasons))
        if all(b.detail.startswith(UNAVAILABLE) for b in a.blockers):
            others = {k: v for k, v in op.unavailable.items() if k != crew.crew_id}
            judged = _judge(replace(op, unavailable=others), crew.crew_id, trip_id)
            if judged.legal:
                ids = tuple(dict.fromkeys(r for _, r in op.unavailable[crew.crew_id]))
                withdrawn.append(Withdrawn(_choice(op, judged, _own_way(judged)), ids))

    substitutes: list[Choice] = []
    if seat.is_pilot and len(own) < SUBSTITUTES_BELOW:
        if seat is Rank.CAPTAIN:
            other, qualified, way = Rank.FIRST_OFFICER, op.command_qualified, Way.COMMAND_UPGRADE
        else:
            other, qualified, way = Rank.CAPTAIN, op.right_seat_qualified, Way.RIGHT_SEAT
        for crew in op.crew_of(trip.airline, other):
            if crew.crew_id in qualified and crew.crew_id not in on_trip:
                a = _judge(op, crew.crew_id, trip_id)
                if a.legal and keeps_crew_legal(crew.crew_id, crew.name, a):
                    substitutes.append(_choice(op, a, way))

    choices = _order(own) + _order(substitutes)
    short = _one_short(op, trip_id, seat, vacated_by)
    if short:
        choices.append(short)
    choices.append(_cancel(op, trip_id))
    choices = [replace(c, position=i) for i, c in enumerate(choices, start=1)]

    funnel = []
    left = len(candidates)
    for stage, _, reason in FUNNEL:
        dropped = sum(1 for e in excluded if e.stage == stage)
        left -= dropped
        funnel.append(Stage(stage, left, dropped, reason))

    return Cover(
        trip_id=trip_id,
        seat=seat,
        vacated_by=vacated_by,
        choices=tuple(choices),
        excluded=tuple(sorted(excluded, key=lambda e: e.crew_id)),
        funnel=tuple(funnel),
        considered=len(candidates),
        facts=_facts(choices),
        withdrawn=tuple(sorted(withdrawn, key=lambda w: (w.choice.cost, w.choice.crew_id or ""))),
    )
