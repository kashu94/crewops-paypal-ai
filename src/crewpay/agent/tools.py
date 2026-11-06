"""The tools a model may call, and who may call each.

Every tool is a thin, typed wrapper over the rules engine or the store. The model picks a tool
and its arguments; the tool validates them, checks the caller may use it, and returns plain
JSON built from the engine's results. No tool asks a model anything, so every crew id, hour
and rupee in an answer can be traced back to a tool result.

Roles:

* ``crew`` read their own record and the trips they fly, nothing about anyone else, answer
  callouts offered to them and report themselves unavailable.
* ``reporter`` (a station duty manager) sees the operation's flights and files disruption
  reports, and follows the ones they filed.
* ``controller`` owns a desk: works the disruptions assigned to it, finds cover, runs
  callouts and proposes roster changes, only to crew the engine offers as legal cover.
* ``approver`` is the second pair of eyes: approves, rejects or sends back each change, and
  signs off a controller's resolution.
* ``auditor`` reads everything, the activity and security logs included, and changes nothing.
* ``admin`` looks after the team and the system: accounts, passwords, alert addresses, roster
  uploads, planned leave, reports filed in error, and duty limits, stricter than the
  regulator's only.
* ``super_admin`` does what an admin does, manages the admins, and may relax a duty limit
  back towards the regulator's figure, never past it.
* ``speaker`` is the ops-room speaker, a device: it hears what is waiting and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from crewpay.domain import Rank, Seat, Trip, Unavailability
from crewpay.engine import Finding, NotFound, Operation, assess
from crewpay.engine.clock import IST, window
from crewpay.engine.cover import Choice, TripStarted, arrangements, find_cover
from crewpay.engine.disruption import Disruption, aircraft_out, station_closure
from crewpay.engine.fatigue import fatigue_flags
from crewpay.engine.gates import (
    GatePlan,
    check_gate,
    departures,
    gate_closure,
    gates_at,
    late_departure,
)
from crewpay.engine.impact import absence_gaps, delay_impact, joint_cover, with_unavailable
from crewpay.engine.risk import crew_risk, risk_json
from crewpay.engine.severity import for_issue
from crewpay.engine.watch import scan
from crewpay.ops.callout import Callout, CalloutDesk, CalloutError, Status
from crewpay.ops.governance import LimitError, limits, log, set_limit
from crewpay.ops.notify import DESK, Notice
from crewpay.ops.plans import Plan, PlanDesk, PlanError, PlanStatus
from crewpay.store.roster import RosterChange, StaleTrip

CREW_ID = r"^C-\d{4}$"
TRIP_ID = r"^T-\d{4}$"


class Role(StrEnum):
    CREW = "crew"
    REPORTER = "reporter"
    CONTROLLER = "controller"
    APPROVER = "approver"
    AUDITOR = "auditor"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"
    SPEAKER = "speaker"


@dataclass(frozen=True, slots=True)
class Caller:
    user: str
    role: Role
    crew_id: str | None = None
    """For the crew role: whose record this caller is."""

    def __post_init__(self) -> None:
        if self.role is Role.CREW and not self.crew_id:
            raise ValueError("a crew caller needs their crew id")


class Roster(Protocol):
    def operation(self, now: datetime | None = None) -> Operation: ...
    def trip(self, trip_id: str) -> tuple[Trip, int]: ...
    def change_crew(
        self,
        trip_id: str,
        crew: list[Seat],
        expected_version: int,
        actor: str,
        reason: str,
        at: datetime | None = None,
    ) -> int: ...
    def changes(self, trip_id: str | None = None) -> list[RosterChange]: ...
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
    ) -> Unavailability: ...
    def clear_unavailable(self, report_id: str) -> Unavailability: ...
    def unavailability(self, crew_id: str | None = None) -> list[Unavailability]: ...


@dataclass(frozen=True, slots=True)
class Context:
    store: Roster
    caller: Caller
    now: datetime | None = None
    """The operation's clock; ``None`` uses the time the demo data was taken."""
    desk: CalloutDesk | None = None
    reports: Any = None
    """The disruption reports (``crewpay.ops.reports.ReportDesk``), where there are any."""
    accounts: Any = None
    """Team accounts (``crewpay.ops.accounts``), for admins."""
    alerts: Any = None
    """Alert addresses (``crewpay.ops.alerts``), for admins."""
    payroll: Any = None
    """Crew pay (``crewpay.pay.Payroll``): the PayPal payouts for approved cover."""

    def operation(self) -> Operation:
        return self.store.operation(self.now)

    def own(self, crew_id: str) -> None:
        """Crew callers may only ask about themselves."""
        if self.caller.role is Role.CREW and crew_id != self.caller.crew_id:
            raise Forbidden("crew members can only look up their own record")

    def on_trip(self, trip: Trip) -> None:
        if self.caller.role is Role.CREW and self.caller.crew_id not in _on_board(trip):
            offered = self.desk is not None and any(
                c.trip_id == trip.trip_id for c in self.desk.offers_for(self.caller.crew_id or "")
            )
            if not offered:
                raise Forbidden("crew members can only look up trips they fly, or are offered")

    def callouts(self) -> CalloutDesk:
        if self.desk is None:
            raise ToolError("callouts are not available here")
        return self.desk

    def plans(self) -> PlanDesk:
        if self.desk is None or self.desk.plans is None:
            raise ToolError("plans are not available here")
        return self.desk.plans


class ToolError(Exception):
    """A problem the model should read and recover from, not a crash."""


class Forbidden(ToolError):
    pass


# -- shaping results -------------------------------------------------------------------------


def _on_board(trip: Trip) -> set[str]:
    return {s.crew_id for s in trip.crew}


def _when(moment: datetime) -> str:
    return moment.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _finding(f: Finding) -> dict[str, Any]:
    out: dict[str, Any] = {"rule": f.rule_id, "outcome": str(f.outcome), "detail": f.detail}
    if f.day is not None:
        out["day"] = f.day.isoformat()
    if f.margin is not None:
        out["margin_hours"] = round(f.margin, 2)
    return out


def _choice(c: Choice) -> dict[str, Any]:
    out: dict[str, Any] = {
        "position": c.position,
        "way": c.way.label,
        "summary": c.summary,
        "cost_inr": c.cost,
        "cost_breakdown": c.breakdown,
    }
    for key in ("crew_id", "name", "base", "positioning_leg", "minutes_to_airport", "note"):
        value = getattr(c, key)
        if value is not None:
            out[key] = value
    if c.rank is not None:
        out["rank"] = str(c.rank)
    if c.slip_hours:
        out["departure_late_hours"] = c.slip_hours
    return out


def _cover_choice(
    op: Operation, trip_id: str, c: Choice, reports: list[Unavailability] | None = None
) -> dict[str, Any]:
    out = _choice(c)
    if c.crew_id:
        out["risk"] = risk_json(crew_risk(op, c.crew_id, reports or ()))
        if fatigue := _fatigue(op, c.crew_id, trip_id):
            out["fatigue"] = fatigue
        if todo := arrangements(op, trip_id, c):
            out["to_arrange"] = list(todo)
    return out


