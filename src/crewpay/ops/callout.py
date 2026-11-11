"""Calling out replacement crew, one at a time, until someone accepts.

A callout fills one empty seat. It offers the seat to the cheapest legal crew member first;
if they decline, or do not answer in time, it offers it to the next. Before every offer, and
again when someone accepts, the cover is worked out afresh, so nobody is offered or given a
seat they stopped being legal for in the meantime.

With ``four_eyes`` (as the console and the agent run it), an accepted offer does not change
the roster by itself either: it becomes a plan that an approver approves, and the seat
is filled when they do (see ``crewpay.ops.plans``). If the plan is rejected or has gone stale,
the next legal crew member is asked.

**A callout never cancels a flight or flies it short on its own.** When nobody is left to
ask, it stops and hands the decision to the crew desk with the remaining options and their
costs. Cancelling or flying one short then needs two people: a controller proposes, an
approver decides. Until then the trip stays as it is.

    OFFERED --accept--> FILLED   (with four eyes: --accept--> ACCEPTED --approved--> FILLED)
       |  decline / no answer: next legal crew member
       v
    NEEDS_DECISION --propose (controller A)--> AWAITING_APPROVAL --approve (controller B)--> CLOSED
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import TypeAdapter

from crewpay.domain import Rank, Seat, Trip
from crewpay.engine.clock import IST
from crewpay.engine.cover import Choice, TripStarted, arrangements, find_cover
from crewpay.engine.operation import NotFound, Operation
from crewpay.ops import flight_changes
from crewpay.ops.notify import DESK, Notice, Outbox
from crewpay.ops.plans import Plan, PlanDesk, PlanError, PlanStatus
from crewpay.store.roster import StaleTrip

RESPOND_WITHIN = timedelta(minutes=20)
NO_CREW_WAYS = ("one_short", "cancel")


class Status(StrEnum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    """Someone accepted; the plan waits for an approver."""
    FILLED = "filled"
    NEEDS_DECISION = "needs_decision"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSED = "closed"


class CalloutError(ValueError):
    """A request the callout cannot take in its current state."""


@dataclass
class Offer:
    crew_id: str
    name: str
    way: str
    cost: int
    offered_at: datetime
    expires_at: datetime
    answer: str | None = None
    """``accepted``, ``declined``, ``no answer``, ``unavailable``, ``no longer legal`` or
    ``not approved``."""
    answered_at: datetime | None = None
    note: str = ""
    """What the crew member said with their answer."""
    breakdown: dict[str, int] = field(default_factory=dict)
    """The cost lines of this cover, from the engine: what the crew member would be paid
    is the pay lines among them."""


@dataclass
class Proposal:
    way: str
    """``one_short`` or ``cancel``."""
    summary: str
    cost: int
    proposed_by: str
    proposed_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None


@dataclass
class Callout:
    callout_id: str
    trip_id: str
    rank: Rank
    vacated_by: str
    opened_by: str
    opened_at: datetime
    status: Status = Status.OFFERED
    offers: list[Offer] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    """What the desk can still do once nobody is left to ask: never applied automatically."""
    reason: str = ""
    proposal: Proposal | None = None
    filled_by: str | None = None
    version: int | None = None
    """The trip's version after the seat was filled."""
    to_arrange: tuple[str, ...] = ()
    """What the desk must still book or retime for the person who accepted."""
    plan_id: str | None = None
    """With four eyes: the plan an accepted offer is waiting on."""

    @property
    def current(self) -> Offer | None:
        if self.status is Status.OFFERED and self.offers and self.offers[-1].answer is None:
            return self.offers[-1]
        return None


class Roster(Protocol):
    """What the desk needs from the store."""

    def operation(self, now: datetime | None = None) -> Operation: ...
    def trip(self, trip_id: str) -> tuple[Trip, int]: ...
    def change_crew(
        self,
        trip_id: str,
        crew: list[Seat],
        expected_version: int,
        actor: str,
        reason: str,
        at: datetime | None = None,
    ) -> int: ...


class Docs(Protocol):
    def put_doc(self, kind: str, key: str, body: dict) -> None: ...
    def docs(self, kind: str) -> list[dict]: ...
    def put_records(self, kind: str, records: Any) -> None: ...


_callout = TypeAdapter(Callout)


