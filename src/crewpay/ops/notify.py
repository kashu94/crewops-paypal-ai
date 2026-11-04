"""Messages to crew and to the crew desk.

Callouts and escalations write ``Notice`` records to an outbox. The outbox decides how they
travel: kept in memory here and in tests, sent by email or SMS when deployed. The wording is
fixed templates filled from the engine's figures, so a notice never says anything a tool did
not compute.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

DESK = "crew-desk"
"""Address of the controllers on duty."""
SUPERVISOR = "duty-supervisor"
"""Address of the duty supervisor, who hears about what the desk has left untouched."""


@dataclass(frozen=True, slots=True)
class Notice:
    to: str
    """A crew id, ``DESK``, ``SUPERVISOR`` or a team (``team:gate_control``)."""
    subject: str
    body: str
    at: datetime
    ref: str
    """What it is about, such as a callout id."""


class Outbox(Protocol):
    def send(self, notice: Notice) -> None: ...


class MemoryOutbox:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sent: list[Notice] = []

    def send(self, notice: Notice) -> None:
        with self._lock:
            self._sent.append(notice)

    def sent(self, to: str | None = None) -> list[Notice]:
        with self._lock:
            return [n for n in self._sent if to is None or n.to == to]


class StoreOutbox:
    """Notices kept as documents in the store, newest last, so every server sees them."""

    def __init__(self, docs: Any) -> None:
        self.docs = docs

    def send(self, notice: Notice) -> None:
        # Sent order breaks ties between notices stamped with the same moment.
        key = f"{notice.at.isoformat()}#{time.time_ns():020d}#{uuid.uuid4().hex[:4]}"
        self.docs.put_doc("notice", key, {**asdict(notice), "at": notice.at.isoformat()})

    def sent(self, to: str | None = None) -> list[Notice]:
        found = [
            Notice(**{**d, "at": datetime.fromisoformat(d["at"])}) for d in self.docs.docs("notice")
        ]
        return [n for n in found if to is None or n.to == to]


TEAM = "team:"
"""Prefix of an operational team's address, such as ``team:gate_control``."""


def alert_group(to: str) -> str | None:
    """The alert group a notice to ``to`` is emailed to, if any: crew are told in the app."""
    if to == DESK:
        return "desk"
    if to == SUPERVISOR:
        return "supervisor"
    return to.removeprefix(TEAM) if to.startswith(TEAM) else None
