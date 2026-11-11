"""Photos into the crew desk: sick notes, roster swaps, certificates, aircraft defects, and
disruption notices (a departures board, a gate screen, an ATC delay message), and conditions at
the airport (fog, flooding, a closed gate).

Every photo passes the same gates, and the code decides what happens, not the model:

1. **Picture quality**, measured from the pixels: blur and darkness ask for a reshoot before
   any model is called.
2. **What is in it**: The vision model reads the photo into a fixed form for its kind. The form has
   no free text the rest of the system acts on.
3. **The decision**, in code:

   ======================  ==============================================================
   ``rejected``            not the kind of thing expected (a selfie as a sick note)
   ``reshoot``             unreadable or unclear, with the reason
   ``confirm``             the person or aircraft it names does not match, or is ambiguous
   ``draft``               a proposal a person must confirm; nothing is changed by a photo
   ======================  ==============================================================

For a defect, a segmentation model, where one is configured, locates the damage named by the
form, and the confidence level is built from independent signals (picture quality, the model's
view of the photo, whether segmentation found the damage, whether the tail number matches)
rather than the model's own score.
"""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Protocol

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
from pydantic import BaseModel

from crewpay.engine.clock import IST
from crewpay.engine.gates import check_gate
from crewpay.engine.operation import NotFound, Operation
from crewpay.models.media import Box

MAX_BYTES = 8 * 1024 * 1024
MIN_SHARPNESS = 200.0
"""Variance of the edge image; sharp phone photos measure in the thousands, blur under 100."""
MIN_BRIGHTNESS, MAX_BRIGHTNESS = 40.0, 235.0
MIN_SAM_CONFIDENCE = 0.4
MAX_BOX_SHARE = 0.6
"""A "damage" box covering most of the photo has found the panel, not the damage."""

Clarity = Literal["clear", "partial", "unclear"]


class Kind(StrEnum):
    SICK_NOTE = "sick_note"
    ROSTER_SWAP = "roster_swap"
    CERTIFICATE = "certificate"
    DEFECT = "defect"
    DISRUPTION = "disruption"
    SCENE = "scene"
    GATE = "gate"
    """A gate closed or blocked: a barrier, tape, a spillage, a broken jet bridge."""


class Status(StrEnum):
    REJECTED = "rejected"
    RESHOOT = "reshoot"
    CONFIRM = "confirm"
    DRAFT = "draft"


# -- what the model fills in -----------------------------------------------------------------


class Draft(BaseModel):
    """Two fields every form ends with: a first draft of the report, which the person filing it
    checks and changes before anything is filed."""

    severity: Literal["low", "medium", "high"] = "medium"
    """How much what the photo shows would hold flights back or matter for safety."""
    description: str = ""
    """One or two plain sentences on what the photo shows, as a duty manager would write it."""


class SickNote(Draft):
    is_medical_note: bool
    readable: Clarity
    patient_name: str
    unfit_from: str
    """The first day the person is unfit for duty: YYYY-MM-DD, or empty if not written."""
    unfit_until: str
    """The last day the person is unfit for duty: YYYY-MM-DD, or empty if not written."""
    issued_by: str
    """The doctor or clinic who signed the note."""


class RosterSwap(Draft):
    is_roster_swap: bool
    readable: Clarity
    crew_names: list[str]
    trip_ids: list[str]
    signed_by_both: bool


class CertificateForm(Draft):
    is_certificate: bool
    readable: Clarity
    holder_name: str
    kind: Literal["licence", "medical", "recurrent_training", "safety_equipment", "other"]
    expires: str
    """YYYY-MM-DD, or empty if not visible."""


class Damage(BaseModel):
    part: str
    kind: Literal[
        "dent", "crack", "scratch", "hole", "tear", "bent", "leak", "burn", "missing", "other"
    ]
    where: str


class DefectForm(Draft):
    shows_aircraft: bool
    """An aircraft or a recognisable part of one, inside or out."""
    subject: str
    """What the photo shows, in a few words."""
    area: Literal["exterior", "engine", "cabin", "cockpit", "cargo", "unknown"]
    view: Clarity
    view_problems: list[str]
    tail_number: str
    """The registration if it can be read in the photo, such as VT-SYA; otherwise empty."""
    damage: list[Damage]
    damage_phrase: str
    """Two or three words naming the damage for locating it, such as "dent" or "cracked panel"."""