class CalloutDesk:
    """Every callout in progress.

    Kept in memory, or with ``docs`` in the store, read afresh at the start of every call and
    written after every change, so a desk on a short-lived server picks up where another left
    off.
    """

    def __init__(
        self,
        store: Roster,
        outbox: Outbox,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        respond_within: timedelta = RESPOND_WITHIN,
        operation_clock: Callable[[], datetime | None] = lambda: None,
        docs: Docs | None = None,
        four_eyes: bool = False,
    ) -> None:
        self.store = store
        self.outbox = outbox
        self.clock = clock
        self.respond_within = respond_within
        self.operation_clock = operation_clock
        """The time the roster is judged at; ``None`` uses the operation's own clock."""
        self.docs = docs
        self._lock = threading.RLock()
        self._callouts: dict[str, Callout] = {}
        self.plans: PlanDesk | None = None
        if four_eyes:
            self.plans = PlanDesk(
                store,
                outbox,
                clock,
                operation_clock,
                docs,
                lock=self._lock,
                on_decided=self._plan_decided,
            )

    def _sync(self) -> None:
        if self.docs is not None:
            self._callouts = {
                d["callout_id"]: _callout.validate_python(d) for d in self.docs.docs("callout")
            }

    def _save(self, *callouts: Callout) -> None:
        if self.docs is not None:
            for c in callouts:
                self.docs.put_doc("callout", c.callout_id, _callout.dump_python(c, mode="json"))

    # -- reading ----------------------------------------------------------------------------

    def get(self, callout_id: str) -> Callout:
        """The callout as it stands now. Methods that change it read it once, at their start."""
        with self._lock:
            self._sync()
            return self._get(callout_id)

    def _get(self, callout_id: str) -> Callout:
        try:
            return self._callouts[callout_id]
        except KeyError:
            raise NotFound("callout", callout_id) from None

    def all(self) -> list[Callout]:
        with self._lock:
            self._sync()
            return sorted(self._callouts.values(), key=lambda c: c.opened_at)

    def offers_for(self, crew_id: str) -> list[Callout]:
        return [c for c in self.all() if c.current and c.current.crew_id == crew_id]

    def answered_by(self, crew_id: str) -> list[tuple[Callout, Offer]]:
        """Offers made to ``crew_id`` that are no longer open, newest first: what they said,
        or that the time ran out and the seat went to the next person."""
        found = [
            (c, o)
            for c in self.all()
            for o in c.offers
            if o.crew_id == crew_id and o.answer is not None
        ]
        return sorted(found, key=lambda co: co[1].offered_at, reverse=True)

    # -- opening and offering ---------------------------------------------------------------

    def open(self, trip_id: str, rank: Rank, vacated_by: str, by: str) -> Callout:
        """Start filling the seat ``vacated_by`` leaves on ``trip_id``.

        Opening the same seat twice returns the callout already running.
        """
        with self._lock:
            self._sync()
            for c in self._callouts.values():
                if (c.trip_id, c.rank, c.vacated_by) == (
                    trip_id,
                    rank,
                    vacated_by,
                ) and c.status in (
                    Status.OFFERED,
                    Status.ACCEPTED,
                    Status.NEEDS_DECISION,
                    Status.AWAITING_APPROVAL,
                ):
                    return c
            trip, _ = self.store.trip(trip_id)
            if vacated_by not in {s.crew_id for s in trip.crew}:
                raise CalloutError(f"{vacated_by} is not on {trip_id}")
            callout = Callout(
                callout_id=f"CO-{uuid.uuid4().hex[:8]}",
                trip_id=trip_id,
                rank=rank,
                vacated_by=vacated_by,
                opened_by=by,
                opened_at=self.clock(),
            )
            self._callouts[callout.callout_id] = callout
            self._offer_next(callout)
            self._save(callout)
            return callout

    def _cover(self, callout: Callout):
        op = self.store.operation(self.operation_clock())
        return find_cover(op, callout.trip_id, callout.rank, callout.vacated_by)

    def _offer_next(self, callout: Callout) -> None:
        now = self.clock()
        try:
            cover = self._cover(callout)
        except TripStarted:
            self._needs_decision(callout, [], "the trip has already reported")
            return
        asked = {o.crew_id for o in callout.offers}
        # Someone already holding an open offer for another seat is not offered a second one
        # at the same time; they come back into the running if they decline that one.
        busy = {
            c.current.crew_id
            for c in self._callouts.values()
            if c.callout_id != callout.callout_id and c.current is not None
        } | {
            c.offers[-1].crew_id
            for c in self._callouts.values()
            if c.callout_id != callout.callout_id and c.status is Status.ACCEPTED
        }
        crewed = [c for c in cover.choices if c.crew_id and c.crew_id not in asked | busy]
        if not crewed:
            options = [c for c in cover.choices if c.way.value in NO_CREW_WAYS]
            why = "every legal crew member has been asked" if asked else "no legal crew"
            self._needs_decision(callout, options, why)
            return
        choice = crewed[0]
        offer = Offer(
            crew_id=choice.crew_id,
            name=choice.name or choice.crew_id,
            way=choice.way.label,
            cost=choice.cost,
            breakdown=dict(choice.breakdown),
            offered_at=now,
            expires_at=now + self.respond_within,
        )
        callout.offers.append(offer)
        callout.status = Status.OFFERED
        minutes = int(self.respond_within.total_seconds() // 60)
        self.outbox.send(
            Notice(
                to=choice.crew_id,
                subject=f"Callout: {callout.rank} on {callout.trip_id}",
                body=(
                    f"{offer.name}, you are asked to fly {callout.trip_id} as {callout.rank} "
                    f"({choice.way.label.lower()}). Please accept or decline within "
                    f"{minutes} minutes, by "
                    f"{offer.expires_at.astimezone(IST):%H:%M} IST, on your CrewPay page. "
                    "After that the seat is offered to the next crew member."
                ),
                at=now,
                ref=callout.callout_id,
            )
        )

    def _needs_decision(self, callout: Callout, options: list[Choice], why: str) -> None:
        callout.status = Status.NEEDS_DECISION
        callout.reason = why
        callout.options = [
            {"way": c.way.value, "summary": c.summary, "cost_inr": c.cost} for c in options
        ]
        listed = "; ".join(f"{o['summary']} (INR {o['cost_inr']:,})" for o in callout.options)
        self.outbox.send(
            Notice(
                to=DESK,
                subject=f"Decision needed: {callout.rank} on {callout.trip_id}",
                body=(
                    f"Nobody is left to call out for {callout.trip_id} ({why}). Nothing has been "
                    "cancelled. A controller must decide"
                    + (f": {listed}." if listed else ".")
                    + " A cancellation or flying short needs an approver."
                ),
                at=self.clock(),
                ref=callout.callout_id,
            )
        )

    # -- answers ----------------------------------------------------------------------------

    def respond(self, callout_id: str, crew_id: str, accept: bool, note: str = "") -> Callout:
        with self._lock:
            self._sync()
            callout = self._get(callout_id)
            offer = callout.current
            if offer is None or offer.crew_id != crew_id:
                raise CalloutError(f"there is no open offer for {crew_id} on {callout_id}")
            now = self.clock()
            if now > offer.expires_at:
                self._answer(offer, "no answer", offer.expires_at)
                self._offer_next(callout)
                self._save(callout)
                raise CalloutError("the offer expired and has gone to the next crew member")
            offer.note = note.strip()
            if not accept:
                self._answer(offer, "declined", now)
                self.outbox.send(
                    Notice(
                        to=crew_id,
                        subject=f"Declined: {callout.rank} on {callout.trip_id}",
                        body=f"You declined to fly {callout.trip_id} as {callout.rank}"
                        + (f" (\u201c{offer.note}\u201d)" if offer.note else "")
                        + ". Thank you for answering; the seat has gone to the next crew member.",
                        at=now,
                        ref=callout.callout_id,
                    )
                )
                if offer.note:
                    self.outbox.send(
                        Notice(
                            to=DESK,
                            subject=f"Declined: {callout.rank} on {callout.trip_id}",
                            body=f"{offer.name} ({crew_id}) declined: \u201c{offer.note}\u201d. "
                            "The next legal crew member is being asked.",
                            at=now,
                            ref=callout.callout_id,
                        )
                    )
                self._offer_next(callout)
            else:
                self._fill(callout, offer, now)
            self._save(callout)
            return callout

    def _answer(self, offer: Offer, answer: str, at: datetime) -> None:
        offer.answer = answer
        offer.answered_at = at

    def _fill(self, callout: Callout, offer: Offer, now: datetime) -> None:
        for _ in range(2):
            try:
                cover = self._cover(callout)
            except TripStarted:
                self._answer(offer, "no longer legal", now)
                self._needs_decision(callout, [], "the trip has already reported")
                return
            choice = next((c for c in cover.choices if c.crew_id == offer.crew_id), None)
            if choice is None:
                self._answer(offer, "no longer legal", now)
                self._offer_next(callout)
                return
            notes = arrangements(
                self.store.operation(self.operation_clock()), callout.trip_id, choice
            )
            trip, version = self.store.trip(callout.trip_id)
            if callout.vacated_by not in {s.crew_id for s in trip.crew}:
                self._answer(offer, "no longer needed", now)
                callout.status = Status.CLOSED
                callout.reason = f"{callout.vacated_by} is no longer on {callout.trip_id}"
                return
            if self.plans is not None:
                self._submit(callout, offer, now)
                return
            crew = [
                Seat(crew_id=offer.crew_id, rank=s.rank) if s.crew_id == callout.vacated_by else s
                for s in trip.crew
            ]
            try:
                callout.version = self.store.change_crew(
                    callout.trip_id,
                    crew,
                    version,
                    actor=f"callout {callout.callout_id} ({callout.opened_by})",
                    reason=f"{offer.name} accepted the callout for {callout.vacated_by}"
                    + "".join(f"; {n}" for n in notes),
                    at=now,
                )
            except StaleTrip:
                continue  # someone changed the trip meanwhile: judge again with the new roster
            self._answer(offer, "accepted", now)
            callout.status = Status.FILLED
            callout.filled_by = offer.crew_id
            callout.to_arrange = notes
            todo = "".join(f" To do: {n}." for n in notes)
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Filled: {callout.rank} on {callout.trip_id}",
                    body=f"{offer.name} ({offer.crew_id}) accepted {callout.trip_id}.{todo}",
                    at=now,
                    ref=callout.callout_id,
                )
            )
            return
        self._answer(offer, "no longer legal", now)
        self._offer_next(callout)

    def _submit(self, callout: Callout, offer: Offer, now: datetime) -> None:
        """Four eyes: the accepted offer becomes a plan for an approver to decide."""
        assert self.plans is not None
        try:
            plan = self.plans.submit(
                callout.trip_id,
                callout.vacated_by,
                offer.crew_id,
                by=callout.opened_by,
                note=f"{offer.name} accepted callout {callout.callout_id}"
                + (f": \u201c{offer.note}\u201d" if offer.note else ""),
                source=callout.callout_id,
            )
        except PlanError as err:
            raise CalloutError(str(err)) from err
        self._answer(offer, "accepted", now)
        callout.status = Status.ACCEPTED
        callout.plan_id = plan.plan_id
        callout.to_arrange = plan.to_arrange
        self.outbox.send(
            Notice(
                to=offer.crew_id,
                subject=f"Accepted: {callout.rank} on {callout.trip_id}",
                body=f"You accepted {callout.trip_id} as {callout.rank}. It is not on your roster "
                "yet: an approver confirms it, and you will get a message when it is.",
                at=now,
                ref=callout.callout_id,
            )
        )

    def _plan_decided(self, plan: Plan) -> None:
        """An approver decided a plan a callout made: fill the seat, or ask the next."""
        self._sync()
        callout = self._callouts.get(plan.source)
        if callout is None or callout.plan_id != plan.plan_id or callout.status is Status.CLOSED:
            return
        if plan.status is PlanStatus.APPROVED:
            callout.status = Status.FILLED
            callout.filled_by = plan.crew_id
            callout.version = plan.version
        else:
            offer = callout.offers[-1]
            self._answer(offer, "not approved", self.clock())
            self.outbox.send(
                Notice(
                    to=offer.crew_id,
                    subject=f"Not approved: {callout.rank} on {callout.trip_id}",
                    body=f"Your acceptance of {callout.trip_id} was not approved"
                    + (f": {plan.decision_note}" if plan.decision_note else "")
                    + ". Your roster is unchanged.",
                    at=self.clock(),
                    ref=callout.callout_id,
                )
            )
            callout.reason = f"{plan.plan_id} {plan.status}: {plan.decision_note}".rstrip(": ")
            callout.plan_id = None
            callout.to_arrange = ()
            self._offer_next(callout)
        self._save(callout)

    def withdraw(self, crew_id: str) -> list[Callout]:
        """Pull open offers from someone who just reported they cannot fly that trip.

        Called the moment they report it, so the next person is asked straight away instead of
        after the answer window runs out. An offer stays if the dates they gave miss the trip.
        """
        moved = []
        with self._lock:
            self._sync()
            for callout in self._callouts.values():
                offer = callout.current
                if offer is None or offer.crew_id != crew_id:
                    continue
                try:
                    still = any(c.crew_id == crew_id for c in self._cover(callout).choices)
                except TripStarted:
                    still = False
                if still:
                    continue
                self._answer(offer, "unavailable", self.clock())
                self.outbox.send(
                    Notice(
                        to=DESK,
                        subject=f"Offer withdrawn: {callout.rank} on {callout.trip_id}",
                        body=f"{offer.name} ({crew_id}) reported they cannot fly. The offer has "
                        "moved to the next legal crew member.",
                        at=self.clock(),
                        ref=callout.callout_id,
                    )
                )
                self._offer_next(callout)
                moved.append(callout)
            self._save(*moved)
        return moved

    def stand_down(self, trip_id: str, vacated_by: str, why: str) -> list[Callout]:
        """Close every callout still working on this seat: the person who left it is back."""
        closed = []
        with self._lock:
            self._sync()
            now = self.clock()
            for c in self._callouts.values():
                if (c.trip_id, c.vacated_by) != (trip_id, vacated_by) or c.status in (
                    Status.FILLED,
                    Status.CLOSED,
                ):
                    continue
                offer = c.current
                if offer is not None:
                    self._answer(offer, "no longer needed", now)
                    self.outbox.send(
                        Notice(
                            to=offer.crew_id,
                            subject=f"Offer withdrawn: {c.rank} on {c.trip_id}",
                            body=f"The callout for {c.trip_id} is no longer needed ({why}). "
                            "Nothing for you to do.",
                            at=now,
                            ref=c.callout_id,
                        )
                    )
                c.status = Status.CLOSED
                c.reason = why
                closed.append(c)
            self._save(*closed)
        return closed

    def tick(self) -> list[Callout]:
        """Move every offer that ran out of time to the next crew member. Run on a schedule."""
        moved = []
        with self._lock:
            self._sync()
            now = self.clock()
            for callout in self._callouts.values():
                offer = callout.current
                if offer is not None and now > offer.expires_at:
                    self._answer(offer, "no answer", offer.expires_at)
                    self._offer_next(callout)
                    moved.append(callout)
            self._save(*moved)
        return moved

    # -- the desk's decision ----------------------------------------------------------------

    def propose(self, callout_id: str, way: str, by: str) -> Callout:
        with self._lock:
            self._sync()
            callout = self._get(callout_id)
            if callout.status is not Status.NEEDS_DECISION:
                raise CalloutError(f"{callout_id} is {callout.status}, not waiting for a decision")
            option = next((o for o in callout.options if o["way"] == way), None)
            if option is None:
                ways = ", ".join(o["way"] for o in callout.options) or "none"
                raise CalloutError(f"{way!r} is not an option here (options: {ways})")
            callout.proposal = Proposal(
                way, option["summary"], option["cost_inr"], by, self.clock()
            )
            callout.status = Status.AWAITING_APPROVAL
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Approval needed: {option['summary']}",
                    body=f"{by} proposes: {option['summary']} for {callout.trip_id}. "
                    "An approver must agree before anything happens.",
                    at=self.clock(),
                    ref=callout.callout_id,
                )
            )
            self._save(callout)
            return callout

    def approve(self, callout_id: str, by: str) -> Callout:
        with self._lock:
            self._sync()
            callout = self._get(callout_id)
            proposal = callout.proposal
            if callout.status is not Status.AWAITING_APPROVAL or proposal is None:
                raise CalloutError(f"{callout_id} has nothing waiting for approval")
            if by == proposal.proposed_by:
                raise CalloutError("a proposal must be approved by someone else")
            proposal.approved_by = by
            proposal.approved_at = self.clock()
            callout.status = Status.CLOSED
            callout.reason = f"{proposal.summary}: approved by {by}"
            if proposal.way == "cancel":
                # Approved, so the trip's flights not yet gone are cancelled now.
                op = self.store.operation(self.operation_clock())
                flight_changes.apply(
                    self.store,
                    flight_changes.cancel_trip(op, callout.trip_id),
                    self.outbox,
                    by,
                    callout.callout_id,
                    self.clock,
                )
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Approved: {proposal.summary}",
                    body=f"{proposal.summary} for {callout.trip_id}, proposed by "
                    f"{proposal.proposed_by} and approved by {by}. Operations to action.",
                    at=proposal.approved_at,
                    ref=callout.callout_id,
                )
            )
            self._save(callout)
            return callout

    def reject(self, callout_id: str, by: str) -> Callout:
        """Send a proposal back; the callout waits for a decision again."""
        with self._lock:
            self._sync()
            callout = self._get(callout_id)
            if callout.status is not Status.AWAITING_APPROVAL:
                raise CalloutError(f"{callout_id} has nothing waiting for approval")
            callout.proposal = None
            callout.status = Status.NEEDS_DECISION
            self._save(callout)
            return callout
