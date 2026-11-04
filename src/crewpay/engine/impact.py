"""What a disruption does to the roster, and the cheapest way to repair all of it at once.

Three questions, each answered from the rules alone:

* An absence: which seats does it empty? ``absence_gaps`` lists every trip the person is
  rostered on in the dates given, with the passengers behind each seat.
* A delay: who does it push over a limit? ``delay_impact`` re-judges everyone on the trip with
  the late departure, including the rest they now owe before their next trip.
* Several empty seats: what is the cheapest legal way to fill them together? ``joint_cover``
  searches the combinations so nobody is used twice, where filling each seat on its own could
  pick the same standby captain for two trips.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from crewpay.domain import Rank
from crewpay.engine.cover import Choice, Cover, TripStarted, Way, find_cover
from crewpay.engine.legality import assess
from crewpay.engine.operation import Operation

MAX_CHOICES_PER_SEAT = 6
"""Crewed choices per seat the joint search considers. Past the six cheapest, a choice can
only be needed when the cheaper ones collide, which six seats deep is already rare."""

MAX_SEATS_SEARCHED = 8
"""Beyond this many seats the search would grow too large; seats are filled in cost order."""


@dataclass(frozen=True, slots=True)
class Gap:
    """One empty seat on one trip."""

    trip_id: str
    rank: Rank
    vacated_by: str
    days: tuple[date, ...]
    legs: int
    passengers: int
    started: bool


def absence_gaps(op: Operation, crew_id: str, first: date, last: date) -> list[Gap]:
    """Every seat ``crew_id`` leaves empty between ``first`` and ``last``, inclusive."""
    op.state(crew_id)
    gaps = []
    for trip in op.trips.values():
        seat = next((s for s in trip.crew if s.crew_id == crew_id), None)
        if seat is None:
            continue
        days = tuple(d.day for d in trip.days)
        if days[-1] < first or days[0] > last:
            continue
        gaps.append(
            Gap(
                trip_id=trip.trip_id,
                rank=seat.rank,
                vacated_by=crew_id,
                days=days,
                legs=len(trip.leg_ids),
                passengers=sum(op.legs[leg].booked for leg in trip.leg_ids),
                started=op.started(trip.trip_id),
            )
        )
    return sorted(gaps, key=lambda g: (g.days[0], g.trip_id))


def with_unavailable(
    op: Operation, crew_id: str, days: list[date] | tuple[date, ...], report_id: str
) -> Operation:
    """The operation with ``crew_id`` out on ``days``, so nobody offers them as cover."""
    unavailable = dict(op.unavailable)
    unavailable[crew_id] = tuple(unavailable.get(crew_id, ())) + tuple((d, report_id) for d in days)
    return replace(op, unavailable=unavailable)


@dataclass(frozen=True, slots=True)
class Breach:
    crew_id: str
    name: str
    rank: Rank
    rule_ids: tuple[str, ...]
    details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DelayImpact:
    trip_id: str
    hours: float
    day: date
    breaches: tuple[Breach, ...]
    """Crew the delay pushes over a limit they met on time."""
    still_legal: tuple[str, ...]
    passengers: int
    delay_cost: int
    already_illegal: tuple[str, ...] = ()
    """Crew who break a rule even on time: another problem, not this delay's."""

    @property
    def crew_affected(self) -> int:
        return len(self.breaches)


def delay_impact(op: Operation, trip_id: str, hours: float, day: date | None = None) -> DelayImpact:
    """Re-judge everyone rostered on ``trip_id`` with its departure ``hours`` late."""
    trip = op.trip(trip_id)
    target = day or trip.first_day.day
    breaches, legal, already = [], [], []
    for seat in trip.crew:
        if not assess(op, seat.crew_id, trip_id).legal:
            already.append(seat.crew_id)
            continue
        a = assess(op, seat.crew_id, trip_id, delay_hours=hours, delay_day=target)
        if a.legal:
            legal.append(seat.crew_id)
            continue
        crew = op.state(seat.crew_id)
        breaches.append(
            Breach(
                crew_id=crew.crew_id,
                name=crew.name,
                rank=seat.rank,
                rule_ids=tuple(f.rule_id for f in a.blockers),
                details=tuple(f.detail for f in a.blockers),
            )
        )
    on_day = next(d for d in trip.days if d.day == target)
    return DelayImpact(
        trip_id=trip_id,
        hours=hours,
        day=target,
        breaches=tuple(breaches),
        still_legal=tuple(legal),
        passengers=sum(op.legs[leg].booked for leg in on_day.legs),
        delay_cost=round(op.rates.delay_per_hour * hours),
        already_illegal=tuple(already),
    )


