from dataclasses import replace
from datetime import timedelta

from crewpay.engine.severity import Level, for_issue, for_trip
from crewpay.engine.watch import scan


def test_a_seat_with_only_cancelling_left_is_high(store):
    op = store.operation()
    s = for_trip(op, "T-1018", no_crew_left=True)
    assert s.level is Level.HIGH and s.reasons[0].startswith("no legal crew left")


def test_urgency_grows_as_the_report_time_comes_closer(store):
    op = store.operation()
    report = op.days_of("T-1018")[0].report
    far = for_trip(op, "T-1018")
    near = for_trip(replace(op, now=report - timedelta(hours=2)), "T-1018")
    assert near.level is Level.HIGH
    assert far.level.rank >= near.level.rank
    assert any("passengers booked" in r for r in near.reasons)


def test_every_issue_gets_a_level_and_illegal_is_never_low(store):
    op = store.operation()
    for issue in scan(op):
        s = for_issue(op, issue)
        assert s.reasons
        if issue.kind == "illegal":
            assert s.level is not Level.LOW
