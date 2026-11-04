"""The nine duty rules.

Each check returns a ``Finding`` that carries the numbers behind it, not just pass or fail:
"60h/7d exceeded by 1h20m on 5 Nov" is something a controller can act on, "illegal" is not.

The checks only read a ``CrewState`` and the duty days being judged, so they are tested without
any store. Limits come from a ``RuleBook`` built from the rule parameters in the data, so an
admin can tighten a limit without a code change.

The rule set follows the shape of Indian flight duty time limitations (a shorter duty period
and a two-landing cap when a duty touches the night, a weekly rest of two clear days, and rest
at least as long as the duty before it), simplified for a demo.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from crewpay.domain import AircraftType, CertKind, CrewStatus, Rank, Rule
from crewpay.engine.clock import (
    IST,
    DutyDay,
    hours_between,
    in_local_night,
    overlaps_local_night,
    spoken_hours,
    window,
)

FDP = "FDTL-01"
NIGHT = "FDTL-02"
DUTY = "FDTL-03"
BLOCK = "FDTL-04"
REST = "FDTL-05"
WEEKLY = "FDTL-06"
RATING = "FDTL-07"
CERT = "FDTL-08"
BASE = "FDTL-09"


class Outcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    outcome: Outcome
    detail: str
    day: date | None = None
    used: float | None = None
    limit: float | None = None

    @property
    def failed(self) -> bool:
        return self.outcome is Outcome.FAIL

    @property
    def margin(self) -> float | None:
        """Hours left before the limit; negative when over it."""
        if self.used is None or self.limit is None:
            return None
        return round(self.limit - self.used, 2)


def passed(rule_id: str, detail: str, **numbers) -> Finding:
    return Finding(rule_id, Outcome.PASS, detail, **numbers)


def failed(rule_id: str, detail: str, **numbers) -> Finding:
    return Finding(rule_id, Outcome.FAIL, detail, **numbers)


@dataclass(frozen=True, slots=True)
class RuleBook:
    fdp_base_hours: float = 13.0
    fdp_cut_per_sector: float = 0.5
    fdp_free_sectors: int = 2
    night_start: time = time(0, 0)
    night_end: time = time(6, 0)
    night_fdp_hours: float = 10.0
    night_landings: int = 2
    duty_hours: float = 60.0
    duty_days: int = 7
    block_hours: float = 100.0
    block_days: int = 28
    rest_hours: float = 12.0
    weekly_rest_days: int = 2
    weekly_window_days: int = 7

    def fdp_limit(self, sectors: int) -> float:
        extra = max(0, sectors - self.fdp_free_sectors)
        return self.fdp_base_hours - self.fdp_cut_per_sector * extra

    @classmethod
    def from_rules(cls, rules: Iterable[Rule]) -> RuleBook:
        """Limits from the rule parameters; anything not given keeps its default."""
        params: dict[str, Mapping[str, float]] = {r.rule_id: r.params for r in rules}
        d = cls()

        def get(rule_id: str, key: str, default: float) -> float:
            return float(params.get(rule_id, {}).get(key, default))

        def hour(rule_id: str, key: str, default: time) -> time:
            value = get(rule_id, key, default.hour + default.minute / 60)
            return time(int(value) % 24, round((value % 1) * 60))

        return cls(
            fdp_base_hours=get(FDP, "base_hours", d.fdp_base_hours),
            fdp_cut_per_sector=get(FDP, "cut_per_extra_sector", d.fdp_cut_per_sector),
            fdp_free_sectors=int(get(FDP, "free_sectors", d.fdp_free_sectors)),
            night_start=hour(NIGHT, "night_start_local", d.night_start),
            night_end=hour(NIGHT, "night_end_local", d.night_end),
            night_fdp_hours=get(NIGHT, "max_fdp_hours", d.night_fdp_hours),
            night_landings=int(get(NIGHT, "max_landings", d.night_landings)),
            duty_hours=get(DUTY, "max_hours", d.duty_hours),
            duty_days=int(get(DUTY, "window_days", d.duty_days)),
            block_hours=get(BLOCK, "max_hours", d.block_hours),
            block_days=int(get(BLOCK, "window_days", d.block_days)),
            rest_hours=get(REST, "min_hours", d.rest_hours),
            weekly_rest_days=int(get(WEEKLY, "clear_days", d.weekly_rest_days)),
            weekly_window_days=int(get(WEEKLY, "window_days", d.weekly_window_days)),
        )


@dataclass(frozen=True, slots=True)
class Duty:
    """A duty day already on someone's roster."""

    trip_id: str
    day: date
    report: datetime
    release: datetime
    block_hours: float

    @property
    def hours(self) -> float:
        return hours_between(self.report, self.release)


