"""Account linking for the Alexa skill: CrewPay's own sign-in, as an OAuth 2.0 authorization
code grant.

The Alexa app opens ``/api/alexa/link``; the person picks their CrewPay account and gives the
demo password, and the page sends Alexa back with a one-time code. Alexa trades the code at
``/api/alexa/token`` (with the client id and secret set on the skill's account linking page)
for an access token and a refresh token, and sends the access token with every request.

Every code and token is signed with a key made from the client secret, so nothing has to be
remembered: a link outlives a restart of the demo (which resets everything else). A token
only says who the person is (account, role, crew id); what they may do is checked again on
every request, the same as in the console.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

CODE_SECONDS = 300
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 365 * 24 * 3600
AMAZON_REDIRECT_HOSTS = (
    "https://pitangui.amazon.com",
    "https://layla.amazon.com",
    "https://alexa.amazon.co.jp",
)
"""Where Alexa's account linking may send the person back to (the skill's page lists the full
URLs); a redirect anywhere else is refused before any code is made."""


class LinkError(Exception):
    """The request is refused; ``error`` is the OAuth error code to answer with."""

    def __init__(self, error: str, detail: str) -> None:
        super().__init__(detail)
        self.error = error


@dataclass(frozen=True, slots=True)
class Linked:
    user: str
    role: str
    crew_id: str | None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Links:
    def __init__(
        self, client_id: str, client_secret: str, redirect_urls: tuple[str, ...] = ()
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_urls = redirect_urls
        self._key = hashlib.sha256(b"crewpay-alexa-link:" + client_secret.encode()).digest()

    @property
    def ready(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # -- signing ------------------------------------------------------------------------------

    def _sign(self, kind: str, who: Linked, seconds: int, **extra: str) -> str:
        body = _b64(
            json.dumps(
                {
                    "k": kind,
                    "u": who.user,
                    "r": who.role,
                    "c": who.crew_id,
                    "x": int(time.time()) + seconds,
                    **extra,
                },
                separators=(",", ":"),
            ).encode()
        )
        sig = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def _open(self, token: str, kind: str) -> dict:
        try:
            body, sig = token.split(".", 1)
            want = _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, want):
                raise ValueError("bad signature")
            data = json.loads(_unb64(body))
        except (ValueError, TypeError) as err:
            raise LinkError("invalid_grant", "not a CrewPay token") from err
        if data.get("k") != kind:
            raise LinkError("invalid_grant", f"not a {kind}")
        if data.get("x", 0) < time.time():
            raise LinkError("invalid_grant", f"the {kind} has expired")
        return data

    # -- the authorization step ------------------------------------------------------------------

    def check_request(self, client_id: str, redirect_uri: str, response_type: str) -> None:
        """Before showing the sign-in: is this Alexa, sending the person back to Alexa?"""
        if not self.ready:
            raise LinkError("server_error", "Alexa linking is not set up on this server")
        if not hmac.compare_digest(client_id.encode(), self.client_id.encode()):
            raise LinkError("unauthorized_client", "unknown client")
        if response_type != "code":
            raise LinkError("unsupported_response_type", "only the code grant is offered")
        if not self.redirect_allowed(redirect_uri):
            raise LinkError("invalid_request", "that redirect is not Alexa's")

    def redirect_allowed(self, uri: str) -> bool:
        if self.redirect_urls:
            return uri in self.redirect_urls
        return any(uri.startswith(host + "/") for host in AMAZON_REDIRECT_HOSTS)

    def code(self, who: Linked, redirect_uri: str) -> str:
        return self._sign("code", who, CODE_SECONDS, ru=redirect_uri)

    # -- the token step --------------------------------------------------------------------------

    def client_ok(self, client_id: str, client_secret: str) -> bool:
        return self.ready and (
            hmac.compare_digest(client_id.encode(), self.client_id.encode())
            and hmac.compare_digest(client_secret.encode(), self.client_secret.encode())
        )

    def tokens(self, who: Linked) -> dict[str, object]:
        return {
            "access_token": self._sign("access", who, ACCESS_SECONDS),
            "refresh_token": self._sign("refresh", who, REFRESH_SECONDS),
            "token_type": "Bearer",
            "expires_in": ACCESS_SECONDS,
        }

    def exchange(self, grant_type: str, value: str, redirect_uri: str = "") -> dict[str, object]:
        """A code (bound to the redirect it was made for) or a refresh token, for new tokens."""
        if grant_type == "authorization_code":
            data = self._open(value, "code")
            if redirect_uri and data.get("ru") != redirect_uri:
                raise LinkError("invalid_grant", "the code was made for another redirect")
        elif grant_type == "refresh_token":
            data = self._open(value, "refresh")
        else:
            raise LinkError("unsupported_grant_type", "use authorization_code or refresh_token")
        return self.tokens(Linked(data["u"], data["r"], data.get("c")))

    def who(self, access_token: str) -> Linked:
        data = self._open(access_token, "access")
        return Linked(data["u"], data["r"], data.get("c"))