class FlightStatus(BaseModel):
    flight_no: str
    """Such as SY221, exactly as shown."""
    status: Literal["delayed", "cancelled", "gate_change", "on_time", "other"]
    new_time: str
    """The new departure time as HH:MM if shown, otherwise empty."""
    delay_minutes: int
    """If the delay is stated in minutes or hours, in minutes; otherwise 0."""
    new_gate: str = ""
    """The gate it now leaves from, as shown, such as 23; otherwise empty."""


class DisruptionNotice(Draft):
    is_flight_notice: bool
    """A departures board, gate screen, delay message or printed notice about flights."""
    readable: Clarity
    flights: list[FlightStatus]


class SceneForm(Draft):
    shows_airport: bool
    """An airport, airfield, apron, runway, terminal or gate area."""
    subject: str
    """What the photo shows, in a few words."""
    condition: Literal[
        "fog",
        "heavy_rain",
        "flooding",
        "snow",
        "storm",
        "closed_gate",
        "barrier",
        "closed_terminal",
        "spillage",
        "none",
        "other",
    ]
    view: Clarity
    detail: str
    """One short sentence on what is visible, such as "apron under standing water"."""
    gate_sign: str = ""
    """The gate name on a sign in the photo, such as G3 or E_G1, if readable; otherwise empty."""


SCENE_WORDS = {
    "fog": "fog",
    "heavy_rain": "heavy rain",
    "flooding": "flooding",
    "snow": "snow",
    "storm": "a storm",
    "closed_gate": "a closed gate",
    "barrier": "a barrier across the way",
    "closed_terminal": "a closed terminal",
    "spillage": "a spillage",
    "other": "a disruption",
}

GATE_CONDITIONS = frozenset(
    {"closed_gate", "barrier", "closed_terminal", "spillage", "flooding", "other"}
)
"""What closes a gate. Fog, rain, snow or a storm hold the whole station, not one gate."""


PROMPTS: dict[Kind, tuple[type[BaseModel], str]] = {
    Kind.SICK_NOTE: (
        SickNote,
        "This should be a doctor's note that a crew member is unfit to fly. Fill the form only "
        "from what is written; leave a field empty if it is not there. Dates as YYYY-MM-DD; a "
        "date written with slashes is day first, as in India (05/11/2026 is 5 November).",
    ),
    Kind.ROSTER_SWAP: (
        RosterSwap,
        "This should be a handwritten request by two crew members to swap trips. Fill the form "
        "only from what is written. Trip ids look like T-1018.",
    ),
    Kind.CERTIFICATE: (
        CertificateForm,
        "This should be an aviation certificate: pilot licence, medical, recurrent training or "
        "safety equipment. Fill the form only from what is printed. Dates as YYYY-MM-DD.",
    ),
    Kind.DEFECT: (
        DefectForm,
        "This should be a photo of possible damage on an aircraft, inside or out. Describe only "
        "what is visible. If you cannot see damage, leave damage empty: do not guess. "
        "tail_number: copy any aircraft registration written in the photo exactly, such as "
        "VT-KFB, whether painted on the fuselage or tail or on a placard; leave it empty only "
        "when no registration can be read.",
    ),
    Kind.DISRUPTION: (
        DisruptionNotice,
        "This should show flight disruptions: a departures board, a gate screen, an ATC delay "
        "message or a printed notice. List only flights shown with a status; copy flight "
        "numbers and times exactly. Times are local (IST).",
    ),
    Kind.SCENE: (
        SceneForm,
        "This should show conditions at an airport that hold flights back: fog, heavy rain, "
        "flooding, snow, a storm, or a gate, jet bridge or terminal that is closed or blocked. "
        "A no-entry or closed sign, tape, a chain or a barrier across a gate counts as closed "
        "or blocked. view is about the photo, not the weather: a sharp photo of thick fog is "
        "clear. Describe only what is visible. If nothing is wrong in the photo, say none.",
    ),
    Kind.GATE: (
        SceneForm,
        "This should show a gate at an airport that is closed or cannot be used: a no-entry or "
        "closed sign, tape, a chain or a barrier across it, a jet bridge out of use, or a "
        "spillage (fuel, oil, water or anything else on the floor or apron). shows_airport is "
        "true for any terminal, gate, jet bridge or apron. gate_sign: copy the gate name on a "
        "sign exactly, such as G3 or E_G1, only if it is readable; otherwise leave it empty. "
        "view is about the photo: is it sharp and well lit enough to see what is wrong. "
        "Describe only what is visible. If nothing is wrong in the photo, say none.",
    ),
}