@dataclass(frozen=True, slots=True)
class CrewState:
    """What the rules need to know about one crew member."""

    crew_id: str
    name: str
    rank: Rank
    airline: str
    base: str
    ratings: tuple[AircraftType, ...]
    status: CrewStatus
    rest_until: datetime | None
    history_duty: Mapping[date, float] = field(default_factory=dict)
    history_block: Mapping[date, float] = field(default_factory=dict)
    certs: tuple[tuple[CertKind, date], ...] = ()
    duties: tuple[Duty, ...] = ()

    def rostered(self, exclude_trip: str | None = None) -> list[Duty]:
        return [d for d in self.duties if d.trip_id != exclude_trip]

    def _daily(
        self,
        history: Mapping[date, float],
        hours_of: Callable[[Duty], float],
        exclude_trip: str | None,
    ) -> dict[date, float]:
        """Hours per day from history or the roster, counted once.

        History is written after the fact and can lag a duty flown today, so a day that appears
        in both takes the larger figure instead of the sum.
        """
        rostered: dict[date, float] = {}
        for d in self.rostered(exclude_trip):
            rostered[d.day] = round(rostered.get(d.day, 0.0) + hours_of(d), 2)
        return {k: max(history.get(k, 0.0), rostered.get(k, 0.0)) for k in {*history, *rostered}}

    def duty_between(self, start: date, end: date, exclude_trip: str | None = None) -> float:
        daily = self._daily(self.history_duty, lambda d: d.hours, exclude_trip)
        return round(sum(h for k, h in daily.items() if start <= k <= end), 2)

    def block_between(self, start: date, end: date, exclude_trip: str | None = None) -> float:
        daily = self._daily(self.history_block, lambda d: d.block_hours, exclude_trip)
        return round(sum(h for k, h in daily.items() if start <= k <= end), 2)

    def working_days(self, exclude_trip: str | None = None) -> set[date]:
        worked = {k for k, h in self.history_duty.items() if h > 0}
        return worked | {d.day for d in self.rostered(exclude_trip)}


def check_fdp(day: DutyDay, book: RuleBook) -> Finding:
    used, limit = day.fdp_hours, book.fdp_limit(day.sectors)
    numbers = {"day": day.day, "used": used, "limit": limit}
    if used > limit:
        over = spoken_hours(used - limit)
        detail = (
            f"duty period {used}h is {over} over the {limit:g}h limit for {day.sectors} sectors"
        )
        return failed(FDP, detail, **numbers)
    return passed(FDP, f"duty period {used}h of {limit:g}h ({day.sectors} sectors)", **numbers)


def check_night(day: DutyDay, book: RuleBook) -> Finding:
    if not overlaps_local_night(day.report, day.release, book.night_start, book.night_end):
        return passed(NIGHT, "no night duty", day=day.day)
    night_landings = sum(in_local_night(t, book.night_start, book.night_end) for t in day.landings)
    if night_landings > book.night_landings:
        return failed(
            NIGHT,
            f"{night_landings} landings at night, at most {book.night_landings} allowed",
            day=day.day,
            used=night_landings,
            limit=book.night_landings,
        )
    if day.fdp_hours > book.night_fdp_hours:
        over = spoken_hours(day.fdp_hours - book.night_fdp_hours)
        return failed(
            NIGHT,
            f"night duty of {day.fdp_hours}h is {over} over the {book.night_fdp_hours:g}h limit",
            day=day.day,
            used=day.fdp_hours,
            limit=book.night_fdp_hours,
        )
    return passed(
        NIGHT,
        f"night duty {day.fdp_hours}h of {book.night_fdp_hours:g}h, "
        f"{night_landings} night landings",
        day=day.day,
        used=day.fdp_hours,
        limit=book.night_fdp_hours,
    )


