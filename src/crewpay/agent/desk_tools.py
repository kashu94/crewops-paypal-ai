"""Tools for disruption reports, desks, risk, teams and the admins' jobs.

Same rules as ``crewpay.agent.tools``: typed arguments, a role check before anything runs,
plain JSON from the engine's own results, and no model asked anything.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from crewpay.agent.pay_tools import PAY_TOOLS
from crewpay.agent.tools import (
    ADMINS,
    CREW_ID,
    EVERYONE,
    OPS,
    READERS,
    TOOLS,
    TRIP_ID,
    AircraftOut,
    Context,
    DelayImpactArgs,
    Forbidden,
    GateChange,
    GateClosure,
    LateAtGate,
    Role,
    StationClosure,
    Tool,
    ToolError,
    _callout,
    _choice,
    _plan,
    _when,
    aircraft_out_tool,
    delay_impact_tool,
    gate_change,
    gate_closure_tool,
    late_at_gate,
    station_closure_tool,
)
from crewpay.domain import Rank
from crewpay.engine import NotFound, Operation
from crewpay.engine.aircraft import aircraft_options
from crewpay.engine.clock import IST
from crewpay.engine.clock import now as clock_now
from crewpay.engine.cover import TripStarted, Way, find_cover
from crewpay.engine.impact import absence_gaps, joint_cover
from crewpay.engine.risk import crew_risk, risk_json
from crewpay.engine.severity import Level, for_trip
from crewpay.ops import flight_changes
from crewpay.ops.accounts import AccountError, check_may_manage, current_role
from crewpay.ops.alerts import AlertError
from crewpay.ops.callout import Status
from crewpay.ops.desks import DESKS
from crewpay.ops.flight_changes import FlightChange
from crewpay.ops.plans import PlanError, PlanStatus
from crewpay.ops.reports import (
    ABSENCES,
    ACTIVE,
    LABELS,
    WORKING,
    Kind,
    Report,
    ReportError,
    ReportStatus,
    summary,
)
from crewpay.ops.roster_upload import upload
from crewpay.ops.teams import ALERT_GROUPS, TEAMS, Team, contacts
from crewpay.settings import ROOT

MAX_ABSENCE_DAYS = 31
"""As long as one absence record may run; days past the published roster need no cover yet."""
OPERATIONAL_FILERS = frozenset({Role.REPORTER})
"""Disruptions are filed by the station; crew control works them and approvers decide."""
APPROVE_ROLES = frozenset({Role.APPROVER})


def _reports(ctx: Context):
    if ctx.reports is None:
        raise ToolError("disruption reports are not available here")
    return ctx.reports


def _fail(err: Exception) -> ToolError:
    return ToolError(str(err))


# -- filing -----------------------------------------------------------------------------------


class FileReport(BaseModel):
    kind: Kind
    crew_ids: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Crew out, 1 to 12; several crew out together go in one report",
    )
    first_day: date | None = None
    last_day: date | None = None
    reason_kind: Literal["sick", "fatigue", "personal", "other"] = "sick"
    since: str | None = Field(
        None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="IST time they fell ill"
    )
    leave: Literal["annual_leave", "medical", "training", "personal"] | None = None
    cert: str | None = Field(None, max_length=40, description="Which certificate lapses")
    trip_id: str | None = Field(None, pattern=TRIP_ID)
    hours: float | None = Field(None, gt=0, le=24)
    day: date | None = None
    tail: str | None = Field(None, pattern=r"^VT-[A-Z]{3}$")
    start: datetime | None = None
    end: datetime | None = None
    station: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    gates: list[str] = Field(default_factory=list, max_length=6)
    leg_id: str | None = None
    gate: str | None = Field(None, max_length=8)
    note: str = Field("", max_length=500)
    severity: Literal["low", "medium", "high"] | None = Field(
        None, description="How severe the person filing judges it; the engine's view still counts"
    )
    evidence_ids: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Photos already checked and kept (POST /api/intake with keep), by id",
    )

    @model_validator(mode="after")
    def _needs(self) -> FileReport:
        need = {
            Kind.CREW_UNAVAILABLE: ("first_day",),
            Kind.PLANNED_LEAVE: ("first_day", "last_day", "leave"),
            Kind.CERTIFICATION_LAPSE: ("first_day", "cert"),
            Kind.FLIGHT_DELAY: ("trip_id", "hours"),
            Kind.AIRCRAFT_TECHNICAL: ("tail", "start", "end"),
            Kind.STATION_DISRUPTION: ("station", "start", "end"),
            Kind.GATE_CLOSURE: ("station", "gates", "start", "end"),
            Kind.GATE_CHANGE: ("leg_id", "gate"),
        }[self.kind]
        missing = [f for f in need if not getattr(self, f)]
        if missing:
            raise ValueError(f"a {self.kind} report needs {', '.join(missing)}")
        for crew_id in self.crew_ids:
            if not crew_id.startswith("C-") or len(crew_id) != 6:
                raise ValueError(f"{crew_id!r} is not a crew id")
        if len(set(self.crew_ids)) != len(self.crew_ids):
            raise ValueError("a crew member is listed twice")
        return self


def _evidence(ctx: Context, args: FileReport) -> list[dict[str, Any]]:
    found = []
    for evidence_id in args.evidence_ids:
        doc = ctx.store.get_doc("evidence", evidence_id) if evidence_id.isalnum() else None  # type: ignore[attr-defined]
        if doc is None or doc["by"] != ctx.caller.user:
            raise ToolError(f"no photo {evidence_id} of yours to attach")
        item = {k: doc[k] for k in ("evidence_id", "kind", "message", "confidence", "at")}
        # A note's own next step ("confirm to record them") is the report itself here.
        item["message"] = item["message"].replace(" Confirm to record them as unavailable.", "")
        if check := _photo_matches(args, doc):
            if check.endswith(NOT_ON_REPORT):
                raise ToolError(
                    check + " Add their own note or certificate, or file without a photo."
                )
            item["check"] = check
        found.append(item)
    return found


NOT_ON_REPORT = "who is not on this report."
"""How a photo of someone else ends: the one check that stops a report being filed."""


def _photo_matches(args: FileReport, doc: dict[str, Any]) -> str | None:
    """Whether a note or certificate names someone on the report, for the dates reported: a
    photo that does not match is kept, and the desk is told why it does not."""
    named = set(doc.get("crew_ids") or [])
    if not named or not args.crew_ids:
        return None
    if not named & set(args.crew_ids):
        return f"The photo names {', '.join(sorted(named))}, {NOT_ON_REPORT}"
    first, last = (doc.get("days") or [None, None])[:2]
    if first and args.first_day and args.kind is Kind.CREW_UNAVAILABLE:
        shown = (date.fromisoformat(first), date.fromisoformat(last or first))
        wanted = (args.first_day, args.last_day or args.first_day)
        if shown[1] < wanted[0] or shown[0] > wanted[1]:
            return (
                f"The photo covers {_days(*shown)}, not the {_days(*wanted)} reported: "
                "check the dates with the crew member."
            )
        if shown[0] > wanted[0] or shown[1] < wanted[1]:
            return f"The photo covers {_days(*shown)}, part of the {_days(*wanted)} reported."
    if first and args.kind is Kind.CREW_UNAVAILABLE:
        return "The photo matches the person and the dates reported."
    return "The photo names the person reported."


def _who_may_file(ctx: Context, args: FileReport) -> list[str]:
    """Check the caller may file this report; returns the crew it is about."""
    role = ctx.caller.role
    if role is Role.CREW:
        if args.kind is not Kind.CREW_UNAVAILABLE:
            raise Forbidden("crew can only report themselves unavailable")
        if args.crew_ids not in ([], [ctx.caller.crew_id]):
            raise Forbidden("crew can only report themselves unavailable")
        return [ctx.caller.crew_id or ""]
    if args.kind is Kind.PLANNED_LEAVE:
        if role not in ADMINS:
            raise Forbidden("planned leave is booked by an admin, the way rostering would")
    elif role not in OPERATIONAL_FILERS:
        raise Forbidden(f"the {role} role cannot file {args.kind} reports")
    if args.kind in ABSENCES:
        if not args.crew_ids:
            raise ToolError("say who is out with crew_ids")
        if args.kind is not Kind.CREW_UNAVAILABLE and len(args.crew_ids) > 1:
            raise ToolError(f"one {LABELS[args.kind].lower()} report per crew member")
    return args.crew_ids


def _span(op: Operation, args: FileReport) -> tuple[date, date]:
    first = args.first_day
    assert first is not None
    end_of_roster = max(leg.day for leg in op.legs.values())
    last = args.last_day or (end_of_roster if args.kind is Kind.CERTIFICATION_LAPSE else first)
    if args.kind is Kind.CERTIFICATION_LAPSE:
        last = min(last, first + timedelta(days=MAX_ABSENCE_DAYS - 1))
    if last < first:
        raise ToolError("last_day is before first_day")
    if (last - first).days >= MAX_ABSENCE_DAYS:
        raise ToolError(
            f"one report covers up to {MAX_ABSENCE_DAYS} days; file a second report for the rest"
        )
    return first, last


def _names(op: Operation, crew_ids: list[str]) -> str:
    names = [op.state(c).name for c in crew_ids]
    if len(names) <= 2:
        return " and ".join(names)
    return f"{len(names)} crew ({', '.join(names[:3])}{', ...' if len(names) > 3 else ''})"


def _days(first: date, last: date) -> str:
    if first == last:
        return first.strftime("%d %b")
    return f"{first.strftime('%d')}-{last.strftime('%d %b')}"


def _absence_analysis(ctx: Context, report: Report) -> tuple[dict[str, Any], Any, list]:
    """The seats the absent crew leave, and the cheapest way to fill all of them together."""
    op = ctx.operation()
    first = date.fromisoformat(report.details["first_day"])
    last = date.fromisoformat(report.details["last_day"])
    since = report.details.get("since_utc")
    since_at = datetime.fromisoformat(since) if since else None
    # Someone who withdrew their absence is back on duty: their seats need no cover.
    live = {u.report_id for u in ctx.store.unavailability()}
    back = {
        crew_id
        for crew_id, uid in zip(report.crew_ids, report.unavailability_ids, strict=False)
        if uid not in live
    }
    gaps = []
    for crew_id in report.crew_ids:
        if crew_id in back:
            continue
        for gap in absence_gaps(op, crew_id, first, last):
            if since_at is not None and all(d.release <= since_at for d in op.days_of(gap.trip_id)):
                continue  # finished before they fell ill
            gaps.append(gap)
    plan = joint_cover(op, gaps)
    in_hand: dict[tuple[str, str], str] = {}
    if ctx.desk is not None:
        for c in ctx.desk.all():
            if str(c.status) in ("offered", "accepted", "needs_decision", "awaiting_approval"):
                in_hand[(c.trip_id, c.vacated_by)] = c.callout_id
        if ctx.desk.plans is not None:
            for p in ctx.desk.plans.waiting():
                in_hand[(p.trip_id, p.replacing)] = p.plan_id
    reports = ctx.store.unavailability()
    fills = []
    for f in plan.fills:
        row = {
            "trip_id": f.gap.trip_id,
            "rank": str(f.gap.rank),
            "replacing": f.gap.vacated_by,
            **_choice(f.choice),
            "cost": f.choice.cost,
        }
        if f.choice.crew_id:
            row["risk"] = risk_json(crew_risk(op, f.choice.crew_id, reports))
        if (f.gap.trip_id, f.gap.vacated_by) in in_hand:
            row["in_hand"] = in_hand[(f.gap.trip_id, f.gap.vacated_by)]
        fills.append(row)
    seats = [
        {
            "trip_id": g.trip_id,
            "day": g.days[0].isoformat(),
            "days": [d.isoformat() for d in g.days],
            "rank": str(g.rank),
            "replacing": g.vacated_by,
            "name": op.state(g.vacated_by).name,
            "reports_at": _when(op.days_of(g.trip_id)[0].report),
            "passengers": g.passengers,
            "started": g.started,
            "choices": [] if g.started else _seat_choices(op, g, reports),
        }
        for g in gaps
    ]
    return (
        {
            "crew": [
                {
                    "crew_id": c,
                    "name": op.state(c).name,
                    "rank": str(op.state(c).rank),
                    **({"back_on_duty": True} if c in back else {}),
                }
                for c in report.crew_ids
            ],
            "seats": seats,
            "plan": {
                "fills": fills,
                "unfilled": [
                    {
                        "trip_id": g.trip_id,
                        "rank": str(g.rank),
                        "replacing": g.vacated_by,
                        "why": "the trip has already reported",
                    }
                    for g in plan.skipped
                ],
                "total_cost": plan.total_cost,
                "every_combination_compared": plan.searched,
            },
            "seats_open": sum(
                1 for g in gaps if not g.started and (g.trip_id, g.vacated_by) not in in_hand
            ),
        },
        plan,
        gaps,
    )


SEAT_CHOICES = 4


def _seat_choices(op: Operation, gap: Any, reports: list) -> list[dict[str, Any]]:
    """The best few ways to fill one seat, cheapest first, with each person's risk."""
    try:
        cover = find_cover(op, gap.trip_id, gap.rank, gap.vacated_by)
    except (NotFound, TripStarted):
        return []
    out = []
    for c in cover.choices:
        if not c.crew_id:
            continue
        row = {**_choice(c), "risk": risk_json(crew_risk(op, c.crew_id, reports))}
        out.append(row)
        if len(out) == SEAT_CHOICES:
            break
    return out