def _fatigue(op: Operation, crew_id: str, trip_id: str) -> list[dict[str, str]]:
    """Fatigue patterns taking this trip would leave: legal, but worth weighing."""
    return [
        {"severity": str(f.severity), "detail": f.detail}
        for f in fatigue_flags(op, crew_id, trip_id)
    ]


def _unavailability(u: Unavailability) -> dict[str, Any]:
    return {
        "report_id": u.report_id,
        "from": u.first_day.isoformat(),
        "to": u.last_day.isoformat(),
        "reason": u.reason,
        "kind": u.kind,
        **({"since": _when(u.since)} if u.since else {}),
        "reported_by": u.reported_by,
        "reported_at": _when(u.reported_at),
    }


def _plan(p: Plan) -> dict[str, Any]:
    out: dict[str, Any] = {
        "plan_id": p.plan_id,
        "status": str(p.status),
        "trip_id": p.trip_id,
        "seat": str(p.rank),
        "replacing": p.replacing,
        "crew_id": p.crew_id,
        "name": p.name,
        "summary": p.summary,
        "cost_inr": p.cost,
        "trip_version": p.trip_version,
        "submitted_by": p.submitted_by,
        "submitted_at": _when(p.submitted_at),
        "source": p.source,
        **({"report_id": p.source} if p.source.startswith("R-") else {}),
    }
    if p.note:
        out["note_from_submitter"] = p.note
    if p.decided_by:
        out["decided_by"] = p.decided_by
        out["decided_at"] = _when(p.decided_at) if p.decided_at else None
    if p.decision_note:
        out["decision_note"] = p.decision_note
    if p.to_arrange:
        out["to_arrange"] = list(p.to_arrange)
    return out


def _callout(c: Callout) -> dict[str, Any]:
    out: dict[str, Any] = {
        "callout_id": c.callout_id,
        "trip_id": c.trip_id,
        "seat": str(c.rank),
        "replacing": c.vacated_by,
        "status": str(c.status),
        "asked": [
            {
                "crew_id": o.crew_id,
                "name": o.name,
                "cost_inr": o.cost,
                "answer": o.answer or "waiting",
                **({"remark": o.note} if o.note else {}),
            }
            for o in c.offers
        ],
    }
    if c.current is not None:
        out["waiting_for"] = c.current.crew_id
        out["answer_by"] = _when(c.current.expires_at)
    if c.plan_id:
        out["plan_id"] = c.plan_id
        out["waiting_for_approval"] = c.status is Status.ACCEPTED
    if c.filled_by:
        out["filled_by"] = c.filled_by
    if c.to_arrange:
        out["to_arrange"] = list(c.to_arrange)
    if c.reason:
        out["reason"] = c.reason
    if c.options:
        out["desk_options"] = c.options
        out["note"] = "Nothing has been cancelled. Cancelling or flying short needs an approver."
    if c.proposal is not None:
        out["proposal"] = {
            "way": c.proposal.way,
            "summary": c.proposal.summary,
            "proposed_by": c.proposal.proposed_by,
            "approved_by": c.proposal.approved_by,
        }
    return out


def _trip_summary(op: Operation, trip: Trip) -> dict[str, Any]:
    days = op.days_of(trip.trip_id)
    legs = [op.legs[leg_id] for leg_id in trip.leg_ids]
    # The aircraft its flights fly on now: an approved swap or spare shows here.
    tails = list(dict.fromkeys(leg.tail for leg in legs if leg.leg_id not in op.cancelled))
    return {
        "trip_id": trip.trip_id,
        "airline": trip.airline,
        "tail": ", ".join(tails) or trip.tail,
        "cancelled": all(leg.leg_id in op.cancelled for leg in legs),
        "flights": [
            {
                "leg_id": leg.leg_id,
                "flight_no": leg.flight_no,
                "from": leg.origin,
                "to": leg.destination,
                "departs": _when(leg.off_block),
                "tail": leg.tail,
                "gate": leg.gate,
                **({"cancelled": True} if leg.leg_id in op.cancelled else {}),
            }
            for leg in legs
        ],
        "days": [d.day.isoformat() for d in days],
        "report": _when(days[0].report),
        "release": _when(days[-1].release),
        "route": " ".join(
            [op.legs[trip.days[0].legs[0]].origin]
            + [op.legs[leg].destination for leg in trip.leg_ids]
        ),
        "started": op.started(trip.trip_id),
    }


# -- the tools -------------------------------------------------------------------------------


class SearchCrew(BaseModel):
    query: str = Field(min_length=2, description="Part of a name, or a crew id such as C-1111")
    airline: str | None = Field(None, description="Airline code: SY, KF or GL")
    rank: Rank | None = None
    limit: int = Field(10, ge=1, le=25)


def name_matches(name: str, query: str) -> bool:
    """Whether ``query`` names this person. Crew are known by an initial and a surname
    ("A. Nair"), so a single letter is an initial and must match the name's first letter."""
    words = [w.strip(".") for w in query.lower().replace(".", ". ").split()]
    words = [w for w in words if w]
    full = name.lower()
    if not words:
        return False
    return all(full.startswith(w) if len(w) == 1 else w in full for w in words)


def search_crew(ctx: Context, args: SearchCrew) -> dict[str, Any]:
    op = ctx.operation()
    found = [
        c
        for c in op.crew.values()
        if (args.airline is None or c.airline == args.airline.upper())
        and (args.rank is None or c.rank is args.rank)
        and (c.crew_id.lower() == args.query.lower() or name_matches(c.name, args.query))
    ]
    found.sort(key=lambda c: c.crew_id)
    return {
        "matches": [
            {
                "crew_id": c.crew_id,
                "name": c.name,
                "rank": str(c.rank),
                "airline": c.airline,
                "base": c.base,
                "status": str(c.status),
            }
            for c in found[: args.limit]
        ],
        "total": len(found),
    }


class FindTrips(BaseModel):
    day: date | None = Field(None, description="Trips flying on this date")
    airline: str | None = Field(None, description="Airline code: SY, KF or GL")
    crew_id: str | None = Field(None, pattern=CREW_ID, description="Trips this person flies")
    flight_no: str | None = Field(None, description="A flight number such as SY101")
    limit: int = Field(20, ge=1, le=50)


def find_trips(ctx: Context, args: FindTrips) -> dict[str, Any]:
    crew_id = args.crew_id
    if ctx.caller.role is Role.CREW:
        crew_id = ctx.caller.crew_id
    op = ctx.operation()
    found = [
        t
        for t in op.trips.values()
        if (args.day is None or any(d.day == args.day for d in t.days))
        and (args.airline is None or t.airline == args.airline.upper())
        and (crew_id is None or crew_id in _on_board(t))
        and (
            args.flight_no is None
            or any(op.legs[leg].flight_no == args.flight_no.upper() for leg in t.leg_ids)
        )
    ]
    found.sort(key=lambda t: (op.days_of(t.trip_id)[0].report, t.trip_id))
    return {
        "trips": [_trip_summary(op, t) for t in found[: args.limit]],
        "total": len(found),
    }


