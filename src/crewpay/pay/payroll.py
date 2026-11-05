"""Paying the crew who step in, the moment their cover is approved.

When an approver approves a plan, the crew member it puts in the seat is owed what the engine
costed for them: the callout, their service allowance and their travel to the airport. Those
figures come from the rules engine and the rate card, never from a model. The payroll turns
them into one PayPal payout per plan:

    approved plan --> payment QUEUED --> SENT (PayPal batch) --> PAID
                          |                                  `-> UNCLAIMED / FAILED / RETURNED
                          `--> HELD (over the cap) --release by a second approver--> QUEUED

* The amount is fixed in rupees (the cost of record) and paid in US dollars at the demo rate
  ``CREWPAY_FX_INR_PER_USD``; both are kept on the payment.
* A payment over ``CREWPAY_PAY_CAP`` waits for a second approver, never the one who approved
  the plan, so no single person can both change the roster and send a large payment.
* The PayPal batch is named after the payment (``sender_batch_id``), so sending twice can
  never pay twice.
* Without PayPal credentials the same steps run in **simulated** mode, marked on every
  payment, so the demo works on any laptop.

What the airline pays others (a positioning seat, delay costs) is listed with the payment as
airline cost, not paid to the crew member. A positioning seat is bought separately, through
PayPal Checkout (see :mod:`crewpay.pay.seats`).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Protocol

from crewpay.ops.notify import DESK, Notice, Outbox
from crewpay.pay.paypal import PayPalClient, PayPalError
from crewpay.pay.seats import SeatConfig, Seats

CREW_PAY = ("callout", "service allowance", "travel allowance")
"""The cost lines that are the crew member's pay; the rest are airline costs."""
KIND = "payment"


class PayStatus(StrEnum):
    HELD = "held"
    QUEUED = "queued"
    SENT = "sent"
    PAID = "paid"
    UNCLAIMED = "unclaimed"
    FAILED = "failed"
    RETURNED = "returned"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


FINAL = frozenset({PayStatus.PAID, PayStatus.FAILED, PayStatus.RETURNED, PayStatus.CANCELLED})
WATCHED = frozenset({PayStatus.SENT, PayStatus.UNCLAIMED, PayStatus.BLOCKED})
"""Sent to PayPal and still able to change."""

PAYPAL_STATUS = {
    "SUCCESS": PayStatus.PAID,
    "PENDING": PayStatus.SENT,
    "ONHOLD": PayStatus.SENT,
    "NEW": PayStatus.SENT,
    "UNCLAIMED": PayStatus.UNCLAIMED,
    "FAILED": PayStatus.FAILED,
    "DENIED": PayStatus.FAILED,
    "RETURNED": PayStatus.RETURNED,
    "REFUNDED": PayStatus.RETURNED,
    "REVERSED": PayStatus.RETURNED,
    "BLOCKED": PayStatus.BLOCKED,
    "CANCELED": PayStatus.CANCELLED,
}


class PayError(ValueError):
    """A request the payment cannot take in its current state."""


class Docs(Protocol):
    def put_doc(self, kind: str, key: str, body: dict) -> None: ...
    def get_doc(self, kind: str, key: str) -> dict | None: ...
    def docs(self, kind: str) -> list[dict]: ...


@dataclass(frozen=True, slots=True)
class PayConfig:
    currency: str = "USD"
    fx_inr_per_usd: float = 84.0
    cap: float = 2500.0
    """Most one payment may be in ``currency`` before a second approver must release it."""
    receiver: str = ""
    """A sandbox personal account every payout goes to; empty pays each crew member's own
    address, which PayPal keeps as unclaimed until someone signs up with it."""
    seller: str = ""
    """The sandbox business account paid for positioning seats (empty: the app's own)."""
    public_url: str = ""
    airlines: dict[str, str] = field(default_factory=dict)


def to_usd(inr: int, rate: float) -> str:
    return str((Decimal(inr) / Decimal(str(rate))).quantize(Decimal("0.01"), ROUND_HALF_UP))


def crew_address(crew_id: str) -> str:
    return f"{crew_id.lower()}@crew.crewpay.example"


