from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SEED_DIR

from crewpay.domain import Rank, Seat
from crewpay.engine.cover import find_cover
from crewpay.ops import DESK, CalloutDesk, CalloutError, MemoryOutbox, Status
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

TRIP, SICK = "T-1018", "C-1111"
LEGAL_CAPTAINS = ["C-1157", "C-1167", "C-1123", "C-1177"]  # cheapest first, from find_cover


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 29, 18, 30, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def outbox():
    return MemoryOutbox()


@pytest.fixture
def desk(mem, outbox, clock):
    return CalloutDesk(mem, outbox, clock=clock)


def open_callout(desk):
    return desk.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")


def test_the_cheapest_legal_crew_member_is_asked_first(desk, outbox):
    callout = open_callout(desk)
    assert callout.status is Status.OFFERED
    assert callout.current.crew_id == LEGAL_CAPTAINS[0]
    [notice] = outbox.sent(LEGAL_CAPTAINS[0])
    assert "fly T-1018 as Captain" in notice.body and "20 minutes" in notice.body
    assert open_callout(desk) is callout  # opening the same seat again joins the running callout


def test_accepting_fills_the_seat_through_the_roster(desk, mem, outbox):
    callout = open_callout(desk)
    desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert (callout.status, callout.filled_by, callout.version) == (
        Status.FILLED,
        LEGAL_CAPTAINS[0],
        2,
    )
    crew = {s.crew_id for s in mem.trip(TRIP)[0].crew}
    assert LEGAL_CAPTAINS[0] in crew and SICK not in crew
    [change] = mem.changes(TRIP)
    assert change.actor.startswith(f"callout {callout.callout_id}") and "accepted" in change.reason
    assert outbox.sent(DESK)[-1].subject == "Filled: Captain on T-1018"


def test_a_decline_moves_to_the_next_legal_crew_member(desk):
    callout = open_callout(desk)
    desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=False)
    assert [(o.crew_id, o.answer) for o in callout.offers] == [
        (LEGAL_CAPTAINS[0], "declined"),
        (LEGAL_CAPTAINS[1], None),
    ]


def test_when_everyone_declines_nothing_is_cancelled_and_the_desk_decides(desk, mem, outbox):
    callout = open_callout(desk)
    for crew_id in LEGAL_CAPTAINS:
        desk.respond(callout.callout_id, crew_id, accept=False)

    assert callout.status is Status.NEEDS_DECISION
    assert callout.reason == "every legal crew member has been asked"
    assert [o["way"] for o in callout.options] == ["cancel"]
    assert mem.changes() == [] and mem.trip(TRIP)[1] == 1  # the trip is untouched
    notice = outbox.sent(DESK)[-1]
    assert "Nothing has been cancelled" in notice.body and "approver" in notice.body


def test_cancelling_needs_two_different_controllers(desk, mem):
    callout = open_callout(desk)
    for crew_id in LEGAL_CAPTAINS:
        desk.respond(callout.callout_id, crew_id, accept=False)

    with pytest.raises(CalloutError, match="not an option"):
        desk.propose(callout.callout_id, "one_short", by="ananya.iyer")
    desk.propose(callout.callout_id, "cancel", by="ananya.iyer")
    assert callout.status is Status.AWAITING_APPROVAL
    with pytest.raises(CalloutError, match="someone else"):
        desk.approve(callout.callout_id, by="ananya.iyer")

    desk.reject(callout.callout_id, by="rohit.malhotra")
    assert callout.status is Status.NEEDS_DECISION and callout.proposal is None
    assert not mem.operation().cancelled, "a rejected proposal cancels nothing"
    desk.propose(callout.callout_id, "cancel", by="ananya.iyer")
    desk.approve(callout.callout_id, by="rohit.malhotra")
    op = mem.operation()
    assert set(op.trip(TRIP).leg_ids) == op.cancelled, "approved: the trip's flights are cancelled"
    assert callout.status is Status.CLOSED
    assert (callout.proposal.proposed_by, callout.proposal.approved_by) == (
        "ananya.iyer",
        "rohit.malhotra",
    )
    assert mem.changes() == []  # the crew stay rostered; their flights are what is cancelled


