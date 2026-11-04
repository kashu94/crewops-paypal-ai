"""Four eyes on every roster change.

Nothing changes a trip's crew on one person's word. A controller **submits** a plan: put this
crew member in that seat. An **approver**, never the person who submitted it, decides it:

* **approve**: the plan is judged again as the roster stands now. If its seat changed since it
  was submitted (the person it replaces is no longer in it, or the new crew member is already
  on the trip), or the crew member is no longer legal cover, the plan goes **stale** and
  nothing changes; otherwise the change is made. A change to another seat on the same trip
  does not make it stale, so the seats of one report can be approved one after another; the
  legality check at approval covers whatever else moved.
* **reject**, with a reason: the plan is closed.
* **send back**, with a note: the plan returns to the controller who submitted it, to rework.

The controller who submitted a plan may **withdraw** it until it is decided. A plan made by a
callout (someone accepted an offer) is submitted in the name of the controller who opened the
callout, so the person who approves it is always someone else.

    SUBMITTED --approve--> APPROVED       (or STALE, when the roster moved on)
        |---reject------> REJECTED
        |---send back---> SENT_BACK
        '---withdraw----> WITHDRAWN
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import TypeAdapter

from crewpay.domain import Rank, Seat, Trip
from crewpay.engine.cover import TripStarted, arrangements, find_cover
from crewpay.engine.operation import NotFound, Operation
from crewpay.ops.notify import DESK, Notice, Outbox
from crewpay.store.roster import StaleTrip


class PlanStatus(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT_BACK = "sent_back"
    WITHDRAWN = "withdrawn"
    STALE = "stale"


OPEN = (PlanStatus.SUBMITTED,)


class PlanError(ValueError):
    """A request the plan cannot take in its current state."""


@dataclass
class Plan:
    plan_id: str
    trip_id: str
    rank: Rank
    replacing: str
    crew_id: str
    name: str
    summary: str
    cost: int
    trip_version: int
    """The trip's version when the plan was made; approval applies it only at this version."""
    submitted_by: str
    submitted_at: datetime
    note: str = ""
    source: str = "desk"
    """``desk``, or the id of the callout whose accepted offer made it."""
    status: PlanStatus = PlanStatus.SUBMITTED
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str = ""
    to_arrange: tuple[str, ...] = ()
    version: int | None = None
    """The trip's version after the change was made."""
    history: list[dict] = field(default_factory=list)


class Roster(Protocol):
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


_plan = TypeAdapter(Plan)


