"""The advisor: one question in, one checked answer out.

    screen the question  ->  route it  ->  plan with tools  ->  check the answer  ->  reply

The advisor model plans: it reads the question, calls the tools the route allows, and writes the
answer from what they return. The two checks either side are pluggable:

* a ``Guard`` screens the question before any model sees it (prompt injection, abuse);
* a ``Verifier`` checks every id and figure in the answer against the tool results. If it
  finds one that no tool returned, the planner gets one chance to rewrite from the evidence;
  an answer that still fails is returned marked unverified, never silently passed.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from crewpay.agent.router import Route, Router
from crewpay.agent.tools import Caller, Context, Role, Roster, ToolBox
from crewpay.domain import Rule
from crewpay.engine.clock import IST
from crewpay.models import ModelClient, ModelError, Usage
from crewpay.ops.callout import CalloutDesk
from crewpay.ops.governance import log

MAX_STEPS = 6
OUT_OF_SCOPE = (
    "I can only help with crew control: absences, delays, cover, duty limits and the roster."
)
NO_ANSWER = "I could not work that out from the roster. Please try asking in another way."


@dataclass(frozen=True, slots=True)
class Screening:
    allowed: bool
    reason: str = ""
    by: str = ""


class Guard(Protocol):
    def screen(self, text: str) -> Screening: ...


class Verifier(Protocol):
    def problems(self, answer: str, question: str, steps: Sequence[Step]) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class Step:
    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    @property
    def failed(self) -> bool:
        return "error" in self.result


@dataclass
class Answer:
    question: str
    text: str
    route: Route | None = None
    steps: list[Step] = field(default_factory=list)
    verified: bool = False
    problems: list[str] = field(default_factory=list)
    blocked: str | None = None
    usage: list[Usage] = field(default_factory=list)
    draft: str | None = None
    """The first answer, when the evidence check made the planner rewrite it."""
    draft_problems: list[str] = field(default_factory=list)
    """What the check found in that first answer."""

    @property
    def tokens(self) -> dict[str, int]:
        return {
            "prompt": sum(u.prompt_tokens for u in self.usage),
            "completion": sum(u.completion_tokens for u in self.usage),
            "reasoning": sum(u.reasoning_tokens for u in self.usage),
            "cached": sum(u.cached_tokens for u in self.usage),
        }


def system_prompt(
    rules: Sequence[Rule], airlines: dict[str, str], caller: Caller, now: datetime
) -> str:
    """Stable text first (rules, airlines), the caller and clock last, so the long prefix is
    the same on every call and can be served from the prompt cache."""
    rule_lines = "\n".join(f"{r.rule_id} {r.title}: {r.text}" for r in rules)
    airline_lines = ", ".join(f"{code} {name}" for code, name in sorted(airlines.items()))
    who = f"{caller.user}, role {caller.role}"
    if caller.role is Role.CREW:
        who += f", crew id {caller.crew_id}; answer only about this person and their trips"
    return f"""\
You are CrewPay, the crew-control assistant for regional airlines based at Hyderabad.
Airlines: {airline_lines}.

How to answer:
- Every crew id, trip id, flight, time, hour figure, count and rupee amount you state must
  appear in a tool result from this conversation. Never calculate, estimate or round
  differently; quote the tools' figures exactly.
- Turn names into crew ids with search_crew. If several people match, narrow it down with
  what you know (who is on the trip named, who was discussed earlier) and ask only if it is
  still unclear.
- Never call someone legal or illegal without check_legality or find_cover saying so.
- check_legality and find_cover judge one seat against the roster as it stands. They do not
  know about other cover you propose. For an absence across several trips use
  absence_impact, which plans every seat together, and never propose the same person for
  more than one trip from separate results.
- Never say the roster was changed unless assign_crew returned a new version, or a callout
  shows filled. Never say anyone accepted a callout unless callout_status says so.
- When the caller asks about themselves (who they are, their role, what they can do or
  should do now), call about_me and answer for their role: their name and role, what the
  role does, and the first things waiting for them.
- When the caller's role cannot see what they ask about, say so once, plainly, and say who
  can (crew control, an admin). Do not then go on to describe that same thing as if they
  could.
- When crew ask about callouts, say what callout_status shows: an open offer and its answer
  time, or an earlier offer and what became of it (answered, or moved on when time ran out).
- When someone says they cannot fly, record it with report_unavailable first, for the dates
  they gave, so they are never offered as cover elsewhere.
- To arrange cover with the crew, start a callout. If nobody accepts, it waits for the desk:
  never cancel or fly short yourself; a controller proposes and an approver decides
  through decide_callout.
- Mention fatigue the tools report next to a choice (legal, but worth weighing), and
  anything listed to arrange (a positioning seat to book, a departure to retime).
- Write for a busy controller reading on a phone: at most about 120 words.
  1. The recommendation in one sentence.
  2. Why, in one or two sentences: only the deciding rule ids and numbers. Do not list rules
     that passed unless asked.
  3. Up to two alternatives, each with its cost.
- Plain sentences or a short list. Never paste JSON, brackets or tool field names. IST times,
  amounts as INR with the tools' figures.

Duty rules (the engine applies them; cite them by id):
{rule_lines}

