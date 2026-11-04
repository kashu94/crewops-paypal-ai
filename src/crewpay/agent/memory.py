"""Conversations, so a follow-up can lean on what came before.

"Pooja is sick on the 5th" then "and is she legal for T-1010?" only works if the second
question knows who "she" is. Each conversation keeps its last few turns, replayed to the
planner as plain user and assistant messages, and a short note of the people and trips
already discussed, added to the system prompt.

A conversation belongs to one caller. If someone else uses the same id they start a new one,
so nobody sees another person's questions. Blocked questions are not remembered.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import TypeAdapter

from crewpay.agent.advisor import Advisor, Answer
from crewpay.agent.tools import Caller

MAX_TURNS = 6
MAX_FOCUS = 4
IDLE = timedelta(minutes=30)
IDS = re.compile(r"\b[CT]-\d{4}\b")


@dataclass
class Turn:
    question: str
    text: str


@dataclass
class Conversation:
    id: str
    caller: Caller
    turns: list[Turn] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    focus: list[str] = field(default_factory=list)
    """Crew and trip ids discussed, most recent first."""
    updated: datetime = field(default_factory=lambda: datetime.now(UTC))

    def history(self) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for turn in self.turns[-MAX_TURNS:]:
            messages.append({"role": "user", "content": turn.question})
            messages.append({"role": "assistant", "content": turn.text})
        return messages

    def context(self) -> str:
        if not self.focus:
            return ""
        people = [f"{i} ({self.names[i]})" if i in self.names else i for i in self.focus]
        return "already discussed " + ", ".join(people) + "."

    def remember(self, answer: Answer) -> None:
        if answer.blocked:
            return
        self.turns = [*self.turns, Turn(answer.question, answer.text)][-MAX_TURNS:]
        seen: list[str] = []
        for step in answer.steps:
            for match in step.result.get("matches", []):
                self.names[match["crew_id"]] = match["name"]
            if "crew_id" in step.result and "name" in step.result:
                self.names[step.result["crew_id"]] = step.result["name"]
            for value in step.arguments.values():
                if isinstance(value, str) and IDS.fullmatch(value):
                    seen.append(value)
        seen += IDS.findall(answer.question) + IDS.findall(answer.text)
        for value in seen:  # later mentions end up in front
            if value in self.focus:
                self.focus.remove(value)
            self.focus.insert(0, value)
        del self.focus[MAX_FOCUS:]
        self.updated = datetime.now(UTC)


_conversation = TypeAdapter(Conversation)


class Conversations:
    """Every open conversation. Idle ones are dropped after 30 minutes.

    Kept in memory, or with ``docs`` in the store, so a follow-up still works when it lands on
    a different server from the question before it.
    """

    def __init__(
        self, clock: Callable[[], datetime] = lambda: datetime.now(UTC), docs: Any = None
    ) -> None:
        self.clock = clock
        self.docs = docs
        self._lock = threading.Lock()
        self._open: dict[str, Conversation] = {}

    def get(self, conversation_id: str | None, caller: Caller) -> Conversation:
        now = self.clock()
        with self._lock:
            for key in [k for k, c in self._open.items() if now - c.updated > IDLE]:
                del self._open[key]
            found = self._open.get(conversation_id or "")
            if found is None and conversation_id and self.docs is not None:
                stored = self.docs.get_doc("conversation", conversation_id)
                if stored is not None:
                    found = _conversation.validate_python(stored)
                    if now - found.updated > IDLE:
                        found = None
            if found is None or found.caller != caller:
                found = Conversation(uuid.uuid4().hex, caller, updated=now)
            self._open[found.id] = found
            return found

    def save(self, conversation: Conversation) -> None:
        if self.docs is not None:
            body = _conversation.dump_python(conversation, mode="json")
            self.docs.put_doc("conversation", conversation.id, body)

    def ask(
        self,
        advisor: Advisor,
        question: str,
        caller: Caller,
        conversation_id: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Conversation, Answer]:
        conversation = self.get(conversation_id, caller)
        answer = advisor.ask(
            question, caller, conversation.history(), conversation.context(), now=now
        )
        conversation.remember(answer)
        conversation.updated = self.clock()
        self.save(conversation)
        return conversation, answer
