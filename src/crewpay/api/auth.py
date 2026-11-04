"""Who is calling.

CrewPay runs with demo identities: the console's identity picker sends ``X-Demo-User``,
``X-Demo-Role`` and ``X-Demo-Crew-Id`` headers, so a judge can act as any role and watch a
disruption go from report to approved plan to paid crew. Every role check behind those headers
is the same one a signed-in deployment would run.
"""

from __future__ import annotations

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


def caller_from(request: Request, config: Settings) -> Caller:
    role = request.headers.get("x-demo-role", Role.CONTROLLER.value)
    try:
        return Caller(
            request.headers.get("x-demo-user", "ananya.iyer"),
            Role(role),
            request.headers.get("x-demo-crew-id") or None,
        )
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
