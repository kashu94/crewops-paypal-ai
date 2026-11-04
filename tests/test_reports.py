import pytest
from conftest import SEED_DIR

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox
from crewpay.ops.reports import ReportDesk
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

TRIP = "T-1018"  # flies 5-6 Nov
CAPTAIN, FIRST_OFFICER, CABIN = "C-1111", "C-1191", "C-1460"
IYER = Caller("ananya.iyer", Role.CONTROLLER)
REDDY = Caller("rohit.malhotra", Role.CONTROLLER)
RAO = Caller("divya.rao", Role.CONTROLLER)
NAIR = Caller("meera.bhatt", Role.APPROVER)
STATION = Caller("imran.sheikh", Role.REPORTER)
MENON = Caller("priya.desai", Role.ADMIN)
ANJALI = Caller("c1157.reddy", Role.CREW, "C-1157")


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def outbox():
    return MemoryOutbox()


@pytest.fixture
def ctx(mem, outbox):
    desk = CalloutDesk(mem, outbox, docs=mem, four_eyes=True)
    reports = ReportDesk(mem, outbox, SEED_DIR)
    return lambda caller: Context(mem, caller, desk=desk, reports=reports)


@pytest.fixture
def box():
    return ToolBox()


def sick(*crew_ids, **more):
    return {
        "kind": "crew_unavailable",
        "crew_ids": list(crew_ids),
        "first_day": "2026-11-05",
        "last_day": "2026-11-06",
        "reason_kind": "sick",
        **more,
    }


def test_several_sick_crew_in_one_report_are_covered_together(box, ctx, mem, outbox):
    report = box.call("report_disruption", sick(CAPTAIN, FIRST_OFFICER, CABIN), ctx(STATION))
    assert report["status"] == "open" and report["crew_ids"] == [CAPTAIN, FIRST_OFFICER, CABIN]
    assert report["title"].startswith("Sick: 3 crew")
    # Every one of them is now off the roster for those days.
    assert {u.crew_id for u in mem.unavailability()} == {CAPTAIN, FIRST_OFFICER, CABIN}

    analysis = report["analysis"]
    seats = {(s["trip_id"], s["replacing"]) for s in analysis["seats"]}
    assert {(TRIP, CAPTAIN), (TRIP, FIRST_OFFICER), (TRIP, CABIN)} <= seats
    fills = [f for f in analysis["plan"]["fills"] if f.get("crew_id")]
    used = [f["crew_id"] for f in fills]
    assert len(used) == len(set(used)), "nobody is used for two seats"
    assert not {CAPTAIN, FIRST_OFFICER, CABIN} & set(used), "nobody covers for another"
    assert all("risk" in f for f in fills)
    assert analysis["plan"]["total_cost"] == sum(f["cost"] for f in analysis["plan"]["fills"])

    assert report["severity"]["level"] in ("high", "medium", "low")
    assert [t["team"] for t in report["teams"]] == ["station_ops"]
    assert report["teams"][0]["contact"]["name"]
    assert any(n.subject.startswith("New disruption") for n in outbox.sent(DESK))
    assert outbox.sent("team:station_ops")


def test_new_reports_go_to_the_least_busy_desk(box, ctx):
    desks = [
        box.call("report_disruption", sick(crew), ctx(STATION))["desk"]["desk_id"]
        for crew in (CAPTAIN, FIRST_OFFICER, CABIN)
    ]
    assert desks == ["desk-1", "desk-2", "desk-3"]
    load = box.call("desk_load", {}, ctx(NAIR))["desks"]
    assert [d["open"] for d in load] == [1, 1, 1]