def _ops_analysis(ctx: Context, kind: Kind, d: dict[str, Any]) -> dict[str, Any]:
    """Rerun the engine for an operational report: the same answer as its own tool."""
    match kind:
        case Kind.FLIGHT_DELAY:
            out = delay_impact_tool(
                ctx, DelayImpactArgs(trip_id=d["trip_id"], hours=d["hours"], day=d.get("day"))
            )
            op = ctx.operation()
            day = date.fromisoformat(out["day"])
            first_leg = next(
                (td.legs[0] for td in op.trip(d["trip_id"]).days if td.day == day), None
            )
            if first_leg and op.legs[first_leg].gate:
                minutes = max(1, round(d["hours"] * 60))
                out["gates"] = late_at_gate(ctx, LateAtGate(leg_id=first_leg, minutes=minutes))
            return out
        case Kind.AIRCRAFT_TECHNICAL:
            out = aircraft_out_tool(
                ctx, AircraftOut(tail=d["tail"], start=d["start"], back=d["end"])
            )
            start = datetime.fromisoformat(str(d["start"]))
            back = datetime.fromisoformat(str(d["end"]))
            if start.tzinfo is None:
                start, back = start.replace(tzinfo=IST), back.replace(tzinfo=IST)
            hold_cost = sum(int(t.get("delay_cost_inr") or 0) for t in out.get("trips", []))
            flights, options = aircraft_options(
                ctx.operation(), d["tail"], start, back, hold_cost, crew_over(out)
            )
            out["flights_taken"] = flights
            out["options"] = [
                {
                    "way": o.way,
                    "summary": o.summary,
                    "cost_inr": o.cost,
                    **({"tail": o.tail} if o.tail else {}),
                    **({"note": o.note} if o.note else {}),
                }
                for o in options
            ]
            return out
        case Kind.STATION_DISRUPTION:
            return station_closure_tool(
                ctx, StationClosure(station=d["station"], start=d["start"], end=d["end"])
            )
        case Kind.GATE_CLOSURE:
            return gate_closure_tool(
                ctx,
                GateClosure(station=d["station"], gates=d["gates"], start=d["start"], end=d["end"]),
            )
        case _:
            return gate_change(ctx, GateChange(leg_id=d["leg_id"], gate=d["gate"]))


def crew_over(a: dict[str, Any]) -> int:
    """How many crew an operational analysis pushes over a limit, whatever its shape."""
    over = a.get("crew_over_a_limit")
    count = over if isinstance(over, int) else len(over or [])
    count += sum(len(i.get("crew_over_a_limit", [])) for i in a.get("crew_impact", []))
    if "gates" in a:
        count += crew_over(a["gates"])
    return count