DRAFT_NOTE = (
    " Last, set severity (high, medium or low: how much what the photo shows would hold flights "
    "back or matter for safety) and description (one or two plain sentences on what the photo "
    "shows, as a station duty manager would write it in a report; no guesses beyond the photo)."
)


class Reader(Protocol):
    """Fills a form from a photo: the vision model in production, a fake in tests."""

    def read(self, image: bytes, mime: str, form: type[BaseModel], instructions: str) -> Any: ...


class Locator(Protocol):
    def segment(self, image: bytes, mime: str, phrase: str) -> list[Box]: ...


class ModelReader:
    def __init__(self, client: Any) -> None:
        self.client = client

    def read(self, image: bytes, mime: str, form: type[BaseModel], instructions: str) -> Any:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        parsed, _ = self.client.parse(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instructions},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            form,
            purpose="intake",
            effort="low",
            max_tokens=4000,
        )
        return parsed


# -- results ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quality:
    width: int
    height: int
    sharpness: float
    brightness: float
    problems: tuple[str, ...]


@dataclass
class Intake:
    kind: Kind
    status: Status
    message: str
    quality: Quality | None = None
    form: dict[str, Any] = field(default_factory=dict)
    confidence: str | None = None
    """``high``, ``medium`` or ``low``, for defects."""
    signals: dict[str, Any] = field(default_factory=dict)
    box: Box | None = None
    crew: list[dict[str, str]] = field(default_factory=list)
    proposal: dict[str, Any] | None = None
    """A tool call a person can make with one click; never made by the photo itself."""
    suggestion: dict[str, str] | None = None
    """A first draft of the report's severity and description, for the person to check."""


PAPER = frozenset({Kind.SICK_NOTE, Kind.CERTIFICATE, Kind.ROSTER_SWAP})
"""Documents: white paper is bright by nature, so brightness alone is never glare."""


SCREENS = frozenset({Kind.DISRUPTION})
"""Departures boards and gate screens: lit text on a dark ground, never too dark to read."""


def measure(image: bytes, paper: bool = False, screen: bool = False) -> Quality:
    try:
        picture = Image.open(io.BytesIO(image))
        picture.load()
    except (UnidentifiedImageError, OSError) as err:
        raise ValueError("not an image this service can read") from err
    width, height = picture.size
    grey = picture.convert("L")
    grey.thumbnail((800, 800))
    sharpness = ImageStat.Stat(grey.filter(ImageFilter.FIND_EDGES)).var[0]
    brightness = ImageStat.Stat(grey).mean[0]
    problems = []
    if brightness < MIN_BRIGHTNESS and not screen:
        problems.append("too dark: add light or use the torch")
    elif brightness > MAX_BRIGHTNESS and not paper:
        problems.append("washed out: step out of direct glare")
    if sharpness < MIN_SHARPNESS:
        problems.append("blurred: hold the phone still and let it focus")
    if min(width, height) < 300:
        problems.append("too small: send the original photo, not a thumbnail")
    return Quality(width, height, round(sharpness, 1), round(brightness, 1), tuple(problems))


TITLES = frozenset(
    {"capt", "captain", "fo", "first", "officer", "mr", "mrs", "ms", "miss", "dr", "shri", "smt"}
)
"""Words before a name on a note or a form ("Capt. Arjun Nair"), never part of the name."""


