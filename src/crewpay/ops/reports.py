"""Disruption reports: one record per thing that went wrong, from the report to the sign-off.

A report is filed by whoever saw the problem: a station duty manager, a controller, a crew
member reporting themselves sick, an admin booking planned leave. Filing it does three things
at once: it goes to the desk with the fewest open disruptions (``crewpay.ops.desks``), the
crew desk and the teams the kind of problem needs are told (``crewpay.ops.teams``), and its
analysis is done by the rules engine, so the desk opens it with the seats to fill already
costed.

    OPEN --plan or callout--> IN_PROGRESS --controller closes--> RESOLVED --approver--> CLOSED
                                    ^                                |
                                    '-------- approver returns ------'
    any state but CLOSED --admin--> REVOKED   (filed in error; its absences are withdrawn)

Only the owning desk's controller works a report; an approver or an admin can move it to
another desk. A controller cannot close their own work for good: an approver signs off the
resolution, or returns it with a note.
"""

from __future__ import annotations

import threading
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from crewpay.engine.operation import NotFound
from crewpay.ops.desks import DESKS, Desk, assign_desk, desk
from crewpay.ops.notify import DESK, Notice, Outbox
from crewpay.ops.teams import teams_to_tell


class Kind(StrEnum):
    CREW_UNAVAILABLE = "crew_unavailable"
    PLANNED_LEAVE = "planned_leave"
    CERTIFICATION_LAPSE = "certification_lapse"
    FLIGHT_DELAY = "flight_delay"
    AIRCRAFT_TECHNICAL = "aircraft_technical"
    STATION_DISRUPTION = "station_disruption"
    GATE_CLOSURE = "gate_closure"
    GATE_CHANGE = "gate_change"


ABSENCES = frozenset({Kind.CREW_UNAVAILABLE, Kind.PLANNED_LEAVE, Kind.CERTIFICATION_LAPSE})
"""Reports that take crew off the roster for some days, leaving seats to fill."""

LABELS = {
    Kind.CREW_UNAVAILABLE: "Crew unavailable",
    Kind.PLANNED_LEAVE: "Planned leave",
    Kind.CERTIFICATION_LAPSE: "Certificate lapse",
    Kind.FLIGHT_DELAY: "Flight delay",
    Kind.AIRCRAFT_TECHNICAL: "Aircraft fault",
    Kind.STATION_DISRUPTION: "Station closed",
    Kind.GATE_CLOSURE: "Gate closure",
    Kind.GATE_CHANGE: "Gate change",
}


class ReportStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REVOKED = "revoked"


ACTIVE = (ReportStatus.OPEN, ReportStatus.IN_PROGRESS, ReportStatus.RESOLVED)
WORKING = (ReportStatus.OPEN, ReportStatus.IN_PROGRESS)
"""States a desk still has work in; these count towards its load."""


class ReportError(ValueError):
    """A request the report cannot take in its current state."""


@dataclass
class Report:
    report_id: str
    kind: Kind
    title: str
    details: dict[str, Any]
    filed_by: str
    filed_role: str
    filed_at: datetime
    desk_id: str
    severity: str
    severity_reasons: list[str]
    stations: list[str]
    status: ReportStatus = ReportStatus.OPEN
    crew_ids: list[str] = field(default_factory=list)
    unavailability_ids: list[str] = field(default_factory=list)
    plan_ids: list[str] = field(default_factory=list)
    callout_ids: list[str] = field(default_factory=list)
    resolution_note: str = ""
    changes: list[dict[str, Any]] = field(default_factory=list)
    """Flight changes the resolution makes (``crewpay.ops.flight_changes``), made only when an
    approver confirms it."""
    history: list[dict[str, Any]] = field(default_factory=list)


class Docs(Protocol):
    def put_doc(self, kind: str, key: str, body: dict) -> None: ...
    def docs(self, kind: str) -> list[dict]: ...


_report = TypeAdapter(Report)


