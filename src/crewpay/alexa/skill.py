"""The CrewPay Alexa skill, served at ``/api/alexa`` on the console's own server.

Every request is checked as coming from Alexa (``crewpay.alexa.verify``). Account linking
goes through CrewPay's own sign-in (``crewpay.alexa.linking``), so a linked person is one of
the console's accounts and gets exactly their role's access, as in the console.

What it handles:

* Launch greets the person by name, or shows a link-account card. The first time it also
  asks for notification and reminder permission. An Echo nobody linked can still be the
  ops-room speaker: it hears what waits for the desk, never anyone's pay, and changes nothing.
* "What's waiting?" reads what waits on that person: plans to approve, disruptions on the
  desk, a callout offer for a crew member.
* "Ask CrewPay <question>" goes through the same assistant as the console, so every figure
  comes from the engine and is checked before it is said.
* Notification permission changes, account linking and skill disable are kept, so the
  notifier knows whose Echo to chime.
* A Skill Messaging message from the notifier sets a reminder a few seconds out with the
  alert's words: a skill cannot otherwise speak unprompted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from crewpay.agent.tools import Caller, Role

log = logging.getLogger(__name__)

PERMISSION = "alexa::devices:all:notifications:write"
REMINDERS = "alexa::alerts:reminders:skill:readwrite"
LOCALES = ("en-IN", "en-US", "en-AU", "en-GB")
HELP = (
    "You can ask: what's waiting for me. Or ask CrewPay a question, such as: ask crew pay "
    "who covers T 1018."
)
QUESTION_STARTS = {
    "AskWhoIntent": "who",
    "AskWhosIntent": "who's",
    "AskWhatIntent": "what",
    "AskWhatsIntent": "what's",
    "AskWhichIntent": "which",
    "AskWhenIntent": "when",
    "AskWhereIntent": "where",
    "AskHowIntent": "how",
    "AskWhyIntent": "why",
    "AskIsIntent": "is",
    "AskAreIntent": "are",
    "AskAmIntent": "am",
    "AskCanIntent": "can",
    "AskCouldIntent": "could",
    "AskShouldIntent": "should",
    "AskWillIntent": "will",
    "AskDoIntent": "do",
    "AskDoesIntent": "does",
    "AskHasIntent": "has",
    "AskHaveIntent": "have",
    "AskExplainIntent": "explain",
    "AskShowIntent": "show",
    "AskListIntent": "list",
    "AskGiveMeIntent": "give me",
    "AskAnyIntent": "any",
}
"""A free-form question needs words before its slot (AMAZON.SearchQuery cannot stand alone),
and the slot holds only what follows them. So each opening word has its own intent, and the
word is put back here: "who covers T 1018" arrives as AskWhoIntent with "covers T 1018"."""
SAID = {"HelloIntent": "hello", "ThanksIntent": "thank you"}

EXAMPLES = {
    Role.CREW: "ask crew pay when do I fly next",
    Role.REPORTER: "ask crew pay is gate G3 free",
    Role.AUDITOR: "ask crew pay how much was paid out tonight",
    Role.ADMIN: "ask crew pay how many open disruptions each controller has",
    Role.SUPER_ADMIN: "ask crew pay how many open disruptions each controller has",
}

_ID = re.compile(r"\b([A-Z])-(\d{3,5})\b")
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RUPEES = re.compile(r"₹\s?(\d(?:[\d,]*\d)?)")
_INR = re.compile(r"\bINR\s?(\d(?:[\d,]*\d)?(?:\.\d+)?)")
_USD = re.compile(r"\b([\d,]+(?:\.\d+)?)\s?USD\b|\bUSD\s?([\d,]+(?:\.\d+)?)")


def _spoken_date(match: re.Match[str]) -> str:
    """Free text can hold something date-shaped that is no date ("2026-02-30"): kept as is."""
    try:
        d = date(*map(int, match.groups()))
    except ValueError:
        return match.group(0)
    return f"{d.day} {d:%B}"


def spoken(text: str) -> str:
    """Written for the eye, said for the ear: "C-1042" becomes "C 1042", "2026-10-30" becomes
    "30 October", "₹31,160" and "INR 31,160" become "31,160 rupees", "370.95 USD" becomes
    "$370.95"."""
    text = _ID.sub(r"\1 \2", text)
    text = _DATE.sub(_spoken_date, text)
    text = _RUPEES.sub(r"\1 rupees", text)
    text = _INR.sub(r"\1 rupees", text)
    text = _USD.sub(lambda m: f"${m.group(1) or m.group(2)}", text)
    return re.sub(r"[*_`#]+", "", text)


def help_for(caller: Caller) -> str:
    """What this person can say: "what's waiting" means something to the desk, the approvers
    and crew with an offer."""
    if caller.role in (Role.CONTROLLER, Role.APPROVER, Role.CREW):
        return (
            HELP
            if caller.role is not Role.CREW
            else (
                "You can ask: what's waiting for me. Or ask crew pay a question, such as: "
                + EXAMPLES[Role.CREW]
                + "."
            )
        )
    example = EXAMPLES.get(caller.role, "ask crew pay what can I do")
    return f"Ask crew pay a question, such as: {example}. Or say: ask crew pay what can I do."


def first_name(user: str) -> str:
    """ "meera.bhatt" -> "Meera"; crew sign-ins ("c1167.iyer") -> "Iyer"."""
    parts = user.split(".")
    if re.fullmatch(r"c\d+", parts[0], re.I) and len(parts) > 1:
        return parts[-1].title()
    return parts[0].title()


def response(
    text: str,
    *,
    end: bool = True,
    reprompt: str | None = None,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "outputSpeech": {"type": "PlainText", "text": spoken(text)[:7000]},
        "shouldEndSession": end,
    }
    if reprompt:
        out["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    if card:
        out["card"] = card
    return {"version": "1.0", "response": out}


def link_card() -> dict[str, Any]:
    return response(
        "To use CrewPay, link your CrewPay account in the Alexa app, then open crew pay again.",
        card={"type": "LinkAccount"},
    )


def system(envelope: dict[str, Any]) -> dict[str, Any]:
    return (envelope.get("context") or {}).get("System") or {}


class AlexaLinks:
    """Who has linked which Alexa account, and whether they allow notifications. Kept in the
    store beside everything else; each skill request refreshes the person's entry."""

    KIND = "alexa_link"

    def __init__(self, docs: Any) -> None:
        self.docs = docs

    def save(
        self,
        alexa_user: str,
        endpoint: str,
        caller: Caller,
        now: datetime,
        locale: str | None,
    ) -> None:
        old = self.docs.get_doc(self.KIND, alexa_user) or {}
        self.docs.put_doc(
            self.KIND,
            alexa_user,
            {
                **old,
                "alexa_user_id": alexa_user,
                "api_endpoint": endpoint or old.get("api_endpoint", ""),
                "user": caller.user,
                "role": caller.role.value,
                "crew_id": caller.crew_id,
                "locale": locale or old.get("locale") or "en-IN",
                "at": now.isoformat(),
            },
        )

    def notifications(self, alexa_user: str, endpoint: str, allowed: bool, now: datetime) -> None:
        old = self.docs.get_doc(self.KIND, alexa_user) or {"alexa_user_id": alexa_user}
        self.docs.put_doc(
            self.KIND,
            alexa_user,
            {
                **old,
                "api_endpoint": endpoint or old.get("api_endpoint", ""),
                "notifications": allowed,
                "at": now.isoformat(),
            },
        )

    def forget(self, alexa_user: str) -> None:
        self.docs.delete_doc(self.KIND, alexa_user)

    def all(self) -> list[dict[str, Any]]:
        return list(self.docs.docs(self.KIND))