def _crew_named(op: Operation, name: str) -> list[dict[str, str]]:
    from crewpay.agent.tools import name_matches

    tokens = name.replace(".", ". ").split()
    while len(tokens) > 1 and tokens[0].lower().rstrip(".") in TITLES:
        tokens = tokens[1:]
    name = " ".join(tokens)
    words = [w for w in name.lower().replace(".", " ").split() if len(w) > 1]
    if not words:
        return []
    found = [c for c in op.crew.values() if name_matches(c.name, name)]
    if not found:
        # A note may give a first name in full ("Arjun Nair") where the roster has "A. Nair".
        initials = " ".join(w[0] for w in words[:-1])
        found = [c for c in op.crew.values() if name_matches(c.name, f"{initials} {words[-1]}")]
    return [
        {"crew_id": c.crew_id, "name": c.name, "rank": str(c.rank), "airline": c.airline}
        for c in sorted(found, key=lambda c: c.crew_id)
    ]


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _fleet(op: Operation) -> set[str]:
    return {leg.tail for leg in op.legs.values()}


def read_photo(
    op: Operation,
    kind: Kind,
    image: bytes,
    mime: str,
    reader: Reader,
    locator: Locator | None = None,
    tail: str | None = None,
) -> Intake:
    """Run a photo through the gates. ``tail`` is the aircraft the reporter chose."""
    if len(image) > MAX_BYTES:
        return Intake(kind, Status.REJECTED, "That file is too large: send a photo under 8 MB.")
    try:
        quality = measure(image, paper=kind in PAPER, screen=kind in SCREENS)
    except ValueError:
        return Intake(kind, Status.REJECTED, "That is not a photo this service can read.")
    if quality.problems:
        return Intake(
            kind,
            Status.RESHOOT,
            "Please take the photo again: " + "; ".join(quality.problems) + ".",
            quality,
        )

    form_type, instructions = PROMPTS[kind]
    form = reader.read(image, mime, form_type, instructions + DRAFT_NOTE)
    result = Intake(kind, Status.DRAFT, "", quality, form.model_dump())
    result.suggestion = {"severity": form.severity, "description": form.description.strip()}
    match kind:
        case Kind.SICK_NOTE:
            return _sick_note(op, form, result)
        case Kind.ROSTER_SWAP:
            return _roster_swap(op, form, result)
        case Kind.CERTIFICATE:
            return _certificate(op, form, result)
        case Kind.DISRUPTION:
            return _disruption(op, form, result)
        case Kind.SCENE | Kind.GATE:
            return _scene(form, result, image, mime, locator, gate=kind is Kind.GATE)
        case _:
            return _defect(op, form, result, image, mime, locator, tail)


def _unreadable(result: Intake, what: str) -> Intake:
    result.status = Status.RESHOOT
    result.message = f"The {what} cannot be read clearly. Please photograph it flat, in good light."
    return result


def _one_person(op: Operation, name: str, result: Intake, what: str) -> dict[str, str] | None:
    result.crew = _crew_named(op, name) if name else []
    if len(result.crew) == 1:
        return result.crew[0]
    result.status = Status.CONFIRM
    if not result.crew:
        result.message = (
            f"The {what} names {name or 'nobody'}, who is not in the roster. Who is it?"
        )
    else:
        result.message = (
            f"The {what} names {name}, which matches {len(result.crew)} people. Which one?"
        )
    return None


def _sick_note(op: Operation, form: SickNote, result: Intake) -> Intake:
    if not form.is_medical_note:
        result.status = Status.REJECTED
        result.message = "This does not look like a doctor's note."
        return result
    if form.readable == "unclear":
        return _unreadable(result, "note")
    person = _one_person(op, form.patient_name, result, "note")
    if person is None:
        return result
    first, last = _date(form.unfit_from), _date(form.unfit_until)
    if first is None:
        result.status = Status.CONFIRM
        result.message = f"Note for {person['name']}: the dates cannot be read. Which days?"
        return result
    last = last or first
    result.message = (
        f"Note for {person['name']} ({person['crew_id']}), unfit {first:%d %b} to {last:%d %b}. "
        "Confirm to record them as unavailable."
    )
    result.proposal = {
        "tool": "report_unavailable",
        "arguments": {
            "crew_id": person["crew_id"],
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "reason": "sick note" + (f" from {form.issued_by}" if form.issued_by else ""),
        },
    }
    return result