class TripDetails(BaseModel):
    trip_id: str = Field(pattern=TRIP_ID)


def trip_details(ctx: Context, args: TripDetails) -> dict[str, Any]:
    trip, version = ctx.store.trip(args.trip_id)
    ctx.on_trip(trip)
    op = ctx.operation()
    legs = [op.legs[leg] for leg in trip.leg_ids]
    return {
        **_trip_summary(op, trip),
        "version": version,
        "legs": [
            {
                "flight": leg.flight_no,
                "from": leg.origin,
                "to": leg.destination,
                "departs": _when(leg.off_block),
                "arrives": _when(leg.on_block),
                "aircraft": str(leg.aircraft_type),
                "booked": leg.booked,
                "seats": leg.seats,
            }
            for leg in legs
        ],
        "crew": [
            {"crew_id": s.crew_id, "name": op.state(s.crew_id).name, "seat": str(s.rank)}
            for s in trip.crew
        ],
    }


class CrewProfile(BaseModel):
    crew_id: str = Field(pattern=CREW_ID)


def crew_profile(ctx: Context, args: CrewProfile) -> dict[str, Any]:
    ctx.own(args.crew_id)
    op = ctx.operation()
    crew = op.state(args.crew_id)
    today = op.now.date()
    book = op.book
    duty_from, duty_to = window(today, book.duty_days)
    block_from, block_to = window(today, book.block_days)
    return {
        "crew_id": crew.crew_id,
        "name": crew.name,
        "rank": str(crew.rank),
        "airline": crew.airline,
        "base": crew.base,
        "ratings": [str(r) for r in crew.ratings],
        "status": str(crew.status),
        "minutes_to_airport": op.minutes_to_airport.get(crew.crew_id),
        "certificates": [
            {"kind": str(kind), "expires": expires.isoformat(), "days_left": (expires - today).days}
            for kind, expires in crew.certs
        ],
        "duty_hours_last_7_days": round(crew.duty_between(duty_from, duty_to), 2),
        "duty_limit_7_days": book.duty_hours,
        "block_hours_last_28_days": round(crew.block_between(block_from, block_to), 2),
        "block_limit_28_days": book.block_hours,
        "rest_until": (
            _when(crew.rest_until) if crew.rest_until and crew.rest_until > op.now else None
        ),
        "upcoming_duties": [
            {"trip_id": d.trip_id, "day": d.day.isoformat(), "report": _when(d.report)}
            for d in crew.rostered()
            if d.release > op.now
        ][:10],
        "unavailable": [_unavailability(u) for u in ctx.store.unavailability(crew.crew_id)],
        "standby_days": sorted(
            {day.isoformat() for s in op.standby.get(crew.crew_id, ()) for day in s.dates}
        ),
        "risk": risk_json(crew_risk(op, crew.crew_id, ctx.store.unavailability())),
    }


class CheckLegality(BaseModel):
    crew_id: str = Field(pattern=CREW_ID)
    trip_id: str = Field(pattern=TRIP_ID)
    delay_hours: float = Field(0, ge=0, le=24, description="Judge with the trip departing late")


def check_legality(ctx: Context, args: CheckLegality) -> dict[str, Any]:
    ctx.own(args.crew_id)
    op = ctx.operation()
    result = assess(op, args.crew_id, args.trip_id, delay_hours=args.delay_hours)
    out: dict[str, Any] = {
        "crew_id": result.crew_id,
        "trip_id": result.trip_id,
        "legal": result.legal,
        "blockers": [_finding(f) for f in result.blockers],
        "findings": [_finding(f) for f in result.findings],
        "on_standby": result.on_standby,
    }
    if result.positioning is not None:
        out["positioning"] = {
            "leg_id": result.positioning.leg_id,
            "from": result.positioning.origin,
            "lands": _when(result.positioning.lands),
        }
    if result.slip_hours:
        out["departure_late_hours"] = result.slip_hours
    fatigue = _fatigue(op, args.crew_id, args.trip_id)
    if fatigue:
        out["fatigue"] = fatigue
    return out


class FindCover(BaseModel):
    trip_id: str = Field(pattern=TRIP_ID)
    rank: Rank = Field(description="The seat to fill")
    vacated_by: str | None = Field(None, pattern=CREW_ID, description="Who left the seat")
    explain: list[str] = Field(
        default_factory=list, max_length=5, description="Crew ids to explain the rejection of"
    )
    limit: int = Field(5, ge=1, le=10, description="How many choices to return")


def find_cover_tool(ctx: Context, args: FindCover) -> dict[str, Any]:
    op = ctx.operation()
    try:
        cover = find_cover(op, args.trip_id, args.rank, args.vacated_by)
    except TripStarted as err:
        raise ToolError(str(err)) from err
    choices = list(cover.choices[: args.limit])
    if cover.choices[-1] not in choices:
        choices.append(cover.choices[-1])
    explained = {}
    for crew_id in args.explain:
        excluded = cover.why_not(crew_id)
        explained[crew_id] = (
            {
                "stage": excluded.stage,
                "rules": list(excluded.rule_ids),
                "reasons": list(excluded.reasons),
            }
            if excluded
            else "not excluded: offered as a choice, or not someone who could take this seat"
        )
    return {
        "trip_id": cover.trip_id,
        "seat": str(cover.seat),
        "choices": [
            _cover_choice(op, args.trip_id, c, ctx.store.unavailability()) for c in choices
        ],
        "more_choices": len(cover.choices) - len(choices),
        "considered": cover.considered,
        "funnel": [
            {"stage": s.stage, "left": s.left, "dropped": s.dropped, "reason": s.reason}
            for s in cover.funnel
        ],
        "facts": cover.facts,
        "picks": {
            aim: {"crew_id": c.crew_id, "summary": c.summary, "cost_inr": c.cost}
            | ({"departure_late_hours": c.slip_hours} if c.slip_hours else {})
            for aim, c in cover.picks().items()
        },
        **({"why_not": explained} if explained else {}),
        **({"withdrawn": _withdrawn(ctx, cover.withdrawn)} if cover.withdrawn else {}),
    }


def _withdrawn(ctx: Context, withdrawn) -> list[dict[str, Any]]:
    """Would-be choices who reported they cannot fly: shown struck through, never offered."""
    reports = {u.report_id: u for u in ctx.store.unavailability()}
    out = []
    for w in withdrawn:
        why = [reports[r] for r in w.report_ids if r in reports]
        out.append(
            {
                **_choice(w.choice),
                "withdrawn": True,
                "reason": "; ".join(f"{u.reason} (reported by {u.reported_by})" for u in why)
                or "reported unavailable",
            }
        )
    return out


class AbsenceImpact(BaseModel):
    crew_id: str = Field(pattern=CREW_ID, description="Who is absent")
    first_day: date
    last_day: date


