from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import SEED_DIR

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.domain import Rank
from crewpay.engine import NotFound, assess
from crewpay.engine.clock import IST
from crewpay.engine.cover import find_cover
from crewpay.engine.watch import scan
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

TRIP, SICK = "T-1018", "C-1111"  # T-1018 flies 5-6 Nov
FIRST, SECOND = "C-1157", "C-1167"  # the two cheapest legal captains for it
NOV5, NOV6, NOV10 = date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 10)
CONTROLLER = Caller("ananya.iyer", Role.CONTROLLER)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


def test_unavailability_is_stored_applied_and_cleared(store):
    before = store.roster_version()
    report = store.mark_unavailable(FIRST, NOV5, NOV6, "sick", "ananya.iyer")
    assert store.roster_version() == before + 1
    assert [u.report_id for u in store.unavailability(FIRST)] == [report.report_id]
    assert store.operation().unavailable[FIRST] == (
        (NOV5, report.report_id),
        (NOV6, report.report_id),
    )

    store.clear_unavailable(report.report_id)
    assert store.unavailability() == [] and FIRST not in store.operation().unavailable
    with pytest.raises(NotFound):
        store.clear_unavailable(report.report_id)
    with pytest.raises(NotFound):
        store.mark_unavailable("C-9999", NOV5, NOV6, "sick", "ananya.iyer")


def test_an_unavailable_choice_is_withdrawn_not_silently_dropped(mem):
    mem.mark_unavailable(FIRST, NOV5, NOV6, "sick", "c1157.reddy")
    cover = find_cover(mem.operation(), TRIP, Rank.CAPTAIN, SICK)
    assert FIRST not in {c.crew_id for c in cover.choices}
    assert cover.best.crew_id == SECOND
    [w] = cover.withdrawn
    assert (w.choice.crew_id, w.choice.cost) == (FIRST, 20840)
    assert cover.why_not(FIRST).reasons[0].startswith("reported unavailable (U-")


def test_dates_that_miss_the_trip_change_nothing(mem):
    mem.mark_unavailable(FIRST, NOV10, NOV10, "family", "c1157.reddy")
    cover = find_cover(mem.operation(), TRIP, Rank.CAPTAIN, SICK)
    assert cover.best.crew_id == FIRST and cover.withdrawn == ()


def test_an_open_offer_moves_on_the_moment_they_report(mem):
    outbox = MemoryOutbox()
    desk = CalloutDesk(mem, outbox, clock=lambda: datetime(2026, 10, 29, 18, 30, tzinfo=UTC))
    callout = desk.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")
    assert callout.current.crew_id == FIRST

    mem.mark_unavailable(FIRST, NOV10, NOV10, "family", "c1157.reddy")
    assert desk.withdraw(FIRST) == [] and callout.current.crew_id == FIRST  # other dates

    mem.mark_unavailable(FIRST, NOV5, NOV5, "sick", "c1157.reddy")
    assert desk.withdraw(FIRST) == [callout]
    assert [(o.crew_id, o.answer) for o in callout.offers] == [
        (FIRST, "unavailable"),
        (SECOND, None),
    ]
    assert outbox.sent(DESK)[-1].subject == "Offer withdrawn: Captain on T-1018"
    assert outbox.sent(SECOND)


def test_the_watch_raises_the_trips_an_unavailable_person_leaves(mem):
    mem.mark_unavailable("C-1191", NOV5, NOV5, "sick", "ananya.iyer")
    trips = {d.trip_id for d in mem.operation().state("C-1191").rostered() if d.day == NOV5}
    raised = {i.trip_id for i in scan(mem.operation()) if i.crew_id == "C-1191"}
    assert trips and trips <= raised


def test_crew_report_for_themselves_and_offers_show_struck_through(mem):
    box = ToolBox()
    desk = CalloutDesk(mem, MemoryOutbox())
    opened = box.call(
        "start_callout",
        {"trip_id": TRIP, "replace_crew_id": SICK},
        Context(mem, CONTROLLER, desk=desk),
    )
    reddy = Caller("c1157.reddy", Role.CREW, FIRST)
    reported = box.call(
        "report_unavailable",
        {
            "first_day": "2026-11-05",
            "last_day": "2026-11-06",
            "reason": "fever",
            "crew_id": "C-1167",
        },  # a crew member cannot report someone else: ignored
        Context(mem, reddy, desk=desk),
    )
    assert reported["crew_id"] == FIRST
    assert reported["callout_offers_withdrawn"] == [opened["callout_id"]]

    cover = box.call(
        "find_cover",
        {"trip_id": TRIP, "rank": "Captain", "vacated_by": SICK},
        Context(mem, CONTROLLER, desk=desk),
    )
    [struck] = cover["withdrawn"]
    assert (struck["crew_id"], struck["withdrawn"]) == (FIRST, True)
    assert struck["reason"] == "fever (reported by c1157.reddy)"
    profile = box.call("crew_profile", {"crew_id": FIRST}, Context(mem, reddy))
    assert profile["unavailable"][0]["reason"] == "fever"

    cleared = box.call(
        "clear_unavailable", {"report_id": reported["report_id"]}, Context(mem, CONTROLLER)
    )
    assert cleared["cleared"] is True
    again = box.call(
        "find_cover",
        {"trip_id": TRIP, "rank": "Captain", "vacated_by": SICK},
        Context(mem, CONTROLLER),
    )
    assert again["choices"][0]["crew_id"] == FIRST and "withdrawn" not in again


def test_a_controller_must_say_who_and_reports_their_own_trips(mem):
    box = ToolBox()
    ctx = Context(mem, CONTROLLER)
    args = {"first_day": "2026-11-05", "last_day": "2026-11-08", "reason": "sick"}
    assert "crew_id" in box.call("report_unavailable", args, ctx)["error"]
    done = box.call("report_unavailable", {**args, "crew_id": SICK}, ctx)
    assert done["own_trips_now_short"] == ["T-1009", "T-1010", "T-1018"]
    someone_else = Context(mem, Caller("c1157.reddy", Role.CREW, FIRST))
    refused = box.call("clear_unavailable", {"report_id": done["report_id"]}, someone_else)
    assert "only withdraw their own" in refused["error"]
    own = Context(mem, Caller("pooja", Role.CREW, SICK))
    assert box.call("clear_unavailable", {"report_id": done["report_id"]}, own)["cleared"]


def out_sick(store, crew_id, trip_id):
    findings = assess(store.operation(), crew_id, trip_id).blockers
    return [f for f in findings if f.detail.startswith("reported unavailable")]


def test_taken_ill_part_way_through_a_day_still_flies_what_ends_before(mem):
    day = next(d for d in mem.operation().days_of(TRIP) if d.day == NOV5)
    box, ctx = ToolBox(), Context(mem, CONTROLLER)
    after = (day.release + timedelta(minutes=30)).astimezone(IST).strftime("%H:%M")
    args = {"first_day": "2026-11-05", "last_day": "2026-11-05", "reason": "fever"}
    late = box.call(
        "report_unavailable", {**args, "crew_id": SICK, "kind": "sick", "since": after}, ctx
    )
    assert late["kind"] == "sick" and late["own_trips_now_short"] == []
    assert not out_sick(mem, SICK, TRIP)
    box.call("clear_unavailable", {"report_id": late["report_id"]}, ctx)

    before = (day.release - timedelta(hours=1)).astimezone(IST).strftime("%H:%M")
    early = box.call("report_unavailable", {**args, "crew_id": SICK, "since": before}, ctx)
    assert early["own_trips_now_short"] == [TRIP]
    assert out_sick(mem, SICK, TRIP)