class Skill:
    """Everything it needs is passed in, so the tests run with no Alexa and no models."""

    def __init__(
        self,
        links: AlexaLinks,
        identify: Callable[[str], Caller],
        ask: Callable[[Caller, str], str],
        waiting: Callable[[Caller], list[str]],
        clock: Callable[[], datetime] | None = None,
        remind: Callable[[str, str, dict[str, Any]], None] | None = None,
        room: Caller | None = None,
    ) -> None:
        from crewpay.alexa.proactive import set_reminder

        self.room = room
        """Who an Echo nobody has linked speaks as: the ops-room speaker, which hears what
        waits for the desk and can change nothing. None: unlinked Echoes are asked to link."""
        self.links = links
        self.identify = identify  # access token -> Caller, or raises
        self.ask = ask
        self.waiting_for = waiting
        self.clock = clock or (lambda: datetime.now(UTC))
        self.remind = remind or set_reminder  # (api_endpoint, api_access_token, body)

    def handle(self, envelope: dict[str, Any]) -> dict[str, Any]:
        request = envelope.get("request") or {}
        kind = request.get("type", "")
        sys = system(envelope)
        alexa_user = (sys.get("user") or {}).get("userId", "")
        endpoint = sys.get("apiEndpoint", "")
        now = self.clock()

        if kind == "AlexaSkillEvent.ProactiveSubscriptionChanged":
            subs = (request.get("body") or {}).get("subscriptions") or []
            allowed = any(s.get("eventName") == "AMAZON.MessageAlert.Activated" for s in subs)
            self.links.notifications(alexa_user, endpoint, allowed, now)
            return {"version": "1.0", "response": {}}
        if kind == "Messaging.MessageReceived":
            return self.announce(request.get("message") or {}, sys, now)
        if kind == "AlexaSkillEvent.SkillDisabled":
            self.links.forget(alexa_user)
            return {"version": "1.0", "response": {}}

        token = (sys.get("user") or {}).get("accessToken")
        if kind == "AlexaSkillEvent.SkillAccountLinked":
            token = (request.get("body") or {}).get("accessToken") or token
        caller = None
        if token:
            try:
                caller = self.identify(token)
            except Exception as exc:  # expired or foreign token: treated as not linked
                log.info("Alexa access token refused: %s", exc)
        if caller is not None and alexa_user:
            self.links.save(alexa_user, endpoint, caller, now, request.get("locale"))
        if kind.startswith("AlexaSkillEvent."):
            return {"version": "1.0", "response": {}}
        if kind == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        if caller is None:
            if self.room is None:
                return link_card()
            caller = self.room
        if kind == "LaunchRequest":
            return self.launch(caller, envelope)
        if kind == "IntentRequest":
            return self.intent(caller, request.get("intent") or {})
        return response("Sorry, I can't do that yet. " + HELP, end=False, reprompt=HELP)

    def launch(self, caller: Caller, envelope: dict[str, Any]) -> dict[str, Any]:
        if caller is self.room:
            text = (
                "Welcome to CrewPay. This Echo isn't linked to anyone, so it speaks for the ops "
                f"room. {HELP} To hear what waits for you, link your CrewPay account in the "
                "Alexa app."
            )
            return response(text, end=False, reprompt=HELP, card={"type": "LinkAccount"})
        text = f"Welcome to CrewPay, {first_name(caller.user)}. {help_for(caller)}"
        granted = ((system(envelope).get("user") or {}).get("permissions") or {}).get(
            "consentToken"
        )
        card = None
        if not granted:
            text += (
                " To hear the moment something reaches you, allow CrewPay notifications and"
                " reminders in the Alexa app."
            )
            card = {"type": "AskForPermissionsConsent", "permissions": [PERMISSION, REMINDERS]}
        return response(text, end=False, reprompt=help_for(caller), card=card)

    def announce(self, message: dict[str, Any], sys: dict[str, Any], now: datetime) -> dict:
        """Turns the notifier's words into a reminder a few seconds out. Only CrewPay's
        notifier holds the skill's messaging credentials, and the request was already checked
        as Alexa's, so the words can be trusted."""
        from crewpay.alexa.proactive import reminder

        text = spoken(str(message.get("announce") or "")).strip()
        locale = str(message.get("locale") or "en-IN")
        if text:
            try:
                self.remind(
                    sys.get("apiEndpoint", ""),
                    sys.get("apiAccessToken", ""),
                    reminder(text, locale if locale in LOCALES else "en-IN", now),
                )
            except Exception as exc:  # no reminder permission and so on: the chime still went
                log.info("announcement reminder not set: %s", exc)
        return {"version": "1.0", "response": {}}

    def intent(self, caller: Caller, intent: dict[str, Any]) -> dict[str, Any]:
        name = intent.get("name", "")
        if name == "WhatsWaitingIntent":
            return self.waiting(caller)
        if name == "AskCrewPayIntent" or name in QUESTION_STARTS or name in SAID:
            question = ((intent.get("slots") or {}).get("question") or {}).get("value", "").strip()
            if name in QUESTION_STARTS and question:
                question = f"{QUESTION_STARTS[name]} {question}"
            question = SAID.get(name, question)
            if not question:
                return response("What would you like to ask?", end=False, reprompt=help_for(caller))
            return response(self.ask(caller, question), end=False, reprompt="Anything else?")
        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return response("Goodbye.")
        if name in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent", "AMAZON.NavigateHomeIntent"):
            return response(help_for(caller), end=False, reprompt=help_for(caller))
        return response(
            "Sorry, I didn't catch that. " + help_for(caller), end=False, reprompt=help_for(caller)
        )

    def waiting(self, caller: Caller) -> dict[str, Any]:
        found = self.waiting_for(caller)
        room = caller is self.room
        if not found:
            nothing = "Nothing is waiting for the desk" if room else "Nothing is waiting for you"
            return response(f"{nothing} right now.", end=False, reprompt=HELP)
        shown = found[:3]
        who = "The desk has" if room else "You have"
        lead = f"{who} {len(found)} thing{'s' if len(found) != 1 else ''} waiting."
        if len(found) > len(shown):
            lead += f" The {len(shown)} most urgent:"
        return response(" ".join([lead, *shown]), end=False, reprompt="Anything else?")