def test_a_report_runs_from_plan_to_sign_off(box, ctx, mem):
    report = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    rid = report["report_id"]
    assert report["desk"]["controller"] == "ananya.iyer"
    assert box.call("show_disruption", {"report_id": rid}, ctx(IYER))["can"]["plan"]

    # Only the owning desk works it.
    other = box.call("submit_report_plan", {"report_id": rid}, ctx(REDDY))
    assert "only its controller" in other["error"]

    done = box.call("submit_report_plan", {"report_id": rid}, ctx(IYER))
    assert done["report"]["status"] == "in_progress"
    assert all(p["report_id"] == rid for p in done["plans"])
    assert [p["status"] for p in done["plans"]] and all(
        p["status"] == "submitted" for p in done["plans"]
    )
    # Seats with a plan are in hand: submitting again adds nothing.
    again = box.call("submit_report_plan", {"report_id": rid}, ctx(IYER))
    assert again["plans"] == [] and all("in hand" in s["why"] for s in again["skipped"])

    for plan in done["plans"]:
        review = {"plan_id": plan["plan_id"], "action": "approve"}
        assert box.call("review_plan", review, ctx(NAIR))["status"] == "approved"
    after = box.call("show_disruption", {"report_id": rid}, ctx(IYER))
    assert after["seats_open"] == 0

    closed = box.call("close_disruption", {"report_id": rid, "note": "covered"}, ctx(IYER))
    assert closed["status"] == "resolved" and closed["can"]["close"] is False
    assert "error" in box.call("confirm_resolution", {"report_id": rid}, ctx(IYER))
    back = {"report_id": rid, "note": "add the hotel booking"}
    assert box.call("return_resolution", back, ctx(NAIR))["status"] == "in_progress"
    box.call("close_disruption", {"report_id": rid, "note": "hotel booked"}, ctx(IYER))
    final = box.call("confirm_resolution", {"report_id": rid}, ctx(NAIR))
    assert final["status"] == "closed"
    assert [h["what"] for h in final["history"]] == [
        "reported",
        "assigned",
        "plan submitted",
        "resolved",
        "returned",
        "resolved",
        "closed",
    ]


def test_who_may_file_what(box, ctx, mem):
    mine = box.call(
        "report_disruption", sick(first_day="2026-11-09", last_day="2026-11-10"), ctx(ANJALI)
    )
    assert mine["crew_ids"] == ["C-1157"]
    someone = box.call("report_disruption", sick(CAPTAIN), ctx(ANJALI))
    assert "only report themselves" in someone["error"]
    delay = {"kind": "flight_delay", "trip_id": TRIP, "hours": 2}
    assert "only report themselves" in box.call("report_disruption", delay, ctx(ANJALI))["error"]
    leave = {
        "kind": "planned_leave",
        "crew_ids": [CABIN],
        "first_day": "2026-11-07",
        "last_day": "2026-11-09",
        "leave": "annual_leave",
    }
    assert "booked by an admin" in box.call("report_disruption", leave, ctx(STATION))["error"]
    booked = box.call("report_disruption", leave, ctx(MENON))
    assert booked["teams"][0]["team"] == "crew_records"
    # Crew see their own report, and only theirs.
    assert box.call("show_disruption", {"report_id": mine["report_id"]}, ctx(ANJALI))["title"]
    theirs = box.call("show_disruption", {"report_id": booked["report_id"]}, ctx(ANJALI))
    assert "reports you filed" in theirs["error"]
    # A report about her, filed by the station, is hers to see too.
    about = box.call("report_disruption", sick("C-1157", CAPTAIN), ctx(STATION))
    assert box.call("show_disruption", {"report_id": about["report_id"]}, ctx(ANJALI))["title"]
    ids = [r["report_id"] for r in box.call("my_reports", {}, ctx(ANJALI))["disruptions"]]
    assert about["report_id"] in ids and mine["report_id"] in ids


def test_operational_reports_carry_the_engines_analysis(box, ctx):
    delay = box.call(
        "report_disruption", {"kind": "flight_delay", "trip_id": TRIP, "hours": 3}, ctx(STATION)
    )
    assert delay["analysis"]["delay_hours"] == 3 and "crew_over_a_limit" in delay["analysis"]
    assert {t["team"] for t in delay["teams"]} >= {"customer_care", "flight_info"}

    gates = {
        "kind": "gate_closure",
        "station": "HYD",
        "gates": ["G1", "G2"],
        "start": "2026-10-30T05:00",
        "end": "2026-10-30T10:00",
    }
    closed = box.call("report_disruption", gates, ctx(STATION))
    assert closed["analysis"]["moves"] and closed["title"] == "HYD gates G1, G2 closed"
    assert {t["team"] for t in closed["teams"]} == {"gate_control", "station_ops", "flight_info"}

    bad = box.call("report_disruption", {"kind": "flight_delay", "hours": 3}, ctx(STATION))
    assert "needs trip_id" in bad["error"]


