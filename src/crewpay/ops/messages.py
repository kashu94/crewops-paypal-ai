"""Words for people outside crew control, written by the writer model from facts the engine holds.

* **Passenger messages.** For a delay, an aircraft fault, a station closure or a gate change or
  closure: the SMS and the app notice customer care sends, in English, Hindi or Telugu.
* **The shift handover.** What each desk did this shift and what the next shift inherits.

Both follow the shift brief's rule: the model writes only from the facts given, the evidence
check makes sure every flight number, time and figure in the text came from those facts, and
a text that fails the check is replaced by one built from the facts by a template. Nothing is
sent anywhere: a person copies the message, or reads the handover.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from crewpay.agent.advisor import Step
from crewpay.agent.verify import EvidenceVerifier
from crewpay.engine.clock import IST
from crewpay.engine.operation import Operation
from crewpay.models.client import ModelClient, ModelError

LANGUAGES = ("English", "Hindi", "Telugu")
SMS_LIMIT = 300


# -- passenger messages -----------------------------------------------------------------------


class PassengerText(BaseModel):
    sms: str
    """At most 300 characters: flight, what changed, the new time or gate, an apology."""
    app_title: str
    app_body: str


def _hhmm(when: str) -> str:
    """``2026-11-05 07:15 IST`` -> ``07:15``."""
    return when[11:16] if len(when) >= 16 else when


def passenger_facts(op: Operation, report: dict[str, Any]) -> dict[str, Any]:
    """What passengers need to know about a report, and nothing they should not: no crew
    names, no costs, no internal ids."""
    kind = report["kind"]
    a = report.get("analysis") or {}
    d = report.get("details") or {}
    flights: list[dict[str, Any]] = []
    cause = {
        "flight_delay": "an operational delay",
        "aircraft_technical": "a technical check on the aircraft",
        "station_disruption": "weather or airport conditions",
        "gate_closure": "a gate closure at the airport",
        "gate_change": "a change of gate",
    }.get(kind, "an operational reason")

    if kind == "flight_delay":
        trip = op.trip(d["trip_id"])
        day = date.fromisoformat(str(a.get("day") or trip.days[0].day))
        slip = timedelta(hours=float(d["hours"]))
        for td in trip.days:
            if td.day != day:
                continue
            for leg_id in td.legs:
                leg = op.legs[leg_id]
                flights.append(
                    {
                        "flight": leg.flight_no,
                        "route": f"{leg.origin} to {leg.destination}",
                        "date": leg.off_block.astimezone(IST).strftime("%d %b"),
                        "was": leg.off_block.astimezone(IST).strftime("%H:%M"),
                        "now": (leg.off_block + slip).astimezone(IST).strftime("%H:%M"),
                    }
                )
    elif kind in ("aircraft_technical", "station_disruption"):
        for f in a.get("flights_held", []):
            flights.append(
                {
                    "flight": f["flight"],
                    "route": f["route"].replace("-", " to "),
                    "date": datetime.fromisoformat(f["planned"][:16]).strftime("%d %b"),
                    "was": _hhmm(f["planned"]),
                    "now": _hhmm(f["leaves"]),
                }
            )
    elif kind == "gate_closure":
        for m in a.get("moves", []):
            flights.append(
                {
                    "flight": m["flight"],
                    "date": datetime.fromisoformat(m["departs"][:16]).strftime("%d %b"),
                    "departs": _hhmm(m["departs"]),
                    "old_gate": m["from_gate"],
                    "new_gate": m["to_gate"],
                }
            )
    elif kind == "gate_change":
        leg = op.legs.get(a.get("leg_id", ""))
        flights.append(
            {
                "flight": a.get("flight"),
                "station": a.get("station"),
                "old_gate": a.get("current_gate"),
                "new_gate": a.get("wanted_gate"),
                **(
                    {"departs": leg.off_block.astimezone(IST).strftime("%H:%M")}
                    if leg is not None
                    else {}
                ),
            }
        )
    return {"what_happened": cause, "flights": flights}


def _passenger_template(f: dict[str, Any]) -> PassengerText:
    parts = []
    for x in f["flights"][:3]:
        if "new_gate" in x:
            parts.append(f"{x['flight']} now boards at gate {x['new_gate']}")
        else:
            parts.append(f"{x['flight']} {x.get('route', '')} now departs {x['now']}".strip())
    body = "; ".join(parts) or "Your flight is affected"
    sms = f"Update: {body}, due to {f['what_happened']}. We are sorry for the inconvenience."
    return PassengerText(
        sms=sms[:SMS_LIMIT],
        app_title="Your flight has changed",
        app_body=f"{body}. This is due to {f['what_happened']}. We are sorry for the wait and "
        "thank you for your patience.",
    )


def write_passenger_message(
    f: dict[str, Any], writer: ModelClient | None, language: str = "English"
) -> dict[str, Any]:
    if not f["flights"]:
        raise ValueError("this report changes no passenger's flight")
    language = language if language in LANGUAGES else "English"
    if writer is not None:
        try:
            text, _ = writer.parse(
                [
                    {
                        "role": "system",
                        "content": "Write the message an airline's customer care sends to booked "
                        f"passengers, in {language}. sms: at most {SMS_LIMIT} characters, plain, "
                        "polite, one apology, the flight number and what changed. app_title: at "
                        "most 8 words. app_body: two or three short sentences. Use only the facts "
                        "given; copy flight numbers, dates, times and gates exactly, in digits; "
                        "never guess a new time; never mention crew, costs or internal reasons.",
                    },
                    {"role": "user", "content": str(f)},
                ],
                PassengerText,
                purpose="passenger message",
                effort="low",
                max_tokens=2000,
            )
            said = " ".join([text.sms, text.app_title, text.app_body])
            problems = EvidenceVerifier().problems(said, "", [Step("facts", {}, f)])
            if not problems and len(text.sms) <= SMS_LIMIT + 40:
                return {**text.model_dump(), "written_by": "model", "verified": True}
        except ModelError:
            pass
    return {**_passenger_template(f).model_dump(), "written_by": "template", "verified": True}


# -- the shift handover ------------------------------------------------------------------------


class HandoverText(BaseModel):
    headline: str
    desks: list[str]
    """One line per desk: what it closed and what it still holds."""
    next_shift: list[str]
    """The first things the next shift must do, most urgent first."""


def handover_facts(
    reports: list[dict[str, Any]], plans: list[dict[str, Any]], callouts: list[dict[str, Any]]
) -> dict[str, Any]:
    desks: dict[str, dict[str, Any]] = {}
    for r in reports:
        name = (r.get("desk") or {}).get("name") or "No desk"
        desk = desks.setdefault(name, {"open": [], "awaiting_sign_off": [], "closed": []})
        entry = {
            "report_id": r["report_id"],
            "title": r["title"],
            "severity": r["severity"]["level"],
        }
        if r["status"] in ("open", "in_progress"):
            desk["open"].append(entry)
        elif r["status"] == "resolved":
            desk["awaiting_sign_off"].append(entry)
        elif r["status"] == "closed":
            desk["closed"].append(entry)
    return {
        "desks": desks,
        "plans_waiting_for_an_approver": [
            {"plan_id": p["plan_id"], "trip_id": p["trip_id"], "seat": p["seat"], "name": p["name"]}
            for p in plans
            if p["status"] == "submitted"
        ],
        "callouts_running": [
            {"trip_id": c["trip_id"], "seat": c["seat"], "status": c["status"]}
            for c in callouts
            if c["status"] in ("offered", "accepted", "needs_decision", "awaiting_approval")
        ],
    }


def _handover_template(f: dict[str, Any]) -> HandoverText:
    lines = []
    for name, d in sorted(f["desks"].items()):
        lines.append(
            f"{name}: {len(d['closed'])} closed, {len(d['awaiting_sign_off'])} awaiting sign-off, "
            f"{len(d['open'])} still open"
            + (f" ({', '.join(x['report_id'] for x in d['open'][:3])})" if d["open"] else "")
            + "."
        )
    todo = [
        f"Decide {c['seat']} on {c['trip_id']}: {c['status'].replace('_', ' ')}."
        for c in f["callouts_running"]
        if c["status"] in ("needs_decision", "awaiting_approval")
    ]
    todo += [
        f"Approve or reject {p['plan_id']} ({p['name']} for {p['seat']} on {p['trip_id']})."
        for p in f["plans_waiting_for_an_approver"]
    ]
    open_total = sum(len(d["open"]) for d in f["desks"].values())
    return HandoverText(
        headline=f"{open_total} disruption{'s' if open_total != 1 else ''} carried over",
        desks=lines or ["No disruptions this shift."],
        next_shift=todo[:5] or ["Nothing waiting for a decision."],
    )


def write_handover(f: dict[str, Any], writer: ModelClient | None) -> dict[str, Any]:
    if writer is not None:
        try:
            text, _ = writer.parse(
                [
                    {
                        "role": "system",
                        "content": "Write the crew-control shift handover for the next shift. "
                        "headline: at most 8 words. desks: one line per desk, at most 25 words: "
                        "what it closed and what it still holds, naming report ids. next_shift: "
                        "at most 5 lines, the most urgent first, each an action. Use only the "
                        "facts given; copy ids, names and counts exactly; never calculate.",
                    },
                    {"role": "user", "content": str(f)},
                ],
                HandoverText,
                purpose="handover",
                effort="low",
                max_tokens=3000,
            )
            said = " ".join([text.headline, *text.desks, *text.next_shift])
            if text.desks and not EvidenceVerifier().problems(said, "", [Step("facts", {}, f)]):
                return {**text.model_dump(), "written_by": "model", "verified": True}
        except ModelError:
            pass
    return {**_handover_template(f).model_dump(), "written_by": "template", "verified": True}


# -- an absence said aloud ---------------------------------------------------------------------


class AbsenceReading(BaseModel):
    understood: bool
    """False when the words do not say the person cannot fly."""
    first_day: str
    """YYYY-MM-DD, or empty if no day is said."""
    last_day: str
    """YYYY-MM-DD, or empty."""
    reason_kind: Literal["sick", "fatigue", "personal", "other"]
    since: str
    """HH:MM if a time they fell ill is said, otherwise empty."""
    note: str
    """What they said, in a few plain English words, for crew control."""


def read_absence(text: str, today: date, reader: ModelClient) -> dict[str, Any]:
    """The fields of an absence report from what someone said, in any of the languages the
    desk hears. Nothing is filed: the person sees the fields, corrects them and sends them."""
    form, _ = reader.parse(
        [
            {
                "role": "system",
                "content": "A crew member says, in English, Hindi, Telugu or a mix, that they "
                f"cannot fly. Today is {today:%A %d %B %Y} (India time); 'kal' after a "
                "statement of being ill means tomorrow. Fill the form: dates as YYYY-MM-DD, "
                "first_day today if no day is said, last_day equal to first_day if no end is "
                "said; reason_kind sick for illness, injury or fever, fatigue for tiredness; "
                "note: what they said, in at most 12 plain English words. If they do not say "
                "they cannot fly, set understood to false.",
            },
            {"role": "user", "content": text},
        ],
        AbsenceReading,
        purpose="absence",
        effort="minimal",
        max_tokens=1500,
    )
    out: dict[str, Any] = {"heard": text, "understood": form.understood, "note": form.note}
    if not form.understood:
        return out
    try:
        first = date.fromisoformat(form.first_day) if form.first_day else today
        last = date.fromisoformat(form.last_day) if form.last_day else first
    except ValueError:
        first = last = today
    if first < today - timedelta(days=1) or first > today + timedelta(days=30):
        first = today
    last = min(max(last, first), first + timedelta(days=30))
    since = form.since if len(form.since) == 5 and form.since[2] == ":" else ""
    return {
        **out,
        "first_day": first.isoformat(),
        "last_day": last.isoformat(),
        "reason_kind": form.reason_kind,
        **({"since": since} if since else {}),
    }


# -- several photos, one description ------------------------------------------------------------


class PhotoSummary(BaseModel):
    description: str
    """At most three short sentences."""


def _sentences(text: str) -> list[str]:
    return [p.strip(" .") + "." for p in text.replace("\n", " ").split(". ") if p.strip(" .")]


def _fit(text: str, limit: int = 500) -> str:
    """Whole sentences up to the description box's limit, never one cut off part way."""
    out = ""
    for sentence in _sentences(text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit]


