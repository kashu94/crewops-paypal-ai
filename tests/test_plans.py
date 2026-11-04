from datetime import date

import pytest
from conftest import SEED_DIR
from test_callout import LEGAL_CAPTAINS, SICK, TRIP, Clock

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.domain import Rank, Seat
from crewpay.engine.cover import find_cover
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox, Status
from crewpay.ops.plans import PlanDesk, PlanError, PlanStatus
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

FIRST, SECOND = LEGAL_CAPTAINS[:2]
IYER = Caller("ananya.iyer", Role.CONTROLLER)
REDDY = Caller("rohit.malhotra", Role.CONTROLLER)
NAIR = Caller("meera.bhatt", Role.APPROVER)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def outbox():
    return MemoryOutbox()


@pytest.fixture
def desk(mem, outbox):
    return CalloutDesk(mem, outbox, clock=Clock(), docs=mem, four_eyes=True)


@pytest.fixture
def plans(desk):
    return desk.plans


def crew_on(mem, trip_id=TRIP):
    trip, version = mem.trip(trip_id)
    return {s.crew_id for s in trip.crew}, version


def test_a_plan_changes_nothing_until_a_second_controller_approves(mem, plans, outbox):
    before, version = crew_on(mem)
    plan = plans.submit(TRIP, SICK, FIRST, "ananya.iyer", "Pooja is sick")
    assert plan.status is PlanStatus.SUBMITTED and plan.trip_version == version
    assert crew_on(mem) == (before, version)
    assert "Approval needed" in outbox.sent(DESK)[-1].subject

    with pytest.raises(PlanError, match="someone else"):
        plans.approve(plan.plan_id, "ananya.iyer")
    done = plans.approve(plan.plan_id, "rohit.malhotra")
    assert done.status is PlanStatus.APPROVED and done.version == version + 1
    after, _ = crew_on(mem)
    assert FIRST in after and SICK not in after
    assert mem.changes(TRIP)[-1].actor == "ananya.iyer, approved by rohit.malhotra"
    assert outbox.sent(FIRST)[-1].subject == f"Rostered: {TRIP}"


def test_only_legal_cover_can_be_proposed_and_only_once_per_seat(plans):
    with pytest.raises(PlanError, match="not legal cover"):
        plans.submit(TRIP, SICK, SICK, "ananya.iyer")
    plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    with pytest.raises(PlanError, match="already proposes"):
        plans.submit(TRIP, SICK, SECOND, "rohit.malhotra")


def test_a_plan_goes_stale_when_its_seat_changed_after_it_was_made(mem, plans):
    plan = plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    trip, version = mem.trip(TRIP)
    crew = [Seat(crew_id=SECOND, rank=s.rank) if s.crew_id == SICK else s for s in trip.crew]
    mem.change_crew(TRIP, crew, version, "ops", "covered by phone")
    stale = plans.approve(plan.plan_id, "rohit.malhotra")
    assert stale.status is PlanStatus.STALE and "changed since" in stale.decision_note
    assert SECOND in crew_on(mem)[0] and FIRST not in crew_on(mem)[0]


def test_two_seats_on_one_trip_are_approved_one_after_the_other(mem, plans):
    trip, _ = mem.trip(TRIP)
    other = next(s for s in trip.crew if s.rank == "First Officer")
    mem.mark_unavailable(other.crew_id, date(2026, 11, 5), date(2026, 11, 6), "sick", "ops")
    cover = find_cover(mem.operation(), TRIP, other.rank, other.crew_id)
    officer = next(c.crew_id for c in cover.choices if c.crew_id)
    first = plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    second = plans.submit(TRIP, other.crew_id, officer, "ananya.iyer")
    assert plans.approve(first.plan_id, "meera.bhatt").status is PlanStatus.APPROVED
    assert plans.approve(second.plan_id, "meera.bhatt").status is PlanStatus.APPROVED
    assert {FIRST, officer} <= crew_on(mem)[0]