class Payroll:
    def __init__(
        self,
        docs: Docs,
        outbox: Outbox,
        paypal: PayPalClient | None,
        config: PayConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.docs = docs
        self.outbox = outbox
        self.paypal = paypal
        self.config = config or PayConfig()
        self.clock = clock
        self._lock = threading.RLock()
        self.seats = Seats(
            docs,
            lambda notice: self.outbox.send(notice),
            paypal,
            SeatConfig(
                currency=self.config.currency,
                fx_inr_per_usd=self.config.fx_inr_per_usd,
                seller=self.config.seller,
                public_url=self.config.public_url,
                airlines=self.config.airlines,
            ),
            clock,
        )

    @property
    def mode(self) -> str:
        return "paypal" if self.paypal is not None else "simulated"

    # -- reading ----------------------------------------------------------------------------

    def get(self, payment_id: str) -> dict[str, Any]:
        found = self.docs.get_doc(KIND, payment_id)
        if found is None:
            raise PayError(f"no payment {payment_id}")
        return found

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.docs.docs(KIND), key=lambda p: p["created_at"], reverse=True)

    def for_plan(self, plan_id: str) -> dict[str, Any] | None:
        return next((p for p in self.docs.docs(KIND) if p["plan_id"] == plan_id), None)

    def totals(self) -> dict[str, Any]:
        rows = self.all()
        by: dict[str, dict[str, Any]] = {}
        for p in rows:
            t = by.setdefault(p["status"], {"count": 0, "amount": Decimal(0), "inr": 0})
            t["count"] += 1
            t["amount"] += Decimal(p["amount"])
            t["inr"] += p["total_inr"]
        return {
            "currency": self.config.currency,
            "mode": self.mode,
            "count": len(rows),
            "by_status": {k: {**v, "amount": str(v["amount"])} for k, v in by.items()},
        }

    # -- the plan's money -------------------------------------------------------------------

    def on_plan(self, plan: Any) -> dict[str, Any] | None:
        """Called for every decided plan; an approved one creates and sends its payment."""
        if str(plan.status) != "approved" or self.for_plan(plan.plan_id) is not None:
            return None
        breakdown: dict[str, int] = dict(getattr(plan, "breakdown", {}) or {})
        if breakdown.get("positioning"):
            self.seats.on_plan(plan, breakdown["positioning"])
        lines = [{"label": k, "inr": v} for k, v in breakdown.items() if k in CREW_PAY and v]
        airline = [{"label": k, "inr": v} for k, v in breakdown.items() if k not in CREW_PAY and v]
        total = sum(line["inr"] for line in lines)
        if total <= 0:
            return None
        now = self.clock()
        amount = to_usd(total, self.config.fx_inr_per_usd)
        held = Decimal(amount) > Decimal(str(self.config.cap))
        payment = {
            "payment_id": f"PAY-{plan.plan_id.removeprefix('PL-')}",
            "plan_id": plan.plan_id,
            "trip_id": plan.trip_id,
            "crew_id": plan.crew_id,
            "name": plan.name,
            "rank": str(plan.rank),
            "replacing": plan.replacing,
            "submitted_by": plan.submitted_by,
            "approved_by": plan.decided_by,
            "lines": lines,
            "airline_costs": airline,
            "total_inr": total,
            "fx_inr_per_usd": self.config.fx_inr_per_usd,
            "amount": amount,
            "currency": self.config.currency,
            "receiver": self.config.receiver or crew_address(plan.crew_id),
            "mode": self.mode,
            "status": str(PayStatus.HELD if held else PayStatus.QUEUED),
            "batch_id": None,
            "item_id": None,
            "transaction_id": None,
            "paypal_status": None,
            "error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "history": [],
        }
        self._note(payment, payment["status"], plan.decided_by or "system", "plan approved")
        if held:
            self._save(payment)
            self.outbox.send(
                Notice(
                    to=DESK,
                    subject=f"Payment held: {payment['payment_id']}",
                    body=f"{plan.name} is owed {amount} {self.config.currency} for "
                    f"{plan.trip_id}, over the {self.config.cap:g} cap. A second approver, not "
                    f"{plan.decided_by}, must release it.",
                    at=now,
                    ref=payment["payment_id"],
                )
            )
            return payment
        return self.send(payment)

    def release(self, payment_id: str, by: str) -> dict[str, Any]:
        with self._lock:
            payment = self.get(payment_id)
            if payment["status"] != PayStatus.HELD:
                raise PayError(f"{payment_id} is {payment['status']}, not held")
            if by == payment["approved_by"]:
                raise PayError("the approver of the plan cannot also release its payment")
            payment["released_by"] = by
            self._move(payment, PayStatus.QUEUED, by, "released over the cap")
            return self.send(payment)

    def send(self, payment: dict[str, Any]) -> dict[str, Any]:
        """Send a queued payment to PayPal (or simulate it). Safe to call again after a
        failure: the batch id is the payment id."""
        with self._lock:
            if payment["status"] not in (PayStatus.QUEUED, PayStatus.FAILED):
                raise PayError(f"{payment['payment_id']} is {payment['status']}")
            if self.paypal is None:
                payment["batch_id"] = f"SIM-{uuid.uuid4().hex[:10].upper()}"
                payment["item_id"] = f"SIMI-{uuid.uuid4().hex[:10].upper()}"
                payment["paypal_status"] = "PENDING"
                self._move(payment, PayStatus.SENT, "payroll", "simulated payout")
                return payment
            item = {
                "recipient_type": "EMAIL",
                "receiver": payment["receiver"],
                "amount": {"value": payment["amount"], "currency": payment["currency"]},
                "sender_item_id": payment["payment_id"],
                "note": f"CrewPay: {payment['rank']} cover on {payment['trip_id']} "
                f"(INR {payment['total_inr']:,} at {payment['fx_inr_per_usd']:g}/USD)",
            }
            try:
                reply = self.paypal.create_payout(
                    payment["payment_id"],
                    [item],
                    subject=f"CrewPay: you have been paid for {payment['trip_id']}",
                    message="Thank you for covering at short notice.",
                )
            except PayPalError as err:
                payment["error"] = str(err)
                self._move(payment, PayStatus.FAILED, "paypal", str(err))
                self._tell_desk(payment, "Payment failed", str(err))
                return payment
            header = reply.get("batch_header", {})
            payment["batch_id"] = header.get("payout_batch_id")
            payment["paypal_status"] = header.get("batch_status")
            payment["error"] = None
            self._move(payment, PayStatus.SENT, "paypal", f"batch {payment['batch_id']}")
            return payment

    def cancel(self, payment_id: str, by: str, reason: str) -> dict[str, Any]:
        """Take back a payment nobody has claimed, or one still held or queued."""
        with self._lock:
            payment = self.get(payment_id)
            status = PayStatus(payment["status"])
            if status in (PayStatus.HELD, PayStatus.QUEUED):
                self._move(payment, PayStatus.CANCELLED, by, reason)
                return payment
            if status is not PayStatus.UNCLAIMED:
                raise PayError(
                    f"only an unclaimed, held or queued payment can be cancelled, "
                    f"not a {status} one"
                )
            if self.paypal is not None and payment.get("item_id"):
                try:
                    self.paypal.cancel_item(payment["item_id"])
                except PayPalError as err:
                    raise PayError(f"PayPal would not cancel it: {err}") from err
            self._move(payment, PayStatus.CANCELLED, by, reason)
            return payment

    # -- keeping up with PayPal -------------------------------------------------------------

    def sync(self, payment_id: str | None = None) -> int:
        """Ask PayPal how each sent payment, and each open seat order, stands. Returns how many
        changed."""
        changed = 0 if payment_id else self.seats.sync()
        with self._lock:
            rows = [self.get(payment_id)] if payment_id else self.docs.docs(KIND)
            for payment in rows:
                if payment["status"] not in WATCHED:
                    continue
                if self.paypal is None:
                    payment["paypal_status"] = "SUCCESS"
                    payment["transaction_id"] = f"SIMT-{uuid.uuid4().hex[:12].upper()}"
                    changed += self._apply(payment, "SUCCESS", "simulated settlement")
                    continue
                try:
                    batch = self.paypal.get_payout(payment["batch_id"])
                except PayPalError as err:
                    payment["error"] = str(err)
                    self._save(payment)
                    continue
                items = batch.get("items") or []
                item = next(
                    (
                        i
                        for i in items
                        if (i.get("payout_item") or {}).get("sender_item_id")
                        == payment["payment_id"]
                    ),
                    items[0] if items else None,
                )
                if item is None:
                    continue
                payment["item_id"] = item.get("payout_item_id") or payment.get("item_id")
                payment["transaction_id"] = item.get("transaction_id") or payment.get(
                    "transaction_id"
                )
                fee = (item.get("payout_item_fee") or {}).get("value")
                if fee is not None:
                    payment["fee"] = fee
                changed += self._apply(payment, item.get("transaction_status", ""), "status check")
        return changed

    def webhook(self, event: dict[str, Any]) -> str | None:
        """Apply a PayPal webhook event about a payout item. Returns the payment it moved."""
        kind = event.get("event_type", "")
        if kind.startswith(("CHECKOUT.ORDER.", "PAYMENT.CAPTURE.")):
            return self.seats.webhook(event)
        if not kind.startswith("PAYMENT.PAYOUTS-ITEM."):
            return None
        resource = event.get("resource") or {}
        item_id = resource.get("payout_item_id")
        sender_item = (resource.get("payout_item") or {}).get("sender_item_id")
        with self._lock:
            payment = next(
                (
                    p
                    for p in self.docs.docs(KIND)
                    if p["payment_id"] == sender_item or (item_id and p.get("item_id") == item_id)
                ),
                None,
            )
            if payment is None:
                return None
            payment["item_id"] = item_id or payment.get("item_id")
            payment["transaction_id"] = resource.get("transaction_id") or payment.get(
                "transaction_id"
            )
            status = resource.get("transaction_status") or kind.rsplit(".", 1)[-1]
            self._apply(payment, status, f"webhook {kind}")
            return payment["payment_id"]

    # -- bookkeeping ------------------------------------------------------------------------

    def _apply(self, payment: dict[str, Any], paypal_status: str, why: str) -> int:
        payment["paypal_status"] = paypal_status
        status = PAYPAL_STATUS.get(paypal_status.upper())
        if status is None or status == payment["status"]:
            self._save(payment)
            return 0
        self._move(payment, status, "paypal", why)
        if status is PayStatus.PAID:
            self.outbox.send(
                Notice(
                    to=payment["crew_id"],
                    subject=f"Paid: {payment['amount']} {payment['currency']} for "
                    f"{payment['trip_id']}",
                    body=f"{payment['name']}, your cover on {payment['trip_id']} has been paid "
                    f"to {payment['receiver']} by PayPal"
                    f"{' (simulated)' if payment['mode'] == 'simulated' else ''}: "
                    f"INR {payment['total_inr']:,} "
                    f"({', '.join(f'{x["label"]} {x["inr"]:,}' for x in payment['lines'])}).",
                    at=self.clock(),
                    ref=payment["payment_id"],
                )
            )
        elif status in (PayStatus.FAILED, PayStatus.RETURNED, PayStatus.BLOCKED):
            self._tell_desk(payment, f"Payment {status}", why)
        return 1

    def _tell_desk(self, payment: dict[str, Any], what: str, detail: str) -> None:
        self.outbox.send(
            Notice(
                to=DESK,
                subject=f"{what}: {payment['payment_id']}",
                body=f"{payment['name']} ({payment['crew_id']}) for {payment['trip_id']}: {detail}",
                at=self.clock(),
                ref=payment["payment_id"],
            )
        )

    def _move(self, payment: dict[str, Any], status: PayStatus, by: str, note: str) -> None:
        payment["status"] = str(status)
        self._note(payment, str(status), by, note)
        self._save(payment)

    def _note(self, payment: dict[str, Any], status: str, by: str, note: str) -> None:
        payment["history"].append(
            {"at": self.clock().isoformat(), "status": status, "by": by, "note": note}
        )
        payment["updated_at"] = self.clock().isoformat()

    def _save(self, payment: dict[str, Any]) -> None:
        self.docs.put_doc(KIND, payment["payment_id"], payment)