def absence_impact(ctx: Context, args: AbsenceImpact) -> dict[str, Any]:
    if args.last_day < args.first_day:
        raise ToolError("last_day is before first_day")
    if (args.last_day - args.first_day).days > 30:
        raise ToolError("one absence covers up to 31 days; ask again for the rest")
    op = ctx.operation()
    gaps = absence_gaps(op, args.crew_id, args.first_day, args.last_day)
    span = (args.last_day - args.first_day).days + 1
    days = [date.fromordinal(args.first_day.toordinal() + i) for i in range(span)]
    plan = joint_cover(with_unavailable(op, args.crew_id, days, "absence"), gaps)
    return {
        "crew_id": args.crew_id,
        "name": op.state(args.crew_id).name,
        "seats_left_empty": [
            {
                "trip_id": g.trip_id,
                "seat": str(g.rank),
                "days": [d.isoformat() for d in g.days],
                "legs": g.legs,
                "passengers": g.passengers,
                "started": g.started,
            }
            for g in gaps
        ],
        "plan": [{"trip_id": f.gap.trip_id, **_choice(f.choice)} for f in plan.fills],
        "already_started": [g.trip_id for g in plan.skipped],
        "total_cost_inr": plan.total_cost,
        "every_combination_compared": plan.searched,
    }


class DelayImpactArgs(BaseModel):
    trip_id: str = Field(pattern=TRIP_ID)
    hours: float = Field(gt=0, le=24, description="How late the trip departs")
    day: date | None = Field(None, description="Which day of a multi-day trip; default the first")


def delay_impact_tool(ctx: Context, args: DelayImpactArgs) -> dict[str, Any]:
    op = ctx.operation()
    impact = delay_impact(op, args.trip_id, args.hours, args.day)
    return {
        "trip_id": impact.trip_id,
        "delay_hours": impact.hours,
        "day": impact.day.isoformat(),
        "crew_over_a_limit": [
            {
                "crew_id": b.crew_id,
                "name": b.name,
                "rank": str(b.rank),
                "rules": list(b.rule_ids),
                "details": list(b.details),
            }
            for b in impact.breaches
        ],
        "crew_still_legal": list(impact.still_legal),
        **(
            {"crew_illegal_even_on_time": list(impact.already_illegal)}
            if impact.already_illegal
            else {}
        ),
        "passengers": impact.passengers,
        "delay_cost_inr": impact.delay_cost,
    }


class RosterChanges(BaseModel):
    trip_id: str | None = Field(None, pattern=TRIP_ID)
    limit: int = Field(20, ge=1, le=100)


def roster_changes(ctx: Context, args: RosterChanges) -> dict[str, Any]:
    changes = ctx.store.changes(args.trip_id)
    return {
        "changes": [
            {
                "trip_id": c.trip_id,
                "version": c.version,
                "at": _when(c.at),
                "by": c.actor,
                "reason": c.reason,
                "added": list(c.added),
                "removed": list(c.removed),
            }
            for c in changes[-args.limit :]
        ],
        "total": len(changes),
    }


class AssignCrew(BaseModel):
    trip_id: str = Field(pattern=TRIP_ID)
    replace_crew_id: str = Field(pattern=CREW_ID, description="Who leaves the trip")
    with_crew_id: str = Field(pattern=CREW_ID, description="Who takes the seat")
    expected_version: int = Field(ge=1, description="The trip version from trip_details")
    reason: str = Field(min_length=3, max_length=200)
    report_id: str | None = Field(
        None, pattern=r"^R-[0-9a-f]{6}$", description="The disruption report this work is for"
    )


def _report_work(ctx: Context, report_id: str | None) -> None:
    """Work for a report is done by its desk's controller only."""
    if report_id is None:
        return
    if ctx.reports is None:
        raise ToolError("disruption reports are not available here")
    try:
        ctx.reports.check_mine(report_id, ctx.caller.user)
    except ValueError as err:
        raise ToolError(str(err)) from err


def assign_crew(ctx: Context, args: AssignCrew) -> dict[str, Any]:
    _report_work(ctx, args.report_id)
    trip, _ = ctx.store.trip(args.trip_id)
    seat = next((s for s in trip.crew if s.crew_id == args.replace_crew_id), None)
    if seat is None:
        raise ToolError(f"{args.replace_crew_id} is not on {args.trip_id}")
    if args.with_crew_id in _on_board(trip):
        raise ToolError(f"{args.with_crew_id} is already on {args.trip_id}")
    op = ctx.operation()
    try:
        cover = find_cover(op, args.trip_id, seat.rank, args.replace_crew_id)
    except TripStarted as err:
        raise ToolError(str(err)) from err
    choice = next((c for c in cover.choices if c.crew_id == args.with_crew_id), None)
    if choice is None:
        excluded = cover.why_not(args.with_crew_id)
        why = "; ".join(excluded.reasons) if excluded else "not someone who can take this seat"
        raise ToolError(f"{args.with_crew_id} is not legal cover for this seat: {why}")
    plans = ctx.desk.plans if ctx.desk is not None else None
    if plans is not None:
        _, version = ctx.store.trip(args.trip_id)
        if version != args.expected_version:
            raise ToolError(
                f"{args.trip_id} is at version {version}, not {args.expected_version}. Someone "
                "changed the trip; call trip_details and decide again."
            )
        try:
            plan = plans.submit(
                args.trip_id, args.replace_crew_id, args.with_crew_id, ctx.caller.user, args.reason
            )
        except PlanError as err:
            raise ToolError(str(err)) from err
        if args.report_id:
            ctx.reports.link(args.report_id, ctx.caller.user, plan_id=plan.plan_id)
        return {
            **_plan(plan),
            "note": "Submitted, not yet applied: an approver must approve it.",
        }
    crew = [
        Seat(crew_id=args.with_crew_id, rank=s.rank) if s.crew_id == args.replace_crew_id else s
        for s in trip.crew
    ]
    todo = arrangements(op, args.trip_id, choice)
    try:
        version = ctx.store.change_crew(
            args.trip_id,
            crew,
            args.expected_version,
            ctx.caller.user,
            args.reason + "".join(f"; {n}" for n in todo),
        )
    except StaleTrip as err:
        raise ToolError(
            f"{err}. Someone changed the trip; call trip_details and decide again."
        ) from err
    out = {"trip_id": args.trip_id, "version": version, "assigned": _choice(choice)}
    if todo:
        out["to_arrange"] = list(todo)
    return out


class OpenIssues(BaseModel):
    days: int = Field(7, ge=1, le=14, description="How many days ahead to look")
    limit: int = Field(15, ge=1, le=50)


def open_issues(ctx: Context, args: OpenIssues) -> dict[str, Any]:
    op = ctx.operation()
    graded = [(i, for_issue(op, i)) for i in scan(op, horizon_days=args.days)]
    graded.sort(key=lambda pair: pair[1].level.rank)  # stable: the watch's order within a level
    return {
        "issues": [
            {
                "issue_id": i.issue_id,
                "severity": str(s.level),
                "severity_reasons": list(s.reasons),
                "kind": i.kind,
                "crew_id": i.crew_id,
                "name": i.name,
                "day": i.day.isoformat(),
                "detail": i.detail,
                **({"trip_id": i.trip_id} if i.trip_id else {}),
                **({"rules": list(i.rule_ids)} if i.rule_ids else {}),
                **({"suggested_cover": i.suggestion} if i.suggestion else {}),
                **({"suggested_cost_inr": i.suggestion_cost} if i.suggestion_cost else {}),
            }
            for i, s in graded[: args.limit]
        ],
        "total": len(graded),
    }


