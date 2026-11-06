"""Tools for crew pay: what each approved cover paid, through PayPal, and acting on it.

Every figure comes from the payment record: the rupee lines the engine costed, the exchange
rate, the amount PayPal was asked to pay and what PayPal says happened. Crew see only their own
payments. Releasing a held payment is for an approver who did not approve the plan; cancelling
an unclaimed one is for approvers and admins, with a reason.

Crew choose the PayPal account they are paid at; only they, or an admin, can change it, and
both they and the desk are told when it changes.

Approving cover takes effect only once the approver pays for it with PayPal Checkout. The agent
can approve, list the payment and hand over its PayPal link, but confirming is console-only:
always a person in PayPal.
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
from crewpay.pay import FundingError, PayError

PAYMENT_ID = r"^PAY-[0-9a-f]{8}$"
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
            "receiver_is": p.get("receiver_is"),
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


def _funding_view(f: dict[str, Any]) -> dict[str, Any]:
    return {
        "funding_id": f["funding_id"],
        "status": f["status"],
        "plan_id": f["plan_id"],
        "trip_id": f["trip_id"],
        "crew_id": f["crew_id"],
        "name": f["name"],
        "approver": f["approver"],
        "lines": [
            {"what": line["label"], "inr": line["inr"], "amount": line["amount"]}
            for line in f["lines"]
        ],
        "total_inr": f["total_inr"],
        "amount": f["amount"],
        "currency": f["currency"],
        "paid_with": "PayPal Checkout"
        + (" (simulated)" if f["mode"] == "simulated" else " sandbox"),
        "paypal_order_id": f.get("order_id"),
        "paypal_approve_link": f.get("approve_url") if f["status"] == "to pay" else None,
        "paypal_capture_id": f.get("capture_id"),
        "paypal_refund_id": f.get("refund_id"),
        "error": f.get("error"),
    }


STAFF_WITH_PAY = EVERYONE_WITH_PAY - {Role.CREW}


class ApprovalPayments(BaseModel):
    plan_id: str | None = Field(None, pattern=r"^PL-[0-9a-f]{8}$", description="Only this plan's")
    status: Literal["to pay", "pending", "paid", "failed", "cancelled", "refunded"] | None = None
    refresh: bool = Field(False, description="Ask PayPal for the latest first")


def approval_payments(ctx: Context, args: ApprovalPayments) -> dict[str, Any]:
    funding = _payroll(ctx).funding
    rows = funding.all()
    if args.plan_id:
        rows = [f for f in rows if f["plan_id"] == args.plan_id]
    if args.refresh:
        for f in rows[:5]:
            funding.sync(f["funding_id"])
        rows = [funding.get(f["funding_id"]) for f in rows]
    if args.status:
        rows = [f for f in rows if f["status"] == args.status]
    return {
        "mode": funding.mode,
        "how_it_works": "An approval of cover takes effect only after the approver who "
        "approved it pays for the cover with PayPal. Until then the plan is 'confirming'.",
        "count": len(rows),
        "payments": [_funding_view(f) for f in rows[:20]],
    }


FUNDING_ID = r"^FUND-[0-9a-f]{8}(-\d+)?$"


class ConfirmApproval(BaseModel):
    funding_id: str = Field(pattern=FUNDING_ID)
    order_id: str | None = Field(None, max_length=40, description="The PayPal order approved")


def confirm_approval(ctx: Context, args: ConfirmApproval) -> dict[str, Any]:
    try:
        funding = _payroll(ctx).funding.capture(args.funding_id, ctx.caller.user, args.order_id)
    except FundingError as err:
        raise ToolError(str(err)) from err
    plan = ctx.plans().get(funding["plan_id"])
    return {**_funding_view(funding), "plan_status": str(plan.status)}


class CancelApproval(BaseModel):
    funding_id: str = Field(pattern=FUNDING_ID)
    reason: str = Field("", max_length=300)


def cancel_approval(ctx: Context, args: CancelApproval) -> dict[str, Any]:
    try:
        funding = _payroll(ctx).funding.cancel(
            args.funding_id, ctx.caller.user, args.reason.strip() or "PayPal step cancelled"
        )
    except FundingError as err:
        raise ToolError(str(err)) from err
    return {**_funding_view(funding), "plan_status": "submitted (waiting for a decision again)"}


class PayoutAccount(BaseModel):
    crew_id: str | None = Field(None, pattern=CREW_ID, description="Whose; crew: themselves")


def payout_account(ctx: Context, args: PayoutAccount) -> dict[str, Any]:
    payroll = _payroll(ctx)
    crew_id = args.crew_id or ctx.caller.crew_id
    if crew_id is None:
        raise ToolError("say whose payout account")
    ctx.own(crew_id)
    email, why = payroll.receiver_for(crew_id)
    own = payroll.payee(crew_id)
    return {
        "crew_id": crew_id,
        "next_payout_goes_to": email,
        "because_it_is": why,
        "set_by": own["set_by"] if own else None,
        "set_at": own["set_at"] if own else None,
    }


class SetPayoutEmail(BaseModel):
    email: str = Field(min_length=6, max_length=127, description="A PayPal account's email")
    crew_id: str | None = Field(None, pattern=CREW_ID, description="Admins only: whose")


def set_payout_email(ctx: Context, args: SetPayoutEmail) -> dict[str, Any]:
    crew_id = args.crew_id or ctx.caller.crew_id
    if crew_id is None:
        raise ToolError("say whose payout account")
    if ctx.caller.role is Role.CREW and crew_id != ctx.caller.crew_id:
        raise Forbidden("crew members can only change where they themselves are paid")
    try:
        doc = _payroll(ctx).set_payee(crew_id, args.email, ctx.caller.user)
    except PayError as err:
        raise ToolError(str(err)) from err
    return {
        "crew_id": crew_id,
        "next_payout_goes_to": doc["email"],
        "previous": doc["previous"],
        "told": [crew_id, "crew control"],
        "note": "payouts already sent are not moved",
    }


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
        "approval_payments",
        "PayPal payments that confirm approvals of cover: what the approver pays (the crew's "
        "callout, service and travel allowance, and any positioning seat), its state, and while "
        "unpaid the link to pay it in PayPal.",
        ApprovalPayments,
        approval_payments,
        STAFF_WITH_PAY,
    ),
    Tool(
        "confirm_approval",
        "Confirm an approval once its approver has paid for it in PayPal: the cover then takes "
        "effect. Only at the console, by the approver who approved it.",
        ConfirmApproval,
        confirm_approval,
        frozenset({Role.APPROVER}),
        writes=True,
        console_only=True,
    ),
    Tool(
        "cancel_approval",
        "Back out of an approval still waiting for PayPal: the plan waits for a decision again "
        "(the approver who approved it).",
        CancelApproval,
        cancel_approval,
        frozenset({Role.APPROVER}),
        writes=True,
    ),
    Tool(
        "payout_account",
        "Where a crew member's next PayPal payout goes, and whether they chose it. Crew: only "
        "their own.",
        PayoutAccount,
        payout_account,
        EVERYONE_WITH_PAY,
    ),
    Tool(
        "set_payout_email",
        "Change the PayPal account a crew member is paid at, for future payouts. Crew change "
        "only their own; admins anyone's. The crew member and crew control are both told.",
        SetPayoutEmail,
        set_payout_email,
        frozenset({Role.CREW}) | ADMINS,
        writes=True,
    ),
)
