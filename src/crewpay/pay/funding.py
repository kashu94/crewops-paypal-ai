"""Confirming an approval with PayPal: the approver pays for the cover before it takes effect.

An approver who approves cover does not change the roster with that click alone. The approval
opens a PayPal payment for what the cover costs the airline, worked out by the rules engine and
the rate card: the crew member's callout, service and travel allowance, and the seat on the
positioning flight when they must fly in first. The approver pays it with PayPal, and only
when PayPal has taken the payment does the approval go through:

    approve --> plan CONFIRMING, payment TO PAY (PayPal order) --paid in PayPal--> captured
        --> plan judged once more --> APPROVED: roster changed, crew paid out, seat booked
                                  `-> STALE: nothing changes, the payment is refunded
    cancel --> payment CANCELLED, plan waits for a decision again

* The server creates the order, so the amount and the lines come from the engine, never from
  the browser, and it captures only an order PayPal says the payer approved.
* Before capturing, the plan is judged again; a plan whose trip moved on is not charged.
* ``PayPal-Request-Id`` on create, capture and refund, so a retry never charges twice.
* Webhooks (``CHECKOUT.ORDER.APPROVED``, ``PAYMENT.CAPTURE.*``) and the background status
  check finish a payment whose browser closed halfway.
* Only the approver who approved the plan confirms it. The agent can approve, but never
  confirm: that is always a person in PayPal.
* Without PayPal credentials the same steps run in **simulated** mode.
"""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from crewpay.ops.notify import DESK, Notice
from crewpay.pay.paypal import PayPalClient, PayPalError

KIND = "funding"
CREW_PAY = ("callout", "service allowance", "travel allowance")
SEAT = "positioning"
UNPAID_FOR = timedelta(hours=3)
"""An approval left unconfirmed this long goes back to waiting for a decision."""


class FundingStatus(StrEnum):
    TO_PAY = "to pay"
    PENDING = "pending"
    """Captured, but PayPal is still clearing the money."""
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


OPEN = frozenset({FundingStatus.TO_PAY, FundingStatus.PENDING})

CAPTURE_STATUS = {
    "COMPLETED": FundingStatus.PAID,
    "PENDING": FundingStatus.PENDING,
    "DECLINED": FundingStatus.FAILED,
    "FAILED": FundingStatus.FAILED,
    "REFUNDED": FundingStatus.REFUNDED,
    "PARTIALLY_REFUNDED": FundingStatus.REFUNDED,
}


class FundingError(ValueError):
    """A request the payment cannot take in its current state."""


@dataclass(frozen=True, slots=True)
class FundingConfig:
    currency: str = "USD"
    fx_inr_per_usd: float = 84.0
    public_url: str = ""
    """The console's address, so PayPal can send a payer back to it after an approve link."""
    airlines: dict[str, str] = field(default_factory=dict)


