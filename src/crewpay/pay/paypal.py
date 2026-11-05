"""The PayPal REST API, sandbox only: OAuth, Payouts and webhook verification.

    POST /v1/oauth2/token                                   client credentials -> access token
    POST /v1/payments/payouts                               one batch per approved plan
    GET  /v1/payments/payouts/{batch_id}                    the batch and each item's status
    POST /v1/payments/payouts-item/{item_id}/cancel         take back an unclaimed payment
    POST /v1/notifications/verify-webhook-signature         is this event really from PayPal?

Every request that moves money carries a ``PayPal-Request-Id``, and every batch a
``sender_batch_id`` named after the plan it pays, so a retry after a timeout can never pay
twice: PayPal answers the repeat with the batch it already made.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from crewpay.settings import Settings

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class PayPalError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body

    @property
    def name(self) -> str:
        """PayPal's error name, such as ``USER_BUSINESS_ERROR``."""
        return (self.body or {}).get("name", "") if isinstance(self.body, dict) else ""


class PayPalUnavailable(PayPalError):
    """No sandbox credentials are configured."""


@dataclass
class PayPalClient:
    client_id: str
    client_secret: str
    base_url: str
    http: httpx.Client | None = None
    timeout: float = 30.0
    retries: int = 2
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, config: Settings) -> PayPalClient | None:
        if not config.has_paypal:
            return None
        return cls(config.paypal_client_id, config.paypal_client_secret, config.paypal_base_url)

    # -- transport --------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        return self.http or httpx.Client(timeout=self.timeout)

    def token(self) -> str:
        with self._lock:
            if self._token and self._token[1] > time.monotonic() + 60:
                return self._token[0]
            http = self._client()
            try:
                response = http.post(
                    f"{self.base_url}/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.client_secret),
                    headers={"Accept": "application/json"},
                )
            except httpx.HTTPError as err:
                raise PayPalError(f"PayPal unreachable: {err}") from err
            finally:
                if self.http is None:
                    http.close()
            if response.is_error:
                raise PayPalError(
                    f"PayPal refused the credentials ({response.status_code})",
                    response.status_code,
                    _body(response),
                )
            body = response.json()
            self._token = (body["access_token"], time.monotonic() + int(body.get("expires_in", 0)))
            return self._token[0]

    def request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        http = self._client()
        try:
            for attempt in range(1, self.retries + 2):
                last = attempt > self.retries
                try:
                    response = http.request(
                        method, f"{self.base_url}{path}", json=json, headers=headers
                    )
                except httpx.TransportError as err:
                    if last:
                        raise PayPalError(f"PayPal unreachable: {err}") from err
                    self.sleep(2 ** (attempt - 1))
                    continue
                if response.status_code in RETRY_STATUSES and not last:
                    self.sleep(2 ** (attempt - 1))
                    continue
                if response.status_code == 401:
                    self._token = None
                if response.is_error:
                    body = _body(response)
                    detail = body.get("message") if isinstance(body, dict) else None
                    raise PayPalError(
                        f"PayPal returned {response.status_code}"
                        + (f": {detail}" if detail else ""),
                        response.status_code,
                        body,
                    )
                return response.json() if response.content else {}
        finally:
            if self.http is None:
                http.close()
        raise AssertionError("unreachable")

    # -- payouts ----------------------------------------------------------------------------

    def create_payout(
        self, sender_batch_id: str, items: list[dict[str, Any]], subject: str, message: str
    ) -> dict[str, Any]:
        """Send one batch. ``items`` are PayPal payout items (receiver, amount, note,
        sender_item_id). Returns the batch header with its ``payout_batch_id``."""
        body = {
            "sender_batch_header": {
                "sender_batch_id": sender_batch_id,
                "email_subject": subject,
                "email_message": message,
            },
            "items": items,
        }
        return self.request("POST", "/v1/payments/payouts", body, request_id=sender_batch_id)

    def get_payout(self, payout_batch_id: str) -> dict[str, Any]:
        return self.request("GET", f"/v1/payments/payouts/{payout_batch_id}?fields=items")

    def cancel_item(self, payout_item_id: str) -> dict[str, Any]:
        return self.request("POST", f"/v1/payments/payouts-item/{payout_item_id}/cancel")

    # -- webhooks ---------------------------------------------------------------------------

    def verify_webhook(
        self, headers: dict[str, str], event: dict[str, Any], webhook_id: str
    ) -> bool:
        h = {k.lower(): v for k, v in headers.items()}
        body = {
            "auth_algo": h.get("paypal-auth-algo", ""),
            "cert_url": h.get("paypal-cert-url", ""),
            "transmission_id": h.get("paypal-transmission-id", ""),
            "transmission_sig": h.get("paypal-transmission-sig", ""),
            "transmission_time": h.get("paypal-transmission-time", ""),
            "webhook_id": webhook_id,
            "webhook_event": event,
        }
        reply = self.request("POST", "/v1/notifications/verify-webhook-signature", body)
        return reply.get("verification_status") == "SUCCESS"


def _body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:2000]
