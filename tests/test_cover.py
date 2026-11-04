from datetime import UTC, datetime, time, timedelta

import pytest
from builders import DAY, Ops, at

from crewpay.domain import Rank
from crewpay.engine import build_operation
from crewpay.engine.cover import TripStarted, Way, arrangements, find_cover
from crewpay.engine.rules import CERT
from crewpay.seed import load_dataset
from crewpay.settings import ROOT

CAPTAINS_ON_TRIP = [("C-1001", Rank.CAPTAIN), ("C-1002", Rank.FIRST_OFFICER)]


def base_ops() -> Ops:
    """T-0001 on DAY with C-1001 (sick) in command; three captains who might cover."""
    ops = Ops()
    ops.person("C-1001").person("C-1002", Rank.FIRST_OFFICER)
    ops.person("C-2001").on_standby("C-2001", DAY, time(0), time(6))
    ops.person("C-2002")
    ops.person("C-2003", cert_expiry=DAY.replace(day=1))
    return ops.day_trip("T-0001", crew=CAPTAINS_ON_TRIP)


def test_standby_is_cheapest_and_cancel_is_always_last():
    cover = find_cover(base_ops().build(), "T-0001", Rank.CAPTAIN, vacated_by="C-1001")
    ways = [(c.way, c.crew_id) for c in cover.choices]
    assert ways == [(Way.STANDBY, "C-2001"), (Way.DAY_OFF, "C-2002"), (Way.CANCEL, None)]
    best = cover.best
    assert best.breakdown == {"callout": 15000, "service allowance": 4000, "travel allowance": 600}
    assert best.cost == 19600
    assert [c.position for c in cover.choices] == [1, 2, 3]


def test_facts_are_precomputed_for_answers():
    facts = find_cover(base_ops().build(), "T-0001", Rank.CAPTAIN).facts
    assert facts["cancel_cost"] == 600000
    assert facts["saving_vs_cancel"] == 600000 - 19600
    assert facts["next_price_premium"] == 7000
    assert facts["next_price_crew"] == "C-2002"
    assert facts["cancel_times_best"] == 30.6


def test_everyone_left_out_has_a_stage_and_a_reason():
    cover = find_cover(base_ops().build(), "T-0001", Rank.CAPTAIN)
    excluded = cover.why_not("C-2003")
    assert excluded.stage == "current"
    assert excluded.rule_ids == (CERT,)
    assert cover.considered == 3
    assert [(s.stage, s.left, s.dropped) for s in cover.funnel][:2] == [
        ("rated", 3, 0),
        ("current", 2, 1),
    ]


def test_crew_already_on_the_trip_are_not_candidates():
    cover = find_cover(base_ops().build(), "T-0001", Rank.CAPTAIN)
    assert "C-1001" not in {c.crew_id for c in cover.choices}
    assert cover.why_not("C-1001") is None


def test_command_qualified_first_officers_follow_when_captains_run_short():
    ops = base_ops()
    ops.person("C-3001", Rank.FIRST_OFFICER, command_qualified=True)
    ops.person("C-3002", Rank.FIRST_OFFICER)
    cover = find_cover(ops.build(), "T-0001", Rank.CAPTAIN)
    ways = [(c.way, c.crew_id) for c in cover.choices]
    assert ways[:3] == [
        (Way.STANDBY, "C-2001"),
        (Way.DAY_OFF, "C-2002"),
        (Way.COMMAND_UPGRADE, "C-3001"),
    ]
    assert "C-3002" not in {c.crew_id for c in cover.choices}


def test_no_substitutes_while_three_own_rank_crew_are_legal():
    ops = base_ops().person("C-2004")
    ops.person("C-3001", Rank.FIRST_OFFICER, command_qualified=True)
    cover = find_cover(ops.build(), "T-0001", Rank.CAPTAIN)
    assert Way.COMMAND_UPGRADE not in {c.way for c in cover.choices}


def test_positioning_costs_the_flight_and_the_delay():
    ops = Ops().person("C-1001").person("C-4001", base="BOM")
    ops.leg(DAY, "BOM", "HYD", at(DAY, 0, 30), 1.5, tail="VT-KFA", flight_no="KF900")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    [choice, cancel] = find_cover(ops.build(), "T-0001", Rank.CAPTAIN).choices
    assert choice.way is Way.POSITIONING
    assert choice.positioning_leg == "KF900-2026-11-03"
    assert choice.slip_hours == 1.33
    assert choice.breakdown["positioning"] == 7000
    assert choice.breakdown["delay"] == 7980
    assert cancel.way is Way.CANCEL


def test_someone_called_too_late_to_reach_the_airport_delays_the_departure():
    ops = Ops().person("C-1001").person("C-2001", minutes=90)
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    op = ops.build(as_of=datetime(2026, 11, 3, 0, 0, tzinfo=UTC))  # report is 01:00Z
    choice = find_cover(op, "T-0001", Rank.CAPTAIN).best
    assert choice.slip_hours == 0.5
    assert choice.breakdown["delay"] == 3000