Caller: {who}.
Operation clock: {now.astimezone(IST):%A %d %B %Y, %H:%M} IST."""


def refusal(reason: str) -> str:
    """What the person sees when a question is refused. Someone who may harm themselves is
    answered with care and pointed to help, never just turned away."""
    if reason.startswith("self-harm"):
        return (
            "I'm sorry you're feeling this way. You don't have to go through it alone: please "
            "talk to someone you trust, or call Tele-MANAS on 14416 (free, 24 hours, in Hindi, "
            "Telugu and English). If you are in danger now, call 112. Crew control can take you "
            "off duty: just say you can't fly, no reason needed."
        )
    if reason.startswith("harmful"):
        return (
            "I can't help with that. Questions about hurting people or endangering a flight are "
            "recorded and sent to the administrators."
        )
    return "I can't help with that request."


class Advisor:
    def __init__(
        self,
        store: Roster,
        planner: ModelClient,
        rules: Sequence[Rule],
        *,
        router: Router | None = None,
        toolbox: ToolBox | None = None,
        guard: Guard | None = None,
        verifier: Verifier | None = None,
        desk: CalloutDesk | None = None,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.store = store
        self.planner = planner
        self.rules = tuple(rules)
        self.router = router or Router()
        self.toolbox = toolbox or ToolBox()
        self.guard = guard
        self.verifier = verifier
        self.desk = desk
        self.reports = None
        self.payroll = None
        """Disruption reports (``crewpay.ops.reports.ReportDesk``), set by the API."""
        self.max_steps = max_steps

    def _refused(self, question: str, caller: Caller, reason: str, source: str) -> None:
        """Keep a record of every refused question for the admin."""
        if hasattr(self.store, "put_doc"):
            log(
                self.store,
                "security",
                by=caller.user,
                role=str(caller.role),
                question=question[:300],
                reason=reason,
                source=source,
            )

    def ask(
        self,
        question: str,
        caller: Caller,
        history: Sequence[dict[str, str]] = (),
        context: str = "",
        now: datetime | None = None,
    ) -> Answer:
        """Answer ``question`` for ``caller``.

        ``history`` is earlier user and assistant turns, oldest first. ``context`` is a short
        note of what those turns were about (people and trips), added to the system prompt.
        """
        answer = Answer(question=question, text="")
        if self.guard is not None:
            screening = self.guard.screen(question)
            if not screening.allowed:
                answer.text = refusal(screening.reason)
                answer.blocked = screening.reason or "blocked"
                self._refused(question, caller, answer.blocked, screening.by)
                return answer

        route = self.router.route(question)
        answer.route = route
        if route.harm:
            answer.blocked = f"{route.harm} (flagged by the router, {route.by})"
            answer.text = refusal(route.harm)
            self._refused(question, caller, answer.blocked, f"router ({route.by})")
            return answer
        if route.attack:
            answer.text = "I can't help with that request."
            answer.blocked = f"prompt injection (flagged by the router, {route.by})"
            self._refused(question, caller, answer.blocked, f"router ({route.by})")
            return answer
        if not route.in_scope and not history:
            answer.text = OUT_OF_SCOPE
            answer.verified = True
            return answer

        ctx = Context(
            self.store, caller, now, self.desk, reports=self.reports, payroll=self.payroll
        )
        op = ctx.operation()
        allowed = {t.name for t in self.toolbox.available(caller)}
        names = [n for n in route.tools if n in allowed] or sorted(allowed)
        tools = [self.toolbox.tools[n].spec() for n in names]
        system = system_prompt(self.rules, dict(op.airline_names), caller, op.now)
        if context:
            system += f"\nEarlier in this conversation: {context}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": question},
        ]

        try:
            answer.text = self._plan(messages, tools, names, route.effort, ctx, answer)
            self._check(messages, route.effort, answer)
        except ModelError as err:
            answer.text = f"I could not reach the model just now ({err}). Please ask again."
            answer.problems.append(str(err))
            answer.verified = False
        return answer

    def _plan(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        names: list[str],
        effort: str,
        ctx: Context,
        answer: Answer,
    ) -> str:
        for _ in range(self.max_steps):
            reply = self.planner.chat(
                messages, purpose="plan", effort=effort, tools=tools, tool_choice="auto"
            )
            answer.usage.append(reply.usage)
            if not reply.tool_calls:
                return reply.content.strip() or NO_ANSWER
            messages.append(reply.message())
            for call in reply.tool_calls:
                if call.name not in names:
                    result = {"error": f"{call.name} is not available for this question"}
                else:
                    result = self.toolbox.call(call.name, call.arguments, ctx)
                answer.steps.append(Step(call.name, _arguments(call.arguments), result))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        # Out of steps: answer from what has been gathered, with no more tool calls.
        reply = self.planner.chat(
            [*messages, {"role": "user", "content": "Answer now from the tool results above."}],
            purpose="plan",
            effort=effort,
        )
        answer.usage.append(reply.usage)
        return reply.content.strip() or NO_ANSWER

    def _check(self, messages: list[dict[str, Any]], effort: str, answer: Answer) -> None:
        if self.verifier is None:
            answer.verified = False
            return
        problems = self.verifier.problems(answer.text, answer.question, answer.steps)
        if problems:
            answer.draft = answer.text
            answer.draft_problems = list(problems)
            reply = self.planner.chat(
                [
                    *messages,
                    {"role": "assistant", "content": answer.text},
                    {
                        "role": "user",
                        "content": "Your answer states things no tool returned:\n- "
                        + "\n- ".join(problems)
                        + "\nRewrite it using only ids and figures from the tool results.",
                    },
                ],
                purpose="repair",
                effort=effort,
            )
            answer.usage.append(reply.usage)
            if reply.content.strip():
                answer.text = reply.content.strip()
            problems = self.verifier.problems(answer.text, answer.question, answer.steps)
        answer.problems = problems
        answer.verified = not problems


def _arguments(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"unparsed": raw}
    return value if isinstance(value, dict) else {"value": value}