def _worst_window(
    crew: CrewState,
    day: DutyDay,
    earlier: Iterable[DutyDay],
    days: int,
    recorded: Callable[[date, date, str | None], float],
    hours_of: Callable[[DutyDay], float],
    exclude_trip: str | None,
) -> tuple[float, date]:
    """The fullest window the new duty falls in.

    That is not only the window ending on the duty date: adding hours today can break the limit
    on a trip already rostered later in the week, so every window ending on a later rostered
    day it overlaps is checked too.
    """
    new_days = [*earlier, day]
    horizon = day.day + timedelta(days=days - 1)
    ends = {day.day} | {d.day for d in crew.rostered(exclude_trip) if day.day < d.day <= horizon}
    totals = []
    for end in sorted(ends):
        start, _ = window(end, days)
        extra = sum(hours_of(d) for d in new_days if start <= d.day <= end)
        totals.append((round(recorded(start, end, exclude_trip) + extra, 2), end))
    return max(totals, key=lambda t: t[0])


def _window_finding(
    rule_id: str, total: float, end: date, day: DutyDay, limit: float, days: int
) -> Finding:
    label = f"{limit:g}h in {days} days"
    where = f"to {day.day}" if end == day.day else f"to {end}, a later rostered day"
    numbers = {"day": day.day, "used": total, "limit": limit}
    if total > limit:
        over = spoken_hours(total - limit)
        return failed(rule_id, f"{total}h {where}: {over} over {label}", **numbers)
    return passed(rule_id, f"{total}h {where}, within {label}", **numbers)


def check_duty_window(
    crew: CrewState,
    day: DutyDay,
    book: RuleBook,
    earlier: Iterable[DutyDay] = (),
    exclude_trip: str | None = None,
) -> Finding:
    total, end = _worst_window(
        crew, day, earlier, book.duty_days, crew.duty_between, lambda d: d.fdp_hours, exclude_trip
    )
    return _window_finding(DUTY, total, end, day, book.duty_hours, book.duty_days)


def check_block_window(
    crew: CrewState,
    day: DutyDay,
    book: RuleBook,
    earlier: Iterable[DutyDay] = (),
    exclude_trip: str | None = None,
) -> Finding:
    total, end = _worst_window(
        crew,
        day,
        earlier,
        book.block_days,
        crew.block_between,
        lambda d: d.block_hours,
        exclude_trip,
    )
    return _window_finding(BLOCK, total, end, day, book.block_hours, book.block_days)


def required_rest(book: RuleBook, preceding_duty_hours: float) -> float:
    """Rest after a duty is at least the minimum, and at least as long as that duty."""
    return max(book.rest_hours, round(preceding_duty_hours, 2))


def check_rest(
    crew: CrewState, day: DutyDay, book: RuleBook, exclude_trip: str | None = None
) -> Finding:
    """Rest before this duty, and before whatever they are rostered for next: covering
    tonight's trip must not strand tomorrow's departure."""
    if crew.rest_until is not None and day.report < crew.rest_until:
        short = spoken_hours(hours_between(day.report, crew.rest_until))
        return failed(
            REST,
            f"resting until {crew.rest_until:%d %b %H:%M}Z, {short} after report",
            day=day.day,
        )
    duties = crew.rostered(exclude_trip)
    # Two trips at once is the real problem; report it before any short rest around it.
    for duty in duties:
        if duty.report < day.release and day.report < duty.release:
            return failed(REST, f"already on {duty.trip_id} at that time", day=day.day)
    for duty in duties:
        if duty.report >= day.release:
            rest, need = hours_between(day.release, duty.report), required_rest(book, day.fdp_hours)
            where = f"before {duty.trip_id} on {duty.day:%d %b}"
        else:
            rest, need = hours_between(duty.release, day.report), required_rest(book, duty.hours)
            where = f"after {duty.trip_id} on {duty.day:%d %b}"
        if rest < need:
            detail = f"only {rest}h rest {where}, {need:g}h needed"
            return failed(REST, detail, day=day.day, used=rest, limit=need)
    return passed(REST, f"at least {book.rest_hours:g}h rest either side", day=day.day)


