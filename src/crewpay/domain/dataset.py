"""Every record of one operation, as loaded from files or the database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from crewpay.domain.records import (
    Airline,
    Certificate,
    Crew,
    DutyHistory,
    Gate,
    Leg,
    Rates,
    Rule,
    Standby,
    Trip,
)


@dataclass(frozen=True, slots=True)
class Dataset:
    airlines: tuple[Airline, ...]
    crew: tuple[Crew, ...]
    certificates: tuple[Certificate, ...]
    histories: tuple[DutyHistory, ...]
    standby: tuple[Standby, ...]
    legs: tuple[Leg, ...]
    trips: tuple[Trip, ...]
    rules: tuple[Rule, ...]
    rates: Rates
    as_of: datetime
    """The moment the duty histories were taken. Nothing before it can be re-crewed."""
    gates: tuple[Gate, ...] = ()
