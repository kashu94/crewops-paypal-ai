"""Who is calling.

CrewPay runs with demo identities: anyone may act as any of the demo accounts, so a judge can
go from report to approved plan to paid crew in one sitting. Every role check behind them is the
same one a real deployment would run.

With ``CREWPAY_DEMO_PASSWORD`` set (as the hosted demo is), signing in needs that one shared
password: ``POST /api/login`` checks it and returns a signed session token, and every request
must carry it as ``Authorization: Bearer``. The identity comes from the token alone, so the
demo headers cannot be used to skip the password. Without a password (on a laptop, in tests),
the console's ``X-Demo-User``, ``X-Demo-Role`` and ``X-Demo-Crew-Id`` headers say who is
calling.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

from fastapi import HTTPException, Request

from crewpay.agent.tools import Caller, Role
from crewpay.settings import Settings

ROLE_ORDER = (
    Role.SUPER_ADMIN,
    Role.ADMIN,
    Role.APPROVER,
    Role.CONTROLLER,
    Role.AUDITOR,
    Role.REPORTER,
    Role.CREW,
    Role.SPEAKER,
)
"""Someone with several roles acts with the first of them here."""

SESSION_HOURS = 12
_SECRET = secrets.token_bytes(32)
"""Signs session tokens; new on every start, so a restart (which resets the demo) signs
everyone out too. ``CREWPAY_SESSION_SECRET`` keeps sessions across restarts."""


def _key(config: Settings) -> bytes:
    return config.session_secret.encode() if config.session_secret else _SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(config: Settings, user: str, role: Role, crew_id: str | None) -> str:
    body = _b64(
        json.dumps(
            {
                "u": user,
                "r": role.value,
                "c": crew_id,
                "x": int(time.time()) + SESSION_HOURS * 3600,
            },
            separators=(",", ":"),
        ).encode()
    )
    sig = _b64(hmac.new(_key(config), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _from_token(token: str, config: Settings) -> Caller:
    try:
        body, sig = token.split(".", 1)
        want = _b64(hmac.new(_key(config), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, want):
            raise ValueError("bad signature")
        data = json.loads(_unb64(body))
        if data["x"] < time.time():
            raise HTTPException(401, "your session ended: sign in again")
        return Caller(data["u"], Role(data["r"]), data.get("c"))
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as err:
        raise HTTPException(401, "sign in again") from err


def password_ok(config: Settings, given: str) -> bool:
    return hmac.compare_digest(given.encode(), config.demo_password.encode())


class Throttle:
    """At most ``limit`` wrong passwords per address in ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 300.0) -> None:
        self.limit, self.window = limit, window
        self._misses: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, who: str) -> None:
        with self._lock:
            now = time.monotonic()
            recent = [t for t in self._misses.get(who, []) if now - t < self.window]
            self._misses[who] = recent
            if len(recent) >= self.limit:
                raise HTTPException(
                    429, "Too many wrong passwords. Wait a few minutes and try again."
                )

    def miss(self, who: str) -> None:
        with self._lock:
            self._misses.setdefault(who, []).append(time.monotonic())


def caller_from(request: Request, config: Settings) -> Caller:
    if config.demo_password:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise HTTPException(401, "sign in first")
        return _from_token(auth[7:].strip(), config)
    role = request.headers.get("x-demo-role", Role.CONTROLLER.value)
    try:
        return Caller(
            request.headers.get("x-demo-user", "ananya.iyer"),
            Role(role),
            request.headers.get("x-demo-crew-id") or None,
        )
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