def test_an_admin_revokes_a_report_filed_in_error(box, ctx, mem):
    report = box.call("report_disruption", sick(CAPTAIN, FIRST_OFFICER), ctx(STATION))
    assert len(mem.unavailability()) == 2
    refused = box.call(
        "revoke_report", {"report_id": report["report_id"], "reason": "x"}, ctx(IYER)
    )
    assert "cannot use" in refused["error"]
    done = box.call(
        "revoke_report", {"report_id": report["report_id"], "reason": "wrong crew"}, ctx(MENON)
    )
    assert done["status"] == "revoked" and mem.unavailability() == []


def test_reassigning_moves_the_work(box, ctx):
    report = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    rid = report["report_id"]
    moved = box.call("reassign_disruption", {"report_id": rid, "desk_id": "desk-3"}, ctx(NAIR))
    assert moved["desk"]["controller"] == "divya.rao"
    assert (
        "only its controller"
        in box.call("submit_report_plan", {"report_id": rid}, ctx(IYER))["error"]
    )
    assert box.call("submit_report_plan", {"report_id": rid}, ctx(RAO))["plans"]


def test_the_queue_says_what_waits_for_each_role(box, ctx):
    report = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    queue = box.call("my_queue", {}, ctx(IYER))["items"]
    assert [i["ref"] for i in queue] == [report["report_id"]]
    box.call("submit_report_plan", {"report_id": report["report_id"]}, ctx(IYER))
    waiting = box.call("my_queue", {}, ctx(NAIR))["items"]
    assert waiting and all(i["kind"] == "plan" for i in waiting)
    assert box.call("my_queue", {}, ctx(STATION))["items"] == [], "nothing waits on the station"


def test_station_and_aircraft_reports_are_graded_from_the_engine(box, ctx):
    station = {
        "kind": "station_disruption",
        "station": "HYD",
        "start": "2026-11-05T06:00",
        "end": "2026-11-05T11:00",
        "note": "fog",
    }
    shut = box.call("report_disruption", station, ctx(STATION))
    assert shut["analysis"]["flights_held"] and shut["title"].startswith("HYD closed")
    assert "maintenance_control" not in {t["team"] for t in shut["teams"]}
    assert shut["summary"].endswith("duty limit.") or shut["summary"] == "No crew over a limit."

    aog = {
        "kind": "aircraft_technical",
        "tail": "VT-KFA",
        "start": "2026-11-05T05:00",
        "end": "2026-11-05T13:00",
    }
    fault = box.call("report_disruption", aog, ctx(STATION))
    assert fault["teams"][0]["team"] == "maintenance_control"
    assert fault["severity"]["level"] in ("high", "medium", "low")
    assert fault["severity"]["reasons"][-1].endswith("passengers affected") or fault["severity"][
        "reasons"
    ][-1].startswith("a trip")


def test_an_absence_already_recorded_is_not_recorded_twice(box, ctx, mem):
    args = {"first_day": "2026-11-05", "last_day": "2026-11-06", "reason": "food poisoning"}
    box.call("report_unavailable", {**args, "crew_id": CAPTAIN}, ctx(IYER))
    report = box.call("report_disruption", sick(CAPTAIN, CABIN), ctx(STATION))
    assert "cannot use" in box.call("report_disruption", sick(CABIN), ctx(IYER))["error"]
    assert sorted(u.crew_id for u in mem.unavailability()) == sorted([CAPTAIN, CABIN])
    assert report["crew_ids"] == [CAPTAIN, CABIN]


def test_about_me_answers_for_each_role(box, ctx):
    box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    iyer = box.call("about_me", {}, ctx(IYER))
    assert (iyer["name"], iyer["role_title"], iyer["desk"]["desk_id"]) == (
        "Ananya Iyer",
        "Crew controller",
        "desk-1",
    )
    assert iyer["waiting_on_you"] == 1 and iyer["first_things"][0].startswith("Sick:")
    reddy = box.call("about_me", {}, ctx(ANJALI))
    assert (reddy["name"], reddy["crew_id"], reddy["rank"]) == (
        "D. Reddy",
        "C-1157",
        "Captain",
    )
    assert "accept or decline a callout" in " ".join(reddy["you_can"])
    menon = box.call("about_me", {}, ctx(MENON))
    assert menon["role_title"] == "Administrator" and "relax a limit" in menon["you_cannot"][0]


