"""The pieces every store shares: record keys, roster changes and the operation cache.

``Store.operation()`` is how the rest of the system gets the operation: built from the table
once and kept in memory until the roster changes. Every change to a trip's crew goes through
``change_crew``, which only succeeds against the version of the trip the caller last saw. If
someone else changed it in between, the caller gets ``StaleTrip`` and must look again, so two
controllers can never quietly overwrite each other.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, TypeAdapter

from crewpay.domain import (
    Airline,
    Certificate,
    Crew,
    DutyHistory,
    Gate,
    Leg,
    Rule,
    Seat,
    Standby,
    Unavailability,
)
from crewpay.domain.dataset import Dataset
from crewpay.engine import Operation, build_operation
from crewpay.engine.clock import now as clock_now

TRIP = "TRIP"
CHANGE = "CHANGE"
META = "META"
UNAVAILABLE = "UNAVAIL"


def new_report(
    crew_id: str,
    first_day: date,
    last_day: date,
    reason: str,
    by: str,
    at: datetime | None,
    kind: str = "other",
    since: datetime | None = None,
) -> Unavailability:
    return Unavailability(
        report_id=f"U-{uuid.uuid4().hex[:8]}",
        crew_id=crew_id,
        first_day=first_day,
        last_day=last_day,
        reason=reason,
        reported_by=by,
        reported_at=(at or clock_now()),
        kind=kind,
        since=since,
    )


ROSTER_VERSION = "roster_version"
BATCH_SIZE = 25


class StaleTrip(RuntimeError):
    """The trip changed since the caller read it."""

    def __init__(self, trip_id: str, expected: int, actual: int) -> None:
        super().__init__(f"{trip_id} is at version {actual}, not {expected}: read it again")
        self.trip_id = trip_id
        self.expected = expected
        self.actual = actual


def _key(kind: str, record: BaseModel) -> str:
    match kind:
        case "airlines":
            return record.code
        case "crew" | "histories":
            return record.crew_id
        case "certificates":
            return f"{record.crew_id}:{record.kind}"
        case "standby":
            return f"{record.crew_id}:{record.dates[0].isoformat()}"
        case "legs":
            return record.leg_id
        case "rules":
            return record.rule_id
        case "gates":
            return f"{record.station}:{record.gate}"
    raise ValueError(f"unknown record kind {kind!r}")


KINDS: dict[str, type[BaseModel]] = {
    "airlines": Airline,
    "crew": Crew,
    "certificates": Certificate,
    "histories": DutyHistory,
    "standby": Standby,
    "legs": Leg,
    "rules": Rule,
    "gates": Gate,
}


@dataclass(frozen=True, slots=True)
class RosterChange:
    change_id: str
    trip_id: str
    version: int
    at: datetime
    actor: str
    reason: str
    before: tuple[Seat, ...]
    after: tuple[Seat, ...]

    @property
    def added(self) -> tuple[str, ...]:
        old = {s.crew_id for s in self.before}
        return tuple(s.crew_id for s in self.after if s.crew_id not in old)

    @property
    def removed(self) -> tuple[str, ...]:
        new = {s.crew_id for s in self.after}
        return tuple(s.crew_id for s in self.before if s.crew_id not in new)


_seats = TypeAdapter(tuple[Seat, ...])


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _n(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _json(value: Any) -> dict[str, str]:
    return _s(json.dumps(value, separators=(",", ":")))


def _item(pk: str, sk: str, **attrs: dict[str, str]) -> dict[str, dict[str, str]]:
    return {"pk": _s(pk), "sk": _s(sk), **attrs}


class OperationCache:
    """Builds the operation from a store's records, again only when the roster version moves."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached: tuple[int, Operation] | None = None
        self.live = False
        """Judge the roster at the wall clock when no moment is given."""
        self.clock: Callable[[], datetime] | None = None
        """Or at this clock: the demo's own, so the header, offers and payments agree."""

    def roster_version(self) -> int:
        raise NotImplementedError

    def dataset(self) -> Dataset:
        raise NotImplementedError

    def unavailability(self, crew_id: str | None = None) -> list[Unavailability]:
        raise NotImplementedError

    def operation(self, now: datetime | None = None) -> Operation:
        """The operation at ``now``, rebuilt only when the roster has changed since last time.

        Reported unavailability is part of it: nobody is offered for a date they cannot fly.
        """
        version = self.roster_version()
        with self._lock:
            if self._cached is None or self._cached[0] != version:
                op = build_operation(self.dataset())
                out: dict[str, list[tuple[date, str]]] = {}
                since: dict[str, datetime] = {}
                for u in self.unavailability():
                    out.setdefault(u.crew_id, []).extend((d, u.report_id) for d in u.days())
                    if u.since is not None:
                        since[u.report_id] = u.since
                op = replace(
                    op,
                    unavailable={k: tuple(v) for k, v in out.items()},
                    unavailable_since=since,
                )
                self._cached = (version, op)
            op = self._cached[1]
        if now is None and self.clock is not None:
            now = self.clock()
        elif now is None and self.live:
            now = datetime.now(UTC)
        if now is not None and now > op.now:
            op = replace(op, now=now)
        return op
