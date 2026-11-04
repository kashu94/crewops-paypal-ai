"""Desktop alerts by Web Push, so a controller hears about a callout with the tab closed.

A browser that allows notifications subscribes once (``/api/push/subscribe``) and is kept
against the signed-in person. When a notice is sent, every browser of the people it is for
gets a short alert: the desk's and the supervisor's notices go to desk roles, a crew
member's to that crew member.

Messages are encrypted for the browser (RFC 8291, ``aes128gcm``), so the push service in
between cannot read them, and signed with the server's VAPID key (RFC 8292). The key pair is
made on first use and kept in the store. Only the browser makers' push services are called,
so a subscription cannot point the server at an address of someone's choosing.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crewpay.ops.notify import DESK, SUPERVISOR, Notice

PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)
CONTACT = "mailto:crewpay-alerts@example.com"
DESK_ROLES = frozenset({"controller", "approver", "auditor", "admin", "super_admin"})
TIMEOUT = 4.0


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed(endpoint: str) -> bool:
    url = urlparse(endpoint)
    host = url.hostname or ""
    return url.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    salt: bytes | None = None,
    server_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """The request body for one message (RFC 8291 with the RFC 8188 header)."""
    ua_public = unb64u(p256dh)
    secret = unb64u(auth)
    salt = salt or os.urandom(16)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = server_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), peer)
    ikm = _hmac(_hmac(secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public + body


class Push:
    def __init__(self, docs: Any, post: Any = None) -> None:
        self.docs = docs
        self._post = post or self._http_post
        self._key: ec.EllipticCurvePrivateKey | None = None

    # -- keys -------------------------------------------------------------------------------

    def _private(self) -> ec.EllipticCurvePrivateKey:
        if self._key is None:
            found = self.docs.get_doc("config", "vapid")
            if found is None:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ).decode()
                self.docs.put_doc("config", "vapid", {"pem": pem})
                found = {"pem": pem}
            loaded = serialization.load_pem_private_key(found["pem"].encode(), None)
            assert isinstance(loaded, ec.EllipticCurvePrivateKey)
            self._key = loaded
        return self._key

    def public_key(self) -> str:
        return b64u(
            self._private()
            .public_key()
            .public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        )

    def _vapid(self, endpoint: str) -> str:
        url = urlparse(endpoint)
        token = jwt.encode(
            {
                "aud": f"{url.scheme}://{url.netloc}",
                "exp": int(time.time()) + 12 * 3600,
                "sub": CONTACT,
            },
            self._private(),
            algorithm="ES256",
        )
        return f"vapid t={token}, k={self.public_key()}"

    # -- subscriptions ----------------------------------------------------------------------

    @staticmethod
    def _id(endpoint: str) -> str:
        return hashlib.sha256(endpoint.encode()).hexdigest()[:32]

    def subscribe(self, subscription: dict, user: str, role: str, crew_id: str | None) -> None:
        endpoint = subscription.get("endpoint", "")
        keys = subscription.get("keys") or {}
        if not allowed(endpoint):
            raise ValueError("that is not a browser push service")
        if not keys.get("p256dh") or not keys.get("auth"):
            raise ValueError("the subscription has no keys")
        self.docs.put_doc(
            "push",
            self._id(endpoint),
            {
                "endpoint": endpoint,
                "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
                "user": user,
                "role": role,
                "crew_id": crew_id,
            },
        )

    def unsubscribe(self, endpoint: str) -> None:
        self.docs.delete_doc("push", self._id(endpoint))

    def subscribed(self, user: str) -> bool:
        return any(s["user"] == user for s in self.docs.docs("push"))

    # -- sending ----------------------------------------------------------------------------

    @staticmethod
    def _http_post(url: str, body: bytes, headers: dict[str, str]) -> int:
        return httpx.post(url, content=body, headers=headers, timeout=TIMEOUT).status_code

    def _for(self, notice: Notice) -> list[dict]:
        subs = self.docs.docs("push")
        if notice.to in (DESK, SUPERVISOR):
            return [s for s in subs if s["role"] in DESK_ROLES]
        return [s for s in subs if s.get("crew_id") and s["crew_id"] == notice.to]

    def send(self, notice: Notice) -> int:
        """Alert every browser ``notice`` is for. Returns how many took it."""
        payload = json.dumps(
            {"title": notice.subject, "body": notice.body[:240], "tag": notice.ref, "url": "/"}
        ).encode()
        sent = 0
        for sub in self._for(notice):
            try:
                status = self._post(
                    sub["endpoint"],
                    encrypt(payload, sub["keys"]["p256dh"], sub["keys"]["auth"]),
                    {
                        "Authorization": self._vapid(sub["endpoint"]),
                        "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream",
                        "TTL": "600",
                        "Urgency": "high",
                    },
                )
            except httpx.HTTPError:
                continue
            if status in (404, 410):  # the browser dropped the subscription
                self.unsubscribe(sub["endpoint"])
            elif status < 300:
                sent += 1
        return sent


class PushingOutbox:
    """Keeps every notice like ``inner`` does, and alerts the browsers it is for."""

    def __init__(self, inner: Any, push: Push) -> None:
        self.inner, self.push = inner, push

    def send(self, notice: Notice) -> None:
        self.inner.send(notice)
        # An alert that fails to go must never lose the notice itself.
        with contextlib.suppress(Exception):
            self.push.send(notice)

    def sent(self, to: str | None = None) -> list[Notice]:
        return self.inner.sent(to)