def test_crew_answer_a_callout_with_a_remark_and_see_what_became_of_it(box, ctx, outbox):
    started = box.call("start_callout", {"trip_id": TRIP, "replace_crew_id": CAPTAIN}, ctx(IYER))
    assert started["waiting_for"] == "C-1157"
    assert box.call("trip_details", {"trip_id": TRIP}, ctx(ANJALI))["trip_id"] == TRIP
    mine = box.call("callout_status", {}, ctx(ANJALI))
    assert [c["callout_id"] for c in mine["offers_to_me"]] == [started["callout_id"]]
    assert "by " in outbox.sent("C-1157")[-1].body and "IST" in outbox.sent("C-1157")[-1].body

    answer = {"callout_id": started["callout_id"], "accept": False, "note": "family wedding"}
    box.call("answer_callout", answer, ctx(ANJALI))
    after = box.call("callout_status", {}, ctx(ANJALI))
    assert after["offers_to_me"] == []
    [earlier] = after["earlier_offers"]
    assert (earlier["outcome"], earlier["your_remark"]) == ("declined", "family wedding")
    declined = outbox.sent(DESK)[-1]
    assert declined.subject.startswith("Declined:") and "family wedding" in declined.body
    desk_view = box.call("callout_status", {"callout_id": started["callout_id"]}, ctx(IYER))
    assert desk_view["asked"][0]["remark"] == "family wedding"


def test_the_person_filing_can_raise_the_severity_but_not_lower_it(box, ctx):
    delay = {"kind": "flight_delay", "trip_id": TRIP, "hours": 3}
    worked_out = box.call("report_disruption", delay, ctx(STATION))
    assert worked_out["severity"]["level"] != "low"
    box.call("revoke_report", {"report_id": worked_out["report_id"], "reason": "test"}, ctx(MENON))
    lower = box.call("report_disruption", {**delay, "severity": "low"}, ctx(STATION))
    assert lower["severity"]["level"] == worked_out["severity"]["level"]
    assert lower["severity"]["reasons"][-1].startswith("reported as low by imran.sheikh")
    small = {"kind": "flight_delay", "trip_id": "T-1009", "hours": 0.25, "severity": "high"}
    higher = box.call("report_disruption", small, ctx(STATION))
    assert higher["severity"]["level"] == "high"
    assert higher["severity"]["reasons"][0] == "reported as high by imran.sheikh"


def test_the_same_thing_is_not_reported_twice(box, ctx):
    first = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    again = box.call("report_disruption", sick(CAPTAIN, FIRST_OFFICER), ctx(STATION))
    assert first["report_id"] in again["error"] and "already reported out" in again["error"]
    gates = {
        "kind": "gate_closure",
        "station": "HYD",
        "gates": ["G1"],
        "start": "2026-11-05T06:00",
        "end": "2026-11-05T09:00",
    }
    filed = box.call("report_disruption", gates, ctx(STATION))
    twice = box.call("report_disruption", {**gates, "gates": ["G1", "G2"]}, ctx(STATION))
    assert filed["report_id"] in twice["error"]


def test_crew_back_on_duty_closes_their_disruption_and_its_callouts(box, ctx, mem, outbox):
    report = box.call(
        "report_disruption",
        sick("C-1157", first_day="2026-11-08", last_day="2026-11-08"),
        ctx(ANJALI),
    )
    filed = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    started = box.call(
        "start_callout",
        {"trip_id": TRIP, "replace_crew_id": CAPTAIN, "report_id": filed["report_id"]},
        ctx(REDDY),
    )
    [mine] = [u for u in mem.unavailability() if u.crew_id == CAPTAIN]
    pooja = Caller("c1111.nair", Role.CREW, CAPTAIN)
    back = box.call("clear_unavailable", {"report_id": mine.report_id}, ctx(pooja))
    assert "callout_id" in started, started
    assert filed["report_id"] in back["stood_down"] and started["callout_id"] in back["stood_down"]
    assert (
        box.call("show_disruption", {"report_id": filed["report_id"]}, ctx(IYER))["status"]
        == "revoked"
    )
    assert (
        box.call("callout_status", {"callout_id": started["callout_id"]}, ctx(IYER))["status"]
        == "closed"
    )
    assert outbox.sent(DESK)[-1].subject == "Back on duty: A. Nair"
    assert (
        box.call("show_disruption", {"report_id": report["report_id"]}, ctx(IYER))["status"]
        == "open"
    )