def _ops_severity(ctx: Context, kind: Kind, a: dict[str, Any]) -> tuple[str, list[str]]:
    if kind is Kind.GATE_CHANGE:
        if a.get("free"):
            return "low", [f"gate {a['wanted_gate']} is free for boarding"]
        return "medium", [f"gate {a['wanted_gate']} is held by {', '.join(a.get('held_by', []))}"]
    op = ctx.operation()
    over = crew_over(a)
    trips = [a["trip_id"]] if a.get("trip_id") else [t["trip_id"] for t in a.get("trips", [])]
    trips += [m["trip_id"] for m in a.get("moves", []) if m.get("trip_id")]
    passengers = a.get("passengers") or a.get("passengers_moved") or 0
    soon = False
    for trip_id in set(trips):
        try:
            if for_trip(op, trip_id).level is Level.HIGH:
                soon = True
        except (NotFound, IndexError):
            continue
    reasons = [f"{passengers} passengers affected"]
    if over:
        reasons.insert(0, f"{over} crew pushed over a duty limit")
    if over and soon:
        return "high", reasons + ["a trip reports within hours"]
    if over or passengers >= 300:
        return "medium", reasons
    held = len(a.get("flights_held") or [])
    if kind is Kind.AIRCRAFT_TECHNICAL and held:
        # An aircraft that cannot fly takes its flights with it until the desk acts.
        return "medium", [f"{held} flight{'s' if held != 1 else ''} without an aircraft", *reasons]
    return "low", reasons


def _absence_severity(op: Operation, plan, gaps) -> tuple[str, list[str]]:
    live = list(plan.fills)
    if not live:
        return "low", ["no rostered duty left to cover in those days"]
    worst = None
    for f in live:
        grade = for_trip(op, f.gap.trip_id, no_crew_left=f.choice.way is Way.CANCEL)
        if worst is None or grade.level.rank < worst.level.rank:
            worst = grade
    assert worst is not None
    return str(worst.level), [
        f"{len(live)} seat{'s' if len(live) != 1 else ''} to fill",
        *worst.reasons,
    ]


def _by_flight(op: Operation, args: FileReport, crew_ids: list[str]) -> list[list[str]]:
    """Crew out together, grouped by the flights they leave: those who share a trip are one
    disruption (covered jointly), those on different trips are separate ones. Someone with no
    trip in those days goes with the first group."""
    first, last = _span(op, args)
    trips = {c: {g.trip_id for g in absence_gaps(op, c, first, last)} for c in crew_ids}
    groups: list[tuple[set[str], list[str]]] = []
    for crew_id in crew_ids:
        mine = trips[crew_id]
        joined = [g for g in groups if g[0] & mine]
        merged = (set(mine), [crew_id])
        for g in joined:
            merged[0].update(g[0])
            merged[1][:0] = g[1]
            groups.remove(g)
        groups.append(merged)
    grounded = [g for g in groups if g[0]]
    idle = [c for g in groups if not g[0] for c in g[1]]
    if not grounded:
        return [crew_ids]
    grounded[0][1].extend(idle)
    order = {c: i for i, c in enumerate(crew_ids)}
    return sorted((sorted(g[1], key=order.get) for g in grounded), key=lambda g: order[g[0]])


def _already_reported(ctx: Context, args: FileReport, crew_ids: list[str]) -> None:
    """Refuse a second report for the same thing: the desk would work it twice."""
    op = ctx.operation()
    active = [r for r in _reports(ctx).all() if r.status in ACTIVE]
    if args.kind in ABSENCES:
        first, last = _span(op, args)
        for r in active:
            if r.kind not in ABSENCES:
                continue
            r_first = date.fromisoformat(r.details["first_day"])
            r_last = date.fromisoformat(r.details["last_day"])
            both = set(crew_ids) & set(r.crew_ids)
            if both and r_first <= last and first <= r_last:
                who = ", ".join(op.state(c).name for c in sorted(both))
                raise ToolError(
                    f"{who} is already reported out {_days(r_first, r_last)} in {r.report_id} "
                    f"({r.title}); add to that report's note instead of filing again"
                )
        return
    for r in active:
        if r.kind is not args.kind:
            continue
        d = r.details
        same = {
            Kind.FLIGHT_DELAY: d.get("trip_id") == args.trip_id,
            Kind.GATE_CHANGE: d.get("leg_id") == args.leg_id,
            Kind.AIRCRAFT_TECHNICAL: d.get("tail") == args.tail,
            Kind.STATION_DISRUPTION: d.get("station") == args.station,
            Kind.GATE_CLOSURE: d.get("station") == args.station
            and bool({g.upper() for g in d.get("gates") or []} & {g.upper() for g in args.gates}),
        }.get(args.kind, False)
        if same:
            raise ToolError(
                f"this is already reported as {r.report_id} ({r.title}); follow that report "
                "instead of filing again"
            )


def _cert_words(cert: str | None) -> str:
    """A certificate kind as people say it: recurrent_training is "recurrent training"."""
    return (cert or "certificate").replace("_", " ")


def report_disruption(ctx: Context, args: FileReport) -> dict[str, Any]:
    desk = _reports(ctx)
    crew_ids = _who_may_file(ctx, args)
    op = ctx.operation()
    _already_reported(ctx, args, crew_ids)
    if args.kind is Kind.CREW_UNAVAILABLE and len(crew_ids) > 1:
        groups = _by_flight(op, args, crew_ids)
        if len(groups) > 1:
            filed = [
                report_disruption(ctx, args.model_copy(update={"crew_ids": group}))
                for group in groups
            ]
            first = filed[0]
            first["also_filed"] = [
                {"report_id": f["report_id"], "title": f["title"], "desk": f["desk"]}
                for f in filed[1:]
            ]
            first["note"] = (
                f"Filed as {len(filed)} disruptions, one per flight: crew on different flights "
                "are covered separately; crew on the same flight are covered together."
            )
            return first
    by = ctx.caller.user
    details: dict[str, Any] = {"note": args.note} if args.note else {}
    if args.evidence_ids:
        details["evidence"] = _evidence(ctx, args)
    elif args.kind is Kind.AIRCRAFT_TECHNICAL:
        details["evidence_note"] = "No photo: maintenance confirms the fault on the aircraft."
    unavailability: list[str] = []
    stations: list[str] = []

    if args.kind in ABSENCES:
        for crew_id in crew_ids:
            op.state(crew_id)
        first, last = _span(op, args)
        since = None
        if args.since:
            hour, minute = map(int, args.since.split(":"))
            since = datetime.combine(first, time(hour, minute), IST).astimezone(UTC)
        kind, reason = {
            Kind.CREW_UNAVAILABLE: (args.reason_kind, args.note or args.reason_kind),
            Kind.PLANNED_LEAVE: ("leave", (args.leave or "leave").replace("_", " ")),
            Kind.CERTIFICATION_LAPSE: ("certificate", f"{_cert_words(args.cert)} lapsed"),
        }[args.kind]
        details |= {
            "crew_ids": crew_ids,
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "reason": reason,
            **({"reason_kind": args.reason_kind} if args.kind is Kind.CREW_UNAVAILABLE else {}),
            **({"leave": args.leave} if args.leave else {}),
            **({"cert": args.cert} if args.cert else {}),
            **({"since": args.since, "since_utc": since.isoformat()} if since else {}),
        }
        recorded = ctx.store.unavailability()
        for crew_id in crew_ids:
            # Already recorded for these days (report_unavailable first): keep that record.
            u = next(
                (
                    u
                    for u in recorded
                    if u.crew_id == crew_id and u.first_day <= first and u.last_day >= last
                ),
                None,
            ) or ctx.store.mark_unavailable(
                crew_id, first, last, reason[:200], by, kind=kind, since=since
            )
            unavailability.append(u.report_id)
            if ctx.desk is not None:
                ctx.desk.withdraw(crew_id)
        stations = [ctx.operation().state(c).base for c in crew_ids]
        word = {
            Kind.CREW_UNAVAILABLE: args.reason_kind.capitalize(),
            Kind.PLANNED_LEAVE: "Leave",
            Kind.CERTIFICATION_LAPSE: f"{_cert_words(args.cert).capitalize()} lapsed",
        }[args.kind]
        title = f"{word}: {_names(op, crew_ids)}, {_days(first, last)}"
        draft = Report(
            report_id="R-000000",
            kind=args.kind,
            title=title,
            details=details,
            filed_by=by,
            filed_role=str(ctx.caller.role),
            filed_at=clock_now(),
            desk_id="desk-1",
            severity="low",
            severity_reasons=[],
            stations=stations,
            crew_ids=crew_ids,
        )
        _, plan, gaps = _absence_analysis(ctx, draft)
        severity = _absence_severity(ctx.operation(), plan, gaps)
    else:
        raw = args.model_dump(
            mode="json",
            include={
                "trip_id",
                "hours",
                "day",
                "tail",
                "start",
                "end",
                "station",
                "gates",
                "leg_id",
                "gate",
            },
            exclude_none=True,
        )
        if args.kind is Kind.GATE_CLOSURE:
            raw["gates"] = [g.strip().upper() for g in args.gates if g.strip()]
        try:
            analysis = _ops_analysis(ctx, args.kind, raw)
        except (NotFound, ValueError, KeyError) as err:
            raise ToolError(f"cannot analyse this report: {err}") from err
        details |= raw
        severity = _ops_severity(ctx, args.kind, analysis)
        stations, title = _ops_where(op, args, analysis)

    severity = _with_reported(severity, args.severity, by)
    report = desk.file(
        args.kind,
        title,
        details,
        by,
        str(ctx.caller.role),
        severity,
        stations,
        crew_ids=crew_ids,
        unavailability_ids=unavailability,
    )
    return _report_json(ctx, report)


