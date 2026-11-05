"""Buying the positioning seat, through PayPal Checkout, when cover must fly in first.

Some cover is based at another airport: they fly in as passengers on a same-day flight, often
another airline's. Approving that plan means buying them a seat. The airline pays it with
PayPal, and a person always approves the payment in PayPal itself:

    approved plan --> seat TO PAY (PayPal order created) --payer approves in PayPal-->
        captured by the server --> PAID          (or CANCELLED, FAILED, REFUNDED)

* The order is created by the server when the plan is approved, so the amount, the flight and
  the payee come from the engine and the rate card, not from the browser.
* The approver pays it with the PayPal button on the Crew pay page, or the assistant hands them
  the order's approve link. Either way, the server captures the order only after PayPal says the
  payer approved it, and webhooks (``CHECKOUT.ORDER.APPROVED``, ``PAYMENT.CAPTURE.*``) and the
  background status check catch up if the browser closes halfway.
* ``PayPal-Request-Id`` is the seat id on create and capture, so a retry never buys two seats.
* Without PayPal credentials the same steps run in **simulated** mode.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from crewpay.ops.notify import DESK, Notice
from crewpay.pay.paypal import PayPalClient, PayPalError

KIND = "seat"


class SeatStatus(StrEnum):
    TO_PAY = "to pay"
    PENDING = "pending"
    """Captured, but PayPal is still clearing the money."""
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


OPEN = frozenset({SeatStatus.TO_PAY, SeatStatus.PENDING})

CAPTURE_STATUS = {
    "COMPLETED": SeatStatus.PAID,
    "PENDING": SeatStatus.PENDING,
    "DECLINED": SeatStatus.FAILED,
    "FAILED": SeatStatus.FAILED,
    "REFUNDED": SeatStatus.REFUNDED,
    "PARTIALLY_REFUNDED": SeatStatus.REFUNDED,
}


class SeatError(ValueError):
    """A request the seat cannot take in its current state."""


@dataclass(frozen=True, slots=True)
class SeatConfig:
    currency: str = "USD"
    fx_inr_per_usd: float = 84.0
    seller: str = ""
    """The sandbox business account paid for the seat; empty pays the app's own account."""
    public_url: str = ""
    """The console's address, so PayPal can send a payer back to it after an approve link."""
    airlines: dict[str, str] = field(default_factory=dict)
    """Airline code to name, for the order's description."""