def test_a_plan_goes_stale_when_its_crew_is_no_longer_legal(mem, plans):
    plan = plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    mem.mark_unavailable(FIRST, date(2026, 11, 5), date(2026, 11, 6), "sick", "c1157.reddy")
    stale = plans.approve(plan.plan_id, "rohit.malhotra")
    assert stale.status is PlanStatus.STALE and "no longer legal" in stale.decision_note
    assert FIRST not in crew_on(mem)[0]


def test_reject_and_send_back_need_words_and_withdraw_is_the_submitters(plans):
    plan = plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    with pytest.raises(PlanError, match="say why"):
        plans.reject(plan.plan_id, "rohit.malhotra", "")
    with pytest.raises(PlanError, match="only ananya.iyer"):
        plans.withdraw(plan.plan_id, "rohit.malhotra")
    back = plans.send_back(plan.plan_id, "rohit.malhotra", "use the standby, keep the day off")
    assert back.status is PlanStatus.SENT_BACK
    assert [h["what"] for h in back.history] == ["submitted", "sent_back"]

    again = plans.submit(TRIP, SICK, SECOND, "ananya.iyer")
    assert plans.withdraw(again.plan_id, "ananya.iyer").status is PlanStatus.WITHDRAWN
    with pytest.raises(PlanError, match="not waiting"):
        plans.approve(again.plan_id, "rohit.malhotra")


def test_an_accepted_callout_waits_for_approval_then_fills(mem, desk, plans):
    callout = desk.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")
    accepted = desk.respond(callout.callout_id, FIRST, accept=True)
    assert accepted.status is Status.ACCEPTED and accepted.plan_id
    assert SICK in crew_on(mem)[0]
    [plan] = plans.waiting()
    assert plan.source == callout.callout_id and plan.submitted_by == "ananya.iyer"

    plans.approve(plan.plan_id, "rohit.malhotra")
    filled = desk.get(callout.callout_id)
    assert filled.status is Status.FILLED and filled.filled_by == FIRST
    assert FIRST in crew_on(mem)[0]


def test_a_rejected_callout_plan_asks_the_next_crew_member(desk, plans):
    callout = desk.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")
    desk.respond(callout.callout_id, FIRST, accept=True)
    [plan] = plans.waiting()
    plans.reject(plan.plan_id, "rohit.malhotra", "she flew a night duty, use someone fresher")
    moved = desk.get(callout.callout_id)
    assert moved.status is Status.OFFERED
    assert moved.offers[0].answer == "not approved"
    assert moved.current.crew_id == SECOND


def test_the_tools_submit_and_review_plans(mem, desk):
    box = ToolBox()
    _, version = mem.trip(TRIP)
    args = {
        "trip_id": TRIP,
        "replace_crew_id": SICK,
        "with_crew_id": FIRST,
        "expected_version": version,
        "reason": "medical lapses",
    }
    submitted = box.call("assign_crew", args, Context(mem, IYER, desk=desk))
    assert submitted["status"] == "submitted" and "not yet applied" in submitted["note"]
    waiting = box.call("plan_status", {}, Context(mem, REDDY, desk=desk))["plans"]
    assert [p["plan_id"] for p in waiting] == [submitted["plan_id"]]

    review = {"plan_id": submitted["plan_id"], "action": "approve"}
    own = box.call("review_plan", review, Context(mem, IYER, desk=desk))
    assert "only an approver" in own["error"]
    other = box.call("review_plan", review, Context(mem, REDDY, desk=desk))
    assert "only an approver" in other["error"]
    done = box.call("review_plan", review, Context(mem, NAIR, desk=desk))
    assert done["status"] == "approved" and done["decided_by"] == "meera.bhatt"


def test_plans_survive_a_restart(mem, outbox, plans):
    plan = plans.submit(TRIP, SICK, FIRST, "ananya.iyer")
    fresh = PlanDesk(mem, outbox, docs=mem)
    assert fresh.get(plan.plan_id).status is PlanStatus.SUBMITTED
    assert fresh.approve(plan.plan_id, "rohit.malhotra").status is PlanStatus.APPROVED
    assert FIRST in crew_on(mem)[0]
