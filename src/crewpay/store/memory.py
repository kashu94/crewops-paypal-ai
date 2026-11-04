"""The operation held in memory, the one store CrewPay has.

Used by tests and to run the tools and the MCP server on a laptop without AWS
(``CREWPAY_STORE=memory``). Changes last as long as the process.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from crewpay.domain import Rule, Seat, Trip, Unavailability
from crewpay.domain.dataset import Dataset
from crewpay.engine import NotFound
from crewpay.store.roster import (
    KINDS,
    OperationCache,
    RosterChange,
    StaleTrip,
    _key,
    _seats,
    new_report,
)


class MemoryStore(OperationCache):
    def __init__(self) -> None:
        super().__init__()
        self._write_lock = threading.Lock()
        self._data: Dataset | None = None
        self._meta: dict[str, Any] = {}
        self._trips: dict[str, tuple[Trip, int]] = {}
        self._changes: list[RosterChange] = []
        self._unavailable: dict[str, Unavailability] = {}
        self._docs: dict[str, dict[str, dict[str, Any]]] = {}
        self._version = 0

    def is_empty(self) -> bool:
        return not self._trips

    def load(self, data: Dataset, meta: dict[str, Any]) -> None:
        with self._write_lock:
            self._data = data
            self._meta = {**copy.deepcopy(meta), "as_of": data.as_of.isoformat()}
            self._trips = {t.trip_id: (t, 1) for t in data.trips}
            self._changes = []
            self._unavailable = {}
            self._docs = {}
            self._version = 1

    def meta(self) -> dict[str, Any]:
        return copy.deepcopy(self._meta)

    def roster_version(self) -> int:
        return self._version

    def dataset(self) -> Dataset:
        if self._data is None:
            raise NotFound("operation", "nothing loaded")
        trips = tuple(t for t, _ in sorted(self._trips.values(), key=lambda tv: tv[0].trip_id))
        return replace(self._data, trips=trips)

    def trip(self, trip_id: str) -> tuple[Trip, int]:
        if trip_id not in self._trips:
            raise NotFound("trip", trip_id)
        return self._trips[trip_id]

    def change_crew(
        self,
        trip_id: str,
        crew: Iterable[Seat],
        expected_version: int,
        actor: str,
        reason: str,
        at: datetime | None = None,
    ) -> int:
        at = (at or datetime.now(UTC)).astimezone(UTC)
        seats = _seats.validate_python(list(crew))
        with self._write_lock:
            before, current = self.trip(trip_id)
            if current != expected_version:
                raise StaleTrip(trip_id, expected_version, current)
            version = current + 1
            self._trips[trip_id] = (before.model_copy(update={"crew": seats}), version)
            self._changes.append(
                RosterChange(
                    change_id=f"{at.isoformat()}#{trip_id}#{version:06d}",
                    trip_id=trip_id,
                    version=version,
                    at=at,
                    actor=actor,
                    reason=reason,
                    before=before.crew,
                    after=seats,
                )
            )
            self._version += 1
        return version

    def mark_unavailable(
        self,
        crew_id: str,
        first_day: date,
        last_day: date,
        reason: str,
        by: str,
        at: datetime | None = None,
        kind: str = "other",
        since: datetime | None = None,
    ) -> Unavailability:
        self.operation().state(crew_id)
        report = new_report(crew_id, first_day, last_day, reason, by, at, kind, since)
        with self._write_lock:
            self._unavailable[report.report_id] = report
            self._version += 1
        return report

    def put_records(self, kind: str, records: Iterable[Any]) -> None:
        """Add or replace reference records of one kind (crew, certificates, standby), by id."""
        if kind not in KINDS or kind == "rules":
            raise ValueError(f"cannot write {kind} records")
        with self._write_lock:
            assert self._data is not None
            merged = {_key(kind, r): r for r in getattr(self._data, kind)}
            merged.update({_key(kind, r): r for r in records})
            self._data = replace(self._data, **{kind: tuple(merged.values())})
            self._version += 1

    def add_trips(self, trips: Iterable[Trip]) -> None:
        """Add trips the roster does not have yet, at version 1."""
        with self._write_lock:
            for trip in trips:
                if trip.trip_id in self._trips:
                    raise ValueError(f"{trip.trip_id} already exists")
                self._trips[trip.trip_id] = (trip, 1)
            self._version += 1

    def set_rule(self, rule: Rule) -> None:
        """Replace one duty rule; the operation is judged by it at once."""
        with self._write_lock:
            if self._data is None:
                raise NotFound("operation", "nothing loaded")
            rules = tuple(rule if r.rule_id == rule.rule_id else r for r in self._data.rules)
            self._data = replace(self._data, rules=rules)
            self._version += 1

    def clear_unavailable(self, report_id: str) -> Unavailability:
        with self._write_lock:
            report = self._unavailable.pop(report_id, None)
            if report is None:
                raise NotFound("unavailability report", report_id)
            self._version += 1
        return report

    def unavailability(self, crew_id: str | None = None) -> list[Unavailability]:
        found = sorted(self._unavailable.values(), key=lambda u: (u.reported_at, u.report_id))
        return [u for u in found if crew_id is None or u.crew_id == crew_id]

    def put_doc(self, kind: str, key: str, body: dict[str, Any]) -> None:
        with self._write_lock:
            self._docs.setdefault(kind, {})[key] = copy.deepcopy(body)

    def get_doc(self, kind: str, key: str) -> dict[str, Any] | None:
        found = self._docs.get(kind, {}).get(key)
        return copy.deepcopy(found) if found is not None else None

    def docs(self, kind: str) -> list[dict[str, Any]]:
        found = self._docs.get(kind, {})
        return [copy.deepcopy(found[k]) for k in sorted(found)]

    def delete_doc(self, kind: str, key: str) -> None:
        with self._write_lock:
            self._docs.get(kind, {}).pop(key, None)

    def changes(self, trip_id: str | None = None) -> list[RosterChange]:
        found = sorted(self._changes, key=lambda c: c.change_id)
        return [c for c in found if trip_id is None or c.trip_id == trip_id]