def check_weekly_rest(
    crew: CrewState,
    day: DutyDay,
    book: RuleBook,
    earlier: Iterable[DutyDay] = (),
    exclude_trip: str | None = None,
) -> Finding:
    """Every window of ``weekly_window_days`` that holds the new duty still has
    ``weekly_rest_days`` clear days in a row. Days past the end of the roster count as clear."""
    busy = crew.working_days(exclude_trip) | {d.day for d in earlier} | {day.day}
    span, need = book.weekly_window_days, book.weekly_rest_days
    for offset in range(span):
        end = day.day + timedelta(days=offset)
        start, _ = window(end, span)
        run = best = 0
        for i in range(span):
            run = 0 if start + timedelta(days=i) in busy else run + 1
            best = max(best, run)
        if best < need:
            return failed(
                WEEKLY,
                f"no {need} clear days in a row between {start:%d %b} and {end:%d %b}",
                day=day.day,
                used=best,
                limit=need,
            )
    return passed(WEEKLY, f"{need} clear days kept in every {span}-day window", day=day.day)


def check_rating(crew: CrewState, day: DutyDay) -> Finding:
    if day.aircraft_type not in crew.ratings:
        return failed(RATING, f"not rated on the {day.aircraft_type}", day=day.day)
    if crew.status is not CrewStatus.ACTIVE:
        return failed(RATING, f"status is {crew.status}, not active", day=day.day)
    return passed(RATING, f"rated on the {day.aircraft_type} and active", day=day.day)


def check_certificates(crew: CrewState, day: DutyDay) -> Finding:
    """Every certificate must still be valid on every date the duty touches, in IST.

    A certificate is not valid on its expiry date: nobody flies on the day it runs out, and a
    duty that lands after midnight must be covered on the later date too.
    """
    last = max(day.day, day.release.astimezone(IST).date(), day.report.astimezone(IST).date())
    for kind, expires in crew.certs:
        if expires <= last:
            label = kind.value.replace("_", " ")
            return failed(
                CERT, f"{label} runs out {expires:%d %b %Y}, not valid that day", day=day.day
            )
    return passed(CERT, f"all {len(crew.certs)} certificates valid on {day.day}", day=day.day)


def check_base(crew: CrewState, day: DutyDay, positioned: bool) -> Finding:
    if crew.base == day.origin:
        return passed(BASE, f"home base {crew.base}", day=day.day)
    if positioned:
        return passed(BASE, f"positioned from {crew.base} to {day.origin}", day=day.day)
    detail = f"based at {crew.base}, trip starts at {day.origin}, no positioning flight"
    return failed(BASE, detail, day=day.day)


def evaluate(
    crew: CrewState,
    days: list[DutyDay],
    book: RuleBook,
    exclude_trip: str | None = None,
    positioned: bool = False,
) -> list[Finding]:
    """Every rule on every day of a trip. The base rule applies to day one only: that is where
    the crew member has to get to; later days start where the trip night-stopped."""
    findings: list[Finding] = []
    for i, day in enumerate(days):
        earlier = days[:i]
        findings += [
            check_rating(crew, day),
            check_certificates(crew, day),
            check_fdp(day, book),
            check_night(day, book),
            check_duty_window(crew, day, book, earlier, exclude_trip),
            check_block_window(crew, day, book, earlier, exclude_trip),
            check_rest(crew, day, book, exclude_trip),
            check_weekly_rest(crew, day, book, earlier, exclude_trip),
        ]
        if i == 0:
            findings.append(check_base(crew, day, positioned))
    return findings


def blockers(findings: Iterable[Finding]) -> list[Finding]:
    """The failures, each distinct rule and detail once."""
    seen: set[tuple[str, str]] = set()
    out = []
    for f in findings:
        if f.failed and (f.rule_id, f.detail) not in seen:
            seen.add((f.rule_id, f.detail))
            out.append(f)
    return out