def _roster_swap(op: Operation, form: RosterSwap, result: Intake) -> Intake:
    if not form.is_roster_swap:
        result.status = Status.REJECTED
        result.message = "This does not look like a roster swap request."
        return result
    if form.readable == "unclear":
        return _unreadable(result, "request")
    people = [_crew_named(op, n) for n in form.crew_names]
    result.crew = [p[0] for p in people if len(p) == 1]
    if len(form.crew_names) != 2 or len(result.crew) != 2:
        result.status = Status.CONFIRM
        result.message = "The request should name exactly two crew members on the roster."
        return result
    missing = [t for t in form.trip_ids if t not in op.trips]
    if missing or not form.trip_ids:
        result.status = Status.CONFIRM
        result.message = "Check the trip ids: " + (", ".join(missing) or "none were written") + "."
        return result
    names = " and ".join(p["name"] for p in result.crew)
    signed = "" if form.signed_by_both else " It is not signed by both."
    result.message = (
        f"Swap request from {names} for {', '.join(form.trip_ids)}. Each swap is checked "
        f"against the rules before it is made.{signed}"
    )
    return result


def _certificate(op: Operation, form: CertificateForm, result: Intake) -> Intake:
    if not form.is_certificate:
        result.status = Status.REJECTED
        result.message = "This does not look like an aviation certificate."
        return result
    if form.readable == "unclear":
        return _unreadable(result, "certificate")
    person = _one_person(op, form.holder_name, result, "certificate")
    if person is None:
        return result
    expires = _date(form.expires)
    on_file = (
        dict(op.state(person["crew_id"]).certs).get(form.kind) if form.kind != "other" else None
    )
    result.message = (
        f"{form.kind.replace('_', ' ').capitalize()} for {person['name']}"
        + (f", expiring {expires:%d %b %Y}" if expires else ", expiry not readable")
        + (f"; on file: {on_file:%d %b %Y}" if on_file else "")
        + ". A person updates the record after checking the original."
    )
    return result


def _defect(
    op: Operation,
    form: DefectForm,
    result: Intake,
    image: bytes,
    mime: str,
    locator: Locator | None,
    tail: str | None,
) -> Intake:
    if not form.shows_aircraft:
        result.status = Status.REJECTED
        result.message = (
            f"This shows {form.subject or 'something else'}, not an aircraft. "
            "Please photograph the affected area."
        )
        return result
    if form.view == "unclear":
        result.status = Status.RESHOOT
        reasons = "; ".join(form.view_problems) or "the damage area is not clear"
        result.message = f"Please take the photo again: {reasons}. Get closer and hold steady."
        return result

    seen = form.tail_number.strip().upper()
    fleet = _fleet(op)
    result.signals = {"tail_seen": seen or None}
    tail_ok: bool | None = None
    if seen:
        tail_ok = seen == (tail or "").upper()
        if seen not in fleet or (tail and not tail_ok):
            result.status = Status.CONFIRM
            chosen = f", not {tail}" if tail else ""
            where = "" if seen in fleet else " That registration is not in our fleet."
            result.message = f"This looks like {seen}{chosen}.{where} Please confirm the aircraft."
            return result

    box = None
    if form.damage and locator is not None:
        tried = dict.fromkeys(p for p in (form.damage_phrase, "damage", form.damage[0].kind) if p)
        boxes = [b for phrase in tried for b in locator.segment(image, mime, phrase)]
        usable = [
            b for b in boxes if b.confidence >= MIN_SAM_CONFIDENCE and b.share <= MAX_BOX_SHARE
        ]
        box = max(usable, key=lambda b: b.confidence, default=None)
    result.box = box

    result.signals = {
        **result.signals,
        "picture_quality": "good",
        "model_view": form.view,
        "damage_described": bool(form.damage),
        "damage_located": box is not None,
        "located_confidence": round(box.confidence, 2) if box else None,
        "tail_matches": tail_ok,
    }
    if not form.damage:
        result.confidence = "low"
        if not seen:
            result.status = Status.REJECTED
            result.message = (
                "Neither damage nor the aircraft's registration can be seen in this photo, so it "
                "does not back up a fault report. That is not a clearance: inspect in person."
            )
            return result
        result.message = (
            f"Shows the registration {seen}; no damage in this photo. That is not a clearance: "
            "inspect in person."
        )
        return result
    if form.view == "clear" and box is not None and tail_ok is not False:
        result.confidence = "high"
    elif box is not None or form.view == "clear":
        result.confidence = "medium"
    else:
        result.confidence = "low"
    first = form.damage[0]
    on = tail or seen or "the aircraft"
    what = "damage" if first.kind == "other" else first.kind
    result.message = (
        f"Draft defect report for {on}: {what} on the {first.part} ({first.where}). "
        f"Confidence {result.confidence}. Needs engineer confirmation before anything changes."
    )
    if result.confidence == "low":
        result.message += (
            " The damage could not be located in the photo: a closer photo would help."
        )
    return result


