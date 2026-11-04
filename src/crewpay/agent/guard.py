"""Screening what people type before any model reads it.

Two layers, cheapest first; the first to block wins:

1. ``PatternGuard``: length limit and well-known injection phrasings, in microseconds.
2. ``PromptGuard``: an optional prompt-injection classifier, served behind a small HTTP
   endpoint (``POST {"text": ...}`` returning ``{"label": ..., "score": ...}``).

What a screened question can still do is bounded anyway: tools check the caller's role, and
roster changes need legal cover and the trip's current version. The guard keeps abuse away
from the models and out of the logs. If the classifier is unreachable the pattern layer still
applies and the answer is marked with what screened it, rather than refusing every question.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import httpx

from crewpay.agent.advisor import Guard, Screening

MAX_CHARS = 2000

INJECTION = re.compile(
    r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|prompt|guidelines)\b"
    r"|\b(system|developer)\s+(prompt|message|instructions?)\b"
    r"|\byou are now\b|\bdeveloper mode\b|\bjailbreak\w*\b|\bDAN\b"
    r"|\b(reveal|print|show|repeat)\b.{0,30}\b(your|the)\s+(prompt|instructions|rules)\b",
    re.IGNORECASE | re.DOTALL,
)


SELF_HARM = re.compile(
    r"\b(kill|hurt|harm)\s+my\s?self\b|\bsuicid\w*|\bend\s+(my|it)\s+(life|all)\b"
    r"|\bwant\s+to\s+die\b|\bkhud\s?ko\s+(maar|khatam)|\baatma\s?hatya\b|\bmar\s+jaana?\b"
    r"|आत्महत्या|खुद\s?को\s+(मार|खत्म)|मर\s+जाना|ఆత్మహత్య|చనిపోవాలని",
    re.IGNORECASE,
)
"""Someone saying they may harm themselves: answered with care, and an admin is told."""

VIOLENCE = re.compile(
    r"\b(murder\w*|homicide|assassinat\w*|massacre)\b"
    r"|\b(kill|stab|shoot|strangle|poison|beat\s+up)\s+(some\s?one|somebody|him|her|them|"
    r"people|a\s+person|my\s+(wife|husband|boss|colleague|captain|neighbou?r)|the\s+(captain|"
    r"pilot|crew|passengers?|controller))\b"
    r"|\bhow\s+(do\s+i|to|can\s+i|would\s+i)\s+(kill|murder|hurt|poison|stab)\b"
    r"|\b(bomb|explosives?|ied|grenade)\b"
    r"|\b(bring|carry|smuggle|sneak|hide|take)\b.{0,30}\b(gun|firearm|pistol|knife|weapon)s?\b"
    r"|\b(hijack\w*|terror\s+attack|terroris\w*)\b|\bcrash\s+(the|a)\s+(plane|aircraft|flight)\b"
    r"|\b(maar\s+daal\w*|jaan\s+se\s+maar\w*|khoon\s+kar\w*|qatl)\b|\busko\s+maar\w*"
    r"|मार\s+(डाल|दूं|दूँ|देना)|हत्या|खून\s+कर|बम|చంపు\w*|హత్య|బాంబు",
    re.IGNORECASE | re.DOTALL,
)
"""Violence against people, weapons or threats to a flight: refused, and an admin is told."""


class PatternGuard:
    def screen(self, text: str) -> Screening:
        if len(text) > MAX_CHARS:
            return Screening(False, f"longer than {MAX_CHARS} characters", "patterns")
        if INJECTION.search(text):
            return Screening(False, "prompt injection", "patterns")
        if SELF_HARM.search(text):
            return Screening(False, "self-harm concern", "patterns")
        if VIOLENCE.search(text):
            return Screening(False, "harmful: violence or a threat", "patterns")
        return Screening(True, by="patterns")


class PromptGuard:
    BLOCK_LABELS = frozenset({"MALICIOUS", "INJECTION", "JAILBREAK", "LABEL_1"})

    def __init__(
        self,
        url: str,
        token: str = "",
        threshold: float = 0.8,
        http: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.url = url
        self.token = token
        self.threshold = threshold
        self.http = http
        self.timeout = timeout

    def screen(self, text: str) -> Screening:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        http = self.http or httpx.Client(timeout=self.timeout)
        try:
            response = http.post(self.url, json={"text": text}, headers=headers)
            response.raise_for_status()
            body = response.json()
            label, score = str(body["label"]).upper(), float(body["score"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return Screening(True, "classifier unavailable", "prompt-guard-unavailable")
        finally:
            if self.http is None:
                http.close()
        if label in self.BLOCK_LABELS and score >= self.threshold:
            return Screening(False, f"prompt injection ({score:.2f})", "prompt-guard")
        return Screening(True, by="prompt-guard")


class GuardChain:
    def __init__(self, guards: Sequence[Guard]) -> None:
        self.guards = tuple(guards)

    def screen(self, text: str) -> Screening:
        last = Screening(True)
        for guard in self.guards:
            last = guard.screen(text)
            if not last.allowed:
                return last
        return last
