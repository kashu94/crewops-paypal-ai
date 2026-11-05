"""Tools for crew pay: what each approved cover paid, through PayPal, and acting on it.

Every figure comes from the payment record: the rupee lines the engine costed, the exchange
rate, the amount PayPal was asked to pay and what PayPal says happened. Crew see only their own
payments. Releasing a held payment is for an approver who did not approve the plan; cancelling
an unclaimed one is for approvers and admins, with a reason.

Positioning seats are bought with PayPal Checkout. The agent can find the seat and hand over
the order's approve link, and take the payment once the payer has approved it in PayPal; it can
never approve a payment itself.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from crewpay.agent.tools import (
    ADMINS,
    CREW_ID,
    TRIP_ID,
    Context,
    Forbidden,
    Role,
    Tool,
    ToolError,
)
from crewpay.pay import PayError, SeatError

PAYMENT_ID = r"^PAY-[0-9a-f]{8}$"
SEAT_ID = r"^SEAT-[0-9a-f]{8}$"
EVERYONE_WITH_PAY = frozenset(
    {
        Role.CREW,
        Role.CONTROLLER,
        Role.APPROVER,
        Role.AUDITOR,
        Role.ADMIN,
        Role.SUPER_ADMIN,
    }
)


def _payroll(ctx: Context) -> Any:
    if ctx.payroll is None:
        raise ToolError("crew pay is not available here")
    return ctx.payroll


def _view(p: dict[str, Any], full: bool = False) -> dict[str, Any]:
    out = {
        "payment_id": p["payment_id"],
        "status": p["status"],
        "crew_id": p["crew_id"],
        "name": p["name"],
        "trip_id": p["trip_id"],
        "plan_id": p["plan_id"],
        "amount": p["amount"],
        "currency": p["currency"],
        "total_inr": p["total_inr"],
        "pay_lines_inr": {line["label"]: line["inr"] for line in p["lines"]},
        "fx_inr_per_usd": p["fx_inr_per_usd"],
        "paid_by": "PayPal Payouts" + (" (simulated)" if p["mode"] == "simulated" else " sandbox"),
        "paypal_batch_id": p.get("batch_id"),
        "paypal_status": p.get("paypal_status"),
    }
    if full:
        out |= {
            "receiver": p["receiver"],
            "approved_by": p.get("approved_by"),
            "released_by": p.get("released_by"),
            "paypal_item_id": p.get("item_id"),
            "paypal_transaction_id": p.get("transaction_id"),
            "airline_costs_inr": {line["label"]: line["inr"] for line in p["airline_costs"]},
            "error": p.get("error"),
            "history": p["history"][-6:],
        }
    return out


def _mine(ctx: Context, p: dict[str, Any]) -> None:
    if ctx.caller.role is Role.CREW and p["crew_id"] != ctx.caller.crew_id:
        raise Forbidden("crew members can only see their own payments")


class CrewPayments(BaseModel):
    crew_id: str | None = Field(None, pattern=CREW_ID, description="Only this crew member's")
    trip_id: str | None = Field(None, pattern=TRIP_ID, description="Only payments for this trip")
    status: (
        Literal["held", "queued", "sent", "paid", "unclaimed", "failed", "returned", "cancelled"]
        | None
    ) = Field(None, description="Only payments in this state")


def crew_payments(ctx: Context, args: CrewPayments) -> dict[str, Any]:
    payroll = _payroll(ctx)
    rows = payroll.all()
    if ctx.caller.role is Role.CREW:
        rows = [p for p in rows if p["crew_id"] == ctx.caller.crew_id]
    if args.crew_id:
        ctx.own(args.crew_id)
        rows = [p for p in rows if p["crew_id"] == args.crew_id]
    if args.trip_id:
        rows = [p for p in rows if p["trip_id"] == args.trip_id]
    if args.status:
        rows = [p for p in rows if p["status"] == args.status]
    totals: dict[str, float] = {}
    for p in rows:
        totals[p["status"]] = round(totals.get(p["status"], 0) + float(p["amount"]), 2)
    return {
        "mode": payroll.mode,
        "currency": payroll.config.currency,
        "pay_cap": payroll.config.cap,
        "count": len(rows),
        "amount_by_status": totals,
        "payments": [_view(p) for p in rows[:25]],
    }


class PaymentStatus(BaseModel):
    payment_id: str = Field(pattern=PAYMENT_ID)
    refresh: bool = Field(True, description="Ask PayPal for the latest status first")


def payment_status(ctx: Context, args: PaymentStatus) -> dict[str, Any]:
    payroll = _payroll(ctx)
    try:
        _mine(ctx, payroll.get(args.payment_id))
        if args.refresh:
            payroll.sync(args.payment_id)
        return _view(payroll.get(args.payment_id), full=True)
    except PayError as err:
        raise ToolError(str(err)) from err


class ReleasePayment(BaseModel):
    payment_id: str = Field(pattern=PAYMENT_ID)


def release_payment(ctx: Context, args: ReleasePayment) -> dict[str, Any]:
    try:
        return _view(_payroll(ctx).release(args.payment_id, ctx.caller.user), full=True)
    except PayError as err:
        raise ToolError(str(err)) from err


class CancelPayment(BaseModel):
    payment_id: str = Field(pattern=PAYMENT_ID)
    reason: str = Field(min_length=3, max_length=300)


def cancel_payment(ctx: Context, args: CancelPayment) -> dict[str, Any]:
    try:
        return _view(_payroll(ctx).cancel(args.payment_id, ctx.caller.user, args.reason), full=True)
    except PayError as err:
        raise ToolError(str(err)) from err


def _seat_view(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "seat_id": s["seat_id"],
        "status": s["status"],
        "crew_id": s["crew_id"],
        "name": s["name"],
        "trip_id": s["trip_id"],
        "plan_id": s["plan_id"],
        "flight": f"{s['flight_no']} {s['origin']}-{s['destination']}",
        "departs": s["departs"],
        "airline": s["airline"],
        "amount": s["amount"],
        "currency": s["currency"],
        "inr": s["inr"],
        "paid_with": "PayPal Checkout"
        + (" (simulated)" if s["mode"] == "simulated" else " sandbox"),
        "paypal_order_id": s.get("order_id"),
        "paypal_approve_link": s.get("approve_url") if s["status"] == "to pay" else None,
        "paypal_capture_id": s.get("capture_id"),
        "paid_by": s.get("paid_by"),
        "error": s.get("error"),
    }


class PositioningSeats(BaseModel):
    crew_id: str | None = Field(None, pattern=CREW_ID, description="Only this crew member's")
    trip_id: str | None = Field(None, pattern=TRIP_ID, description="Only seats for this trip")
    status: Literal["to pay", "pending", "paid", "failed", "cancelled", "refunded"] | None = None


def positioning_seats(ctx: Context, args: PositioningSeats) -> dict[str, Any]:
    seats = _payroll(ctx).seats
    rows = seats.all()
    if ctx.caller.role is Role.CREW:
        rows = [s for s in rows if s["crew_id"] == ctx.caller.crew_id]
    if args.crew_id:
        ctx.own(args.crew_id)
        rows = [s for s in rows if s["crew_id"] == args.crew_id]
    if args.trip_id:
        rows = [s for s in rows if s["trip_id"] == args.trip_id]
    if args.status:
        rows = [s for s in rows if s["status"] == args.status]
    return {"mode": seats.mode, "count": len(rows), "seats": [_seat_view(s) for s in rows[:25]]}


class PaySeat(BaseModel):
    seat_id: str = Field(pattern=SEAT_ID)
    order_id: str | None = Field(None, max_length=40, description="The PayPal order approved")


def pay_seat(ctx: Context, args: PaySeat) -> dict[str, Any]:
    try:
        return _seat_view(_payroll(ctx).seats.capture(args.seat_id, ctx.caller.user, args.order_id))
    except SeatError as err:
        raise ToolError(str(err)) from err


class CancelSeat(BaseModel):
    seat_id: str = Field(pattern=SEAT_ID)
    reason: str = Field(min_length=3, max_length=300)


def cancel_seat(ctx: Context, args: CancelSeat) -> dict[str, Any]:
    try:
        return _seat_view(_payroll(ctx).seats.cancel(args.seat_id, ctx.caller.user, args.reason))
    except SeatError as err:
        raise ToolError(str(err)) from err


PAY_TOOLS = (
    Tool(
        "crew_payments",
        "PayPal payouts made to crew for approved cover: amount, the rupee lines it pays "
        "(callout, service and travel allowance), the exchange rate and the PayPal status. "
        "Filter by crew, trip or state. Crew see only their own.",
        CrewPayments,
        crew_payments,
        EVERYONE_WITH_PAY,
    ),
    Tool(
        "payment_status",
        "One payment in full, after asking PayPal for its latest status: batch, item and "
        "transaction ids, who approved and released it, its history and any error.",
        PaymentStatus,
        payment_status,
        EVERYONE_WITH_PAY,
    ),
    Tool(
        "release_payment",
        "Release a payment held over the pay cap so PayPal pays it (approvers; never the "
        "approver of the plan it pays).",
        ReleasePayment,
        release_payment,
        frozenset({Role.APPROVER}),
        writes=True,
    ),
    Tool(
        "cancel_payment",
        "Cancel a held or queued payment, or take back one PayPal reports unclaimed. Needs a "
        "reason; logged.",
        CancelPayment,
        cancel_payment,
        frozenset({Role.APPROVER}) | ADMINS,
        writes=True,
    ),
    Tool(
        "positioning_seats",
        "Seats bought for crew who fly in to cover a trip, paid with PayPal Checkout: flight, "
        "amount, state, the PayPal order id and, while unpaid, the link to approve the payment "
        "in PayPal. Crew see only their own.",
        PositioningSeats,
        positioning_seats,
        EVERYONE_WITH_PAY,
    ),
    Tool(
        "pay_seat",
        "Take the payment for a positioning seat once the payer has approved its PayPal order "
        "(approvers and admins). If it is not approved yet, give them the approve link.",
        PaySeat,
        pay_seat,
        frozenset({Role.APPROVER}) | ADMINS,
        writes=True,
    ),
    Tool(
        "cancel_seat",
        "Cancel an unpaid positioning seat, with a reason (approvers and admins).",
        CancelSeat,
        cancel_seat,
        frozenset({Role.APPROVER}) | ADMINS,
        writes=True,
    ),
)