class StartCallout(BaseModel):
    trip_id: str = Field(pattern=TRIP_ID)
    replace_crew_id: str = Field(pattern=CREW_ID, description="Who leaves the trip")
    report_id: str | None = Field(
        None, pattern=r"^R-[0-9a-f]{6}$", description="The disruption report this work is for"
    )


def start_callout(ctx: Context, args: StartCallout) -> dict[str, Any]:
    _report_work(ctx, args.report_id)
    trip, _ = ctx.store.trip(args.trip_id)
    seat = next((s for s in trip.crew if s.crew_id == args.replace_crew_id), None)
    if seat is None:
        raise ToolError(f"{args.replace_crew_id} is not on {args.trip_id}")
    try:
        callout = ctx.callouts().open(
            args.trip_id, seat.rank, args.replace_crew_id, ctx.caller.user
        )
    except CalloutError as err:
        raise ToolError(str(err)) from err
    if args.report_id:
        ctx.reports.link(args.report_id, ctx.caller.user, callout_id=callout.callout_id)
    return _callout(callout)


class CalloutStatus(BaseModel):
    callout_id: str | None = Field(None, description="One callout; leave out to list them all")


def callout_status(ctx: Context, args: CalloutStatus) -> dict[str, Any]:
    desk = ctx.callouts()
    if ctx.caller.role is Role.CREW:
        mine = ctx.caller.crew_id or ""
        return {
            "offers_to_me": [_callout(c) for c in desk.offers_for(mine)],
            "earlier_offers": [
                {
                    "callout_id": c.callout_id,
                    "trip_id": c.trip_id,
                    "seat": str(c.rank),
                    "offered_at": _when(o.offered_at),
                    "answer_by": _when(o.expires_at),
                    "outcome": o.answer,
                    **({"answered_at": _when(o.answered_at)} if o.answered_at else {}),
                    **({"your_remark": o.note} if o.note else {}),
                    "seat_now": "filled by you"
                    if c.filled_by == mine
                    else "filled by someone else"
                    if c.filled_by
                    else str(c.status),
                }
                for c, o in desk.answered_by(mine)[:5]
            ],
        }
    if args.callout_id:
        return _callout(desk.get(args.callout_id))
    return {"callouts": [_callout(c) for c in desk.all()]}


class AnswerCallout(BaseModel):
    callout_id: str
    accept: bool
    note: str = Field("", max_length=200, description="The crew member's remark, if any")
    crew_id: str | None = Field(
        None, pattern=CREW_ID, description="For a controller recording a crew member's answer"
    )


def answer_callout(ctx: Context, args: AnswerCallout) -> dict[str, Any]:
    crew_id = ctx.caller.crew_id if ctx.caller.role is Role.CREW else args.crew_id
    if not crew_id:
        raise ToolError("say whose answer this is with crew_id")
    try:
        return _callout(ctx.callouts().respond(args.callout_id, crew_id, args.accept, args.note))
    except CalloutError as err:
        raise ToolError(str(err)) from err


class DecideCallout(BaseModel):
    callout_id: str
    action: str = Field(
        pattern="^(propose_cancel|propose_one_short|approve|reject)$",
        description="Propose cancelling or flying one short, or approve or reject a proposal",
    )


def decide_callout(ctx: Context, args: DecideCallout) -> dict[str, Any]:
    desk, by = ctx.callouts(), ctx.caller.user
    proposing = args.action.startswith("propose")
    if proposing and ctx.caller.role is not Role.CONTROLLER:
        raise Forbidden("a controller proposes; an approver decides")
    if not proposing and ctx.caller.role is not Role.APPROVER:
        raise Forbidden("only an approver can approve or reject a proposal")
    try:
        match args.action:
            case "propose_cancel":
                callout = desk.propose(args.callout_id, "cancel", by)
            case "propose_one_short":
                callout = desk.propose(args.callout_id, "one_short", by)
            case "approve":
                callout = desk.approve(args.callout_id, by)
            case _:
                callout = desk.reject(args.callout_id, by)
    except CalloutError as err:
        raise ToolError(str(err)) from err
    return _callout(callout)


class ReportUnavailable(BaseModel):
    first_day: date
    last_day: date
    reason: str = Field(min_length=2, max_length=200, description="Such as 'sick' or 'family'")
    kind: Literal["sick", "fatigue", "personal", "other"] = Field(
        "other", description="sick, fatigue, personal or other"
    )
    since: str | None = Field(
        None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="IST time (HH:MM) they fell ill on first_day; duties ending earlier are "
        "still flown",
    )
    crew_id: str | None = Field(
        None, pattern=CREW_ID, description="For a controller reporting someone else"
    )


def report_unavailable(ctx: Context, args: ReportUnavailable) -> dict[str, Any]:
    crew_id = ctx.caller.crew_id if ctx.caller.role is Role.CREW else args.crew_id
    if not crew_id:
        raise ToolError("say who cannot fly with crew_id")
    if args.last_day < args.first_day:
        raise ToolError("last_day is before first_day")
    since = None
    if args.since:
        hour, minute = map(int, args.since.split(":"))
        since = datetime.combine(args.first_day, time(hour, minute), IST).astimezone(UTC)
    report = ctx.store.mark_unavailable(
        crew_id,
        args.first_day,
        args.last_day,
        args.reason,
        ctx.caller.user,
        kind=args.kind,
        since=since,
    )
    op = ctx.operation()
    days = set(report.days())
    affected = sorted(
        {
            d.trip_id
            for d in op.state(crew_id).rostered()
            if d.day in days and d.report > op.now and (since is None or d.release > since)
        }
    )
    moved = ctx.desk.withdraw(crew_id) if ctx.desk is not None else []
    return {
        **_unavailability(report),
        "crew_id": crew_id,
        "own_trips_now_short": affected,
        "callout_offers_withdrawn": [c.callout_id for c in moved],
    }


class ClearUnavailable(BaseModel):
    report_id: str = Field(pattern=r"^U-[0-9a-f]{8}$")


def clear_unavailable(ctx: Context, args: ClearUnavailable) -> dict[str, Any]:
    if ctx.caller.role is Role.CREW:
        mine = {u.report_id for u in ctx.store.unavailability(ctx.caller.crew_id)}
        if args.report_id not in mine:
            raise Forbidden("crew members can only withdraw their own reports")
    report = ctx.store.clear_unavailable(args.report_id)
    out: dict[str, Any] = {**_unavailability(report), "crew_id": report.crew_id, "cleared": True}
    out["stood_down"] = _back_on_duty(ctx, report)
    return out