class Funding:
    def __init__(
        self,
        docs: Any,
        send: Callable[[Notice], None],
        paypal: PayPalClient | None,
        config: FundingConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.docs = docs
        self.send = send
        self.paypal = paypal
        self.config = config or FundingConfig()
        self.clock = clock
        self.plans: Any = None
        """The plan desk whose approvals this confirms (set by ``attach``)."""
        self._lock = threading.RLock()

    @property
    def mode(self) -> str:
        return "paypal" if self.paypal is not None else "simulated"

    def attach(self, plans: Any) -> None:
        """Make every approval on ``plans`` that costs money wait for PayPal.

        From here on the funding shares the plans' lock: an approval opens a funding while it
        holds the plans, and a capture checks and confirms the plan while it holds the funding,
        so two locks taken in opposite orders could leave both waiting for ever."""
        self.plans = plans
        self._lock = getattr(plans, "_lock", self._lock)
        plans.gate = self.open
        plans.listeners.append(self.on_plan)

    # -- reading ----------------------------------------------------------------------------

    def get(self, funding_id: str) -> dict[str, Any]:
        found = self.docs.get_doc(KIND, funding_id)
        if found is None:
            raise FundingError(f"no approval payment {funding_id}")
        return found

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.docs.docs(KIND), key=lambda f: f["created_at"], reverse=True)

    def for_plan(self, plan_id: str) -> dict[str, Any] | None:
        """The plan's latest approval payment."""
        rows = [f for f in self.all() if f["plan_id"] == plan_id]
        return rows[0] if rows else None

    # -- opening the payment, when an approver approves --------------------------------------

    def open(self, plan: Any) -> bool:
        """The plan desk's gate: open the PayPal payment for this approval. False when the
        cover costs nothing, so the approval goes straight through."""
        from crewpay.pay.payroll import to_usd

        breakdown: dict[str, int] = dict(getattr(plan, "breakdown", {}) or {})
        where = getattr(plan, "positioning", None)
        lines = []
        for label in CREW_PAY:
            if breakdown.get(label):
                lines.append({"label": label, "inr": breakdown[label], "for": "crew"})
        if breakdown.get(SEAT) and where:
            airline = self.config.airlines.get(where["airline"], where["airline"])
            lines.append(
                {
                    "label": f"seat on {where['flight_no']} {where['origin']}-"
                    f"{where['destination']} ({airline})",
                    "inr": breakdown[SEAT],
                    "for": "seat",
                }
            )
        total = sum(line["inr"] for line in lines)
        if total <= 0:
            return False
        rate = self.config.fx_inr_per_usd
        for line in lines:
            line["amount"] = to_usd(line["inr"], rate)
        now = self.clock()
        tries = sum(1 for f in self.docs.docs(KIND) if f["plan_id"] == plan.plan_id)
        suffix = plan.plan_id.removeprefix("PL-")
        funding = {
            "funding_id": f"FUND-{suffix}" + (f"-{tries + 1}" if tries else ""),
            "plan_id": plan.plan_id,
            "trip_id": plan.trip_id,
            "crew_id": plan.crew_id,
            "name": plan.name,
            "rank": str(plan.rank),
            "replacing": plan.replacing,
            "submitted_by": plan.submitted_by,
            "approver": plan.decided_by,
            "lines": lines,
            "seat": (
                {**where, "airline": self.config.airlines.get(where["airline"], where["airline"])}
                if where and breakdown.get(SEAT)
                else None
            ),
            "total_inr": total,
            "fx_inr_per_usd": rate,
            "amount": str(sum(Decimal(line["amount"]) for line in lines)),
            "currency": self.config.currency,
            "mode": self.mode,
            "status": str(FundingStatus.TO_PAY),
            "order_id": None,
            "approve_url": None,
            "capture_id": None,
            "refund_id": None,
            "paypal_status": None,
            "error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "history": [],
        }
        self._note(funding, funding["status"], plan.decided_by or "approver", "approved")
        with self._lock:
            self._order(funding)
        self.send(
            Notice(
                to=DESK,
                subject=f"Waiting for PayPal: {plan.rank} on {plan.trip_id}",
                body=f"{plan.decided_by} approved {plan.name} for {plan.trip_id}. It takes effect "
                f"once they confirm it with PayPal ({funding['amount']} {funding['currency']}).",
                at=now,
                ref=funding["funding_id"],
            )
        )
        return True

    def _order(self, funding: dict[str, Any]) -> None:
        """Create the PayPal order (or a simulated one). Raises PayPalError if PayPal refuses,
        so the approval does not go ahead."""
        if self.paypal is None:
            funding["order_id"] = f"SIM-{uuid.uuid4().hex[:12].upper()}"
            funding["paypal_status"] = "CREATED"
            self._save(funding)
            return
        cur = funding["currency"]
        items = [
            {
                "name": f"{line['label'].capitalize()}"[:127],
                "description": (
                    f"{funding['name']} covering {funding['trip_id']}"
                    if line["for"] == "crew"
                    else f"Positioning {funding['name']} for {funding['trip_id']}"
                )[:127],
                "quantity": "1",
                "unit_amount": {"currency_code": cur, "value": line["amount"]},
                "category": "DIGITAL_GOODS",
            }
            for line in funding["lines"]
        ]
        total = {"currency_code": cur, "value": funding["amount"]}
        context: dict[str, Any] = {
            "brand_name": "CrewPay",
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
        }
        if self.config.public_url:
            back = f"{self.config.public_url}/approvals/?fund={funding['funding_id']}"
            context["return_url"] = back
            context["cancel_url"] = back
        body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": funding["funding_id"],
                    "custom_id": funding["plan_id"],
                    "invoice_id": funding["funding_id"],
                    "description": f"Cover for {funding['trip_id']}: {funding['name']} "
                    f"replaces {funding['replacing']} ({funding['plan_id']})"[:127],
                    "soft_descriptor": "CREWPAY COVER",
                    "amount": {**total, "breakdown": {"item_total": total}},
                    "items": items,
                }
            ],
            "payment_source": {"paypal": {"experience_context": context}},
        }
        order = self.paypal.create_order(body, request_id=f"{funding['funding_id']}-order")
        funding["order_id"] = order.get("id")
        funding["paypal_status"] = order.get("status")
        funding["approve_url"] = next(
            (
                link["href"]
                for link in order.get("links", [])
                if link.get("rel") in ("payer-action", "approve")
            ),
            None,
        )
        self._note(funding, funding["status"], "paypal", f"order {funding['order_id']}")
        self._save(funding)

    # -- confirming -------------------------------------------------------------------------

    def capture(self, funding_id: str, by: str, order_id: str | None = None) -> dict[str, Any]:
        """The approver paid in PayPal: check the plan still holds, take the payment, and let
        the approval go through. In simulated mode, the approver's click is the payment."""
        with self._lock:
            funding = self.get(funding_id)
            if by not in (funding["approver"], "paypal"):
                raise FundingError(
                    f"only {funding['approver']}, who approved {funding['plan_id']}, can confirm "
                    "it with PayPal"
                )
            if order_id and order_id != funding["order_id"]:
                raise FundingError(f"order {order_id} is not the order for {funding_id}")
            if funding["status"] != FundingStatus.TO_PAY:
                if funding["status"] in (FundingStatus.PAID, FundingStatus.PENDING):
                    return funding
                raise FundingError(f"{funding_id} is {funding['status']}, not waiting for PayPal")
            stale = self.plans.check(funding["plan_id"]) if self.plans else None
            if stale is not None:
                self._move(funding, FundingStatus.CANCELLED, "payroll", f"not charged: {stale}")
                self.plans.expire(funding["plan_id"], stale)
                raise FundingError(f"not charged, and the plan did not go through: {stale}")
            if self.paypal is None:
                funding["capture_id"] = f"SIMC-{uuid.uuid4().hex[:12].upper()}"
                self._settle(funding, "COMPLETED", by, "simulated payment")
                return self.get(funding_id)
            try:
                reply = self.paypal.capture_order(
                    funding["order_id"], request_id=f"{funding_id}-capture"
                )
            except PayPalError as err:
                issue = _issue(err)
                if issue == "ORDER_NOT_APPROVED":
                    raise FundingError("the payment has not been approved in PayPal yet") from err
                if issue == "ORDER_ALREADY_CAPTURED":
                    self.sync(funding_id)
                    return self.get(funding_id)
                funding["error"] = str(err)
                self._save(funding)
                raise FundingError(f"PayPal would not take the payment: {err}") from err
            self._from_order(funding, reply, by, "paid with PayPal")
            return self.get(funding_id)

    def cancel(self, funding_id: str, by: str, why: str = "") -> dict[str, Any]:
        """Back out of the PayPal step: the plan waits for a decision again."""
        with self._lock:
            funding = self.get(funding_id)
            if funding["status"] != FundingStatus.TO_PAY:
                raise FundingError(
                    f"only an unpaid approval can be cancelled, not a {funding['status']} one"
                )
            if by != funding["approver"] and by != "payroll":
                raise FundingError(f"only {funding['approver']}, who approved it, can cancel it")
            note = why or "PayPal step cancelled"
            self._move(funding, FundingStatus.CANCELLED, by, note)
            if self.plans is not None:
                with contextlib.suppress(Exception):
                    self.plans.reopen(funding["plan_id"], by, note)
            return funding

    def on_plan(self, plan: Any) -> None:
        """A plan was decided some other way while its payment was open (stood down, stale):
        drop the payment, or give the money back if it was taken."""
        if str(plan.status) == "approved":
            return
        with self._lock:
            funding = self.for_plan(plan.plan_id)
            if funding is None:
                return
            if funding["status"] == FundingStatus.TO_PAY:
                self._move(funding, FundingStatus.CANCELLED, "payroll", f"plan {plan.status}")
            elif funding["status"] in (FundingStatus.PAID, FundingStatus.PENDING):
                self._refund(funding, f"plan {plan.status}: {plan.decision_note}")

    def _refund(self, funding: dict[str, Any], why: str) -> None:
        if self.paypal is None or not funding.get("capture_id"):
            funding["refund_id"] = f"SIMR-{uuid.uuid4().hex[:12].upper()}"
            self._move(funding, FundingStatus.REFUNDED, "payroll", why)
            self._tell_desk(funding, "Refunded", why)
            return
        try:
            reply = self.paypal.refund_capture(
                funding["capture_id"],
                request_id=f"{funding['funding_id']}-refund",
                note=f"CrewPay: {funding['plan_id']} did not go through ({why})",
            )
        except PayPalError as err:
            funding["error"] = f"refund failed: {err}"
            self._save(funding)
            self._tell_desk(funding, "Refund failed", str(err))
            return
        funding["refund_id"] = reply.get("id")
        self._move(funding, FundingStatus.REFUNDED, "paypal", why)
        self._tell_desk(funding, "Refunded", why)

    # -- keeping up with PayPal -------------------------------------------------------------

    def sync(self, funding_id: str | None = None) -> int:
        """Ask PayPal about each open payment: capture one approved through the approve link,
        settle a pending capture, and give up on one left unconfirmed too long."""
        changed = 0
        with self._lock:
            rows = [self.get(funding_id)] if funding_id else self.docs.docs(KIND)
            for funding in rows:
                if funding["status"] not in OPEN:
                    continue
                before = funding["status"]
                order = None
                if self.paypal is not None and funding.get("order_id"):
                    try:
                        order = self.paypal.get_order(funding["order_id"])
                    except PayPalError as err:
                        funding["error"] = str(err)
                        self._save(funding)
                        continue
                taken = order is not None and any(
                    (unit.get("payments") or {}).get("captures")
                    for unit in order.get("purchase_units", [])
                )
                # Give up on an unpaid approval only once PayPal confirms nothing was taken: a
                # capture whose reply was lost must be settled, not dropped with the money.
                if before == FundingStatus.TO_PAY and self._too_old(funding) and not taken:
                    with contextlib.suppress(FundingError):
                        self.cancel(funding["funding_id"], "payroll", "not confirmed in time")
                    changed += 1
                    continue
                if order is None:
                    continue
                if order.get("status") == "APPROVED" and before == FundingStatus.TO_PAY:
                    with contextlib.suppress(FundingError):
                        self.capture(funding["funding_id"], "paypal")
                else:
                    self._from_order(funding, order, "paypal", "status check")
                changed += int(self.get(funding["funding_id"])["status"] != before)
        return changed

    def webhook(self, event: dict[str, Any]) -> str | None:
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
            funding = next(
                (f for f in self.docs.docs(KIND) if order_id and f.get("order_id") == order_id),
                None,
            )
            if funding is None:
                return None
            if kind == "CHECKOUT.ORDER.APPROVED":
                if funding["status"] == FundingStatus.TO_PAY:
                    with contextlib.suppress(FundingError):
                        self.capture(funding["funding_id"], "paypal")
            elif kind.startswith("PAYMENT.CAPTURE."):
                funding["capture_id"] = resource.get("id") or funding.get("capture_id")
                status = resource.get("status") or kind.rsplit(".", 1)[-1]
                self._settle(funding, status, "paypal", f"webhook {kind}")
            return funding["funding_id"]

    # -- bookkeeping ------------------------------------------------------------------------

    def _too_old(self, funding: dict[str, Any]) -> bool:
        return self.clock() - datetime.fromisoformat(funding["created_at"]) > UNPAID_FOR

    def _from_order(
        self, funding: dict[str, Any], order: dict[str, Any], by: str, why: str
    ) -> None:
        funding["paypal_status"] = order.get("status") or funding.get("paypal_status")
        captures = [
            c
            for unit in order.get("purchase_units", [])
            for c in (unit.get("payments") or {}).get("captures", [])
        ]
        if captures:
            funding["capture_id"] = captures[0].get("id")
            funding["error"] = None
            took = captures[0].get("amount") or {}
            if took and (
                Decimal(str(took.get("value", "0"))) != Decimal(funding["amount"])
                or took.get("currency_code") != funding["currency"]
            ):
                # Never let an approval through on a payment that is not the one asked for.
                wrong = f"PayPal took {took.get('value')} {took.get('currency_code')}, not "
                wrong += f"{funding['amount']} {funding['currency']}"
                funding["error"] = wrong
                self._tell_desk(funding, "PayPal payment did not match", wrong)
                if captures[0].get("status", "").upper() == "COMPLETED":
                    self._refund(funding, wrong)
                if self.plans is not None:
                    with contextlib.suppress(Exception):
                        self.plans.reopen(funding["plan_id"], "payroll", wrong)
                return
            self._settle(funding, captures[0].get("status", ""), by, why)
        elif order.get("status") == "VOIDED":
            self.cancel(funding["funding_id"], "payroll", "PayPal voided the order")
        else:
            self._save(funding)

    def _settle(self, funding: dict[str, Any], capture_status: str, by: str, why: str) -> None:
        status = CAPTURE_STATUS.get(capture_status.upper())
        funding["capture_status"] = capture_status
        if status is None or status == funding["status"]:
            self._save(funding)
            return
        was = funding["status"]
        # The end of a payment is one-way: a late or replayed notice never takes a refunded or
        # cancelled payment back to paid, and a paid one can only be refunded.
        if was == FundingStatus.CANCELLED and status is FundingStatus.PAID:
            # Captured after the approval was dropped: nothing will use it, so give it back.
            self._note(funding, str(status), by, f"captured after it was cancelled ({why})")
            self._refund(funding, "captured after the approval was cancelled")
            return
        if was in (FundingStatus.REFUNDED, FundingStatus.CANCELLED) or (
            was == FundingStatus.PAID and status is not FundingStatus.REFUNDED
        ):
            self._note(funding, str(was), by, f"ignored {capture_status} ({why}): already {was}")
            self._save(funding)
            return
        self._move(funding, status, by, why)
        if status is FundingStatus.PAID and was in (FundingStatus.TO_PAY, FundingStatus.PENDING):
            self._go_through(funding)
        elif status is FundingStatus.FAILED:
            self._tell_desk(funding, "PayPal payment failed", why)
            # Nothing was taken: the plan must not wait for ever, it waits for a decision again.
            if self.plans is not None:
                with contextlib.suppress(Exception):
                    self.plans.reopen(funding["plan_id"], by, f"PayPal payment {capture_status}")

    def _go_through(self, funding: dict[str, Any]) -> None:
        """The money is in: the approval takes effect."""
        if self.plans is None:
            return
        plan = self.plans.get(funding["plan_id"])
        if str(plan.status) != "confirming":
            return
        plan = self.plans.confirm(funding["plan_id"])
        if str(plan.status) != "approved":
            return  # went stale at the last moment: on_plan has refunded it
        seat = funding.get("seat")
        if seat:
            self.send(
                Notice(
                    to=funding["crew_id"],
                    subject=f"Seat booked: {seat['flight_no']} to {seat['destination']}",
                    body=f"{funding['name']}, your seat on {seat['flight_no']} ({seat['origin']}-"
                    f"{seat['destination']}) to cover {funding['trip_id']} is paid"
                    f"{' (simulated)' if funding['mode'] == 'simulated' else ''}. Report at the "
                    "positioning flight's gate.",
                    at=self.clock(),
                    ref=funding["funding_id"],
                )
            )

    def _tell_desk(self, funding: dict[str, Any], what: str, detail: str) -> None:
        self.send(
            Notice(
                to=DESK,
                subject=f"{what}: {funding['funding_id']}",
                body=f"{funding['plan_id']} ({funding['name']} for {funding['trip_id']}): {detail}",
                at=self.clock(),
                ref=funding["funding_id"],
            )
        )

    def _move(self, funding: dict[str, Any], status: FundingStatus, by: str, note: str) -> None:
        funding["status"] = str(status)
        self._note(funding, str(status), by, note)
        self._save(funding)

    def _note(self, funding: dict[str, Any], status: str, by: str, note: str) -> None:
        funding["history"].append(
            {"at": self.clock().isoformat(), "status": status, "by": by, "note": note}
        )
        funding["updated_at"] = self.clock().isoformat()

    def _save(self, funding: dict[str, Any]) -> None:
        self.docs.put_doc(KIND, funding["funding_id"], funding)


def _issue(err: PayPalError) -> str:
    body = err.body if isinstance(err.body, dict) else {}
    details = body.get("details") or [{}]
    return details[0].get("issue", "") or body.get("name", "")
