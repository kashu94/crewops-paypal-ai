"""Chimes the Echo of whoever a notice is waiting on.

Who gets chimed, among the people who linked CrewPay and allowed notifications:

* a new disruption, a callout nobody took, an escalation: the controllers;
* a plan to approve, an approval waiting for PayPal, a payment held at the cap: the approvers;
* a callout offer, a payment, a roster change for a crew member: that crew member.

With the chime the skill is sent the words too (Skill Messaging), and it sets a reminder a
few seconds out, so the Echo actually says what happened (see ``crewpay.alexa.proactive``).

Sending happens on its own thread, after the notice is kept: a slow or failing Alexa never
holds up the request that made the notice, and never loses the notice itself. Failures are
logged and not retried: the console and the speaker carry the same news, and a double chime
is worse than a missed one.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from crewpay.alexa.skill import AlexaLinks, spoken
from crewpay.ops.notify import DESK, SUPERVISOR, Notice

log = logging.getLogger(__name__)

FOR_CONTROLLERS = ("New disruption", "Decision needed", "Escalated", "Needs a person")
FOR_APPROVERS = ("Approval needed", "Waiting for PayPal", "Payment held")
FOR_CREW = ("Callout", "Paid", "CrewPay: you have been paid", "Offer withdrawn", "Rostered")
DESK_ROLES = {
    FOR_CONTROLLERS: {"controller", "admin", "super_admin"},
    FOR_APPROVERS: {"approver"},
}


def recipients(notice: Notice, links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = [link for link in links if link.get("notifications") and link.get("user")]
    if notice.to in (DESK, SUPERVISOR):
        for prefixes, roles in DESK_ROLES.items():
            if notice.subject.startswith(prefixes):
                return [link for link in allowed if link.get("role") in roles]
        return []
    if notice.subject.startswith(FOR_CREW):
        return [link for link in allowed if link.get("crew_id") and link["crew_id"] == notice.to]
    return []


SEVERITY = re.compile(r"severity (high|medium|low) \(([^)]*)\)")
ACCOUNT = re.compile(r"\b[a-z]\d*\.[a-z]+\b")


def announcement(notice: Notice) -> str:
    """What the Echo says. A new disruption leads with its severity and why ("CrewPay, high
    severity. New on your desk: Sick: A. Verma, 30 Oct. Reports in 6 hours, 541 passengers
    booked");
    anything else is its subject and first sentence. Sign-in names are never read out."""
    found = SEVERITY.search(notice.body)
    if notice.subject.startswith("New disruption: ") and found:
        level, why = found.groups()
        title = notice.subject.removeprefix("New disruption: ")
        # Said aloud, an alert has to be short: the last two reasons are when and how many.
        reasons = ", ".join(why.split("; ")[-2:])
        reasons = reasons[:1].upper() + reasons[1:]
        text = f"CrewPay, {level} severity. New on your desk: {title}. {reasons}."
    else:
        first = notice.body.split("\n")[0].split(". ")[0].strip().rstrip(".")
        text = f"CrewPay. {notice.subject}." + (f" {first}." if first else "")
    text = ACCOUNT.sub("someone", text)
    return spoken(text)[:240]


Send = Callable[[dict[str, Any], datetime, str], None]


class AlexaNotifier:
    def __init__(self, links: AlexaLinks, send: Send | None) -> None:
        self.links, self._send = links, send

    @property
    def ready(self) -> bool:
        return self._send is not None

    def notify(self, notice: Notice) -> int:
        """Chime everyone ``notice`` is for; returns how many were sent."""
        if self._send is None:
            return 0
        sent = 0
        text = announcement(notice)
        for link in recipients(notice, self.links.all()):
            try:
                self._send(link, datetime.now(UTC), text)
                sent += 1
            except Exception:
                log.exception("Alexa notification to %s failed", link.get("user"))
        return sent


def sender(client_id: str, client_secret: str, stage: str) -> Send:
    """The real thing: a notification (the chime) and a skill message (the words)."""
    from crewpay.alexa.proactive import MESSAGING_SCOPE, Tokens, message_alert, message_skill
    from crewpay.alexa.proactive import send as send_event

    events = Tokens(client_id, client_secret)
    messaging = Tokens(client_id, client_secret, scope=MESSAGING_SCOPE)

    def send(link: dict[str, Any], now: datetime, text: str) -> None:
        send_event(events, link["api_endpoint"], message_alert(link["alexa_user_id"], now), stage)
        log.info("Alexa notification sent to %s", link["user"])
        if text:
            # A notification cannot carry speech: the skill turns this into a reminder.
            try:
                message_skill(
                    messaging,
                    link["api_endpoint"],
                    link["alexa_user_id"],
                    {"announce": text, "locale": link.get("locale") or "en-IN"},
                )
            except Exception:
                log.exception("Alexa announcement to %s failed", link["user"])

    return send


class AlexaOutbox:
    """Keeps every notice like ``inner`` does, then chimes the Echoes it is for."""

    def __init__(self, inner: Any, notifier: AlexaNotifier, background: bool = True) -> None:
        self.inner, self.notifier, self.background = inner, notifier, background

    def send(self, notice: Notice) -> None:
        self.inner.send(notice)
        if not self.notifier.ready:
            return
        if self.background:
            threading.Thread(target=self._quietly, args=(notice,), daemon=True).start()
        else:
            self._quietly(notice)

    def _quietly(self, notice: Notice) -> None:
        try:
            self.notifier.notify(notice)
        except Exception:
            log.exception("Alexa notifications failed")

    def sent(self, to: str | None = None) -> list[Notice]:
        return self.inner.sent(to)