SCENE_PHRASES = {
    "fog": "fog",
    "heavy_rain": "standing water",
    "flooding": "flood water",
    "snow": "snow",
    "storm": "storm clouds",
    "closed_gate": "closed gate",
    "barrier": "barrier",
    "closed_terminal": "closed door",
    "spillage": "spill on the floor",
    "other": "obstruction",
}
"""What segmentation is asked to outline for each condition."""
MAX_SCENE_SHARE = 0.95
"""Weather can fill most of a photo; only a box that is the whole frame says nothing."""


def _scene(
    form: SceneForm,
    result: Intake,
    image: bytes = b"",
    mime: str = "image/jpeg",
    locator: Locator | None = None,
    gate: bool = False,
) -> Intake:
    if not form.shows_airport:
        result.status = Status.REJECTED
        result.message = (
            f"This shows {form.subject or 'something else'}, not an airport. Please photograph "
            "the conditions, gate or area affected."
        )
        return result
    if form.view == "unclear":
        result.status = Status.RESHOOT
        result.message = "The photo does not show the conditions clearly. Please take another."
        return result
    sign = gate_name(form.gate_sign) if gate else ""
    result.signals = {"picture_quality": "good", "model_view": form.view}
    if gate:
        result.signals["gate_seen"] = sign or None
        if form.condition not in GATE_CONDITIONS and form.condition != "none":
            result.status = Status.REJECTED
            result.message = (
                f"This shows {SCENE_WORDS[form.condition]}, which holds the whole station, not "
                "one gate. Report it as a station disruption, or add a photo of the gate."
            )
            return result
        if form.condition == "none":
            result.confidence = "low"
            if not sign:
                result.status = Status.REJECTED
                result.message = (
                    "Nothing wrong with a gate and no gate sign can be seen in this photo, so it "
                    "does not back up a gate closure."
                )
            else:
                result.message = f"Shows the sign for gate {sign}; nothing visibly wrong in it."
            return result
    if form.condition == "none":
        result.confidence = "low"
        result.message = (
            "The photo shows the airport but nothing visibly wrong. It is kept with the report; "
            "the desk will confirm the closure another way."
        )
        return result
    box = None
    if locator is not None and image:
        boxes = locator.segment(image, mime, SCENE_PHRASES[form.condition])
        usable = [
            b for b in boxes if b.confidence >= MIN_SAM_CONFIDENCE and b.share <= MAX_SCENE_SHARE
        ]
        box = max(usable, key=lambda b: b.confidence, default=None)
    result.box = box
    result.signals["located"] = box is not None
    if box is not None:
        result.signals["located_confidence"] = round(box.confidence, 2)
        result.signals["label"] = SCENE_PHRASES[form.condition]
    if form.view == "clear" and box is not None:
        result.confidence = "high"
    elif form.view == "clear" or box is not None:
        result.confidence = "medium"
    else:
        result.confidence = "low"
    result.message = (
        f"Shows {SCENE_WORDS[form.condition]}: {form.detail.rstrip('.')}. "
        + (f"Gate {sign} on the sign. " if sign else "")
        + ("Marked in the photo. " if box is not None else "")
        + f"Confidence {result.confidence}."
    )
    return result


