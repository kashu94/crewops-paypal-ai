import json

import pytest
from conftest import SEED_DIR

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.agent.desk_tools import ALL_TOOLS
from crewpay.domain import Rank
from crewpay.engine.cover import find_cover
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

LAPSED = "C-1111"  # medical lapses 2026-11-05; planted in the demo data
TRIP = "T-1018"

CONTROLLER = Caller("ananya.iyer", Role.CONTROLLER)
VIEWER = Caller("ops.board", Role.AUDITOR)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def box():
    return ToolBox()


def call(box, store, name, caller=CONTROLLER, **args):
    result = box.call(name, json.dumps(args), Context(store, caller))
    json.dumps(result)  # every result must be plain JSON
    return result


def test_each_role_sees_only_its_tools(box):
    names = {role: {t.name for t in box.available(Caller("u", role, "C-1111"))} for role in Role}
    admin_only = {
        "set_rule_limit",
        "revoke_report",
        "team_accounts",
        "create_account",
        "set_account",
        "reset_password",
        "alert_addresses",
        "add_alert_address",
        "remove_alert_address",
        "upload_roster",
    }
    approvals = {"confirm_resolution", "return_resolution", "reassign_disruption"}
    filing = {"report_disruption"}  # the station files; crew control works them
    # approvers act on money, not controllers
    pay_writes = {"release_payment", "cancel_payment", "confirm_approval", "cancel_approval"}
    payee_writes = {"set_payout_email"}  # crew themselves, or an admin
    assert (
        names[Role.CONTROLLER]
        == {t.name for t in ALL_TOOLS} - admin_only - approvals - filing - pay_writes - payee_writes
    )
    controller_writes = {
        "assign_crew",
        "start_callout",
        "answer_callout",
        "report_unavailable",
        "clear_unavailable",
        "submit_report_plan",
        "close_disruption",
    }
    assert names[Role.APPROVER] == names[Role.CONTROLLER] - controller_writes | approvals | (
        pay_writes
    )
    assert names[Role.AUDITOR] == names[Role.APPROVER] - approvals - pay_writes - {
        "decide_callout",
        "review_plan",
    }
    assert names[Role.ADMIN] == names[Role.AUDITOR] | admin_only | {
        "report_disruption",
        "reassign_disruption",
        "cancel_payment",
        "set_payout_email",
    }
    assert names[Role.SUPER_ADMIN] == names[Role.ADMIN]
    assert names[Role.SPEAKER] == {"my_queue", "about_me"}
    assert names[Role.REPORTER] == {
        "search_crew",
        "find_trips",
        "trip_details",
        "gate_status",
        "report_unavailable",
        "clear_unavailable",
        "report_disruption",
        "my_reports",
        "show_disruption",
        "contacts",
        "my_queue",
        "about_me",
        "operations",
        "catalog",
    }
    assert names[Role.CREW] == {
        "find_trips",
        "trip_details",
        "crew_profile",
        "check_legality",
        "callout_status",
        "answer_callout",
        "report_unavailable",
        "clear_unavailable",
        "report_disruption",
        "my_reports",
        "show_disruption",
        "crew_risk",
        "my_queue",
        "about_me",
        "crew_payments",
        "payment_status",
        "payout_account",
        "set_payout_email",
    }


def test_specs_are_chat_tools_with_object_parameters(box):
    for spec in box.specs(CONTROLLER):
        assert spec["type"] == "function"
        params = spec["function"]["parameters"]
        assert params["type"] == "object" and params["properties"]
        assert '"title"' not in json.dumps(params)


def test_a_crew_member_only_sees_themselves_and_their_trips(box, mem):
    me = Caller("pooja", Role.CREW, LAPSED)
    assert "error" not in call(box, mem, "crew_profile", me, crew_id=LAPSED)
    assert "own record" in call(box, mem, "crew_profile", me, crew_id="C-1157")["error"]

    trips = call(box, mem, "find_trips", me, crew_id="C-1157")["trips"]
    assert {t["trip_id"] for t in trips} == {"T-1018", "T-1009", "T-1010"}

    other = next(t for t in mem.dataset().trips if LAPSED not in {s.crew_id for s in t.crew})
    assert "trips they fly" in call(box, mem, "trip_details", me, trip_id=other.trip_id)["error"]
    assert "cannot use" in call(box, mem, "find_cover", me, trip_id=TRIP, rank="Captain")["error"]


