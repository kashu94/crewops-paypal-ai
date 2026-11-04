"""Duty-time arithmetic.

Two conventions decide most legality answers and are easy to get subtly wrong, so they live
here and nowhere else:

* Hour limits use calendar-day windows (UTC dates, the duty date included), not rolling
  168- or 672-hour windows measured from an instant. Seven days ending on the 14th are the
  8th to the 14th.
* Night is local time at the operator's home (India Standard Time), because that is the
  body clock the night-duty rule protects.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from crewpay.domain import AircraftType

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def hours_between(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 3600, 2)


def window(end: date, days: int) -> tuple[date, date]:
    """Inclusive window of ``days`` calendar days ending on ``end``."""
    return end - timedelta(days=days - 1), end


def spoken_hours(value: float) -> str:
    """``1.33`` -> ``'1h20m'``: the form a controller reads a margin in."""
    minutes = round(abs(value) * 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


def in_local_night(moment: datetime, start: time, end: time) -> bool:
    """Whether ``moment`` falls in the local night window (``start`` <= t < ``end``)."""
    local = moment.astimezone(IST).time().replace(tzinfo=None)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def overlaps_local_night(report: datetime, release: datetime, start: time, end: time) -> bool:
    """Whether any part of a duty period touches the local night window."""
    if in_local_night(report, start, end) or in_local_night(release, start, end):
        return True
    # A duty can span the whole window without either end inside it.
    probe = report.astimezone(IST)
    finish = release.astimezone(IST)
    day = probe.date()
    while datetime.combine(day, start, IST) <= finish:
        night_start = datetime.combine(day, start, IST)
        if probe <= night_start <= finish:
            return True
        day += timedelta(days=1)
    return False


@dataclass(frozen=True, slots=True)
class DutyDay:
    """One duty day as the rules see it."""

    day: date
    report: datetime
    release: datetime
    sectors: int
    block_hours: float
    aircraft_type: AircraftType
    origin: str
    landings: tuple[datetime, ...]
    """Touchdown time of each leg, for the night-landing limit."""

    @property
    def fdp_hours(self) -> float:
        return hours_between(self.report, self.release)

    def delayed(self, hours: float) -> DutyDay:
        """A late first departure. Crew still report on time, so the day gets longer and every
        landing moves; this is how a delay pushes someone past a limit."""
        shift = timedelta(hours=hours)
        return replace(
            self,
            release=self.release + shift,
            landings=tuple(t + shift for t in self.landings),
        )

    def shifted(self, shift: timedelta) -> DutyDay:
        """The whole day moved later (crew positioning in first). Its length is unchanged."""
        return replace(
            self,
            report=self.report + shift,
            release=self.release + shift,
            landings=tuple(t + shift for t in self.landings),
        )
