from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import pytest
from builders import DAY, Ops, at

from crewpay.domain import AircraftType, Rank, Rule
from crewpay.engine import NotFound, RuleBook, assess, build_operation
from crewpay.engine.clock import IST, in_local_night, overlaps_local_night, spoken_hours, window
from crewpay.engine.rules import BASE, BLOCK, CERT, DUTY, FDP, NIGHT, RATING, REST, WEEKLY


def failing(assessment, rule_id):
    return [f for f in assessment.blockers if f.rule_id == rule_id]


def captain(ops=None, crew_id="C-1001", **kw):
    return (ops or Ops()).person(crew_id, Rank.CAPTAIN, **kw)


# --- clock ---------------------------------------------------------------------------------


def test_calendar_window_includes_the_end_day():
    assert window(date(2026, 11, 14), 7) == (date(2026, 11, 8), date(2026, 11, 14))


def test_spoken_hours():
    assert spoken_hours(1.33) == "1h20m"
    assert spoken_hours(-0.5) == "0h30m"


def test_night_is_local_time():
    # 19:00Z is 00:30 IST
    assert in_local_night(datetime(2026, 11, 3, 19, 0, tzinfo=UTC), time(0), time(6))
    assert not in_local_night(datetime(2026, 11, 3, 1, 0, tzinfo=UTC), time(0), time(6))


def test_a_duty_spanning_the_whole_night_touches_it():
    report = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)  # 23:30 IST
    release = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)  # 06:30 IST next day
    assert overlaps_local_night(report, release, time(0), time(6))


# --- rule book -----------------------------------------------------------------------------


def test_rule_book_reads_limits_from_rule_parameters():
    book = RuleBook.from_rules(
        [
            Rule(rule_id=FDP, title="FDP", text="", params={"base_hours": 12}),
            Rule(rule_id=NIGHT, title="Night", text="", params={"night_end_local": 5.5}),
        ]
    )
    assert book.fdp_limit(2) == 12
    assert book.fdp_limit(4) == 11
    assert book.night_end == time(5, 30)
    assert book.duty_hours == 60


# --- assess --------------------------------------------------------------------------------


def test_standby_captain_at_base_is_legal():
    op = captain().on_standby("C-1001", DAY, time(0), time(6)).day_trip("T-0001").build()
    result = assess(op, "C-1001", "T-0001")
    assert result.legal, result.blockers
    assert result.on_standby
    assert {f.rule_id for f in result.findings} >= {FDP, NIGHT, DUTY, BLOCK, REST, WEEKLY}


def test_four_long_sectors_break_the_reduced_duty_period():
    op = captain().day_trip("T-0001", sectors=4, hours=2.5).build()
    [finding] = failing(assess(op, "C-1001", "T-0001"), FDP)
    assert finding.limit == 12
    assert finding.used == 13.75
    assert "1h45m over the 12h limit for 4 sectors" in finding.detail


def test_a_delay_lengthens_the_day_until_it_breaks_the_limit():
    op = captain().day_trip("T-0001").build()
    assert assess(op, "C-1001", "T-0001", delay_hours=7).legal
    [finding] = failing(assess(op, "C-1001", "T-0001", delay_hours=8), FDP)
    assert finding.used == 13.25


def test_three_landings_in_the_local_night_are_one_too_many():
    op = captain().day_trip("T-0001", start=at(DAY, 19), sectors=3, hours=1, turn=0.5).build()
    [finding] = failing(assess(op, "C-1001", "T-0001"), NIGHT)
    assert finding.used == 3 and finding.limit == 2


def test_a_night_duty_has_a_shorter_limit():
    op = captain().day_trip("T-0001", start=at(DAY, 18, 30), hours=4, turn=1).build()
    result = assess(op, "C-1001", "T-0001")
    [finding] = failing(result, NIGHT)
    assert finding.used == 10.5 and finding.limit == 10
    assert not failing(result, FDP)


def test_duty_window_is_checked_up_to_a_later_rostered_day():
    ops = captain().history(
        "C-1001",
        rest_until=at(DAY - timedelta(days=1), 14),
        days={DAY - timedelta(days=n): (12.0, 8.0) for n in range(1, 5)},
    )
    ops.day_trip("T-0001")
    ops.day_trip(
        "T-0002", day=DAY + timedelta(days=1), sectors=4, hours=2, crew=[("C-1001", Rank.CAPTAIN)]
    )
    [finding] = failing(assess(ops.build(), "C-1001", "T-0001"), DUTY)
    assert finding.used == 65.0
    assert "a later rostered day" in finding.detail


def test_rest_must_be_as_long_as_the_duty_before_it():
    ops = captain()
    yesterday = DAY - timedelta(days=1)
    ops.day_trip(
        "T-0002",
        day=yesterday,
        start=at(yesterday, 0, 45),
        hours=5.25,
        crew=[("C-1001", Rank.CAPTAIN)],
    )
    ops.day_trip("T-0001")  # reports 01:00Z, 12.5h after yesterday's release
    [finding] = failing(assess(ops.build(), "C-1001", "T-0001"), REST)
    assert finding.used == 12.5
    assert finding.limit == 12.75


def test_overlapping_trips_are_a_double_booking():
    ops = captain().day_trip("T-0002", crew=[("C-1001", Rank.CAPTAIN)], tail="VT-SYT")
    ops.day_trip("T-0001")
    [finding] = failing(assess(ops.build(), "C-1001", "T-0001"), REST)
    assert "already on T-0002" in finding.detail


