"""The shift brief on the ops board: what the desk needs to know, in three lines.

The writer model writes the brief from the facts the watch and the callout desk hold, and the same
evidence check every answer gets makes sure each id and figure in it came from those facts.
A brief that fails the check is replaced by one built from the facts by a template, so the
board never shows a figure nobody computed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from crewpay.agent.advisor import Step
from crewpay.agent.verify import EvidenceVerifier
from crewpay.engine.clock import IST
from crewpay.engine.operation import Operation
from crewpay.engine.watch import Issue, scan
from crewpay.models.client import ModelClient, ModelError
from crewpay.ops.callout import Callout, Status

Mood = Literal["calm", "busy", "critical"]


class BriefText(BaseModel):
    headline: str
    lines: list[str]


@dataclass
class Brief:
    mood: Mood
    headline: str
    lines: list[str]
    written_by: str
    """``model`` or ``template``."""
    verified: bool
    facts: dict[str, Any] = field(default_factory=dict)
    image_key: str | None = None


def facts(op: Operation, callouts: list[Callout]) -> dict[str, Any]:
    issues = scan(op)
    open_callouts = [c for c in callouts if c.status not in (Status.FILLED, Status.CLOSED)]

    def issue(i: Issue) -> dict[str, Any]:
        out = {"kind": i.kind, "crew_id": i.crew_id, "name": i.name, "detail": i.detail}
        if i.trip_id:
            out["trip_id"] = i.trip_id
        if i.suggestion:
            out["suggested_cover"] = i.suggestion
            out["cost_inr"] = i.suggestion_cost
        return out

    return {
        "clock": op.now.astimezone(IST).strftime("%d %b %H:%M IST"),
        "illegal": [issue(i) for i in issues if i.kind == "illegal"],
        "fatigue": [issue(i) for i in issues if i.kind == "fatigue"],
        "certificates": [issue(i) for i in issues if i.kind == "certificate"],
        "callouts_waiting_on_crew": [
            {"trip_id": c.trip_id, "seat": str(c.rank), "asking": c.current.crew_id}
            for c in open_callouts
            if c.current is not None
        ],
        "callouts_needing_a_decision": [
            {"trip_id": c.trip_id, "seat": str(c.rank)}
            for c in open_callouts
            if c.status in (Status.NEEDS_DECISION, Status.AWAITING_APPROVAL)
        ],
    }


def mood_of(f: dict[str, Any]) -> Mood:
    if f["illegal"] or f["callouts_needing_a_decision"]:
        return "critical"
    if f["fatigue"] or f["callouts_waiting_on_crew"]:
        return "busy"
    return "calm"


def template(f: dict[str, Any]) -> list[str]:
    lines = []
    for i in f["illegal"][:2]:
        cover = f", cover {i['suggested_cover']}" if i.get("suggested_cover") else ""
        lines.append(f"{i['trip_id']}: {i['name']} ({i['crew_id']}) {i['detail']}{cover}.")
    if f["callouts_needing_a_decision"]:
        trips = ", ".join(c["trip_id"] for c in f["callouts_needing_a_decision"])
        lines.append(f"Decision needed: nobody accepted the callout for {trips}.")
    if f["fatigue"]:
        lines.append(f"{len(f['fatigue'])} crew at high fatigue in the next 7 days.")
    if f["certificates"]:
        n = len(f["certificates"])
        lines.append(
            f"{n} {'certificate runs' if n == 1 else 'certificates run'} out in the next 14 days."
        )
    return lines[:3] or ["No open problems in the next 7 days."]


def write_brief(op: Operation, callouts: list[Callout], writer: ModelClient | None) -> Brief:
    f = facts(op, callouts)
    mood = mood_of(f)
    headline = {"calm": "All quiet", "busy": "Busy shift", "critical": "Action needed"}[mood]
    if writer is not None:
        try:
            text, _ = writer.parse(
                [
                    {
                        "role": "system",
                        "content": "Write the crew-desk shift brief for the ops board: a headline "
                        "of at most 6 words and at most 3 lines of at most 18 words each, most "
                        "urgent first. Use only the facts given; copy ids, names and figures "
                        "exactly; never calculate. Write plain sentences: amounts as INR 31160, "
                        "never field names such as cost_inr or suggested_cover.",
                    },
                    {"role": "user", "content": str(f)},
                ],
                BriefText,
                purpose="brief",
                effort="low",
                max_tokens=3000,
            )
            evidence = [Step("facts", {}, f)]
            said = " ".join([text.headline, *text.lines])
            if text.lines and not EvidenceVerifier().problems(said, "", evidence):
                return Brief(mood, text.headline, text.lines[:3], "model", True, f)
        except ModelError:
            pass
    return Brief(mood, headline, template(f), "template", True, f)


# -- pictures --------------------------------------------------------------------------------


class Images(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, data: bytes) -> None: ...


class MemoryImages:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._images: dict[str, bytes] = {}

    def get(self, key: str) -> bytes | None:
        return self._images.get(key)

    def put(self, key: str, data: bytes) -> None:
        with self._lock:
            self._images[key] = data