def _back_on_duty(ctx: Context, cleared: Unavailability) -> list[str]:
    """Someone who said they could not fly says they can: stop finding cover for them, tell
    the desk, and close a disruption that has nobody left out."""
    op = ctx.operation()
    name = op.state(cleared.crew_id).name
    why = f"{name} can fly after all"
    done: list[str] = []
    if ctx.desk is not None:
        for trip_id in {
            d.trip_id
            for d in op.state(cleared.crew_id).duties
            if cleared.first_day <= d.day <= cleared.last_day
        }:
            done += [c.callout_id for c in ctx.desk.stand_down(trip_id, cleared.crew_id, why)]
            if ctx.desk.plans is not None:
                done += [
                    p.plan_id
                    for p in ctx.desk.plans.stand_down(
                        trip_id, cleared.crew_id, ctx.caller.user, why
                    )
                ]
    if ctx.reports is not None:
        still_out = {u.report_id for u in ctx.store.unavailability()}
        for r in ctx.reports.all():
            active = str(r.status) in ("open", "in_progress", "resolved")
            mine = cleared.report_id in r.unavailability_ids
            if active and mine and not set(r.unavailability_ids) & still_out:
                ctx.reports.revoke(r.report_id, ctx.caller.user, f"withdrawn: {why}")
                done.append(r.report_id)
        ctx.reports.outbox.send(
            Notice(
                to=DESK,
                subject=f"Back on duty: {name}",
                body=f"{name} ({cleared.crew_id}) withdrew the report that they could not fly "
                f"{cleared.first_day:%d %b} to {cleared.last_day:%d %b}."
                + (f" Closed or withdrawn: {', '.join(done)}." if done else ""),
                at=datetime.now(UTC),
                ref=cleared.report_id,
            )
        )
    return done


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[Context, Any], dict[str, Any]]
    roles: frozenset[Role]
    writes: bool = False
    console_only: bool = False
    """Only a person at the console may use it, never the agent or an MCP client: confirming an
    approval with PayPal."""

    def parameters(self) -> dict[str, Any]:
        return _untitled(self.args.model_json_schema())

    def spec(self) -> dict[str, Any]:
        """The tool in the chat-completions ``tools`` format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters(),
            },
        }


def _untitled(node: Any) -> Any:
    if isinstance(node, list):
        return [_untitled(n) for n in node]
    if isinstance(node, dict):
        return {k: _untitled(v) for k, v in node.items() if k != "title"}
    return node


class PlanStatusArgs(BaseModel):
    plan_id: str | None = Field(None, pattern=r"^PL-[0-9a-f]{8}$")
    waiting_only: bool = Field(True, description="Only plans still waiting for a decision")


def plan_status(ctx: Context, args: PlanStatusArgs) -> dict[str, Any]:
    plans = ctx.plans()
    if args.plan_id:
        return _plan(plans.get(args.plan_id))
    found = plans.waiting() if args.waiting_only else plans.all()[:20]
    return {"plans": [_plan(p) for p in found]}


class ReviewPlan(BaseModel):
    plan_id: str = Field(pattern=r"^PL-[0-9a-f]{8}$")
    action: Literal["approve", "reject", "send_back", "withdraw"]
    note: str = Field("", max_length=300, description="Required to reject or send back")


def review_plan(ctx: Context, args: ReviewPlan) -> dict[str, Any]:
    plans, by = ctx.plans(), ctx.caller.user
    if args.action == "withdraw" and ctx.caller.role is not Role.CONTROLLER:
        raise Forbidden("only the controller who submitted a plan can withdraw it")
    if args.action != "withdraw" and ctx.caller.role is not Role.APPROVER:
        raise Forbidden("only an approver can approve, reject or send back a plan")
    try:
        match args.action:
            case "approve":
                plan = plans.approve(args.plan_id, by)
            case "reject":
                plan = plans.reject(args.plan_id, by, args.note)
            case "send_back":
                plan = plans.send_back(args.plan_id, by, args.note)
            case _:
                plan = plans.withdraw(args.plan_id, by)
    except PlanError as err:
        raise ToolError(str(err)) from err
    out = _plan(plan)
    if plan.status is PlanStatus.CONFIRMING and ctx.payroll is not None:
        funding = ctx.payroll.funding.for_plan(plan.plan_id)
        out["next_step"] = (
            f"Not in effect yet: {by} must confirm this approval by paying "
            f"{funding['amount']} {funding['currency']} with PayPal, on the Approvals page"
            + (" or through the PayPal link" if funding and funding.get("approve_url") else "")
            + ". The roster changes only after that."
        )
        out["funding_id"] = funding["funding_id"]
        if funding.get("approve_url"):
            out["paypal_approve_link"] = funding["approve_url"]
    return out


def _ist(moment: datetime) -> datetime:
    """Times without a zone are India time, as a controller says them."""
    return moment.replace(tzinfo=IST) if moment.tzinfo is None else moment


def _delays(impact) -> dict[str, Any]:
    return {
        "trip_id": impact.trip_id,
        "day": impact.day.isoformat(),
        "delay_hours": impact.hours,
        "crew_over_a_limit": [
            {"crew_id": b.crew_id, "name": b.name, "rules": list(b.rule_ids)}
            for b in impact.breaches
        ],
        **(
            {"crew_illegal_even_on_time": list(impact.already_illegal)}
            if impact.already_illegal
            else {}
        ),
        "passengers": impact.passengers,
        "delay_cost_inr": impact.delay_cost,
    }


def _gate_plan(plan: GatePlan) -> dict[str, Any]:
    return {
        "station": plan.station,
        "what": plan.what,
        "moves": [
            {
                "flight": m.flight_no,
                "leg_id": m.leg_id,
                **({"trip_id": m.trip_id} if m.trip_id else {}),
                "from_gate": m.from_gate,
                "to_gate": m.to_gate,
                "to": str(m.to_kind),
                "boarding_from": _when(m.boarding_from),
                "departs": _when(m.departs),
                "passengers": m.booked,
                "delay_minutes": m.delay_minutes,
                **({"other_free_gates": list(m.alternatives)} if m.alternatives else {}),
            }
            for m in plan.moves
        ],
        "flights_moved": len(plan.moves),
        "passengers_moved": plan.passengers,
        "crew_impact": [_delays(i) for i in plan.crew],
    }


class GateStatus(BaseModel):
    station: str = Field("HYD", pattern=r"^[A-Z]{3}$")
    day: date = Field(description="The UTC date, YYYY-MM-DD")


def gate_status(ctx: Context, args: GateStatus) -> dict[str, Any]:
    op = ctx.operation()
    kinds = gates_at(op, args.station)
    start = datetime.combine(args.day, time(0), UTC)
    slots = departures(op, args.station, start, start + timedelta(days=1))
    return {
        "station": args.station,
        "gates": [{"gate": g, "kind": str(k)} for g, k in kinds.items()],
        "departures": [
            {
                "flight": s.leg.flight_no,
                "leg_id": s.leg.leg_id,
                "to": s.leg.destination,
                "gate": s.gate,
                "boarding_from": _when(s.start),
                "departs": _when(s.end),
                "passengers": s.leg.booked,
            }
            for s in slots
        ],
    }


class GateClosure(BaseModel):
    station: str = Field("HYD", pattern=r"^[A-Z]{3}$")
    gates: list[str] = Field(min_length=1, max_length=8, description="The gates that close")
    start: datetime = Field(description="When it closes; without a zone, India time")
    end: datetime = Field(description="When it reopens; without a zone, India time")


def gate_closure_tool(ctx: Context, args: GateClosure) -> dict[str, Any]:
    plan = gate_closure(ctx.operation(), args.station, args.gates, _ist(args.start), _ist(args.end))
    return _gate_plan(plan)


class LateAtGate(BaseModel):
    leg_id: str = Field(description="Flight and date, such as SY201-2026-11-05")
    minutes: int = Field(gt=0, le=600, description="How late it leaves")


def late_at_gate(ctx: Context, args: LateAtGate) -> dict[str, Any]:
    return _gate_plan(late_departure(ctx.operation(), args.leg_id, args.minutes))


class GateChange(BaseModel):
    leg_id: str = Field(description="Flight and date, such as SY201-2026-11-05")
    gate: str = Field(min_length=1, max_length=8, description="The gate it should move to")


def gate_change(ctx: Context, args: GateChange) -> dict[str, Any]:
    c = check_gate(ctx.operation(), args.leg_id, args.gate)
    out: dict[str, Any] = {
        "flight": c.flight_no,
        "leg_id": c.leg_id,
        "station": c.station,
        "current_gate": c.current,
        "wanted_gate": c.wanted,
        "free": c.free,
    }
    if c.clashes:
        out["held_by"] = list(c.clashes)
    if c.alternatives:
        out["free_gates"] = list(c.alternatives)
    return out


def _disruption(d: Disruption) -> dict[str, Any]:
    return {
        "what": d.what,
        "flights_held": [
            {
                "flight": f.flight_no,
                "leg_id": f.leg_id,
                "route": f.route,
                "planned": _when(f.planned),
                "leaves": _when(f.leaves),
                "late_minutes": f.minutes,
                "passengers": f.booked,
            }
            for f in d.flights
        ],
        "passengers": d.passengers,
        "trips": [_delays(t) for t in d.trips],
        "crew_over_a_limit": d.crew_over_limits,
    }


class StationClosure(BaseModel):
    station: str = Field(pattern=r"^[A-Z]{3}$")
    start: datetime = Field(description="When it closes; without a zone, India time")
    end: datetime = Field(description="When it reopens; without a zone, India time")


def station_closure_tool(ctx: Context, args: StationClosure) -> dict[str, Any]:
    try:
        d = station_closure(ctx.operation(), args.station, _ist(args.start), _ist(args.end))
    except ValueError as err:
        raise ToolError(str(err)) from err
    return _disruption(d)


class AircraftOut(BaseModel):
    tail: str = Field(pattern=r"^VT-[A-Z]{3}$")
    start: datetime = Field(description="When it went out of service; without a zone, India time")
    back: datetime = Field(description="When it is expected back; without a zone, India time")


def aircraft_out_tool(ctx: Context, args: AircraftOut) -> dict[str, Any]:
    try:
        d = aircraft_out(ctx.operation(), args.tail, _ist(args.start), _ist(args.back))
    except ValueError as err:
        raise ToolError(str(err)) from err
    return _disruption(d)


class RuleLimits(BaseModel):
    rule_id: str | None = Field(None, pattern=r"^FDTL-\d{2}$", description="Only this rule")


def rule_limits(ctx: Context, args: RuleLimits) -> dict[str, Any]:
    return {
        "limits": [
            {
                "rule": x.rule_id,
                "title": x.title,
                "param": x.param,
                "value": x.value,
                "regulator": x.regulator,
                "stricter_is": x.stricter,
                "tightened": x.tightened,
                "may_relax": ctx.caller.role is Role.SUPER_ADMIN,
            }
            for x in limits(ctx.store)
            if args.rule_id in (None, x.rule_id)
        ]
    }


class SetRuleLimit(BaseModel):
    rule_id: str = Field(pattern=r"^FDTL-\d{2}$")
    param: str
    value: float = Field(gt=0)
    reason: str = Field(min_length=5, max_length=300)


def set_rule_limit(ctx: Context, args: SetRuleLimit) -> dict[str, Any]:
    try:
        limit, rule = set_limit(
            ctx.store,
            args.rule_id,
            args.param,
            args.value,
            ctx.caller.user,
            args.reason,
            may_relax=ctx.caller.role is Role.SUPER_ADMIN,
            role=str(ctx.caller.role),
        )
    except LimitError as err:
        raise ToolError(str(err)) from err
    return {
        "rule": limit.rule_id,
        "param": limit.param,
        "value": limit.value,
        "regulator": limit.regulator,
        "params_now": rule.params,
    }


READERS = frozenset({Role.CONTROLLER, Role.APPROVER, Role.AUDITOR, Role.ADMIN, Role.SUPER_ADMIN})
"""Who reads the whole operation: the desk, its approvers, auditors and admins."""
OPS = READERS | {Role.REPORTER}
"""Readers and station duty managers: flights, gates and who is who."""
EVERYONE = frozenset(Role) - {Role.SPEAKER}
"""Every person; the ops-room speaker is a device and only hears what is waiting."""
ADMINS = frozenset({Role.ADMIN, Role.SUPER_ADMIN})

TOOLS = (
    Tool(
        "search_crew",
        "Find crew by part of their name or by crew id. Use it to turn a name into a crew id.",
        SearchCrew,
        search_crew,
        OPS,
    ),
    Tool(
        "find_trips",
        "List trips by date, airline, crew member or flight number, earliest first.",
        FindTrips,
        find_trips,
        EVERYONE,
    ),
    Tool(
        "trip_details",
        "One trip: its legs with IST times and bookings, its crew, and its current version.",
        TripDetails,
        trip_details,
        EVERYONE,
    ),
    Tool(
        "crew_profile",
        "One crew member: rank, base, ratings, certificates, recent hours against the limits, "
        "rest and upcoming duties.",
        CrewProfile,
        crew_profile,
        READERS | {Role.CREW},
    ),
    Tool(
        "check_legality",
        "Judge one crew member on one trip against all nine duty rules, optionally with a "
        "late departure. Returns every rule's verdict and the numbers behind it.",
        CheckLegality,
        check_legality,
        READERS | {Role.CREW},
    ),
    Tool(
        "find_cover",
        "Legal replacements for an empty seat, cheapest first, ending with cancelling the trip. "
        "Includes how many crew were ruled out at each stage, and why named crew were.",
        FindCover,
        find_cover_tool,
        READERS,
    ),
    Tool(
        "absence_impact",
        "Every seat an absent crew member leaves empty between two dates, and the cheapest legal "
        "way to fill all of them together without using anyone twice.",
        AbsenceImpact,
        absence_impact,
        READERS,
    ),
    Tool(
        "delay_impact",
        "Who on a trip a late departure pushes over a duty limit, and what the delay costs.",
        DelayImpactArgs,
        delay_impact_tool,
        READERS,
    ),
    Tool(
        "roster_changes",
        "Recent changes to the roster: who changed which trip, when and why.",
        RosterChanges,
        roster_changes,
        READERS,
    ),
    Tool(
        "assign_crew",
        "Propose replacing one crew member on a trip. Only for someone find_cover offers as "
        "legal, and only if the trip is still at expected_version. An approver "
        "must approve it with review_plan before the roster changes.",
        AssignCrew,
        assign_crew,
        frozenset({Role.CONTROLLER}),
        writes=True,
    ),
    Tool(
        "report_unavailable",
        "Record that someone cannot fly on some dates (sick, family, stuck). From then on they "
        "are never offered as cover for those dates, and any callout offer to them moves on.",
        ReportUnavailable,
        report_unavailable,
        frozenset({Role.CONTROLLER, Role.REPORTER, Role.CREW}),
        writes=True,
    ),
    Tool(
        "clear_unavailable",
        "Remove an unavailability report, for someone who can fly again. Crew may withdraw "
        "their own.",
        ClearUnavailable,
        clear_unavailable,
        frozenset({Role.CONTROLLER, Role.REPORTER, Role.CREW}),
        writes=True,
    ),
    Tool(
        "open_issues",
        "Problems in the coming days nobody has reported yet: crew who would break a rule on a "
        "rostered trip (with the cheapest cover), high fatigue, certificates running out.",
        OpenIssues,
        open_issues,
        READERS,
    ),
    Tool(
        "start_callout",
        "Start calling out replacement crew for one person's seat: offers it to the cheapest "
        "legal crew member, then the next if they decline or do not answer. Never cancels.",
        StartCallout,
        start_callout,
        frozenset({Role.CONTROLLER}),
        writes=True,
    ),
    Tool(
        "callout_status",
        "Where callouts stand: who was asked, who answered what, who is being waited for. For "
        "crew, the offers waiting for them.",
        CalloutStatus,
        callout_status,
        READERS | {Role.CREW},
    ),
    Tool(
        "answer_callout",
        "Accept or decline a callout offer. Crew answer their own; a controller can record an "
        "answer given by phone.",
        AnswerCallout,
        answer_callout,
        frozenset({Role.CONTROLLER, Role.CREW}),
        writes=True,
    ),
    Tool(
        "decide_callout",
        "When nobody accepted: propose cancelling or flying one short, or approve or reject "
        "a proposal (approvers only). A controller proposes; an approver decides.",
        DecideCallout,
        decide_callout,
        frozenset({Role.CONTROLLER, Role.APPROVER}),
        writes=True,
    ),
    Tool(
        "plan_status",
        "Roster changes waiting for an approver, or one plan by id: who proposed what, "
        "and what was decided.",
        PlanStatusArgs,
        plan_status,
        READERS,
    ),
    Tool(
        "review_plan",
        "Decide a plan a controller submitted (approvers): approve (the rules are checked again "
        "first), reject or send back with a note. The submitter may withdraw their own.",
        ReviewPlan,
        review_plan,
        frozenset({Role.CONTROLLER, Role.APPROVER}),
        writes=True,
    ),
    Tool(
        "gate_status",
        "The gates and stands at a station and which departure holds which gate on a day.",
        GateStatus,
        gate_status,
        OPS,
    ),
    Tool(
        "gate_closure",
        "Gates close for a while: where each departure holding one moves (a free gate, then "
        "the emergency gate, then a remote stand by bus, which adds 15 minutes) and what any "
        "delay does to crew duty.",
        GateClosure,
        gate_closure_tool,
        READERS,
    ),
    Tool(
        "late_at_gate",
        "A flight leaves late and keeps its gate: which next departures at that gate must "
        "move, where to, and what any delay does to crew duty.",
        LateAtGate,
        late_at_gate,
        READERS,
    ),
    Tool(
        "gate_change",
        "Check moving one departure to another gate: is it free for the boarding window, "
        "who holds it if not, and which gates are free.",
        GateChange,
        gate_change,
        READERS,
    ),
    Tool(
        "station_closure",
        "A station closes for a while (weather, runway): every flight held, how late each "
        "leaves, and which crew a delay pushes over a limit.",
        StationClosure,
        station_closure_tool,
        READERS,
    ),
    Tool(
        "aircraft_out_of_service",
        "An aircraft is out of service until a time: the flights it holds back that day and "
        "which crew the delay pushes over a limit.",
        AircraftOut,
        aircraft_out_tool,
        READERS,
    ),
    Tool(
        "rule_limits",
        "The duty limits an admin can make stricter, their values now and the regulator's.",
        RuleLimits,
        rule_limits,
        READERS,
    ),
    Tool(
        "set_rule_limit",
        "Make one duty limit stricter than the regulator's, or return it to the regulator's "
        "figure; never less strict. Needs a reason; logged.",
        SetRuleLimit,
        set_rule_limit,
        ADMINS,
        writes=True,
    ),
)


class ToolBox:
    def __init__(self, tools: tuple[Tool, ...] | None = None) -> None:
        if tools is None:
            from crewpay.agent.desk_tools import ALL_TOOLS

            tools = ALL_TOOLS
        self.tools = {t.name: t for t in tools}

    def available(self, caller: Caller, agent: bool = False) -> list[Tool]:
        """The tools this caller may use; ``agent`` leaves out those only a person may use."""
        return [
            t
            for t in self.tools.values()
            if caller.role in t.roles and not (agent and t.console_only)
        ]

    def specs(self, caller: Caller) -> list[dict[str, Any]]:
        return [t.spec() for t in self.available(caller, agent=True)]

    def call(
        self, name: str, arguments: str | dict[str, Any], ctx: Context, agent: bool = False
    ) -> dict[str, Any]:
        """Run a tool for the model. Problems come back as ``{"error": ...}`` for it to read.
        ``agent`` is set for the assistant and MCP clients, which may not use console-only
        tools."""
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"no tool named {name!r}"}
        if ctx.caller.role not in tool.roles:
            return {"error": f"the {ctx.caller.role} role cannot use {name}"}
        if agent and tool.console_only:
            return {"error": f"{name} needs a person at the console, in PayPal"}
        try:
            raw = json.loads(arguments or "{}") if isinstance(arguments, str) else arguments
            args = tool.args.model_validate(raw)
        except json.JSONDecodeError as err:
            return {"error": f"arguments are not valid JSON: {err}"}
        except ValidationError as err:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}" for e in err.errors()
            )
            return {"error": f"invalid arguments: {problems}"}
        try:
            result = tool.run(ctx, args)
        except (NotFound, CalloutError) as err:
            return {"error": str(err)}
        except ToolError as err:
            return {"error": str(err)}
        if tool.writes and tool.name != "set_rule_limit" and hasattr(ctx.store, "put_doc"):
            log(
                ctx.store,
                "activity",
                by=ctx.caller.user,
                role=str(ctx.caller.role),
                action=name,
                detail=json.dumps(args.model_dump(mode="json"), ensure_ascii=False)[:300],
            )
        return result