LEVELS = ("low", "medium", "high")


def _with_reported(
    worked_out: tuple[str, list[str]], reported: str | None, by: str
) -> tuple[str, list[str]]:
    """The higher of the engine's severity and the one the person filing gave: what they saw
    on the ground can raise it, never lower what the engine found it does to the flights."""
    level, reasons = worked_out[0], list(worked_out[1])
    if reported is None or reported == level:
        return level, reasons
    if LEVELS.index(reported) > LEVELS.index(level):
        return reported, [f"reported as {reported} by {by}", *reasons]
    return level, [
        *reasons,
        f"reported as {reported} by {by}; the flights affected keep it {level}",
    ]


def _ops_where(op: Operation, args: FileReport, a: dict[str, Any]) -> tuple[list[str], str]:
    match args.kind:
        case Kind.FLIGHT_DELAY:
            legs = op.trip(args.trip_id or "").leg_ids
            origin = op.legs[legs[0]].origin
            return [origin], f"{args.trip_id} {args.hours:g} h late"
        case Kind.AIRCRAFT_TECHNICAL:
            held = a.get("flights_held", [])
            stations = sorted({f["route"].split("-")[0] for f in held})
            return stations, f"{args.tail} out of service, {len(held)} flights held"
        case Kind.STATION_DISRUPTION:
            held = a.get("flights_held", [])
            return [args.station or ""], f"{args.station} closed, {len(held)} flights held"
        case Kind.GATE_CLOSURE:
            return [args.station or ""], (
                f"{args.station} gate{'s' if len(args.gates) > 1 else ''} "
                f"{', '.join(args.gates)} closed"
            )
        case _:
            return [a.get("station", "")], f"{a.get('flight')} to gate {a.get('wanted_gate')}"


# -- reading ----------------------------------------------------------------------------------


def _can(ctx: Context, report: Report, seats_open: int = 0) -> dict[str, bool]:
    role, user = ctx.caller.role, ctx.caller.user
    owner = next(d for d in DESKS if d.desk_id == report.desk_id)
    working = report.status in WORKING
    mine = role is Role.CONTROLLER and owner.controller == user
    return {
        "plan": mine and working and report.kind in ABSENCES and seats_open > 0,
        "close": mine and working,
        "confirm": role in APPROVE_ROLES and report.status is ReportStatus.RESOLVED,
        "return": role in APPROVE_ROLES and report.status is ReportStatus.RESOLVED,
        "revoke": role in ADMINS
        and report.status not in (ReportStatus.CLOSED, ReportStatus.REVOKED),
        "reassign": role in APPROVE_ROLES | ADMINS and report.status in ACTIVE,
    }


def _report_json(ctx: Context, report: Report) -> dict[str, Any]:
    desk = _reports(ctx)
    out = summary(report)
    out["filed_at"] = _when(report.filed_at)
    out["details"] = report.details
    if report.status is ReportStatus.REVOKED:
        out["analysis"] = {"note": "revoked: filed in error"}
        out["summary"] = "Revoked; its absences were withdrawn."
    elif report.kind in ABSENCES:
        analysis, _, _ = _absence_analysis(ctx, report)
        out["analysis"] = analysis
        out["seats_open"] = analysis["seats_open"]
        out["summary"] = (
            f"{len(analysis['seats'])} seat{'s' if len(analysis['seats']) != 1 else ''} left "
            f"empty; {analysis['seats_open']} still to fill. The cheapest way to fill them "
            f"together costs INR {analysis['plan']['total_cost']:,}."
        )
    else:
        try:
            out["analysis"] = _ops_analysis(ctx, report.kind, report.details)
        except (NotFound, ValueError, ToolError) as err:
            out["analysis"] = {"note": f"cannot analyse it any more: {err}"}
        over = crew_over(out["analysis"])
        out["summary"] = (
            f"{over} crew pushed over a duty limit." if over else "No crew over a limit."
        )
    out["teams"] = desk.teams(report)
    plans = [{**_plan(p), "report_id": report.report_id} for p in _report_plans(ctx, report)]
    out["plans"] = plans
    callouts = []
    if ctx.desk is not None:
        for callout_id in report.callout_ids:
            try:
                callouts.append(_callout(ctx.desk.get(callout_id)))
            except (NotFound, Exception):
                continue
    out["callouts"] = callouts
    out["plans_waiting"] = sum(p["status"] == "submitted" for p in plans)
    out["history"] = [{**h, "at": _when(datetime.fromisoformat(h["at"]))} for h in report.history]
    out["resolution_note"] = report.resolution_note
    if report.changes:
        out["changes"] = [FlightChange(**c).describe() for c in report.changes]
    out["can"] = _can(ctx, report, out.get("seats_open", 0))
    return out


class Disruptions(BaseModel):
    status: str = Field(
        "active", description="active (open, in progress or resolved), all, or one status"
    )
    desk_id: str | None = Field(None, pattern=r"^desk-\d$")
    limit: int = Field(30, ge=1, le=100)


def disruptions(ctx: Context, args: Disruptions) -> dict[str, Any]:
    found = _reports(ctx).all()
    counts = {str(s): sum(r.status is s for r in found) for s in ReportStatus}
    if args.status == "active":
        found = [r for r in found if r.status in ACTIVE]
    elif args.status != "all":
        found = [r for r in found if str(r.status) == args.status]
    if args.desk_id:
        found = [r for r in found if r.desk_id == args.desk_id]
    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda r: (r.status not in WORKING, order.get(r.severity, 3)))
    return {
        "disruptions": [_listed(ctx, r) for r in found[: args.limit]],
        "counts": counts,
    }


def _report_plans(ctx: Context, report: Report) -> list[Any]:
    """The plans made for a report: submitted from it, or made when someone accepted one of
    its callouts."""
    if ctx.desk is None or ctx.desk.plans is None:
        return []
    callouts = set(report.callout_ids)
    ids = set(report.plan_ids)
    return [p for p in ctx.desk.plans.all() if p.plan_id in ids or p.source in callouts]


def _listed(ctx: Context, report: Report) -> dict[str, Any]:
    out = summary(report)
    out["filed_at"] = _when(report.filed_at)
    out["plans_waiting"] = sum(p.status is PlanStatus.SUBMITTED for p in _report_plans(ctx, report))
    if report.resolution_note:
        out["resolution_note"] = report.resolution_note
    if report.changes:
        out["changes"] = [FlightChange(**c).describe() for c in report.changes]
    return out


class MyReports(BaseModel):
    active_only: bool = Field(False, description="Only reports not yet closed or revoked")


def my_reports(ctx: Context, args: MyReports) -> dict[str, Any]:
    crew_id = ctx.caller.crew_id if ctx.caller.role is Role.CREW else None
    mine = [
        r
        for r in _reports(ctx).all()
        if r.filed_by == ctx.caller.user or (crew_id is not None and crew_id in r.crew_ids)
    ]
    if args.active_only:
        mine = [r for r in mine if r.status in ACTIVE]
    return {"disruptions": [_listed(ctx, r) for r in mine]}