def test_an_unanswered_offer_moves_on_after_the_deadline(desk, clock):
    callout = open_callout(desk)
    clock.advance(19)
    assert desk.tick() == []
    clock.advance(2)
    assert desk.tick() == [callout]
    assert [(o.crew_id, o.answer) for o in callout.offers] == [
        (LEGAL_CAPTAINS[0], "no answer"),
        (LEGAL_CAPTAINS[1], None),
    ]
    with pytest.raises(CalloutError, match="no open offer"):
        desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)


def test_a_late_answer_is_refused_and_the_offer_moves_on(desk, clock, mem):
    callout = open_callout(desk)
    clock.advance(25)
    with pytest.raises(CalloutError, match="expired"):
        desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert callout.current.crew_id == LEGAL_CAPTAINS[1]
    assert mem.changes() == []


def test_someone_no_longer_legal_cannot_accept(desk, monkeypatch, mem):
    callout = open_callout(desk)
    judge = desk._cover

    def first_now_illegal(c):
        cover = judge(c)
        return replace(
            cover, choices=tuple(x for x in cover.choices if x.crew_id != LEGAL_CAPTAINS[0])
        )

    # Between the offer and the answer, the engine stops offering them (say a new duty clashed).
    monkeypatch.setattr(desk, "_cover", first_now_illegal)
    desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert callout.offers[0].answer == "no longer legal"
    assert callout.current.crew_id == LEGAL_CAPTAINS[1]
    assert mem.changes() == []


def test_a_seat_someone_else_already_filled_closes_the_callout(desk, mem):
    callout = open_callout(desk)
    trip, version = mem.trip(TRIP)
    crew = [Seat(crew_id="C-1167", rank=s.rank) if s.crew_id == SICK else s for s in trip.crew]
    mem.change_crew(TRIP, crew, version, "rohit.malhotra", "filled by phone")
    desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert callout.status is Status.CLOSED and "no longer on" in callout.reason
    assert len(mem.changes()) == 1


def test_a_trip_that_has_reported_goes_straight_to_the_desk(mem, outbox, clock):
    report = mem.operation().days_of(TRIP)[0].report
    desk = CalloutDesk(mem, outbox, clock=clock, operation_clock=lambda: report)
    callout = open_callout(desk)
    assert (callout.status, callout.options) == (Status.NEEDS_DECISION, [])
    assert callout.reason == "the trip has already reported"


def test_only_crew_on_the_trip_can_be_replaced(desk):
    with pytest.raises(CalloutError, match="not on T-1018"):
        desk.open(TRIP, Rank.CAPTAIN, "C-1157", by="ananya.iyer")


def test_accepting_a_positioned_seat_tells_the_desk_what_to_book(desk, mem, outbox, monkeypatch):
    callout = open_callout(desk)
    judge = desk._cover

    def first_positions_in(c):
        cover = judge(c)
        first = replace(cover.choices[0], positioning_leg="KF502-2026-11-05", slip_hours=1.5)
        return replace(cover, choices=(first, *cover.choices[1:]))

    monkeypatch.setattr(desk, "_cover", first_positions_in)
    desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert callout.to_arrange[0].startswith("book a positioning seat on KF502")
    assert "T-1018 departs 1h30m late" in callout.to_arrange[1]
    assert "To do: book a positioning seat on KF502" in outbox.sent(DESK)[-1].body
    assert "book a positioning seat" in mem.changes(TRIP)[0].reason


def test_nobody_is_offered_two_seats_at_once(desk, mem):
    first = open_callout(desk)
    trip = next(
        t
        for t in mem.dataset().trips
        if t.trip_id != TRIP
        and any(s.rank is Rank.CAPTAIN for s in t.crew)
        and mem.operation().days_of(t.trip_id)[0].report > mem.operation().now
        and first.current.crew_id
        in {
            c.crew_id
            for c in find_cover(
                mem.operation(),
                t.trip_id,
                Rank.CAPTAIN,
                next(s.crew_id for s in t.crew if s.rank is Rank.CAPTAIN),
            ).choices
        }
    )
    captain = next(s.crew_id for s in trip.crew if s.rank is Rank.CAPTAIN)
    second = desk.open(trip.trip_id, Rank.CAPTAIN, captain, by="ananya.iyer")
    assert second.current.crew_id != first.current.crew_id