@dataclass(frozen=True, slots=True)
class Fill:
    gap: Gap
    choice: Choice


@dataclass(frozen=True, slots=True)
class JointPlan:
    fills: tuple[Fill, ...]
    """One per seat that can still be acted on, in the order the gaps were given."""
    skipped: tuple[Gap, ...]
    """Seats on trips that have already reported."""
    total_cost: int
    searched: bool
    """True when every combination was compared; False when seats were filled one by one."""

    @property
    def crew_ids(self) -> tuple[str, ...]:
        return tuple(f.choice.crew_id for f in self.fills if f.choice.crew_id)


def joint_cover(op: Operation, gaps: list[Gap]) -> JointPlan:
    """The cheapest way to fill every gap together, using nobody twice.

    Exhaustive over each seat's cheapest crewed choices plus its no-crew choices, so the
    answer is the true minimum within that set. The caller marks the absent crew unavailable
    first (``with_unavailable``) so they are never offered for someone else's seat.

    Two seats on the same trip, or the same person wanted for two trips, are the cases filling
    seats one by one gets wrong.
    """
    live = [g for g in gaps if not g.started]
    skipped = tuple(g for g in gaps if g.started)
    covers: list[Cover] = []
    for gap in live:
        try:
            covers.append(find_cover(op, gap.trip_id, gap.rank, vacated_by=gap.vacated_by))
        except TripStarted:
            skipped += (gap,)
    live = [g for g in live if g not in skipped]
    pools = [
        [c for c in cover.choices if c.crew_id][:MAX_CHOICES_PER_SEAT]
        + [c for c in cover.choices if c.crew_id is None]
        for cover in covers
    ]

    if len(live) > MAX_SEATS_SEARCHED:
        used: set[str] = set()
        picks = []
        for pool in pools:
            pick = next((c for c in pool if c.crew_id and c.crew_id not in used), pool[-1])
            if pick.crew_id:
                used.add(pick.crew_id)
            picks.append(pick)
        return _plan(live, picks, skipped, searched=False)

    # The least the seats still to fill can cost, for pruning. A seat on a trip that an earlier
    # seat may cancel can cost nothing, so it counts as zero.
    floor = [0] * (len(pools) + 1)
    for i in range(len(pools) - 1, -1, -1):
        shared = any(g.trip_id == live[i].trip_id for g in live[:i])
        floor[i] = floor[i + 1] + (0 if shared else min(c.cost for c in pools[i]))

    best: tuple[int, list[Choice]] | None = None
    picks: list[Choice] = []

    def search(i: int, used: frozenset[str], cost: int) -> None:
        nonlocal best
        if best is not None and cost + floor[i] >= best[0]:
            return
        if i == len(pools):
            best = (cost, list(picks))
            return
        trip_id = live[i].trip_id
        earlier = [c for g, c in zip(live, picks, strict=False) if g.trip_id == trip_id]
        if any(c.way is Way.CANCEL for c in earlier):
            # The trip is already cancelled for another of its seats: nothing more to pay.
            cancel = next(c for c in covers[i].choices if c.way is Way.CANCEL)
            picks.append(replace(cancel, cost=0, breakdown={}, note="cancelled with the trip"))
            search(i + 1, used, cost)
            picks.pop()
            return
        for choice in pools[i]:
            if choice.crew_id and choice.crew_id in used:
                continue
            if choice.way is Way.ONE_SHORT and any(c.way is Way.ONE_SHORT for c in earlier):
                continue  # one short is allowed once per trip
            picks.append(choice)
            search(i + 1, used | {choice.crew_id} if choice.crew_id else used, cost + choice.cost)
            picks.pop()

    search(0, frozenset(), 0)
    assert best is not None  # every pool ends with cancelling, which is always possible
    return _plan(live, best[1], skipped, searched=True)


def _plan(
    gaps: list[Gap], picks: list[Choice], skipped: tuple[Gap, ...], searched: bool
) -> JointPlan:
    # A cancelled trip is cancelled for every seat on it, paid for once.
    cancelled = {g.trip_id: c for g, c in zip(gaps, picks, strict=True) if c.way is Way.CANCEL}
    fills, charged = [], set()
    for gap, choice in zip(gaps, picks, strict=True):
        cancel = cancelled.get(gap.trip_id)
        if cancel is not None:
            first = gap.trip_id not in charged
            charged.add(gap.trip_id)
            choice = (
                cancel
                if first
                else replace(cancel, cost=0, breakdown={}, note="cancelled with the trip")
            )
        fills.append(Fill(gap, choice))
    return JointPlan(
        fills=tuple(fills),
        skipped=skipped,
        total_cost=sum(f.choice.cost for f in fills),
        searched=searched,
    )