def gate_name(text: str) -> str:
    """A gate as written on a sign, in the form the operation uses: "Gate g-3" -> "G3",
    "E-G1" or "E G1" -> "E_G1"."""
    t = re.sub(r"^GATE\s*", "", text.strip().upper())
    t = re.sub(r"[^A-Z0-9]+", "_", t).strip("_")
    return re.sub(r"(?<=[A-Z])_(?=\d)", "", t)


def _next_leg(op: Operation, flight_no: str):
    legs = [
        leg
        for leg in op.legs.values()
        if leg.flight_no == flight_no.upper().replace(" ", "") and leg.off_block > op.now
    ]
    return min(legs, key=lambda leg: leg.off_block, default=None)


def _disruption(op: Operation, form: DisruptionNotice, result: Intake) -> Intake:
    if not form.is_flight_notice:
        result.status = Status.REJECTED
        result.message = "This does not look like a flight board or disruption notice."
        return result
    if form.readable == "unclear":
        return _unreadable(result, "notice")
    trip_of = {leg_id: t.trip_id for t in op.trips.values() for leg_id in t.leg_ids}
    found, unknown, checks = [], [], []
    for f in form.flights:
        leg = _next_leg(op, f.flight_no)
        if leg is None:
            unknown.append(f.flight_no)
            continue
        trip_id = trip_of.get(leg.leg_id)
        minutes = f.delay_minutes
        if not minutes and f.new_time:
            try:
                hh, mm = (int(x) for x in f.new_time.split(":"))
                scheduled = leg.off_block.astimezone(IST)
                minutes = (hh * 60 + mm) - (scheduled.hour * 60 + scheduled.minute)
                minutes += 24 * 60 if minutes < -12 * 60 else 0
            except ValueError:
                minutes = 0
        entry = {
            "flight_no": leg.flight_no,
            "status": f.status,
            "trip_id": trip_id,
            "scheduled": leg.off_block.astimezone(IST).strftime("%d %b %H:%M IST"),
        }
        gate = f.new_gate.strip().upper().removeprefix("GATE").strip()
        if f.status == "gate_change" and gate and leg.gate:
            try:
                check = check_gate(op, leg.leg_id, gate)
            except NotFound:
                entry["gate"] = f"{gate} is not one of our gates at {leg.origin}"
            else:
                entry["gate"] = (
                    f"{leg.gate} to {gate}: free"
                    if check.free
                    else f"{leg.gate} to {gate}: held by {', '.join(check.clashes)}"
                    + (
                        f"; free instead: {', '.join(check.alternatives)}"
                        if check.alternatives
                        else ""
                    )
                )
                checks.append(
                    {"tool": "gate_change", "arguments": {"leg_id": leg.leg_id, "gate": gate}}
                )
        if f.status == "delayed" and minutes > 0 and trip_id:
            entry["delay_minutes"] = minutes
            checks.append(
                {
                    "tool": "delay_impact",
                    "arguments": {
                        "trip_id": trip_id,
                        "hours": round(minutes / 60, 2),
                        "day": leg.day.isoformat(),
                    },
                }
            )
        found.append(entry)
    result.form["matched"] = found
    if not found:
        result.status = Status.CONFIRM
        result.message = (
            "None of the flights shown are ours in the coming days: "
            + (", ".join(unknown) or "no flights could be read")
            + "."
        )
        return result
    delayed = [f for f in found if "delay_minutes" in f]
    parts = [f"{f['flight_no']} ({f['trip_id']}) delayed {f['delay_minutes']} min" for f in delayed]
    parts += [
        f"{f['flight_no']} cancelled: the desk decides" for f in found if f["status"] == "cancelled"
    ]
    parts += [
        f"{f['flight_no']} gate change" + (f" ({f['gate']})" if "gate" in f else ": no crew impact")
        for f in found
        if f["status"] == "gate_change"
    ]
    result.message = ("; ".join(parts) or "No delays or cancellations shown") + "."
    if unknown:
        result.message += f" Not ours: {', '.join(unknown)}."
    if checks:
        result.message += " Check each change before acting."
        result.proposal = checks[0] if len(checks) == 1 else {"tool": "several", "calls": checks}
    return result
