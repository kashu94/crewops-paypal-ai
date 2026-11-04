import pytest
from conftest import SEED_DIR

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.ops import CalloutDesk, MemoryOutbox
from crewpay.ops.reports import ReportDesk
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

STATION = Caller("imran.sheikh", Role.REPORTER)
APPROVER = Caller("meera.bhatt", Role.APPROVER)
AOG = {
    "kind": "aircraft_technical",
    "tail": "VT-KFA",
    "start": "2026-11-04T06:00",
    "end": "2026-11-04T09:00",
}
GATES = {
    "kind": "gate_closure",
    "station": "HYD",
    "gates": ["G1", "G2"],
    "start": "2026-11-05T06:00",
    "end": "2026-11-05T09:00",
}


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


def controller(report):
    return Caller(report["desk"]["controller"], Role.CONTROLLER)


def legs_of(mem, flights):
    return [leg for leg in mem.operation().legs.values() if leg.flight_no in flights]


def test_an_aircraft_swap_changes_the_flights_only_when_the_approver_confirms(
    box, ctx, mem, outbox
):
    report = box.call("report_disruption", AOG, ctx(STATION))
    options = report["analysis"]["options"]
    spare = next(i for i, o in enumerate(options) if o["way"] == "spare")
    taken = report["analysis"]["flights_taken"]
    rid = report["report_id"]

    resolved = box.call(
        "close_disruption",
        {"report_id": rid, "note": f"Plan: {options[spare]['summary']}.", "option": spare},
        ctx(controller(report)),
    )
    assert resolved["status"] == "resolved"
    assert resolved["changes"] == [f"{f} on 04 Nov flies on VT-SPA" for f in taken]
    before = {leg.leg_id: leg.tail for leg in mem.operation().legs.values()}
    assert {before[leg.leg_id] for leg in legs_of(mem, taken) if leg.day.day == 4} == {"VT-KFA"}

    done = box.call("confirm_resolution", {"report_id": rid}, ctx(APPROVER))
    assert done["status"] == "closed" and len(done["flights_changed"]) == len(taken)
    moved = [leg for leg in legs_of(mem, taken) if leg.day.day == 4]
    assert {leg.tail for leg in moved} == {"VT-SPA"}
    others = [leg for leg in legs_of(mem, taken) if leg.day.day != 4]
    assert {leg.tail for leg in others} == {"VT-KFA"}, "only the first day's flights move"

    trip = next(t for t in mem.operation().trips.values() if moved[0].leg_id in t.leg_ids)
    crew = trip.crew[0].crew_id
    told = outbox.sent(crew)
    assert told and "flies on VT-SPA" in told[-1].body
    mine = box.call("find_trips", {"crew_id": crew, "limit": 30}, ctx(APPROVER))["trips"]
    shown = next(t for t in mine if t["trip_id"] == trip.trip_id)
    assert "VT-SPA" in shown["tail"]
    assert any(f["tail"] == "VT-SPA" for f in shown["flights"])


def test_cancelling_the_flights_takes_them_out_of_the_operation(box, ctx, mem):
    report = box.call("report_disruption", AOG, ctx(STATION))
    options = report["analysis"]["options"]
    cancel = next(i for i, o in enumerate(options) if o["way"] == "cancel")
    rid = report["report_id"]
    box.call(
        "close_disruption",
        {"report_id": rid, "note": "Plan: cancel.", "option": cancel},
        ctx(controller(report)),
    )
    assert not mem.operation().cancelled, "nothing is cancelled before the approver confirms"
    box.call("confirm_resolution", {"report_id": rid}, ctx(APPROVER))
    gone = mem.operation().cancelled
    assert gone and all(mem.operation().legs[leg_id].cancelled for leg_id in gone)
    catalog = box.call("catalog", {}, ctx(STATION))
    assert not {f["leg_id"] for f in catalog["flights"]} & gone, "forms no longer offer them"


def test_a_returned_resolution_changes_nothing(box, ctx, mem):
    report = box.call("report_disruption", AOG, ctx(STATION))
    rid = report["report_id"]
    box.call(
        "close_disruption",
        {"report_id": rid, "note": "Plan: spare.", "option": 1},
        ctx(controller(report)),
    )
    back = box.call(
        "return_resolution", {"report_id": rid, "note": "use the free aircraft"}, ctx(APPROVER)
    )
    assert back["status"] == "in_progress" and "changes" not in back
    assert "VT-SPA" not in {leg.tail for leg in mem.operation().legs.values()}


def test_a_gate_closure_moves_each_flight_to_its_chosen_gate_on_approval(box, ctx, mem):
    report = box.call("report_disruption", GATES, ctx(STATION))
    moves = report["analysis"]["moves"]
    assert moves
    rid = report["report_id"]
    first = moves[0]
    wrong = box.call(
        "close_disruption",
        {"report_id": rid, "note": "gates", "gates": {first["leg_id"]: "Z9"}},
        ctx(controller(report)),
    )
    assert "not Z9" in wrong["error"]

    box.call("close_disruption", {"report_id": rid, "note": "gates"}, ctx(controller(report)))
    assert mem.operation().legs[first["leg_id"]].gate == first["from_gate"]
    box.call("confirm_resolution", {"report_id": rid}, ctx(APPROVER))
    legs = mem.operation().legs
    assert all(legs[m["leg_id"]].gate == m["to_gate"] for m in moves)


def test_a_reported_gate_change_is_made_when_confirmed(box, ctx, mem):
    op = mem.operation()
    leg = next(
        leg
        for leg in sorted(op.legs.values(), key=lambda leg: leg.off_block)
        if leg.origin == "HYD" and leg.gate and leg.off_block > op.now
    )
    free = next(g.gate for g in op.gates["HYD"] if g.gate != leg.gate and str(g.kind) == "contact")
    report = box.call(
        "report_disruption",
        {"kind": "gate_change", "leg_id": leg.leg_id, "gate": free},
        ctx(STATION),
    )
    rid = report["report_id"]
    box.call("close_disruption", {"report_id": rid, "note": "moved"}, ctx(controller(report)))
    assert mem.operation().legs[leg.leg_id].gate == leg.gate
    box.call("confirm_resolution", {"report_id": rid}, ctx(APPROVER))
    assert mem.operation().legs[leg.leg_id].gate == free