def test_bad_calls_come_back_as_errors_the_model_can_read(box, mem):
    ctx = Context(mem, VIEWER)
    assert "no tool named" in box.call("delete_roster", "{}", ctx)["error"]
    assert "not valid JSON" in box.call("trip_details", "{trip", ctx)["error"]
    assert "trip_id" in box.call("trip_details", '{"trip_id": "1018"}', ctx)["error"]
    assert "T-9999" in box.call("trip_details", '{"trip_id": "T-9999"}', ctx)["error"]
    assert "auditor role cannot use assign_crew" in box.call("assign_crew", "{}", ctx)["error"]


def test_search_finds_crew_by_name_or_id(box, mem):
    by_name = call(box, mem, "search_crew", query="a. nair")["matches"]
    assert [m["crew_id"] for m in by_name] == [LAPSED]
    by_id = call(box, mem, "search_crew", query=LAPSED.lower())["matches"]
    assert [m["name"] for m in by_id] == ["A. Nair"]
    captains = call(box, mem, "search_crew", query="re", rank="Captain", airline="sy", limit=25)
    assert {m["rank"] for m in captains["matches"]} == {"Captain"}
    assert {m["airline"] for m in captains["matches"]} == {"SY"}
    assert "at least 2" in call(box, mem, "search_crew", query="a")["error"]


def test_legality_reports_the_lapsed_medical(box, mem):
    result = call(box, mem, "check_legality", crew_id=LAPSED, trip_id=TRIP)
    assert result["legal"] is False
    assert [(b["rule"], b["day"]) for b in result["blockers"]] == [("FDTL-08", "2026-11-05")]
    assert len({f["rule"] for f in result["findings"]}) == 9


def test_cover_lists_cheapest_first_and_always_ends_with_cancelling(box, mem):
    op = mem.operation()
    excluded = find_cover(op, TRIP, Rank.CAPTAIN, LAPSED).excluded[0]
    result = call(
        box,
        mem,
        "find_cover",
        trip_id=TRIP,
        rank="Captain",
        vacated_by=LAPSED,
        explain=[excluded.crew_id],
        limit=2,
    )
    costs = [c["cost_inr"] for c in result["choices"]]
    assert costs == sorted(costs) and len(costs) == 3
    assert result["choices"][-1]["way"] == "Cancel the trip"
    assert result["why_not"][excluded.crew_id]["reasons"] == list(excluded.reasons)


def test_an_absence_is_covered_without_using_anyone_twice(box, mem):
    result = call(
        box, mem, "absence_impact", crew_id=LAPSED, first_day="2026-11-05", last_day="2026-11-08"
    )
    assert [g["trip_id"] for g in result["seats_left_empty"]] == ["T-1018", "T-1009", "T-1010"]
    crew = [p["crew_id"] for p in result["plan"]]
    assert len(set(crew)) == len(crew) == 3 and LAPSED not in crew
    assert result["total_cost_inr"] == sum(p["cost_inr"] for p in result["plan"])
    assert (
        "before"
        in call(
            box,
            mem,
            "absence_impact",
            crew_id=LAPSED,
            first_day="2026-11-08",
            last_day="2026-11-05",
        )["error"]
    )


def test_assigning_cover_changes_the_roster_once(box, mem):
    best = call(box, mem, "find_cover", trip_id=TRIP, rank="Captain", vacated_by=LAPSED)
    pick = best["choices"][0]["crew_id"]
    args = dict(
        trip_id=TRIP,
        replace_crew_id=LAPSED,
        with_crew_id=pick,
        expected_version=1,
        reason="medical lapses 5 Nov",
    )

    done = call(box, mem, "assign_crew", **args)
    assert (done["version"], done["assigned"]["crew_id"]) == (2, pick)
    crew = {c["crew_id"] for c in call(box, mem, "trip_details", trip_id=TRIP)["crew"]}
    assert pick in crew and LAPSED not in crew
    [change] = call(box, mem, "roster_changes", trip_id=TRIP)["changes"]
    assert (change["by"], change["added"], change["removed"]) == ("ananya.iyer", [pick], [LAPSED])

    again = call(
        box, mem, "assign_crew", **{**args, "replace_crew_id": pick, "with_crew_id": LAPSED}
    )
    assert "not legal cover" in again["error"]
    stale = dict(args, replace_crew_id=pick, with_crew_id=best["choices"][1]["crew_id"])
    assert "trip_details" in call(box, mem, "assign_crew", **stale)["error"]
    assert mem.roster_version() == 2


def test_assigning_someone_the_engine_rules_out_is_refused(box, mem):
    excluded = find_cover(mem.operation(), TRIP, Rank.CAPTAIN, LAPSED).excluded[0]
    result = call(
        box,
        mem,
        "assign_crew",
        trip_id=TRIP,
        replace_crew_id=LAPSED,
        with_crew_id=excluded.crew_id,
        expected_version=1,
        reason="test",
    )
    assert "not legal cover" in result["error"]
    assert mem.changes() == []