def test_crew_on_different_flights_are_separate_disruptions(box, ctx, mem):
    op = mem.operation()
    other = next(
        c
        for c, state in sorted(op.crew.items())
        if c not in (CAPTAIN, FIRST_OFFICER, CABIN)
        and any(
            d.trip_id != TRIP and str(d.day) in ("2026-11-05", "2026-11-06") for d in state.duties
        )
    )
    first = box.call("report_disruption", sick(CAPTAIN, FIRST_OFFICER, other), ctx(STATION))
    assert first["crew_ids"] == [CAPTAIN, FIRST_OFFICER], "the two pilots of T-1018 stay together"
    [second] = first["also_filed"]
    assert box.call("show_disruption", {"report_id": second["report_id"]}, ctx(STATION))[
        "crew_ids"
    ] == [other]
    assert "one per flight" in first["note"]


def test_the_station_sees_a_week_of_flights_and_who_is_out(box, ctx):
    box.call("report_disruption", sick(CAPTAIN), ctx(STATION))
    week = box.call("operations", {"start": "2026-11-02", "day": "2026-11-05"}, ctx(STATION))
    assert [d["day"] for d in week["week"]][0] == "2026-11-02" and len(week["week"]) == 7
    ours = [f for f in week["flights"] if f["trip_id"] == TRIP]
    assert ours and all(
        f["captain"][0] == {"crew_id": CAPTAIN, "name": "A. Nair", "out": True} for f in ours
    )
    assert all(f["disruptions"] for f in ours), "the sick report marks the trip's flights"
    [out] = [c for c in week["crew_out"] if c["crew_id"] == CAPTAIN]
    assert out["days"] == ["2026-11-05", "2026-11-06"] and "reason" not in out
    listed = box.call("catalog", {}, ctx(STATION))
    assert TRIP in {t["trip_id"] for t in listed["trips"]}
    assert "G1" in listed["gates"]["HYD"]
    assert "cannot use" in box.call("operations", {}, ctx(ANJALI))["error"]


def test_a_plan_from_an_accepted_callout_belongs_to_its_report(box, ctx):
    rid = box.call("report_disruption", sick(CAPTAIN), ctx(STATION))["report_id"]
    started = box.call(
        "start_callout", {"trip_id": TRIP, "replace_crew_id": CAPTAIN, "report_id": rid}, ctx(IYER)
    )
    box.call("answer_callout", {"callout_id": started["callout_id"], "accept": True}, ctx(ANJALI))
    report = box.call("show_disruption", {"report_id": rid}, ctx(IYER))
    [plan] = report["plans"]
    assert (plan["source"], plan["status"], plan["report_id"]) == (
        started["callout_id"],
        "submitted",
        rid,
    )
    listed = box.call("disruptions", {"status": "active"}, ctx(IYER))["disruptions"]
    assert next(r for r in listed if r["report_id"] == rid)["plans_waiting"] == 1


def test_a_crew_member_back_on_duty_leaves_only_the_others_seats(box, ctx, mem):
    filed = box.call("report_disruption", sick(CAPTAIN, FIRST_OFFICER), ctx(STATION))
    [fo] = [u for u in mem.unavailability() if u.crew_id == FIRST_OFFICER]
    box.call(
        "clear_unavailable",
        {"report_id": fo.report_id},
        ctx(Caller("c1191.nair", Role.CREW, FIRST_OFFICER)),
    )
    report = box.call("show_disruption", {"report_id": filed["report_id"]}, ctx(IYER))
    assert report["status"] == "open", "the captain is still out"
    assert {s["replacing"] for s in report["analysis"]["seats"]} == {CAPTAIN}
    assert [c.get("back_on_duty", False) for c in report["analysis"]["crew"]] == [False, True]
    assert [len(s["choices"]) > 0 for s in report["analysis"]["seats"]] == [True]
