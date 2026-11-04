from datetime import timedelta

from builders import DAY, Ops, at

from crewpay.domain import Rank
from crewpay.engine.cover import Way, find_cover
from crewpay.engine.impact import absence_gaps, delay_impact, joint_cover, with_unavailable
from crewpay.engine.rules import FDP, RATING

NEXT = DAY + timedelta(days=1)


def test_absence_lists_every_seat_in_the_dates():
    ops = Ops().person("C-1001")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    ops.day_trip("T-0002", day=NEXT, crew=[("C-1001", Rank.CAPTAIN)])
    ops.day_trip("T-0003", day=DAY + timedelta(days=4), crew=[("C-1001", Rank.CAPTAIN)])
    gaps = absence_gaps(ops.build(), "C-1001", DAY, NEXT)
    assert [(g.trip_id, g.rank, g.legs, g.passengers) for g in gaps] == [
        ("T-0001", Rank.CAPTAIN, 2, 120),
        ("T-0002", Rank.CAPTAIN, 2, 120),
    ]
    assert not any(g.started for g in gaps)


def test_someone_reported_unavailable_is_never_offered_as_cover():
    ops = Ops().person("C-1001").person("C-2001")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    op = with_unavailable(ops.build(), "C-2001", [DAY], "R-0001")
    cover = find_cover(op, "T-0001", Rank.CAPTAIN)
    assert cover.why_not("C-2001").rule_ids == (RATING,)
    assert "R-0001" in cover.why_not("C-2001").reasons[0]


def test_a_delay_pushes_the_whole_crew_over_the_duty_period():
    ops = Ops().person("C-1001").person("C-1002", Rank.FIRST_OFFICER)
    ops.day_trip(
        "T-0001",
        sectors=4,
        hours=2,
        crew=[("C-1001", Rank.CAPTAIN), ("C-1002", Rank.FIRST_OFFICER)],
    )
    op = ops.build()
    assert not delay_impact(op, "T-0001", 0.25).breaches
    impact = delay_impact(op, "T-0001", 1)
    assert impact.crew_affected == 2
    assert all(b.rule_ids == (FDP,) for b in impact.breaches)
    assert impact.passengers == 240
    assert impact.delay_cost == 6000


def test_two_trips_never_get_the_same_captain():
    ops = Ops().person("C-1001").person("C-1002")
    ops.person("C-2001").on_standby("C-2001", DAY, at(DAY, 0).time(), at(DAY, 6).time())
    ops.person("C-2002")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    ops.day_trip("T-0002", tail="VT-KFA", crew=[("C-1002", Rank.CAPTAIN)])
    op = ops.build()
    one = find_cover(op, "T-0001", Rank.CAPTAIN).best.crew_id
    two = find_cover(op, "T-0002", Rank.CAPTAIN).best.crew_id
    assert one == two == "C-2001"  # each seat alone wants the standby

    gaps = absence_gaps(op, "C-1001", DAY, DAY) + absence_gaps(op, "C-1002", DAY, DAY)
    plan = joint_cover(op, gaps)
    assert plan.searched
    assert sorted(plan.crew_ids) == ["C-2001", "C-2002"]
    assert plan.total_cost == 19600 + 26600


def test_two_empty_cabin_seats_on_one_trip_cost_one_cancellation():
    ops = Ops()
    for crew_id in ("C-5001", "C-5002", "C-5003"):
        ops.person(crew_id, Rank.CABIN_CREW)
    ops.day_trip(
        "T-0001",
        crew=[("C-5001", Rank.PURSER), ("C-5002", Rank.CABIN_CREW), ("C-5003", Rank.CABIN_CREW)],
    )
    op = ops.build()
    gaps = absence_gaps(op, "C-5002", DAY, DAY) + absence_gaps(op, "C-5003", DAY, DAY)
    plan = joint_cover(op, gaps)
    assert [f.choice.way for f in plan.fills] == [Way.CANCEL, Way.CANCEL]
    assert [f.choice.cost for f in plan.fills] == [600000, 0]
    assert plan.total_cost == 600000


def test_trips_already_under_way_are_skipped():
    ops = Ops().person("C-1001").person("C-2001")
    ops.day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    ops.day_trip("T-0002", day=NEXT, crew=[("C-1001", Rank.CAPTAIN)])
    op = ops.build(as_of=at(DAY, 3))
    plan = joint_cover(op, absence_gaps(op, "C-1001", DAY, NEXT))
    assert [g.trip_id for g in plan.skipped] == ["T-0001"]
    assert [f.gap.trip_id for f in plan.fills] == ["T-0002"]