def test_a_delay_is_not_blamed_for_who_is_illegal_anyway(box, mem):
    result = call(box, mem, "delay_impact", trip_id=TRIP, hours=3)
    # The lapsed medical makes LAPSED illegal on time; the delay is not its cause.
    assert result["crew_illegal_even_on_time"] == [LAPSED]
    assert LAPSED not in [b["crew_id"] for b in result["crew_over_a_limit"]]
    assert result["delay_cost_inr"] > 0


def desk_ctx(mem, caller):
    from crewpay.ops import CalloutDesk, MemoryOutbox

    desk = CalloutDesk(mem, MemoryOutbox())
    return lambda who=caller: Context(mem, who, desk=desk)


def test_open_issues_lead_with_the_lapsed_medical_and_its_cover(box, mem):
    result = call(box, mem, "open_issues", VIEWER)
    first = result["issues"][0]
    assert (first["kind"], first["crew_id"], first["trip_id"]) == ("illegal", LAPSED, TRIP)
    assert first["suggested_cost_inr"] == 20840 and first["rules"] == ["FDTL-08"]
    assert result["total"] >= len(result["issues"])


def test_fatigue_is_shown_next_to_cover_choices_that_carry_it(box, mem):
    # Covering C-1123 on T-1001 with standby C-1157 would make her 4th early start in a row.
    result = call(box, mem, "find_cover", trip_id="T-1001", rank="Captain", vacated_by="C-1123")
    best = result["choices"][0]
    assert best["crew_id"] == "C-1157"
    assert best["fatigue"] == [
        {"severity": "caution", "detail": "4th early start in a row (report before 07:00 IST)"}
    ]
    assert "fatigue" not in result["choices"][-1]  # cancelling has no crew to tire
    legality = call(box, mem, "check_legality", crew_id="C-1157", trip_id="T-1001")
    assert legality["legal"] is True and legality["fatigue"] == best["fatigue"]


def test_a_callout_runs_through_the_tools_and_never_cancels_by_itself(box, mem):
    ctx = desk_ctx(mem, CONTROLLER)
    opened = box.call("start_callout", {"trip_id": TRIP, "replace_crew_id": LAPSED}, ctx())
    callout_id, first = opened["callout_id"], opened["waiting_for"]

    me = Caller("crew", Role.CREW, first)
    mine = box.call("callout_status", {}, ctx(me))["offers_to_me"]
    assert [c["callout_id"] for c in mine] == [callout_id]
    # They see what they would be paid, and nothing about anyone else asked.
    assert "asked" not in mine[0] and mine[0]["pay_inr"] == opened["asked"][0]["cost_inr"]
    assert {line["label"] for line in mine[0]["pay_lines"]} <= {
        "callout",
        "service allowance",
        "travel allowance",
    }

    status = box.call("answer_callout", {"callout_id": callout_id, "accept": False}, ctx(me))
    while status["status"] == "offered":
        status = box.call(
            "answer_callout",
            {"callout_id": callout_id, "accept": False, "crew_id": status["waiting_for"]},
            ctx(),
        )
    assert status["status"] == "needs_decision"
    assert "Nothing has been cancelled" in status["note"]
    assert mem.changes() == []

    decide = {"callout_id": callout_id, "action": "propose_cancel"}
    assert box.call("decide_callout", decide, ctx())["status"] == "awaiting_approval"
    same = box.call("decide_callout", {**decide, "action": "approve"}, ctx())
    assert "only an approver" in same["error"]
    approver = Caller("meera.bhatt", Role.APPROVER)
    refused = box.call("decide_callout", decide, ctx(approver))
    assert "a controller proposes" in refused["error"]
    done = box.call("decide_callout", {**decide, "action": "approve"}, ctx(approver))
    assert done["status"] == "closed" and done["proposal"]["approved_by"] == "meera.bhatt"


def test_an_accepted_callout_fills_the_seat(box, mem):
    ctx = desk_ctx(mem, CONTROLLER)
    opened = box.call("start_callout", {"trip_id": TRIP, "replace_crew_id": LAPSED}, ctx())
    me = Caller("crew", Role.CREW, opened["waiting_for"])
    done = box.call("answer_callout", {"callout_id": opened["callout_id"], "accept": True}, ctx(me))
    assert done["status"] == "filled" and done["filled_by"] == opened["waiting_for"]
    assert opened["waiting_for"] in {s.crew_id for s in mem.trip(TRIP)[0].crew}


def test_callouts_need_a_desk(box, mem):
    assert "not available" in call(box, mem, "callout_status")["error"]
