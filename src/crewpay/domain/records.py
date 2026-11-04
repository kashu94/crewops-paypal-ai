"""Typed records for one crew operation.

Everything the rest of the system reads about the operation is one of these models. They are
frozen and validated on the way in, so a bad seed file or upload fails at load time with the
field named, and nothing downstream ever handles a raw dict or a timestamp string.

Conventions: instants are timezone-aware UTC, calendar fields are dates (UTC), standby windows
are UTC times of day, and money is whole rupees.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _station(value: str) -> str:
    if len(value) != 3 or not value.isalpha() or not value.isupper():
        raise ValueError(f"expected a 3-letter station code, got {value!r}")
    return value


Instant = Annotated[AwareDatetime, AfterValidator(_utc)]
Station = Annotated[str, AfterValidator(_station)]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Rank(StrEnum):
    CAPTAIN = "Captain"
    FIRST_OFFICER = "First Officer"
    PURSER = "Purser"
    CABIN_CREW = "Cabin Crew"

    @property
    def is_pilot(self) -> bool:
        return self in (Rank.CAPTAIN, Rank.FIRST_OFFICER)

    @property
    def is_cabin(self) -> bool:
        return not self.is_pilot


class AircraftType(StrEnum):
    A320 = "A320"
    ATR72 = "ATR72"


class CrewStatus(StrEnum):
    ACTIVE = "active"
    LEAVE = "leave"
    TRAINING = "training"


class CertKind(StrEnum):
    LICENCE = "licence"
    MEDICAL = "medical"
    RECURRENT = "recurrent_training"
    SAFETY_EQUIPMENT = "safety_equipment"


class Aircraft(Record):
    tail: str
    type: AircraftType
    seats: int = Field(gt=0)


class Airline(Record):
    code: str = Field(pattern=r"^[A-Z0-9]{2}$")
    name: str
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    hub: Station
    fleet: tuple[Aircraft, ...]


class Crew(Record):
    crew_id: str = Field(pattern=r"^C-\d{4}$")
    name: str
    rank: Rank
    airline: str
    base: Station
    ratings: tuple[AircraftType, ...]
    years_of_service: int = Field(ge=0)
    """Paid as an allowance on a callout, so seniority only ever ranks through cost."""
    minutes_to_airport: int = Field(ge=0)
    status: CrewStatus = CrewStatus.ACTIVE
    command_qualified: bool = False
    """First officer cleared to command; may fill a captain's seat when captains run short."""
    right_seat_qualified: bool = False
    """Captain current in the right seat; may fill a first officer's seat."""


class Certificate(Record):
    crew_id: str
    kind: CertKind
    expires: date


class DayHours(Record):
    day: date
    duty_hours: float = Field(ge=0)
    block_hours: float = Field(ge=0)


class DutyHistory(Record):
    """Hours already worked before the published roster starts."""

    crew_id: str
    rest_until: Instant
    """End of their last rest: the earliest they may report again."""
    days: tuple[DayHours, ...]


class Standby(Record):
    """Days a crew member is on standby, and the daily window a callout must fall in."""

    crew_id: str
    base: Station
    dates: tuple[date, ...]
    window_start: time
    window_end: time

    @model_validator(mode="after")
    def _window(self) -> Standby:
        if self.window_end <= self.window_start:
            raise ValueError("standby window must end after it starts")
        return self

    def covers(self, when: datetime) -> bool:
        moment = when.astimezone(UTC)
        clock = moment.time().replace(tzinfo=None)
        return moment.date() in self.dates and self.window_start <= clock <= self.window_end


class Unavailability(Record):
    """Someone who cannot fly on these dates: sick, a family emergency, stuck in traffic.

    Reported by the crew member or a controller. Until it is cleared, the engine treats them
    as unavailable on every date from ``first_day`` to ``last_day``, inclusive. With ``since``,
    they fell ill part-way through ``first_day``: a duty that day which ends before then is
    still theirs to fly.
    """

    report_id: str = Field(pattern=r"^U-[0-9a-f]{8}$")
    crew_id: str = Field(pattern=r"^C-\d{4}$")
    first_day: date
    last_day: date
    reason: str = Field(min_length=2, max_length=200)
    reported_by: str
    reported_at: Instant
    kind: Literal["sick", "fatigue", "personal", "leave", "certificate", "other"] = "other"
    since: Instant | None = None

    @model_validator(mode="after")
    def _span(self) -> Unavailability:
        if self.last_day < self.first_day:
            raise ValueError("last_day is before first_day")
        if (self.last_day - self.first_day).days > 30:
            raise ValueError("report at most 31 days at a time")
        return self

    def days(self) -> tuple[date, ...]:
        span = (self.last_day - self.first_day).days + 1
        return tuple(date.fromordinal(self.first_day.toordinal() + i) for i in range(span))