class ReportDesk:
    def __init__(
        self,
        docs: Docs,
        outbox: Outbox,
        seed_dir: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.docs = docs
        self.outbox = outbox
        self.seed_dir = seed_dir
        self.clock = clock
        self._lock = threading.RLock()

    # -- reading ----------------------------------------------------------------------------

    def all(self) -> list[Report]:
        found = [_report.validate_python(d) for d in self.docs.docs("report")]
        return sorted(found, key=lambda r: r.filed_at, reverse=True)

    def get(self, report_id: str) -> Report:
        found = next((r for r in self.all() if r.report_id == report_id), None)
        if found is None:
            raise NotFound("report", report_id)
        return found

    def load(self) -> list[dict[str, Any]]:
        """Each desk's disruptions by state."""
        counts = {
            d.desk_id: Counter(str(r.status) for r in self.all() if r.desk_id == d.desk_id)
            for d in DESKS
        }
        return [
            {
                "desk_id": d.desk_id,
                "name": d.name,
                "controller": d.controller,
                "controller_name": d.controller_name,
                **{str(s): counts[d.desk_id][str(s)] for s in ReportStatus},
                "total": sum(counts[d.desk_id].values()),
            }
            for d in DESKS
        ]

    def teams(self, report: Report) -> list[dict]:
        return teams_to_tell(self.seed_dir, report.kind, report.stations)

    # -- filing -----------------------------------------------------------------------------

    def _save(self, report: Report) -> None:
        self.docs.put_doc("report", report.report_id, _report.dump_python(report, mode="json"))

    def _note(self, report: Report, what: str, by: str, note: str = "") -> None:
        report.history.append(
            {
                "at": self.clock().isoformat(),
                "what": what,
                "by": by,
                **({"note": note} if note else {}),
            }
        )

    def file(
        self,
        kind: Kind,
        title: str,
        details: dict[str, Any],
        by: str,
        role: str,
        severity: tuple[str, list[str]],
        stations: list[str],
        crew_ids: list[str] | None = None,
        unavailability_ids: list[str] | None = None,
    ) -> Report:
        with self._lock:
            reports = self.all()
            working = Counter(r.desk_id for r in reports if r.status in WORKING)
            totals = Counter(r.desk_id for r in reports)
            owner = assign_desk(working, totals)
            now = self.clock()
            report = Report(
                report_id=f"R-{uuid.uuid4().hex[:6]}",
                kind=kind,
                title=title,
                details=details,
                filed_by=by,
                filed_role=role,
                filed_at=now,
                desk_id=owner.desk_id,
                severity=severity[0],
                severity_reasons=list(severity[1]),
                stations=sorted(set(stations)),
                crew_ids=list(crew_ids or []),
                unavailability_ids=list(unavailability_ids or []),
            )
            self._note(report, "reported", by, details.get("note", ""))
            self._note(
                report,
                "assigned",
                "crewpay",
                f"{owner.name} ({owner.controller_name}): fewest open disruptions",
            )
            self._save(report)
        self.outbox.send(
            Notice(
                to=DESK,
                subject=f"New disruption: {title}",
                body=f"{LABELS[kind]} reported by {by}. Assigned to {owner.name} "
                f"({owner.controller_name}); severity {report.severity} "
                f"({'; '.join(report.severity_reasons)}).",
                at=now,
                ref=report.report_id,
            )
        )
        for team in self.teams(report):
            where = f" at {team['station']}" if team["station"] else ""
            self.outbox.send(
                Notice(
                    to=f"team:{team['team']}",
                    subject=f"{LABELS[kind]}{where}: {title}",
                    body=f"For {team['title']}{where}, who {team['job']}. {title}. "
                    f"Crew control ({owner.name}) has it as {report.report_id}.",
                    at=now,
                    ref=report.report_id,
                )
            )
        return report

    # -- working it -------------------------------------------------------------------------

    def owner(self, report: Report) -> Desk:
        found = desk(report.desk_id)
        assert found is not None
        return found

    def _active(self, report_id: str) -> Report:
        report = self.get(report_id)
        if report.status not in ACTIVE:
            raise ReportError(f"{report_id} is {report.status}")
        return report

    def _mine(self, report: Report, by: str) -> None:
        owner = self.owner(report)
        if owner.controller != by:
            raise ReportError(
                f"{report.report_id} belongs to {owner.name} ({owner.controller_name}); only "
                "its controller works it"
            )

    def link(self, report_id: str, by: str, plan_id: str = "", callout_id: str = "") -> Report:
        """Record a plan or a callout made for the report; its work is now in progress."""
        with self._lock:
            report = self._active(report_id)
            self._mine(report, by)
            if plan_id and plan_id not in report.plan_ids:
                report.plan_ids.append(plan_id)
                self._note(report, "plan submitted", by, plan_id)
            if callout_id and callout_id not in report.callout_ids:
                report.callout_ids.append(callout_id)
                self._note(report, "callout started", by, callout_id)
            if report.status is ReportStatus.OPEN:
                report.status = ReportStatus.IN_PROGRESS
            self._save(report)
            return report

    def check_mine(self, report_id: str, by: str) -> Report:
        report = self._active(report_id)
        self._mine(report, by)
        return report

    def close(
        self, report_id: str, by: str, note: str, changes: list[dict[str, Any]] | None = None
    ) -> Report:
        if len(note.strip()) < 3:
            raise ReportError("say how it was resolved")
        with self._lock:
            report = self._active(report_id)
            self._mine(report, by)
            if report.status is ReportStatus.RESOLVED:
                raise ReportError(f"{report_id} already waits for an approver to confirm it")
            report.status = ReportStatus.RESOLVED
            report.resolution_note = note.strip()
            report.changes = list(changes or [])
            self._note(report, "resolved", by, note.strip())
            self._save(report)
        self.outbox.send(
            Notice(
                to=DESK,
                subject=f"Approval needed: resolution of {report.report_id}",
                body=f"{by} resolved {report.title}: {report.resolution_note}. An approver "
                "must confirm it or return it.",
                at=self.clock(),
                ref=report.report_id,
            )
        )
        return report

    def _resolved(self, report_id: str) -> Report:
        report = self.get(report_id)
        if report.status is not ReportStatus.RESOLVED:
            raise ReportError(f"{report_id} is {report.status}, not waiting for sign-off")
        return report

    def confirm(self, report_id: str, by: str) -> Report:
        with self._lock:
            report = self._resolved(report_id)
            report.status = ReportStatus.CLOSED
            self._note(report, "closed", by)
            self._save(report)
        self.outbox.send(
            Notice(
                DESK,
                f"Closed: {report.title}",
                f"{by} confirmed the resolution.",
                self.clock(),
                report.report_id,
            )
        )
        return report

    def give_back(self, report_id: str, by: str, note: str) -> Report:
        if len(note.strip()) < 3:
            raise ReportError("say what is still missing")
        with self._lock:
            report = self._resolved(report_id)
            report.status = ReportStatus.IN_PROGRESS
            report.changes = []
            self._note(report, "returned", by, note.strip())
            self._save(report)
        self.outbox.send(
            Notice(
                DESK,
                f"Sent back: {report.title}",
                f"{by} returned it: {note.strip()}",
                self.clock(),
                report.report_id,
            )
        )
        return report

    def reassign(self, report_id: str, desk_id: str, by: str, note: str) -> Report:
        target = desk(desk_id)
        if target is None:
            raise ReportError(f"no desk {desk_id}; desks are {', '.join(d.desk_id for d in DESKS)}")
        with self._lock:
            report = self._active(report_id)
            if report.desk_id == desk_id:
                raise ReportError(f"{report_id} is already on {target.name}")
            report.desk_id = desk_id
            self._note(report, "reassigned", by, f"to {target.name}: {note.strip()}".rstrip(": "))
            self._save(report)
            return report

    def revoke(self, report_id: str, by: str, reason: str) -> Report:
        if len(reason.strip()) < 3:
            raise ReportError("say why the report is revoked")
        with self._lock:
            report = self.get(report_id)
            if report.status in (ReportStatus.CLOSED, ReportStatus.REVOKED):
                raise ReportError(f"{report_id} is {report.status}")
            report.status = ReportStatus.REVOKED
            self._note(report, "revoked", by, reason.strip())
            self._save(report)
            return report


def evidence_level(details: dict[str, Any]) -> dict[str, Any]:
    """How far a report has been checked against a photo, for the lists and the desk."""
    photos = details.get("evidence") or []
    if not photos:
        return {"level": "none", "label": "No photo"}
    rank = {"high": 3, "medium": 2, "low": 1}
    best = max((p.get("confidence") or "" for p in photos), key=lambda c: rank.get(c, 2))
    if best in rank:
        return {"level": best, "label": f"Photo checked, {best} confidence", "photos": len(photos)}
    return {"level": "read", "label": "Photo checked", "photos": len(photos)}


def summary(report: Report) -> dict[str, Any]:
    owner = desk(report.desk_id)
    return {
        "report_id": report.report_id,
        "kind": str(report.kind),
        "label": LABELS[report.kind],
        "title": report.title,
        "status": str(report.status),
        "desk": {
            "desk_id": report.desk_id,
            "name": owner.name if owner else report.desk_id,
            "controller": owner.controller if owner else None,
            "controller_name": owner.controller_name if owner else None,
        },
        "filed_by": report.filed_by,
        "filed_role": report.filed_role,
        "filed_at_iso": report.filed_at.isoformat(),
        "severity": {"level": report.severity, "reasons": report.severity_reasons},
        "crew_ids": report.crew_ids,
        "stations": report.stations,
        "evidence": evidence_level(report.details),
    }