class PlanDesk:
    """Plans waiting for an approver, and what became of the rest."""

    def __init__(
        self,
        store: Roster,
        outbox: Outbox,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        operation_clock: Callable[[], datetime | None] = lambda: None,
        docs: Docs | None = None,
        lock: threading.RLock | None = None,
        on_decided: Callable[[Plan], None] | None = None,
    ) -> None:
        self.store = store
        self.outbox = outbox
        self.clock = clock
        self.operation_clock = operation_clock
        self.docs = docs
        self._lock = lock or threading.RLock()
        self._plans: dict[str, Plan] = {}
        self.on_decided = on_decided
        """Told about every decided plan: the callout desk fills or reopens its seat."""

    def _sync(self) -> None:
        if self.docs is not None:
            self._plans = {d["plan_id"]: _plan.validate_python(d) for d in self.docs.docs("plan")}

    def _save(self, plan: Plan) -> None:
        self._plans[plan.plan_id] = plan
        if self.docs is not None:
            self.docs.put_doc("plan", plan.plan_id, _plan.dump_python(plan, mode="json"))

    def _get(self, plan_id: str) -> Plan:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise NotFound("plan", plan_id) from None

    def get(self, plan_id: str) -> Plan:
        with self._lock:
            self._sync()
            return self._get(plan_id)

    def all(self) -> list[Plan]:
        with self._lock:
            self._sync()
            return sorted(self._plans.values(), key=lambda p: p.submitted_at, reverse=True)

    def waiting(self) -> list[Plan]:
        return [p for p in self.all() if p.status in OPEN]

    def _note(self, plan: Plan, what: str, by: str, text: str = "") -> None:
        plan.history.append(
            {
                "at": self.clock().isoformat(),
                "what": what,
                "by": by,
                **({"note": text} if text else {}),
            }
        )

    # -- submitting -------------------------------------------------------------------------

    def submit(
        self, trip_id: str, replacing: str, crew_id: str, by: str, note: str = "", source="desk"
    ) -> Plan:
        """Propose putting ``crew_id`` in the seat ``replacing`` holds on ``trip_id``.

        Only someone the engine offers as legal cover can be proposed.
        """
        with self._lock:
            self._sync()
            for p in self._plans.values():
                if p.status in OPEN and (p.trip_id, p.replacing) == (trip_id, replacing):
                    raise PlanError(
                        f"{p.plan_id} already proposes cover for {replacing} on {trip_id}"
                    )
            trip, version = self.store.trip(trip_id)
            seat = next((s for s in trip.crew if s.crew_id == replacing), None)
            if seat is None:
                raise PlanError(f"{replacing} is not on {trip_id}")
            op = self.store.operation(self.operation_clock())
            try:
                cover = find_cover(op, trip_id, seat.rank, replacing)
            except TripStarted as err:
                raise PlanError(str(err)) from err
            choice = next((c for c in cover.choices if c.crew_id == crew_id), None)
            if choice is None:
                why = cover.why_not(crew_id)
                reason = f": {'; '.join(why.reasons)}" if why else ""
                raise PlanError(f"{crew_id} is not legal cover for {trip_id}{reason}")
            plan = Plan(
                plan_id=f"PL-{uuid.uuid4().hex[:8]}",
                trip_id=trip_id,
                rank=seat.rank,
                replacing=replacing,
                crew_id=crew_id,
                name=choice.name or crew_id,
                summary=choice.summary,
                cost=choice.cost,
                trip_version=version,
                submitted_by=by,
                submitted_at=self.clock(),
                note=note,
                source=source,
                to_arrange=arrangements(op, trip_id, choice),
            )
            self._note(plan, "submitted", by, note)
            self._save(plan)
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Approval needed: {plan.rank} on {trip_id}",
                    body=f"{by} proposes {choice.summary} in place of {replacing} on {trip_id} "
                    f"(INR {choice.cost:,}). An approver must agree before the "
                    "roster changes.",
                    at=plan.submitted_at,
                    ref=plan.plan_id,
                )
            )
            return plan

    # -- deciding ---------------------------------------------------------------------------

    def _open(self, plan_id: str) -> Plan:
        plan = self._get(plan_id)
        if plan.status not in OPEN:
            raise PlanError(f"{plan_id} is {plan.status}, not waiting for a decision")
        return plan

    def _second(self, plan: Plan, by: str) -> None:
        if by == plan.submitted_by:
            raise PlanError(f"{plan.plan_id} was submitted by {by}; someone else must decide it")

    def _decide(self, plan: Plan, status: PlanStatus, by: str, note: str) -> Plan:
        plan.status = status
        plan.decided_by = by
        plan.decided_at = self.clock()
        plan.decision_note = note
        self._note(plan, str(status), by, note)
        self._save(plan)
        if self.on_decided is not None:
            self.on_decided(plan)
        return plan

    def _stale_reason(self, plan: Plan) -> str | None:
        trip, version = self.store.trip(plan.trip_id)
        on_board = {s.crew_id for s in trip.crew}
        if plan.replacing not in on_board:
            return (
                f"{plan.trip_id} changed since the plan was made: {plan.replacing} is no longer "
                f"in that seat (version {plan.trip_version}, now {version})"
            )
        if plan.crew_id in on_board:
            return (
                f"{plan.trip_id} changed since the plan was made: {plan.crew_id} is already on "
                f"it (version {plan.trip_version}, now {version})"
            )
        op = self.store.operation(self.operation_clock())
        try:
            cover = find_cover(op, plan.trip_id, plan.rank, plan.replacing)
        except TripStarted as err:
            return str(err)
        if not any(c.crew_id == plan.crew_id for c in cover.choices):
            why = cover.why_not(plan.crew_id)
            detail = f": {'; '.join(why.reasons)}" if why else ""
            return f"{plan.name} ({plan.crew_id}) is no longer legal cover{detail}"
        return None

    def approve(self, plan_id: str, by: str) -> Plan:
        """Check the plan again against the roster now, then make the change."""
        with self._lock:
            self._sync()
            plan = self._open(plan_id)
            self._second(plan, by)
            stale = self._stale_reason(plan)
            if stale is None:
                trip, current = self.store.trip(plan.trip_id)
                crew = [
                    Seat(crew_id=plan.crew_id, rank=s.rank) if s.crew_id == plan.replacing else s
                    for s in trip.crew
                ]
                try:
                    plan.version = self.store.change_crew(
                        plan.trip_id,
                        crew,
                        current,
                        actor=f"{plan.submitted_by}, approved by {by}",
                        reason=f"{plan.plan_id}: {plan.name} replaces {plan.replacing}"
                        + "".join(f"; {n}" for n in plan.to_arrange),
                        at=self.clock(),
                    )
                except StaleTrip as err:
                    stale = str(err)
            if stale is not None:
                self._decide(plan, PlanStatus.STALE, by, stale)
                self.outbox.send(
                    Notice(
                        to=DESK,
                        subject=f"Stale: {plan.rank} on {plan.trip_id}",
                        body=f"{plan.plan_id} was not applied: {stale}. Nothing changed; "
                        "submit a new plan.",
                        at=self.clock(),
                        ref=plan.plan_id,
                    )
                )
                return plan
            self._decide(plan, PlanStatus.APPROVED, by, "")
            todo = "".join(f" To do: {n}." for n in plan.to_arrange)
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Approved: {plan.rank} on {plan.trip_id}",
                    body=f"{plan.name} ({plan.crew_id}) replaces {plan.replacing} on "
                    f"{plan.trip_id}, submitted by {plan.submitted_by} and approved by {by}."
                    + todo,
                    at=self.clock(),
                    ref=plan.plan_id,
                )
            )
            self.outbox.send(
                Notice(
                    to=plan.crew_id,
                    subject=f"Rostered: {plan.trip_id}",
                    body=f"{plan.name}, you are now rostered on {plan.trip_id} as {plan.rank}.",
                    at=self.clock(),
                    ref=plan.plan_id,
                )
            )
            return plan

    def reject(self, plan_id: str, by: str, reason: str) -> Plan:
        if len(reason.strip()) < 3:
            raise PlanError("say why the plan is rejected")
        with self._lock:
            self._sync()
            plan = self._open(plan_id)
            self._second(plan, by)
            return self._decide(plan, PlanStatus.REJECTED, by, reason.strip())

    def send_back(self, plan_id: str, by: str, note: str) -> Plan:
        if len(note.strip()) < 3:
            raise PlanError("say what to change before sending the plan back")
        with self._lock:
            self._sync()
            plan = self._open(plan_id)
            self._second(plan, by)
            plan = self._decide(plan, PlanStatus.SENT_BACK, by, note.strip())
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Sent back to {plan.submitted_by}: {plan.trip_id}",
                    body=f"{by} sent {plan.plan_id} back: {plan.decision_note}",
                    at=self.clock(),
                    ref=plan.plan_id,
                )
            )
            return plan

    def stand_down(self, trip_id: str, replacing: str, by: str, why: str) -> list[Plan]:
        """Withdraw every plan still waiting for this seat: the person in it is back."""
        with self._lock:
            self._sync()
            waiting = [
                p
                for p in self._plans.values()
                if (p.trip_id, p.replacing) == (trip_id, replacing) and p.status in OPEN
            ]
            return [self._decide(p, PlanStatus.WITHDRAWN, by, why) for p in waiting]

    def withdraw(self, plan_id: str, by: str) -> Plan:
        with self._lock:
            self._sync()
            plan = self._open(plan_id)
            if by != plan.submitted_by:
                raise PlanError(f"only {plan.submitted_by}, who submitted it, can withdraw it")
            return self._decide(plan, PlanStatus.WITHDRAWN, by, "")