class GateKind(StrEnum):
    CONTACT = "contact"
    """Boards through a jet bridge or a short walk from the terminal."""
    REMOTE = "remote"
    """A stand away from the terminal: passengers go by bus, which takes longer."""
    EMERGENCY = "emergency"
    """Kept free for when another gate is lost."""


class Gate(Record):
    """A departure gate or stand at a station, as allotted to the operation."""

    station: Station
    gate: str = Field(min_length=1, max_length=8)
    kind: GateKind


class Leg(Record):
    """One flight on one day."""

    leg_id: str
    """Unique per day, e.g. ``SY215-2026-11-03``."""
    flight_no: str
    day: date
    origin: Station
    destination: Station
    off_block: Instant
    on_block: Instant
    tail: str
    aircraft_type: AircraftType
    seats: int = Field(gt=0)
    booked: int = Field(ge=0)
    gate: str | None = None
    """The departure gate or stand at ``origin``."""
    cancelled: bool = False
    """Cancelled by an approved plan: nobody flies or positions on it."""

    @model_validator(mode="after")
    def _consistent(self) -> Leg:
        if self.on_block <= self.off_block:
            raise ValueError(f"{self.leg_id}: arrives before it departs")
        if self.booked > self.seats:
            raise ValueError(f"{self.leg_id}: {self.booked} booked on {self.seats} seats")
        return self

    @property
    def block_hours(self) -> float:
        return round((self.on_block - self.off_block).total_seconds() / 3600, 2)


class TripDay(Record):
    day: date
    legs: tuple[str, ...] = Field(min_length=1)
    report: Instant
    release: Instant

    @model_validator(mode="after")
    def _order(self) -> TripDay:
        if self.release <= self.report:
            raise ValueError(f"trip day {self.day}: release is not after report")
        return self


class Seat(Record):
    crew_id: str
    rank: Rank


class Trip(Record):
    """A crew trip on one aircraft: one or more duty days and the people who fly it."""

    trip_id: str = Field(pattern=r"^T-\d{4}$")
    airline: str
    tail: str
    days: tuple[TripDay, ...] = Field(min_length=1)
    crew: tuple[Seat, ...]

    @property
    def first_day(self) -> TripDay:
        return self.days[0]

    @property
    def leg_ids(self) -> tuple[str, ...]:
        return tuple(leg for day in self.days for leg in day.legs)

    def holders(self, rank: Rank) -> tuple[str, ...]:
        return tuple(seat.crew_id for seat in self.crew if seat.rank is rank)


class Rule(Record):
    rule_id: str
    title: str
    text: str
    params: dict[str, float] = Field(default_factory=dict)


class Rates(Record):
    """What each way of covering a seat costs, in rupees."""

    currency: str = "INR"
    standby_callout_pilot: int
    standby_callout_cabin: int
    day_off_callout_pilot: int
    day_off_callout_cabin: int
    positioning: int
    delay_per_hour: int
    cancellation_per_leg: int
    hotel_night: int
    service_allowance_pilot: int
    """Per year of service on a callout."""
    service_allowance_cabin: int
    travel_per_10_minutes: int

    def callout(self, rank: Rank, on_standby: bool) -> int:
        if on_standby:
            return self.standby_callout_pilot if rank.is_pilot else self.standby_callout_cabin
        return self.day_off_callout_pilot if rank.is_pilot else self.day_off_callout_cabin

    def service_allowance(self, rank: Rank, years: int) -> int:
        rate = self.service_allowance_pilot if rank.is_pilot else self.service_allowance_cabin
        return rate * years

    def travel(self, minutes: int) -> int:
        return self.travel_per_10_minutes * -(-minutes // 10)