def test_six_days_straight_leaves_no_weekly_rest():
    ops = captain().history(
        "C-1001",
        rest_until=at(DAY - timedelta(days=1), 12),
        days={DAY - timedelta(days=n): (6.0, 3.0) for n in range(1, 7)},
    )
    [finding] = failing(assess(ops.day_trip("T-0001").build(), "C-1001", "T-0001"), WEEKLY)
    assert finding.used == 0 and finding.limit == 2


def test_an_expired_certificate_blocks_the_trip():
    op = captain(cert_expiry=DAY - timedelta(days=1)).day_trip("T-0001").build()
    [finding] = failing(assess(op, "C-1001", "T-0001"), CERT)
    assert finding.detail == "licence runs out 02 Nov 2026, not valid that day"


def test_a_certificate_is_not_valid_on_the_day_it_runs_out():
    on_the_day = captain(cert_expiry=DAY).day_trip("T-0001").build()
    assert failing(assess(on_the_day, "C-1001", "T-0001"), CERT)
    day_after = captain(cert_expiry=DAY + timedelta(days=1)).day_trip("T-0001").build()
    assert not failing(assess(day_after, "C-1001", "T-0001"), CERT)


def test_a_duty_landing_after_midnight_needs_the_certificate_on_the_later_date():
    # Departs 22:30 IST on DAY and lands after midnight IST, on the day the licence runs out.
    ops = captain(cert_expiry=DAY + timedelta(days=1))
    op = ops.day_trip("T-0001", start=at(DAY, 17), hours=2.0).build()
    assert op.days_of("T-0001")[0].release.astimezone(IST).date() == DAY + timedelta(days=1)
    assert failing(assess(op, "C-1001", "T-0001"), CERT)


def test_not_rated_on_the_aircraft():
    op = captain().day_trip("T-0001", tail="VT-SYT").build()
    assert failing(assess(op, "C-1001", "T-0001"), RATING)
    rated = captain(ratings=(AircraftType.ATR72,)).day_trip("T-0001", tail="VT-SYT").build()
    assert assess(rated, "C-1001", "T-0001").legal


def test_crew_from_another_base_are_positioned_and_the_day_slips():
    ops = captain(base="BOM")
    ops.leg(DAY, "BOM", "HYD", at(DAY, 0), 1.5, tail="VT-KFA", flight_no="KF900")
    result = assess(ops.day_trip("T-0001").build(), "C-1001", "T-0001")
    assert result.legal, result.blockers
    assert result.positioning.leg_id == "KF900-2026-11-03"
    assert result.slip_hours == 0.83
    # Positioning is duty: the day starts an hour before the 00:00Z positioning flight, not at
    # the trip's own (slipped) report, and the trip's release moves with the slip.
    assert result.days[0].report == at(DAY, 0) - timedelta(hours=1)
    unpositioned = build_operation(ops.dataset()).days_of("T-0001")[0]
    assert result.days[0].release == unpositioned.release + timedelta(minutes=50)
    assert result.days[0].fdp_hours > unpositioned.fdp_hours + 1.5


def test_positioning_time_can_push_a_day_over_the_duty_limit():
    ops = captain(base="BOM")
    ops.leg(
        DAY, "BOM", "HYD", at(DAY, 0) - timedelta(hours=4), 2.0, tail="VT-KFA", flight_no="KF901"
    )
    op = ops.day_trip("T-0001", sectors=4, hours=1.75, turn=0.75).build()
    result = assess(op, "C-1001", "T-0001")
    assert result.positioning.leg_id.startswith("KF901")
    assert "FDTL-01" in {b.rule_id for b in result.blockers}


def test_a_full_positioning_flight_cannot_be_used():
    ops = captain(base="BOM")
    ops.leg(DAY, "BOM", "HYD", at(DAY, 0), 1.5, tail="VT-KFA", flight_no="KF900")
    op = ops.day_trip("T-0001").build()
    full = op.legs["KF900-2026-11-03"]
    op = replace(op, legs={**op.legs, full.leg_id: full.model_copy(update={"booked": full.seats})})
    result = assess(op, "C-1001", "T-0001")
    assert result.positioning is None
    assert [b.rule_id for b in result.blockers] == ["FDTL-09"]


def test_no_positioning_flight_fails_the_base_rule():
    op = captain(base="BOM").day_trip("T-0001").build()
    assert failing(assess(op, "C-1001", "T-0001"), BASE)


def test_a_standby_window_that_misses_the_report_time():
    op = captain().on_standby("C-1001", DAY, time(6), time(12)).day_trip("T-0001").build()
    [finding] = failing(assess(op, "C-1001", "T-0001"), BASE)
    assert "06:00-12:00Z does not cover the 01:00Z report" in finding.detail


def test_crew_only_fly_for_their_own_airline():
    op = captain(airline="KF").day_trip("T-0001").build()
    [finding] = failing(assess(op, "C-1001", "T-0001"), RATING)
    assert "employed by Kaveri Flyers" in finding.detail


def test_reported_unavailable_overrides_the_roster():
    op = captain().day_trip("T-0001").build()
    op = replace(op, unavailable={"C-1001": ((DAY, "R-0007"),)})
    [finding] = failing(assess(op, "C-1001", "T-0001"), RATING)
    assert "R-0007" in finding.detail


def test_crew_already_on_the_trip_are_judged_without_their_own_duty():
    ops = captain().day_trip("T-0001", crew=[("C-1001", Rank.CAPTAIN)])
    assert assess(ops.build(), "C-1001", "T-0001").legal


def test_unknown_ids_are_not_guessed():
    op = captain().day_trip("T-0001").build()
    with pytest.raises(NotFound):
        assess(op, "C-1010", "T-0001")
    with pytest.raises(NotFound):
        assess(op, "C-1001", "T-0010")