class ReportId(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")


def show_disruption(ctx: Context, args: ReportId) -> dict[str, Any]:
    report = _get(ctx, args.report_id)
    about_me = ctx.caller.role is Role.CREW and ctx.caller.crew_id in report.crew_ids
    if ctx.caller.role not in READERS and report.filed_by != ctx.caller.user and not about_me:
        raise Forbidden("you can see the reports you filed or that are about you, not others")
    return _report_json(ctx, report)


def _get(ctx: Context, report_id: str) -> Report:
    return _reports(ctx).get(report_id)


# -- working a report -------------------------------------------------------------------------


class SubmitReportPlan(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")
    note: str = Field("", max_length=300)


def submit_report_plan(ctx: Context, args: SubmitReportPlan) -> dict[str, Any]:
    """Submit the joint plan: one plan per seat the cheapest combination fills with crew."""
    desk = _reports(ctx)
    try:
        report = desk.check_mine(args.report_id, ctx.caller.user)
    except ReportError as err:
        raise _fail(err) from err
    if report.kind not in ABSENCES:
        raise ToolError("only reports that leave seats empty have a crew plan to submit")
    plans = ctx.plans()
    analysis, joint, _ = _absence_analysis(ctx, report)
    submitted, skipped = [], []
    for fill, row in zip(joint.fills, analysis["plan"]["fills"], strict=True):
        seat = f"{fill.gap.rank} on {fill.gap.trip_id}"
        if "in_hand" in row:
            skipped.append({"seat": seat, "why": f"already in hand ({row['in_hand']})"})
            continue
        if not fill.choice.crew_id:
            skipped.append(
                {
                    "seat": seat,
                    "why": f"{fill.choice.summary}: needs a callout and an approver's decision",
                }
            )
            continue
        try:
            plan = plans.submit(
                fill.gap.trip_id,
                fill.gap.vacated_by,
                fill.choice.crew_id,
                ctx.caller.user,
                args.note or f"{report.report_id}: {report.title}",
                source=report.report_id,
            )
        except PlanError as err:
            skipped.append({"seat": seat, "why": str(err)})
            continue
        desk.link(report.report_id, ctx.caller.user, plan_id=plan.plan_id)
        submitted.append(_plan(plan))
    return {
        "report": _report_json(ctx, desk.get(report.report_id)),
        "plans": submitted,
        "skipped": skipped,
    }


class ReturnReport(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")
    note: str = Field(min_length=3, max_length=500, description="What is still missing")


class CloseReport(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")
    note: str = Field(min_length=3, max_length=500, description="How it was resolved")
    option: int | None = Field(
        None, ge=0, le=9, description="For an aircraft fault: the chosen option, by its place"
    )
    gates: dict[str, str] = Field(
        default_factory=dict, description="For a gate closure: leg id -> the gate it boards at"
    )


def close_disruption(ctx: Context, args: CloseReport) -> dict[str, Any]:
    """Send a resolution to an approver. Any flight it changes (another aircraft, a gate, a
    cancellation) changes only when the approver confirms it."""
    desk = _reports(ctx)
    try:
        report = desk.check_mine(args.report_id, ctx.caller.user)
        changes = (
            flight_changes.for_resolution(
                ctx.operation(),
                str(report.kind),
                report.details,
                _ops_analysis(ctx, report.kind, report.details),
                args.option,
                args.gates,
            )
            if str(report.kind) in flight_changes.KINDS
            else []
        )
        report = desk.close(
            args.report_id, ctx.caller.user, args.note, flight_changes.as_dicts(changes)
        )
    except (ReportError, flight_changes.FlightChangeError) as err:
        raise _fail(err) from err
    return _report_json(ctx, report)


def confirm_resolution(ctx: Context, args: ReportId) -> dict[str, Any]:
    """Confirm a resolution and close the report; the flight changes it carries are made now."""
    desk = _reports(ctx)
    try:
        report = desk.confirm(args.report_id, ctx.caller.user)
    except ReportError as err:
        raise _fail(err) from err
    out = _report_json(ctx, report)
    if report.changes:
        out["flights_changed"] = flight_changes.apply(
            ctx.store, report.changes, desk.outbox, ctx.caller.user, report.report_id, desk.clock
        )
    return out


def return_resolution(ctx: Context, args: ReturnReport) -> dict[str, Any]:
    try:
        report = _reports(ctx).give_back(args.report_id, ctx.caller.user, args.note)
    except ReportError as err:
        raise _fail(err) from err
    return _report_json(ctx, report)


class Reassign(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")
    desk_id: str = Field(pattern=r"^desk-\d$")
    note: str = Field("", max_length=300)


def reassign_disruption(ctx: Context, args: Reassign) -> dict[str, Any]:
    try:
        report = _reports(ctx).reassign(args.report_id, args.desk_id, ctx.caller.user, args.note)
    except ReportError as err:
        raise _fail(err) from err
    return _report_json(ctx, report)


class Revoke(BaseModel):
    report_id: str = Field(pattern=r"^R-[0-9a-f]{6}$")
    reason: str = Field(min_length=3, max_length=300)


def revoke_report(ctx: Context, args: Revoke) -> dict[str, Any]:
    desk = _reports(ctx)
    try:
        report = desk.revoke(args.report_id, ctx.caller.user, args.reason)
    except ReportError as err:
        raise _fail(err) from err
    for report_id in report.unavailability_ids:
        try:
            ctx.store.clear_unavailable(report_id)
        except NotFound:
            continue  # already withdrawn
    return _report_json(ctx, report)


class DeskLoad(BaseModel):
    desk_id: str | None = Field(None, pattern=r"^desk-\d$", description="Only this desk")


def desk_load(ctx: Context, args: DeskLoad) -> dict[str, Any]:
    return {
        "desks": [d for d in _reports(ctx).load() if args.desk_id in (None, d["desk_id"])],
        "rule": "a new disruption goes to the desk with the fewest open ones, then the fewest "
        "overall",
    }


# -- risk, teams, the queue -------------------------------------------------------------------


class CrewRisk(BaseModel):
    crew_id: str | None = Field(None, pattern=CREW_ID)
    limit: int = Field(10, ge=1, le=50)


def crew_risk_tool(ctx: Context, args: CrewRisk) -> dict[str, Any]:
    op = ctx.operation()
    reports = ctx.store.unavailability()
    if ctx.caller.role is Role.CREW:
        args = CrewRisk(crew_id=ctx.caller.crew_id, limit=1)
    ids = [args.crew_id] if args.crew_id else list(op.crew)
    rows = []
    for crew_id in ids:
        crew = op.state(crew_id)
        risk = crew_risk(op, crew_id, reports)
        rows.append(
            {
                "crew_id": crew_id,
                "name": crew.name,
                "rank": str(crew.rank),
                "airline": crew.airline,
                **risk_json(risk),
            }
        )
    rows.sort(key=lambda r: -r["score"])
    return {
        "crew": rows[: args.limit],
        "note": "Shown for judgement, never used to rank cover or rule anyone out.",
    }


class Contacts(BaseModel):
    station: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    team: Team | None = None


def contacts_tool(ctx: Context, args: Contacts) -> dict[str, Any]:
    seed = ctx.reports.seed_dir if ctx.reports is not None else ROOT / "data" / "seed"
    everyone = contacts(seed)
    teams = []
    for team, info in TEAMS.items():
        if args.team is not None and team is not args.team:
            continue
        rows = [
            {"station": c.station, "name": c.name, "phone": c.phone}
            for c in everyone
            if c.team is team and (args.station is None or c.station in (args.station, "NETWORK"))
        ]
        teams.append(
            {
                "team": str(team),
                "title": info.title,
                "job": info.job,
                "per_station": info.per_station,
                "contacts": rows,
            }
        )
    return {"teams": teams}


class MyQueue(BaseModel):
    limit: int = Field(20, ge=1, le=50)


def my_queue(ctx: Context, args: MyQueue) -> dict[str, Any]:
    """What is waiting for the caller, most urgent first."""
    role, user = ctx.caller.role, ctx.caller.user
    items: list[dict[str, Any]] = []

    def add(kind: str, ref: str, title: str, detail: str, link: str, tone: str) -> None:
        items.append(
            {"kind": kind, "ref": ref, "title": title, "detail": detail, "link": link, "tone": tone}
        )

    reports = ctx.reports.all() if ctx.reports is not None else []
    callouts = ctx.desk.all() if ctx.desk is not None else []
    plans = ctx.desk.plans.all() if ctx.desk is not None and ctx.desk.plans else []
    view = "/disruptions/view/?id="
    if role is Role.CREW:
        for c in ctx.desk.offers_for(ctx.caller.crew_id or "") if ctx.desk else []:
            add(
                "offer",
                c.callout_id,
                f"Callout offer: {c.rank} on {c.trip_id}",
                f"answer by {_when(c.current.expires_at)}" if c.current else "",
                "/crew/",
                "critical",
            )
    if role is Role.CONTROLLER:
        mine = next((d.desk_id for d in DESKS if d.controller == user), None)
        for r in reports:
            if r.desk_id == mine and r.status in WORKING:
                add(
                    "disruption",
                    r.report_id,
                    r.title,
                    f"{r.status}, severity {r.severity}",
                    view + r.report_id,
                    "critical" if r.severity == "high" else "pending",
                )
        for c in callouts:
            if c.opened_by == user and c.status is Status.NEEDS_DECISION:
                add(
                    "callout",
                    c.callout_id,
                    f"Nobody accepted {c.rank} on {c.trip_id}",
                    "propose cancelling or flying one short",
                    "/desk/",
                    "critical",
                )
        for p in plans:
            if p.submitted_by == user and p.status is PlanStatus.SENT_BACK:
                add(
                    "plan",
                    p.plan_id,
                    f"Sent back: {p.rank} on {p.trip_id}",
                    p.decision_note,
                    "/approvals/",
                    "pending",
                )
    # The auditor only reads: nothing waits on them.
    if role in (Role.APPROVER, Role.SPEAKER, Role.ADMIN, Role.SUPER_ADMIN):
        for p in plans:
            if p.status is PlanStatus.SUBMITTED:
                add(
                    "plan",
                    p.plan_id,
                    f"Approve: {p.summary} on {p.trip_id}",
                    f"by {p.submitted_by}, INR {p.cost:,}",
                    "/approvals/",
                    "pending",
                )
        for c in callouts:
            if c.status is Status.AWAITING_APPROVAL and c.proposal is not None:
                add(
                    "callout",
                    c.callout_id,
                    f"Decide: {c.proposal.summary}",
                    f"proposed by {c.proposal.proposed_by}",
                    "/approvals/",
                    "critical",
                )
        for r in reports:
            if r.status is ReportStatus.RESOLVED:
                add(
                    "disruption",
                    r.report_id,
                    f"Confirm resolution: {r.title}",
                    r.resolution_note,
                    view + r.report_id,
                    "pending",
                )
        if role is not Role.APPROVER:
            for r in reports:
                if r.status in WORKING and r.severity == "high":
                    add(
                        "disruption",
                        r.report_id,
                        r.title,
                        f"{r.status} on {r.desk_id}",
                        view + r.report_id,
                        "critical",
                    )
    # A reporter's own reports wait on crew control, not on them: they follow them on My reports.
    order = {"critical": 0, "pending": 1, "info": 2}
    items.sort(key=lambda i: order[i["tone"]])
    return {"items": items[: args.limit], "count": len(items)}


# -- the station's view of the schedule ----------------------------------------------------

LOOK_BACK_DAYS = 5


class OperationsWeek(BaseModel):
    start: date | None = Field(None, description="First day of the week shown; default today")
    day: date | None = Field(None, description="The day whose flights to list; default the start")


def _ist_day(moment: datetime) -> date:
    return moment.astimezone(IST).date()


def _short_rank(rank: Any) -> str:
    return {"Captain": "CPT", "First Officer": "FO", "Purser": "PUR", "Cabin Crew": "CC"}.get(
        str(rank), str(rank)
    )


def operations_week(ctx: Context, args: OperationsWeek) -> dict[str, Any]:
    """A week of flights, who flies each and who is out: what a station needs to report from.
    Who is out shows when, never why."""
    op = ctx.operation()
    today = _ist_day(op.now)
    days_held = sorted({_ist_day(leg.off_block) for leg in op.legs.values()})
    first_day, last_day = max(days_held[0], today - timedelta(days=LOOK_BACK_DAYS)), days_held[-1]
    start = min(max(args.start or today, first_day), last_day)
    week = [start + timedelta(days=i) for i in range(7)]
    chosen = args.day if args.day in week else start

    trip_of = {leg_id: t for t in op.trips.values() for leg_id in t.leg_ids}
    out_on: dict[str, set[date]] = {c: {d for d, _ in days} for c, days in op.unavailable.items()}
    reports = [r for r in ctx.reports.all() if r.status in ACTIVE] if ctx.reports else []

    def touching(leg: Any, trip: Any) -> list[dict[str, Any]]:
        found = []
        for r in reports:
            d = r.details
            hit = (
                d.get("trip_id") == trip.trip_id
                or d.get("leg_id") == leg.leg_id
                or (d.get("tail") == leg.tail and r.kind is Kind.AIRCRAFT_TECHNICAL)
                or (d.get("station") == leg.origin and r.kind is Kind.STATION_DISRUPTION)
                or (d.get("station") == leg.origin and leg.gate in (d.get("gates") or []))
                or any(c in {s.crew_id for s in trip.crew} for c in r.crew_ids)
            )
            if hit:
                found.append({"report_id": r.report_id, "severity": r.severity, "title": r.title})
        return found

    by_day: dict[date, list[Any]] = {}
    for leg in op.legs.values():
        by_day.setdefault(_ist_day(leg.off_block), []).append(leg)

    def person(crew_id: str, day: date) -> dict[str, Any]:
        state = op.state(crew_id)
        return {"crew_id": crew_id, "name": state.name, "out": day in out_on.get(crew_id, set())}

    summary = []
    for day in week:
        legs = by_day.get(day, [])
        pax: dict[str, int] = {}
        for leg in legs:
            airline = trip_of[leg.leg_id].airline if leg.leg_id in trip_of else "?"
            pax[airline] = pax.get(airline, 0) + leg.booked
        summary.append(
            {
                "day": day.isoformat(),
                "flights": len(legs),
                "passengers": sum(pax.values()),
                "by_airline": pax,
                "crew_out": sum(1 for days in out_on.values() if day in days),
                "disruptions": sum(
                    1
                    for leg in legs
                    if leg.leg_id in trip_of and touching(leg, trip_of[leg.leg_id])
                ),
            }
        )

    flights = []
    for leg in sorted(by_day.get(chosen, []), key=lambda leg: leg.off_block):
        trip = trip_of.get(leg.leg_id)
        if trip is None:
            continue
        seats = {s.rank: [] for s in trip.crew}
        for s in trip.crew:
            seats[s.rank].append(s.crew_id)
        cabin = [person(c, chosen) for r, ids in seats.items() if not r.is_pilot for c in ids]
        found = touching(leg, trip)
        flights.append(
            {
                "leg_id": leg.leg_id,
                "flight_no": leg.flight_no,
                "trip_id": trip.trip_id,
                "airline": trip.airline,
                "from": leg.origin,
                "to": leg.destination,
                "departs": _when(leg.off_block),
                "arrives": _when(leg.on_block),
                "tail": leg.tail,
                "aircraft_type": str(leg.aircraft_type),
                "gate": leg.gate,
                "booked": leg.booked,
                "seats": leg.seats,
                "cancelled": leg.leg_id in op.cancelled,
                "captain": [person(c, chosen) for c in seats.get(Rank.CAPTAIN, [])],
                "first_officer": [person(c, chosen) for c in seats.get(Rank.FIRST_OFFICER, [])],
                "cabin": len(cabin),
                "cabin_out": [c for c in cabin if c["out"]],
                "disruptions": found,
                "departed": leg.off_block <= op.now,
            }
        )

    crew_out = []
    for crew_id, days in sorted(out_on.items()):
        shown = sorted(d for d in days if d in week)
        if not shown:
            continue
        state = op.state(crew_id)
        crew_out.append(
            {
                "crew_id": crew_id,
                "name": state.name,
                "rank": _short_rank(state.rank),
                "airline": state.airline,
                "days": [d.isoformat() for d in shown],
            }
        )

    airlines = [
        {"code": a.code, "name": a.name, "color": a.color} for a in ctx.store.dataset().airlines
    ]
    return {
        "today": today.isoformat(),
        "start": start.isoformat(),
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "day": chosen.isoformat(),
        "airlines": airlines,
        "week": summary,
        "flights": flights,
        "crew_out": crew_out,
    }


class Catalog(BaseModel):
    detail: bool = Field(True, description="Unused; the whole catalogue is returned")


def catalog(ctx: Context, args: Catalog) -> dict[str, Any]:
    """What a report form picks from, so nobody types an id that does not exist: trips, flights,
    aircraft, stations and gates, from today on."""
    op = ctx.operation()
    today = _ist_day(op.now)
    trip_of = {leg_id: t.trip_id for t in op.trips.values() for leg_id in t.leg_ids}
    legs = sorted(
        (
            leg
            for leg in op.legs.values()
            if _ist_day(leg.off_block) >= today and leg.leg_id not in op.cancelled
        ),
        key=lambda leg: leg.off_block,
    )
    trips = []
    for t in sorted(op.trips.values(), key=lambda t: op.days_of(t.trip_id)[0].report):
        days = op.days_of(t.trip_id)
        if _ist_day(days[-1].release) < today:
            continue
        route = " ".join(
            [op.legs[t.days[0].legs[0]].origin] + [op.legs[leg].destination for leg in t.leg_ids]
        )
        trips.append(
            {
                "trip_id": t.trip_id,
                "airline": t.airline,
                "tail": t.tail,
                "route": route,
                "reports": _when(days[0].report),
                "days": [_ist_day(d.report).isoformat() for d in days],
            }
        )
    tails: dict[str, dict[str, Any]] = {}
    for t in op.trips.values():
        leg = op.legs[t.leg_ids[0]]
        tails.setdefault(
            t.tail, {"tail": t.tail, "airline": t.airline, "type": str(leg.aircraft_type)}
        )
    return {
        "trips": trips,
        "flights": [
            {
                "leg_id": leg.leg_id,
                "flight_no": leg.flight_no,
                "trip_id": trip_of.get(leg.leg_id),
                "from": leg.origin,
                "to": leg.destination,
                "departs": _when(leg.off_block),
                "gate": leg.gate,
            }
            for leg in legs
        ],
        "tails": sorted(tails.values(), key=lambda t: t["tail"]),
        "stations": sorted({leg.origin for leg in op.legs.values()}),
        "gates": {
            station: [g.gate for g in gates if str(g.kind) != "emergency"]
            for station, gates in sorted(op.gates.items())
        },
    }


# -- who the caller is -----------------------------------------------------------------------

GUIDE: dict[Role, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    Role.CREW: (
        "Crew member",
        "You fly the trips on your roster. Crew control keeps you legal and calls you out when "
        "a seat needs covering.",
        (
            "see your roster, duty hours, certificates and risk score",
            "accept or decline a callout offered to you, with a remark",
            "report that you cannot fly, and withdraw it if you can",
        ),
        ("see anyone else's roster or record", "change a roster"),
    ),
    Role.REPORTER: (
        "Station reporter",
        "You are the station duty manager: you file disruptions once and follow them to the end.",
        (
            "file sick calls, delays, gate closures and changes, station closures, aircraft "
            "faults and certificate lapses when someone tells you of one, with photos",
            "follow the reports you filed",
            "look up flights, trips and gates, and who to call in each team",
        ),
        (
            "see crew records, certificates or duty hours: crew control watches those",
            "find cover or change a roster",
            "approve anything",
        ),
    ),
    Role.CONTROLLER: (
        "Crew controller",
        "You own a desk: you work the disruption reports assigned to it, find legal cover, run "
        "callouts and propose roster changes.",
        (
            "find cover and submit plans for the reports on your desk",
            "start callouts and propose cancelling or flying one short when nobody accepts",
            "file disruptions and report crew unavailable",
            "resolve a report once its seats are covered",
        ),
        ("approve your own plans: an approver does", "work a report on another desk"),
    ),
    Role.APPROVER: (
        "Approver",
        "You are the second pair of eyes: no roster change happens until you approve it.",
        (
            "approve, reject or send back roster plans",
            "approve or reject a cancellation or flying one short",
            "confirm or return a controller's resolution",
            "move a report to another desk",
        ),
        ("propose changes yourself",),
    ),
    Role.AUDITOR: (
        "Auditor",
        "You read everything, the activity and security logs included, and change nothing.",
        ("read every report, plan, callout and log",),
        ("change anything",),
    ),
    Role.ADMIN: (
        "Administrator",
        "You look after the team and the system.",
        (
            "create accounts, disable them and reset passwords",
            "tighten duty limits beyond the regulator's",
            "upload rosters, book planned leave and set alert email addresses",
            "revoke a report filed in error",
        ),
        ("relax a limit: only a super admin can", "manage other admins"),
    ),
    Role.SUPER_ADMIN: (
        "Super admin",
        "You do everything an administrator does, and look after the administrators too.",
        (
            "everything an administrator can",
            "manage administrator accounts",
            "relax a duty limit back towards the regulator's figure, never past it",
        ),
        ("relax a limit past the regulator's figure",),
    ),
    Role.SPEAKER: (
        "Ops-room speaker",
        "This account is the ops-room speaker: it hears what waits for the desk.",
        ("hear what is waiting",),
        ("change anything",),
    ),
}


class AboutMe(BaseModel):
    detail: bool = Field(True, description="Include what waits for the caller now")


def about_me(ctx: Context, args: AboutMe) -> dict[str, Any]:
    """Who the caller is, what their role does and what waits for them now."""
    from crewpay.ops.accounts import DEMO_ACCOUNTS
    from crewpay.ops.desks import desk_of

    who = ctx.caller
    title, does, can, cannot = GUIDE[who.role]
    out: dict[str, Any] = {
        "user": who.user,
        "role": str(who.role),
        "role_title": title,
        "what_you_do": does,
        "you_can": list(can),
        "you_cannot": list(cannot),
    }
    demo = next((a for a in DEMO_ACCOUNTS if a.user == who.user), None)
    if demo is not None:
        out["name"] = demo.label.split(",")[0]
    if (desk := desk_of(who.user)) is not None:
        out["desk"] = {"desk_id": desk.desk_id, "name": desk.name}
    op = ctx.operation()
    out["now"] = _when(op.now)
    if who.role is Role.CREW and who.crew_id:
        state = op.state(who.crew_id)
        out.update(
            name=state.name,
            crew_id=who.crew_id,
            rank=str(state.rank),
            airline=state.airline,
            base=state.base,
        )
        if state.rest_until is not None and state.rest_until > op.now:
            out["resting_until"] = _when(state.rest_until)
        ahead = [d for d in state.duties if d.release > op.now]
        if ahead:
            out["next_duty"] = {"trip_id": ahead[0].trip_id, "reports": _when(ahead[0].report)}
    if args.detail:
        queue = my_queue(ctx, MyQueue(limit=5))
        out["waiting_on_you"] = queue["count"] or "nothing right now"
        if queue["items"]:
            out["first_things"] = [f"{i['title']} ({i['detail']})" for i in queue["items"]]
    return out


# -- accounts, alerts, roster ----------------------------------------------------------------


def _accounts(ctx: Context):
    if ctx.accounts is None:
        raise ToolError("team accounts are not available here")
    return ctx.accounts


class TeamAccounts(BaseModel):
    role: Role | None = Field(None, description="Only accounts with this role")


def team_accounts(ctx: Context, args: TeamAccounts) -> dict[str, Any]:
    return {
        "accounts": [
            {
                "username": a.username,
                "role": a.role,
                "crew_id": a.crew_id,
                "enabled": a.enabled,
                "status": a.status,
                "created": a.created,
            }
            for a in _accounts(ctx).list()
            if args.role is None or a.role == str(args.role)
        ]
    }


class CreateAccount(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,31}$")
    role: Role
    crew_id: str | None = Field(None, pattern=CREW_ID)
    email: str | None = Field(None, max_length=200)


def create_account(ctx: Context, args: CreateAccount) -> dict[str, Any]:
    accounts = _accounts(ctx)
    try:
        check_may_manage(ctx.caller.role, ctx.caller.user, args.username, args.role, None)
        if args.role is Role.CREW:
            if not args.crew_id:
                raise AccountError("a crew account needs the crew id it belongs to")
            ctx.operation().state(args.crew_id)
        password = accounts.create(args.username, args.role, args.crew_id, args.email)
    except (AccountError, NotFound) as err:
        raise _fail(err) from err
    return {
        "username": args.username,
        "role": str(args.role),
        "temporary_password": password,
        "note": "Shown once. They choose their own password when they first sign in.",
    }


class SetAccount(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,31}$")
    role: Role | None = None
    enabled: bool | None = None


def set_account(ctx: Context, args: SetAccount) -> dict[str, Any]:
    accounts = _accounts(ctx)
    try:
        current = current_role(accounts, args.username)
        if current is None:
            raise AccountError(f"no account {args.username}")
        check_may_manage(ctx.caller.role, ctx.caller.user, args.username, args.role, current)
        if args.role is not None:
            accounts.set_role(args.username, args.role)
        if args.enabled is not None:
            accounts.set_enabled(args.username, args.enabled)
    except AccountError as err:
        raise _fail(err) from err
    found = next(a for a in accounts.list() if a.username == args.username)
    return {
        "username": found.username,
        "role": found.role,
        "enabled": found.enabled,
        "status": found.status,
    }


class Username(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,31}$")


def reset_password(ctx: Context, args: Username) -> dict[str, Any]:
    accounts = _accounts(ctx)
    try:
        current = current_role(accounts, args.username)
        if current is None:
            raise AccountError(f"no account {args.username}")
        check_may_manage(ctx.caller.role, ctx.caller.user, args.username, None, current)
        password = accounts.reset_password(args.username)
    except AccountError as err:
        raise _fail(err) from err
    return {
        "username": args.username,
        "temporary_password": password,
        "note": "Shown once. They must choose a new password when they next sign in.",
    }


def _alerts(ctx: Context):
    if ctx.alerts is None:
        raise ToolError("alert addresses are not available here")
    return ctx.alerts


class AlertList(BaseModel):
    team: str | None = Field(None, description="Only this group, such as desk or gate_control")


def alert_addresses(ctx: Context, args: AlertList) -> dict[str, Any]:
    return {
        "addresses": [a for a in _alerts(ctx).list() if args.team in (None, a["team"])],
        "teams": [{"team": k, "title": v} for k, v in ALERT_GROUPS.items()],
    }


class AlertAddress(BaseModel):
    address: str = Field(min_length=5, max_length=254)
    team: str = Field(description="desk, supervisor, or a team such as gate_control")


def add_alert_address(ctx: Context, args: AlertAddress) -> dict[str, Any]:
    try:
        return _alerts(ctx).add(args.address, args.team, ctx.caller.user)
    except AlertError as err:
        raise _fail(err) from err


def remove_alert_address(ctx: Context, args: AlertAddress) -> dict[str, Any]:
    try:
        _alerts(ctx).remove(args.address, args.team)
    except AlertError as err:
        raise _fail(err) from err
    return {"removed": True, "address": args.address, "team": args.team}


class UploadRoster(BaseModel):
    content: dict[str, Any] = Field(
        description="Collections in the demo data's shapes: crew, certificates, standby, trips"
    )
    dry_run: bool = True


def upload_roster(ctx: Context, args: UploadRoster) -> dict[str, Any]:
    return upload(ctx.store, args.content, ctx.caller.user, args.dry_run, ctx.now)


DESK_ROLES = READERS
MORE_TOOLS = (
    Tool(
        "report_disruption",
        "File a disruption report: one or several crew out (sick, fatigue...), planned leave, a "
        "certificate lapse, a flight delay, an aircraft fault, a station closed, gates closed or "
        "a gate change. It goes to the least busy desk, the right teams are told, and the "
        "engine's analysis comes back with it.",
        FileReport,
        report_disruption,
        OPERATIONAL_FILERS | ADMINS | {Role.CREW},
        writes=True,
    ),
    Tool(
        "disruptions",
        "Disruption reports by state and desk, the busiest first, with counts per state.",
        Disruptions,
        disruptions,
        DESK_ROLES,
    ),
    Tool(
        "my_reports",
        "The disruption reports the caller filed, or for crew that are about them, and where "
        "each one stands.",
        MyReports,
        my_reports,
        EVERYONE,
    ),
    Tool(
        "show_disruption",
        "One disruption report: its desk, severity, the engine's analysis (seats to fill and "
        "the cheapest way to fill them together, or the delays), teams to tell, plans, "
        "callouts and history.",
        ReportId,
        show_disruption,
        EVERYONE,
    ),
    Tool(
        "submit_report_plan",
        "Submit the cheapest joint cover for a report as plans, one per seat, for an approver. "
        "Only the report's desk controller.",
        SubmitReportPlan,
        submit_report_plan,
        frozenset({Role.CONTROLLER}),
        writes=True,
    ),
    Tool(
        "close_disruption",
        "Mark a report resolved, saying how; an approver then confirms it or returns it. For an "
        "aircraft fault give the chosen option's place, for a gate closure the gate per flight: "
        "the flights change only when the approver confirms.",
        CloseReport,
        close_disruption,
        frozenset({Role.CONTROLLER}),
        writes=True,
    ),
    Tool(
        "confirm_resolution",
        "Confirm a controller's resolution and close the report, making any flight change it "
        "carries (approvers).",
        ReportId,
        confirm_resolution,
        APPROVE_ROLES,
        writes=True,
    ),
    Tool(
        "return_resolution",
        "Return a resolved report to its desk with what is still missing (approvers).",
        ReturnReport,
        return_resolution,
        APPROVE_ROLES,
        writes=True,
    ),
    Tool(
        "reassign_disruption",
        "Move a report to another desk.",
        Reassign,
        reassign_disruption,
        APPROVE_ROLES | ADMINS,
        writes=True,
    ),
    Tool(
        "revoke_report",
        "Revoke a report filed in error; any absences it recorded are withdrawn (admins).",
        Revoke,
        revoke_report,
        ADMINS,
        writes=True,
    ),
    Tool(
        "desk_load",
        "Each controller desk's disruptions by state, and how new ones are assigned.",
        DeskLoad,
        desk_load,
        DESK_ROLES,
    ),
    Tool(
        "crew_risk",
        "Crew most at risk of dropping out of the roster, with what drives each score "
        "(fatigue, hours near a limit, absences, certificates, distance). Never ranks cover.",
        CrewRisk,
        crew_risk_tool,
        DESK_ROLES | {Role.CREW},
    ),
    Tool(
        "contacts",
        "The operational teams (gate control, station duty managers, maintenance control...), "
        "their jobs and who is on duty, per station or for the network.",
        Contacts,
        contacts_tool,
        OPS,
    ),
    Tool(
        "my_queue",
        "What is waiting for the caller now: offers, plans to approve, disruptions to work, "
        "resolutions to confirm.",
        MyQueue,
        my_queue,
        frozenset(Role),
    ),
    Tool(
        "team_accounts",
        "Every account that can sign in, with its role and status (admins).",
        TeamAccounts,
        team_accounts,
        ADMINS,
    ),
    Tool(
        "create_account",
        "Add an account with a role; returns a temporary password, shown once. Only a super "
        "admin creates admins.",
        CreateAccount,
        create_account,
        ADMINS,
        writes=True,
    ),
    Tool(
        "set_account",
        "Change an account's role or switch it on or off. Only a super admin changes admins.",
        SetAccount,
        set_account,
        ADMINS,
        writes=True,
    ),
    Tool(
        "reset_password",
        "Reset someone's password to a temporary one they must change at sign-in (admins).",
        Username,
        reset_password,
        ADMINS,
        writes=True,
    ),
    Tool(
        "alert_addresses",
        "Email addresses subscribed to alerts, per group: the desk, the supervisor and each "
        "team (admins).",
        AlertList,
        alert_addresses,
        ADMINS,
    ),
    Tool(
        "add_alert_address",
        "Subscribe an email address to one group's alerts (admins). The owner confirms by email.",
        AlertAddress,
        add_alert_address,
        ADMINS,
        writes=True,
    ),
    Tool(
        "remove_alert_address",
        "Stop an email address hearing one group's alerts (admins).",
        AlertAddress,
        remove_alert_address,
        ADMINS,
        writes=True,
    ),
    Tool(
        "upload_roster",
        "Upload roster records (crew, certificates, standby, trips). Checks the whole merged "
        "roster first and lists every rule problem it would add; dry_run to only check.",
        UploadRoster,
        upload_roster,
        ADMINS,
        writes=True,
    ),
)

ALL_TOOLS = (
    TOOLS
    + MORE_TOOLS
    + PAY_TOOLS
    + (
        Tool(
            "operations",
            "A week of flights for a station: passengers and disruptions per day, each flight of "
            "one day with who flies it, and who is out (when, never why).",
            OperationsWeek,
            operations_week,
            OPS,
        ),
        Tool(
            "catalog",
            "Every trip, flight, aircraft, station and gate from today on, to pick from a list.",
            Catalog,
            catalog,
            OPS,
        ),
        Tool(
            "about_me",
            "Who the caller is: name, role, desk or crew id, what their role does and may not do, "
            "and what waits for them now. For 'who am I', 'what is my role', 'what should I do'.",
            AboutMe,
            about_me,
            frozenset(Role),
        ),
    )
)