def test_a_cabin_seat_may_fly_one_short_when_bookings_allow():
    ops = Ops()
    for crew_id in ("C-5001", "C-5002", "C-5003"):
        ops.person(crew_id, Rank.CABIN_CREW)
    ops.day_trip(
        "T-0001",
        crew=[("C-5001", Rank.PURSER), ("C-5002", Rank.CABIN_CREW), ("C-5003", Rank.CABIN_CREW)],
    )
    cover = find_cover(ops.build(), "T-0001", Rank.CABIN_CREW, vacated_by="C-5003")
    short = next(c for c in cover.choices if c.way is Way.ONE_SHORT)
    assert "minimum of 2" in short.note
    assert cover.choices[-1].way is Way.CANCEL


def test_a_trip_that_has_reported_cannot_be_recrewed():
    op = base_ops().build(as_of=at(DAY, 1, 30))
    with pytest.raises(TripStarted):
        find_cover(op, "T-0001", Rank.CAPTAIN)


def test_cover_on_the_demo_operation():
    data = load_dataset(ROOT / "data" / "seed")
    op = build_operation(data)
    trip = next(t for t in data.trips if t.airline == "SY" and t.first_day.day.day == 2)
    sick = trip.holders(Rank.FIRST_OFFICER)[0]
    cover = find_cover(op, trip.trip_id, Rank.FIRST_OFFICER, vacated_by=sick)
    crewed = [c for c in cover.choices if c.crew_id]
    assert crewed, cover.excluded
    assert all(op.state(c.crew_id).airline == "SY" for c in crewed)
    assert sum(s.dropped for s in cover.funnel) == len(cover.excluded)
    assert all(e.rule_ids for e in cover.excluded)


def positioned(fo_next_rest_hours=None):
    """C-1001 is sick on T-0001; the only captain, C-2001, positions in from BOM, holding the
    departure 50 minutes. The first officer optionally flies again ``fo_next_rest_hours`` after
    T-0001's scheduled release."""
    ops = Ops().person("C-1001").person("C-1002", Rank.FIRST_OFFICER).person("C-2001", base="BOM")
    ops.leg(DAY, "BOM", "HYD", at(DAY, 0), 1.5, tail="VT-KFA", flight_no="KF900")
    ops.day_trip("T-0001", crew=CAPTAINS_ON_TRIP)
    if fo_next_rest_hours is not None:
        probe = ops.build()
        first = probe.days_of("T-0001")[0]
        lead = probe.legs[probe.trips["T-0001"].leg_ids[0]].off_block - first.report
        nxt = DAY + timedelta(days=1)
        start = first.release + timedelta(hours=fo_next_rest_hours) + lead
        ops.day_trip(
            "T-0002", day=nxt, start=start, tail="VT-SYB", crew=[("C-1002", Rank.FIRST_OFFICER)]
        )
    op = ops.build()
    return op, find_cover(op, "T-0001", Rank.CAPTAIN, vacated_by="C-1001")


def test_a_choice_that_holds_the_departure_lists_what_to_arrange():
    op, cover = positioned()
    best = cover.best
    assert (best.crew_id, best.way, best.slip_hours) == ("C-2001", Way.POSITIONING, 0.83)
    assert arrangements(op, "T-0001", best) == (
        "book a positioning seat on KF900 (BOM-HYD, departs 03 Nov 05:30 IST)",
        "T-0001 departs 0h50m late: operations to retime and inform passengers",
    )
    assert arrangements(op, "T-0001", cover.choices[-1]) == ()  # cancelling: nothing to book


def test_a_late_departure_that_breaks_a_colleagues_rest_rules_the_choice_out():
    assert positioned(fo_next_rest_hours=13)[1].best.crew_id == "C-2001"  # 12h10m: still legal

    _, cover = positioned(fo_next_rest_hours=12.5)  # 11h40m after a 50 minute slip
    assert [c.way for c in cover.choices] == [Way.CANCEL]
    excluded = cover.why_not("C-2001")
    assert (excluded.stage, excluded.rule_ids) == ("keeps the crew legal", ("FDTL-05",))
    assert excluded.reasons[0].startswith("departing 0h50m late: Crew C-1002 (C-1002) would fail")
    assert cover.funnel[-1].stage == "keeps the crew legal" and cover.funnel[-1].dropped == 1


def test_each_aim_has_its_own_pick():
    cover = find_cover(base_ops().build(), "T-0001", Rank.CAPTAIN, vacated_by="C-1001")
    picks = {aim: c.crew_id for aim, c in cover.picks().items()}
    # The standby is cheapest; calling the day-off captain keeps the standby free.
    assert picks == {"cheapest": "C-2001", "least_delay": "C-2001", "keeps_standby": "C-2002"}


def test_least_delay_prefers_whoever_holds_the_departure_least():
    ops = Ops().person("C-1001").person("C-4001", base="BOM").person("C-2001", minutes=90)
    ops.leg(DAY, "BOM", "HYD", at(DAY, 0, 30), 1.5, tail="VT-KFA", flight_no="KF900")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    op = ops.build(as_of=datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    cover = find_cover(op, "T-0001", Rank.CAPTAIN)
    late = {c.crew_id: c.slip_hours for c in cover.choices if c.crew_id}
    assert late["C-4001"] > late["C-2001"] > 0
    assert cover.picks()["least_delay"].crew_id == "C-2001"
