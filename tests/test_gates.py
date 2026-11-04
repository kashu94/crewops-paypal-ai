from datetime import datetime

import pytest

from crewpay.domain import GateKind
from crewpay.engine import NotFound
from crewpay.engine.clock import IST
from crewpay.engine.disruption import aircraft_out, station_closure
from crewpay.engine.gates import BOARDING, BUS_EXTRA, check_gate, gate_closure, late_departure


def ist(day, hh, mm=0):
    return datetime(2026, 11, day, hh, mm, tzinfo=IST)


@pytest.fixture
def op(store):
    return store.operation()


def test_every_departure_has_a_gate_and_no_two_board_at_one_gate_at_once(op):
    by_gate = {}
    for leg in op.legs.values():
        assert leg.gate, leg.leg_id
        by_gate.setdefault((leg.origin, leg.gate), []).append(leg.off_block)
    for times in by_gate.values():
        times.sort()
        assert all(b - a >= BOARDING for a, b in zip(times, times[1:], strict=False))


def test_a_closed_gate_sends_its_departure_to_the_emergency_gate_in_the_wave(op):
    # 07:00-07:30 IST: all five contact gates at Hyderabad are boarding.
    plan = gate_closure(op, "HYD", "G1", ist(5, 6, 0), ist(5, 9, 0))
    [move] = plan.moves
    assert (move.flight_no, move.to_gate, move.to_kind) == ("SY201", "E_G1", GateKind.EMERGENCY)
    assert move.delay_minutes == 0 and not plan.crew


def test_with_two_gates_shut_the_second_flight_boards_by_bus_and_crew_are_judged(op):
    plan = gate_closure(op, "HYD", ["G1", "G2"], ist(5, 6, 0), ist(5, 9, 0))
    first, second = plan.moves
    assert (first.flight_no, first.to_gate) == ("SY201", "E_G1")
    assert second.flight_no == "GL701" and second.to_kind is GateKind.REMOTE
    assert second.delay_minutes == BUS_EXTRA.total_seconds() // 60
    assert [i.trip_id for i in plan.crew] == [second.trip_id]


def test_a_gate_change_onto_a_held_gate_names_the_clash(op):
    check = check_gate(op, "SY201-2026-11-05", "G2")
    assert not check.free and check.clashes == ("GL701",)
    assert check_gate(op, "SY243-2026-11-05", "G3").free


def test_a_late_flight_moves_whoever_boards_next_at_its_gate(op):
    plan = late_departure(op, "SY201-2026-11-05", 200)
    assert [m.flight_no for m in plan.moves] == ["SY243"]
    assert plan.moves[0].from_gate == "G1" and plan.moves[0].to_gate != "G1"


def test_unknown_gates_and_stations_are_named(op):
    with pytest.raises(NotFound):
        gate_closure(op, "HYD", "99", ist(5, 6), ist(5, 7))
    with pytest.raises(NotFound):
        station_closure(op, "XXX", ist(5, 6), ist(5, 7))


def test_a_station_closure_holds_departures_and_arrivals_and_judges_the_crew(op):
    d = station_closure(op, "HYD", ist(5, 6, 30), ist(5, 9, 30))
    flights = {f.flight_no: f.minutes for f in d.flights}
    assert flights["SY201"] == 150 and flights["SY241"] == 120
    assert all(t.hours > 0 for t in d.trips)
    assert d.passengers == sum(f.booked for f in d.flights)


def test_an_aircraft_out_of_service_holds_its_day_and_blames_only_the_delay(op):
    d = aircraft_out(op, "VT-SYB", ist(5, 6), ist(5, 11))
    assert [f.flight_no for f in d.flights] == ["SY221", "SY222", "SY223"]
    assert {f.minutes for f in d.flights} == {210}
    [trip] = d.trips
    assert trip.trip_id == "T-1018"
    assert "C-1111" in trip.already_illegal  # the lapsed medical, not the delay
    assert "C-1111" not in [b.crew_id for b in trip.breaches]
