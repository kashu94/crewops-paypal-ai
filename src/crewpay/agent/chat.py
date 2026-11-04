"""Ask CrewPay questions from the terminal.

    uv run python -m crewpay.agent.chat --role controller --user ananya.iyer
    uv run python -m crewpay.agent.chat --ask "A. Nair is sick from 5 to 8 November"

In the conversation: ``/issues`` lists problems the watch finds, ``/outbox`` shows messages
sent to crew and the desk, ``/new`` starts over, ``/usage`` shows tokens used, ``/quit`` ends.
"""

from __future__ import annotations

import argparse
from typing import TextIO

from crewpay.agent.advisor import Advisor, Answer
from crewpay.agent.build import build_advisor
from crewpay.agent.memory import Conversations
from crewpay.agent.tools import Caller, Role
from crewpay.engine.watch import scan
from crewpay.settings import settings
from crewpay.store import open_store


def footer(answer: Answer) -> str:
    if answer.blocked:
        return f"  blocked: {answer.blocked}"
    parts = []
    if answer.route is not None:
        parts.append(f"{answer.route.intent} (routed by {answer.route.by})")
    if answer.steps:
        parts.append(f"{len(answer.steps)} tool calls")
    if answer.verified:
        parts.append("every figure checked against the tools")
    elif answer.problems:
        parts.append("UNVERIFIED: " + "; ".join(answer.problems))
    t = answer.tokens
    if answer.usage:
        parts.append(
            f"{len(answer.usage)} model calls, {t['prompt'] + t['completion']:,} tokens "
            f"({t['reasoning']:,} reasoning, {t['cached']:,} cached)"
        )
    return "  " + " · ".join(parts)


def run(advisor: Advisor, caller: Caller, lines: TextIO, out: TextIO) -> None:
    conversations = Conversations()
    conversation_id: str | None = None
    out.write("CrewPay: ask about absences, delays, cover, duty limits or the roster.\n")
    while True:
        out.write("\nyou> ")
        out.flush()
        line = lines.readline()
        if not line:
            break
        question = line.strip()
        if not question:
            continue
        if question == "/quit":
            break
        if question == "/new":
            conversation_id = None
            out.write("(new conversation)\n")
            continue
        if question == "/issues":
            for issue in scan(advisor.store.operation()):
                where = f" on {issue.trip_id}" if issue.trip_id else ""
                out.write(f"  [{issue.kind}] {issue.name} ({issue.crew_id}){where}, ")
                out.write(f"{issue.day:%d %b}: {issue.detail}\n")
                if issue.suggestion:
                    out.write(f"      cover: {issue.suggestion}, INR {issue.suggestion_cost:,}\n")
            continue
        if question == "/outbox":
            outbox = getattr(advisor.desk, "outbox", None)
            for notice in getattr(outbox, "sent", lambda: [])():
                out.write(f"  to {notice.to}: {notice.subject}\n      {notice.body}\n")
            continue
        if question == "/usage":
            for model, totals in advisor.planner.ledger.totals().items():
                out.write(f"  {model}: {totals}\n")
            continue
        conversation, answer = conversations.ask(advisor, question, caller, conversation_id)
        conversation_id = conversation.id
        out.write(f"\ncrewpay> {answer.text}\n{footer(answer)}\n")


def main() -> None:
    import sys

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--role", choices=[r.value for r in Role], default=Role.CONTROLLER.value)
    parser.add_argument("--user", default="controller")
    parser.add_argument("--crew-id", help="for --role crew: whose record this is")
    parser.add_argument("--ask", help="ask one question and exit")
    args = parser.parse_args()

    config = settings()
    caller = Caller(args.user, Role(args.role), args.crew_id)
    advisor = build_advisor(config, open_store(config))
    if args.ask:
        answer = advisor.ask(args.ask, caller)
        print(f"{answer.text}\n{footer(answer)}")
        return
    run(advisor, caller, sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