def summarise_photos(subject: str, photos: list[str], writer: ModelClient | None) -> str:
    """One description for a report from what each of its photos shows, in at most three
    sentences. The model writes only from the photos' own descriptions, and the evidence check
    makes sure every registration, gate and figure in it came from them."""
    seen = [p.strip() for p in photos if p.strip()]
    if not seen:
        return ""
    if len(seen) == 1:
        return _fit(" ".join(_sentences(seen[0])[:3]))
    facts = {"report_about": subject, "photos": seen}
    if writer is not None:
        try:
            text, _ = writer.parse(
                [
                    {
                        "role": "system",
                        "content": "A station duty manager is filing one disruption report with "
                        "several photos. Write its description: at most three short, plain "
                        "sentences and 60 words in all, that together say what is wrong, "
                        "without repeating yourself or describing the background. Use only "
                        "what the photo descriptions say; copy registrations "
                        "and gate names exactly; never guess a cause or a time.",
                    },
                    {"role": "user", "content": str(facts)},
                ],
                PhotoSummary,
                purpose="photo summary",
                effort="low",
                max_tokens=1500,
            )
            said = text.description.strip()
            if (
                said
                and len(_sentences(said)) <= 3
                and not EvidenceVerifier().problems(said, "", [Step("facts", {}, facts)])
            ):
                return _fit(said)
        except ModelError:
            pass
    out: list[str] = []
    for p in seen:
        first = _sentences(p)[:1]
        if first and first[0] not in out:
            out.append(first[0])
    return _fit(" ".join(out[:3]))
