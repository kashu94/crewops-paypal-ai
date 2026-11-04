from datetime import timedelta

from builders import DAY, Ops, at

from crewpay.domain import Rank
from crewpay.engine import assess, build_operation
from crewpay.engine.fatigue import Severity, fatigue_flags
from crewpay.engine.watch import scan
from crewpay.seed import load_dataset
from crewpay.settings import ROOT


def on(day_offset):
    return DAY + timedelta(days=day_offset)


def early(ops, crew_id, offsets, first=1):
    """Day trips departing 06:00 IST, so the report is before 07:00 IST."""
    for n, i in enumerate(offsets):
        ops.day_trip(
            f"T-{first + n:04d}",
            day=on(i),
            start=at(on(i), 0, 30),
            tail=f"VT-SY{chr(65 + n)}",
            crew=[(crew_id, Rank.CAPTAIN)],
        )
    return ops


def night(ops, crew_id, offsets, first=50):
    """Duties from 20:30 IST to about 02:45 IST, through the small hours."""
    for n, i in enumerate(offsets):
        ops.day_trip(
            f"T-{first + n:04d}",
            day=on(i),
            start=at(on(i), 15),
            hours=2.5,
            tail=f"VT-KF{chr(65 + n)}",
            crew=[(crew_id, Rank.CAPTAIN)],
            airline="SY",
        )
    return ops


def kinds(flags):
    return [(f.kind, f.severity, f.day) for f in flags]


def test_four_early_starts_in_a_row_are_a_caution_and_three_are_not():
    four = early(Ops().person("C-1001"), "C-1001", range(4)).build()
    assert kinds(fatigue_flags(four, "C-1001")) == [("early_starts", Severity.CAUTION, on(3))]
    assert all(assess(four, "C-1001", t).legal for t in four.trips)  # legal, still flagged
    three = early(Ops().person("C-1001"), "C-1001", range(3)).build()
    assert fatigue_flags(three, "C-1001") == []


def test_a_cover_trip_is_judged_with_the_roster_it_joins():
    ops = early(Ops().person("C-1001").person("C-2001"), "C-1001", range(3))
    ops.day_trip(
        "T-0099", day=on(3), start=at(on(3), 0, 30), tail="VT-SYZ", crew=[("C-2001", Rank.CAPTAIN)]
    )
    op = ops.build()
    [flag] = fatigue_flags(op, "C-1001", "T-0099")
    assert (flag.kind, flag.day, flag.detail) == (
        "early_starts",
        on(3),
        "4th early start in a row (report before 07:00 IST)",
    )
    assert fatigue_flags(op, "C-1001") == []


def test_rest_that_only_just_clears_the_minimum_is_tight():
    probe = (
        Ops()
        .person("C-1001")
        .day_trip("T-0001", start=at(DAY, 4), crew=[("C-1001", Rank.CAPTAIN)])
        .build()
    )
    first = probe.days_of("T-0001")[0]
    lead = probe.legs[probe.trips["T-0001"].leg_ids[0]].off_block - first.report
    start = first.release + timedelta(hours=12, minutes=30) + lead

    ops = (
        Ops().person("C-1001").day_trip("T-0001", start=at(DAY, 4), crew=[("C-1001", Rank.CAPTAIN)])
    )
    ops.day_trip("T-0002", day=on(1), start=start, tail="VT-SYB", crew=[("C-1001", Rank.CAPTAIN)])
    [flag] = fatigue_flags(ops.build(), "C-1001")
    assert (flag.kind, flag.severity) == ("tight_rest", Severity.CAUTION)
    assert flag.detail.startswith("rest 12h30m before") and "0h30m above the 12h00m" in flag.detail


def test_an_early_report_soon_after_a_night_is_high_even_when_legal():
    ops = night(Ops().person("C-1001"), "C-1001", [0])
    op = early(ops, "C-1001", [2]).build()
    assert all(assess(op, "C-1001", t).legal for t in op.trips)
    [flag] = fatigue_flags(op, "C-1001")
    assert (flag.kind, flag.severity, flag.day) == ("night_then_early", Severity.HIGH, on(2))
    assert "after a duty through the small hours" in flag.detail


def test_repeated_nights_are_flagged_once_per_block_at_their_worst():
    three = night(Ops().person("C-1001"), "C-1001", range(3)).build()
    assert kinds(fatigue_flags(three, "C-1001")) == [("repeated_nights", Severity.CAUTION, on(2))]
    four = night(Ops().person("C-1001"), "C-1001", range(4)).build()
    [flag] = fatigue_flags(four, "C-1001")
    assert (flag.severity, flag.day, len(flag.days)) == (Severity.HIGH, on(3), 4)


def test_the_watch_finds_the_lapsed_medical_before_anyone_reports_it():
    op = build_operation(load_dataset(ROOT / "data" / "seed"))
    issues = scan(op)
    illegal = [i for i in issues if i.kind == "illegal"]
    assert [(i.crew_id, i.trip_id, i.rule_ids) for i in illegal] == [
        ("C-1111", "T-1018", ("FDTL-08",))
    ]
    assert illegal[0].suggestion.startswith("Captain D. Reddy (C-1157)")
    assert illegal[0].suggestion_cost == 20840
    assert issues[0] is illegal[0]  # the most serious first
    assert [i.issue_id for i in scan(op)] == [i.issue_id for i in issues]  # stable ids
    # The demo roster is kept quiet: one illegal captain and one certificate running out.
    assert [i.kind for i in issues] == ["illegal", "certificate"]


def test_the_watch_reports_a_fatigue_block_once_against_its_worst_day():
    op = night(Ops().person("C-1001"), "C-1001", range(4)).build()
    [issue] = [i for i in scan(op, horizon_days=10) if i.kind == "fatigue"]
    assert issue.issue_id == f"fatigue:C-1001:repeated_nights:{DAY.isoformat()}"
    assert (issue.trip_id, issue.day) == ("T-0053", on(3))


def test_certificates_running_out_soon_are_listed_once():
    op = Ops().person("C-1001", cert_expiry=DAY + timedelta(days=3)).build()
    issues = scan(op)
    assert {(i.kind, i.crew_id) for i in issues} == {("certificate", "C-1001")}
    assert len({i.issue_id for i in issues}) == len(issues)