class Seats:
    def __init__(
        self,
        docs: Any,
        send: Callable[[Notice], None],
        paypal: PayPalClient | None,
        config: SeatConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.docs = docs
        self.send = send
        self.paypal = paypal
        self.config = config or SeatConfig()
        self.clock = clock
        self._lock = threading.RLock()

    @property
    def mode(self) -> str:
        return "paypal" if self.paypal is not None else "simulated"

    # -- reading ----------------------------------------------------------------------------

    def get(self, seat_id: str) -> dict[str, Any]:
        found = self.docs.get_doc(KIND, seat_id)
        if found is None:
            raise SeatError(f"no seat {seat_id}")
        return found

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.docs.docs(KIND), key=lambda s: s["created_at"], reverse=True)

    def for_plan(self, plan_id: str) -> dict[str, Any] | None:
        return next((s for s in self.docs.docs(KIND) if s["plan_id"] == plan_id), None)

    # -- the approved plan ------------------------------------------------------------------

    def on_plan(self, plan: Any, inr: int) -> dict[str, Any] | None:
        """An approved plan that positions its crew creates the seat and its PayPal order."""
        where = getattr(plan, "positioning", None)
        if str(plan.status) != "approved" or not where or self.for_plan(plan.plan_id):
            return None
        from crewpay.pay.payroll import to_usd

        now = self.clock()
        airline = self.config.airlines.get(where["airline"], where["airline"])
        seat = {
            "seat_id": f"SEAT-{plan.plan_id.removeprefix('PL-')}",
            "plan_id": plan.plan_id,
            "trip_id": plan.trip_id,
            "crew_id": plan.crew_id,
            "name": plan.name,
            "approved_by": plan.decided_by,
            "flight_no": where["flight_no"],
            "leg_id": where["leg_id"],
            "airline": airline,
            "origin": where["origin"],
            "destination": where["destination"],
            "departs": where["departs"],
            "inr": inr,
            "fx_inr_per_usd": self.config.fx_inr_per_usd,
            "amount": to_usd(inr, self.config.fx_inr_per_usd),
            "currency": self.config.currency,
            "payee": self.config.seller or None,
            "mode": self.mode,
            "status": str(SeatStatus.TO_PAY),
            "order_id": None,
            "approve_url": None,
            "capture_id": None,
            "paypal_status": None,
            "paid_by": None,
            "error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "history": [],
        }
        self._note(seat, seat["status"], plan.decided_by or "system", "plan approved")
        with self._lock:
            self._order(seat)
        if seat["status"] != SeatStatus.TO_PAY:
            return seat
        self.send(
            Notice(
                to=DESK,
                subject=f"Seat to pay: {seat['flight_no']} for {seat['name']}",
                body=f"{seat['name']} positions on {seat['flight_no']} ({seat['origin']}-"
                f"{seat['destination']}) to cover {seat['trip_id']}. Pay the {airline} seat, "
                f"{seat['amount']} {seat['currency']}, with PayPal on the Crew pay page.",
                at=now,
                ref=seat["seat_id"],
            )
        )
        return seat

    def _order(self, seat: dict[str, Any]) -> None:
        """Create the PayPal order for the seat (or a simulated one)."""
        if self.paypal is None:
            seat["order_id"] = f"SIM-{uuid.uuid4().hex[:12].upper()}"
            seat["paypal_status"] = "CREATED"
            self._save(seat)
            return
        money = {"currency_code": seat["currency"], "value": seat["amount"]}
        unit: dict[str, Any] = {
            "reference_id": seat["seat_id"],
            "custom_id": seat["plan_id"],
            "invoice_id": seat["seat_id"],
            "description": f"Positioning seat {seat['flight_no']} {seat['origin']}-"
            f"{seat['destination']} for {seat['name']} ({seat['trip_id']})"[:127],
            "soft_descriptor": "CREWPAY SEAT",
            "amount": {**money, "breakdown": {"item_total": money}},
            "items": [
                {
                    "name": f"Seat on {seat['flight_no']} {seat['origin']}-{seat['destination']}",
                    "description": f"{seat['airline']}, crew positioning",
                    "quantity": "1",
                    "unit_amount": money,
                    "category": "DIGITAL_GOODS",
                }
            ],
        }
        if seat["payee"]:
            unit["payee"] = {"email_address": seat["payee"]}
        context: dict[str, Any] = {
            "brand_name": "CrewPay",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
        }
        if self.config.public_url:
            back = f"{self.config.public_url}/pay/?seat={seat['seat_id']}"
            context["return_url"] = back
            context["cancel_url"] = back
        body = {
            "intent": "CAPTURE",
            "purchase_units": [unit],
            "payment_source": {"paypal": {"experience_context": context}},
        }
        try:
            order = self.paypal.create_order(
                body, request_id=f"{seat['seat_id']}-order-{len(seat['history'])}"
            )
        except PayPalError as err:
            seat["error"] = str(err)
            self._move(seat, SeatStatus.FAILED, "paypal", f"order not created: {err}")
            self._tell_desk(seat, "Seat order failed", str(err))
            return
        seat["order_id"] = order.get("id")
        seat["paypal_status"] = order.get("status")
        seat["approve_url"] = next(
            (
                link["href"]
                for link in order.get("links", [])
                if link.get("rel") in ("payer-action", "approve")
            ),
            None,
        )
        seat["error"] = None
        self._note(seat, seat["status"], "paypal", f"order {seat['order_id']}")
        self._save(seat)

    # -- paying -----------------------------------------------------------------------------

    def capture(self, seat_id: str, by: str, order_id: str | None = None) -> dict[str, Any]:
        """Take the payment the payer approved in PayPal. In simulated mode, pay it."""
        with self._lock:
            seat = self.get(seat_id)
            if order_id and order_id != seat["order_id"]:
                raise SeatError(f"order {order_id} is not the order for {seat_id}")
            if seat["status"] != SeatStatus.TO_PAY:
                if seat["status"] in (SeatStatus.PAID, SeatStatus.PENDING):
                    return seat
                raise SeatError(f"{seat_id} is {seat['status']}, not waiting to be paid")
            seat["paid_by"] = by
            if self.paypal is None:
                seat["capture_id"] = f"SIMC-{uuid.uuid4().hex[:12].upper()}"
                self._settle(seat, "COMPLETED", by, "simulated payment")
                return seat
            try:
                reply = self.paypal.capture_order(
                    seat["order_id"], request_id=f"{seat['seat_id']}-capture"
                )
            except PayPalError as err:
                issue = _issue(err)
                if issue == "ORDER_NOT_APPROVED":
                    raise SeatError("the payer has not approved this order in PayPal yet") from err
                if issue == "ORDER_ALREADY_CAPTURED":
                    self.sync(seat_id)
                    return self.get(seat_id)
                seat["error"] = str(err)
                self._save(seat)
                raise SeatError(f"PayPal would not take the payment: {err}") from err
            self._from_order(seat, reply, by, "captured")
            return seat

    def cancel(self, seat_id: str, by: str, reason: str) -> dict[str, Any]:
        with self._lock:
            seat = self.get(seat_id)
            if seat["status"] not in (SeatStatus.TO_PAY, SeatStatus.FAILED):
                raise SeatError(f"only an unpaid seat can be cancelled, not a {seat['status']} one")
            self._move(seat, SeatStatus.CANCELLED, by, reason)
            return seat

    # -- keeping up with PayPal -------------------------------------------------------------

    def sync(self, seat_id: str | None = None) -> int:
        """Ask PayPal about each open order: capture one the payer approved elsewhere (through
        the approve link), settle a pending capture, and make again an order PayPal failed to
        create. Returns how many changed."""
        if self.paypal is None:
            return 0
        changed = 0
        with self._lock:
            rows = [self.get(seat_id)] if seat_id else self.docs.docs(KIND)
            for seat in rows:
                if seat["status"] == SeatStatus.FAILED and not seat.get("order_id"):
                    self._move(seat, SeatStatus.TO_PAY, "payroll", "order again")
                    self._order(seat)
                    changed += int(seat["status"] == SeatStatus.TO_PAY)
                    continue
                if seat["status"] not in OPEN or not seat.get("order_id"):
                    continue
                before = seat["status"]
                try:
                    order = self.paypal.get_order(seat["order_id"])
                except PayPalError as err:
                    seat["error"] = str(err)
                    self._save(seat)
                    continue
                if order.get("status") == "APPROVED" and seat["status"] == SeatStatus.TO_PAY:
                    try:
                        self.capture(seat["seat_id"], seat.get("paid_by") or "payer")
                    except SeatError:
                        continue
                else:
                    self._from_order(seat, order, "paypal", "status check")
                changed += int(self.get(seat["seat_id"])["status"] != before)
        return changed

    def webhook(self, event: dict[str, Any]) -> str | None:
        """Apply a Checkout or capture webhook. Returns the seat it concerned."""
        kind = event.get("event_type", "")
        resource = event.get("resource") or {}
        if kind.startswith("CHECKOUT.ORDER."):
            order_id = resource.get("id")
        elif kind.startswith("PAYMENT.CAPTURE."):
            order_id = ((resource.get("supplementary_data") or {}).get("related_ids") or {}).get(
                "order_id"
            )
        else:
            return None
        with self._lock:
            seat = next((s for s in self.docs.docs(KIND) if s.get("order_id") == order_id), None)
            if seat is None:
                return None
            if kind == "CHECKOUT.ORDER.APPROVED":
                if seat["status"] == SeatStatus.TO_PAY:
                    with contextlib.suppress(SeatError):
                        self.capture(seat["seat_id"], seat.get("paid_by") or "payer")
            elif kind.startswith("PAYMENT.CAPTURE."):
                seat["capture_id"] = resource.get("id") or seat.get("capture_id")
                status = resource.get("status") or kind.rsplit(".", 1)[-1]
                self._settle(seat, status, "paypal", f"webhook {kind}")
            return seat["seat_id"]

    # -- bookkeeping ------------------------------------------------------------------------

    def _from_order(self, seat: dict[str, Any], order: dict[str, Any], by: str, why: str) -> None:
        seat["paypal_status"] = order.get("status") or seat.get("paypal_status")
        captures = [
            c
            for unit in order.get("purchase_units", [])
            for c in (unit.get("payments") or {}).get("captures", [])
        ]
        if captures:
            seat["capture_id"] = captures[0].get("id")
            seat["error"] = None
            self._settle(seat, captures[0].get("status", ""), by, why)
        elif order.get("status") == "VOIDED":
            self._move(seat, SeatStatus.CANCELLED, "paypal", "order voided")
        else:
            self._save(seat)

    def _settle(self, seat: dict[str, Any], capture_status: str, by: str, why: str) -> None:
        status = CAPTURE_STATUS.get(capture_status.upper())
        seat["capture_status"] = capture_status
        if status is None or status == seat["status"]:
            self._save(seat)
            return
        self._move(seat, status, by, why)
        if status is SeatStatus.PAID:
            self.send(
                Notice(
                    to=seat["crew_id"],
                    subject=f"Seat booked: {seat['flight_no']} to {seat['destination']}",
                    body=f"{seat['name']}, your seat on {seat['flight_no']} ({seat['origin']}-"
                    f"{seat['destination']}) to cover {seat['trip_id']} is paid"
                    f"{' (simulated)' if seat['mode'] == 'simulated' else ''}. "
                    "Report at the positioning flight's gate.",
                    at=self.clock(),
                    ref=seat["seat_id"],
                )
            )
            self._tell_desk(seat, "Seat paid", f"{seat['amount']} {seat['currency']} by {by}")
        elif status in (SeatStatus.FAILED, SeatStatus.REFUNDED):
            self._tell_desk(seat, f"Seat {status}", why)

    def _tell_desk(self, seat: dict[str, Any], what: str, detail: str) -> None:
        self.send(
            Notice(
                to=DESK,
                subject=f"{what}: {seat['seat_id']}",
                body=f"{seat['flight_no']} for {seat['name']} ({seat['trip_id']}): {detail}",
                at=self.clock(),
                ref=seat["seat_id"],
            )
        )

    def _move(self, seat: dict[str, Any], status: SeatStatus, by: str, note: str) -> None:
        seat["status"] = str(status)
        self._note(seat, str(status), by, note)
        self._save(seat)

    def _note(self, seat: dict[str, Any], status: str, by: str, note: str) -> None:
        seat["history"].append(
            {"at": self.clock().isoformat(), "status": status, "by": by, "note": note}
        )
        seat["updated_at"] = self.clock().isoformat()

    def _save(self, seat: dict[str, Any]) -> None:
        self.docs.put_doc(KIND, seat["seat_id"], seat)


def _issue(err: PayPalError) -> str:
    body = err.body if isinstance(err.body, dict) else {}
    details = body.get("details") or [{}]
    return details[0].get("issue", "") or body.get("name", "")
