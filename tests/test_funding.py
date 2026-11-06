"""An approval of cover takes effect only once its approver confirms it by paying with PayPal."""

import json
from decimal import Decimal

import httpx
import pytest
from conftest import SEED_DIR
from test_callout import LEGAL_CAPTAINS, Clock

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.domain import Rank
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox, Status
from crewpay.ops.plans import PlanStatus
from crewpay.pay import FundingError, FundingStatus, PayConfig, PayPalClient, Payroll, PayStatus
from crewpay.pay.payroll import to_usd
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

TRIP, OUT, IN = "T-1015", "C-1140", "C-1177"
"""S. Rao, based elsewhere, covers the captain of T-1015 by flying in on KF502."""
APPROVER = "meera.bhatt"


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


class FakeOrders:
    """The PayPal Orders and Payouts APIs as a httpx transport; ``approved`` is the payer's
    click in PayPal."""

    def __init__(self):
        self.requests = []
        self.approved = False
        self.captured = False
        self.body = None

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21", "expires_in": 32400})
        if path == "/v1/payments/payouts":
            return httpx.Response(
                201, json={"batch_header": {"payout_batch_id": "B-1", "batch_status": "PENDING"}}
            )
        if path == "/v2/checkout/orders":
            self.body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "id": "5O190127TN364715T",
                    "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/x"}],
                },
            )
        if path.endswith("/capture"):
            if not self.approved:
                return httpx.Response(
                    422,
                    json={
                        "name": "UNPROCESSABLE_ENTITY",
                        "details": [{"issue": "ORDER_NOT_APPROVED"}],
                    },
                )
            self.captured = True
            return httpx.Response(201, json=self._order())
        if path.endswith("/refund"):
            return httpx.Response(201, json={"id": "1JU08902781691411", "status": "COMPLETED"})
        if path.startswith("/v2/checkout/orders/"):
            return httpx.Response(200, json=self._order())
        return httpx.Response(404, json={"message": "unknown"})

    def _order(self):
        if not self.captured:
            return {"id": "5O190127TN364715T", "status": "APPROVED" if self.approved else "CREATED"}
        return {
            "id": "5O190127TN364715T",
            "status": "COMPLETED",
            "purchase_units": [
                {"payments": {"captures": [{"id": "3C679366HH908993F", "status": "COMPLETED"}]}}
            ],
        }


def paypal_for(fake):
    return PayPalClient(
        "id",
        "secret",
        "https://api-m.sandbox.paypal.com",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=lambda s: None,
    )


def wired(mem, paypal=None):
    outbox = MemoryOutbox()
    desk = CalloutDesk(mem, outbox, clock=Clock(), docs=mem, four_eyes=True)
    payroll = Payroll(mem, outbox, paypal, PayConfig(airlines={"KF": "Kaveri Flyers"}))
    desk.plans.listeners.append(payroll.on_plan)
    payroll.funding.attach(desk.plans)
    return desk, payroll, outbox


def crew_of(mem, trip=TRIP):
    return {s.crew_id for s in mem.trip(trip)[0].crew}


def approve(desk):
    plan = desk.plans.submit(TRIP, OUT, IN, "ananya.iyer", "sick")
    return desk.plans.approve(plan.plan_id, APPROVER)


def test_approving_waits_for_paypal_and_changes_nothing_until_paid(mem):
    desk, payroll, outbox = wired(mem)
    plan = approve(desk)
    assert plan.status is PlanStatus.CONFIRMING and plan.decided_by == APPROVER
    assert OUT in crew_of(mem) and IN not in crew_of(mem), "the roster has not changed"
    assert payroll.for_plan(plan.plan_id) is None, "nobody is paid before PayPal confirms"
    assert desk.plans.waiting() == [plan]

    funding = payroll.funding.for_plan(plan.plan_id)
    labels = [line["label"] for line in funding["lines"]]
    assert labels[:3] == ["callout", "service allowance", "travel allowance"]
    assert labels[3].startswith("seat on KF502") and "Kaveri Flyers" in labels[3]
    assert funding["total_inr"] == sum(
        v
        for k, v in plan.breakdown.items()
        if k in ("callout", "service allowance", "travel allowance", "positioning")
    )
    assert Decimal(funding["amount"]) == sum(Decimal(x["amount"]) for x in funding["lines"])
    assert funding["status"] == FundingStatus.TO_PAY and funding["order_id"].startswith("SIM-")
    assert any(n.subject.startswith("Waiting for PayPal") for n in outbox.sent(DESK))

    with pytest.raises(FundingError, match="only meera.bhatt"):
        payroll.funding.capture(funding["funding_id"], "karan.mehta")
    paid = payroll.funding.capture(funding["funding_id"], APPROVER)
    assert paid["status"] == FundingStatus.PAID

    done = desk.plans.get(plan.plan_id)
    assert done.status is PlanStatus.APPROVED
    assert IN in crew_of(mem) and OUT not in crew_of(mem)
    payment = payroll.for_plan(plan.plan_id)
    assert payment["status"] == PayStatus.SENT, "the crew payout follows the confirmation"
    subjects = [n.subject for n in outbox.sent(IN)]
    assert "Rostered: T-1015" in subjects and any(s.startswith("Seat booked") for s in subjects)


def test_backing_out_of_paypal_returns_the_plan_to_waiting(mem):
    desk, payroll, _ = wired(mem)
    plan = approve(desk)
    first = payroll.funding.for_plan(plan.plan_id)
    payroll.funding.cancel(first["funding_id"], APPROVER, "wrong card")
    again = desk.plans.get(plan.plan_id)
    assert again.status is PlanStatus.SUBMITTED and again.decided_by is None
    assert OUT in crew_of(mem)

    plan = desk.plans.approve(plan.plan_id, "karan.mehta")
    second = payroll.funding.for_plan(plan.plan_id)
    assert second["funding_id"] == first["funding_id"] + "-2"
    assert second["approver"] == "karan.mehta"


def test_a_plan_whose_trip_moved_on_is_not_charged(mem):
    desk, payroll, _ = wired(mem)
    plan = approve(desk)
    funding = payroll.funding.for_plan(plan.plan_id)
    trip, version = mem.trip(TRIP)
    mem.change_crew(TRIP, [s for s in trip.crew if s.crew_id != OUT], version, "ops", "test")
    with pytest.raises(FundingError, match="not charged"):
        payroll.funding.capture(funding["funding_id"], APPROVER)
    assert desk.plans.get(plan.plan_id).status is PlanStatus.STALE
    assert payroll.funding.get(funding["funding_id"])["status"] == FundingStatus.CANCELLED


def test_with_paypal_the_order_is_the_servers_and_is_captured_only_once_approved(mem):
    fake = FakeOrders()
    desk, payroll, _ = wired(mem, paypal_for(fake))
    plan = approve(desk)
    funding = payroll.funding.for_plan(plan.plan_id)
    unit = fake.body["purchase_units"][0]
    assert fake.body["intent"] == "CAPTURE" and unit["reference_id"] == funding["funding_id"]
    assert unit["amount"]["value"] == funding["amount"]
    assert len(unit["items"]) == 4 and unit["items"][0]["unit_amount"]["value"] == to_usd(
        funding["lines"][0]["inr"], 84.0
    )
    create = next(r for r in fake.requests if r.url.path == "/v2/checkout/orders")
    assert create.headers["PayPal-Request-Id"] == f"{funding['funding_id']}-order"
    assert funding["approve_url"].startswith("https://www.sandbox.paypal.com")

    with pytest.raises(FundingError, match="not been approved"):
        payroll.funding.capture(funding["funding_id"], APPROVER, funding["order_id"])
    assert desk.plans.get(plan.plan_id).status is PlanStatus.CONFIRMING

    fake.approved = True
    paid = payroll.funding.capture(funding["funding_id"], APPROVER, funding["order_id"])
    assert paid["capture_id"] == "3C679366HH908993F"
    assert desk.plans.get(plan.plan_id).status is PlanStatus.APPROVED


def test_an_order_approved_through_the_link_goes_through_on_the_status_check(mem):
    fake = FakeOrders()
    desk, payroll, _ = wired(mem, paypal_for(fake))
    plan = approve(desk)
    payroll.sync()
    assert desk.plans.get(plan.plan_id).status is PlanStatus.CONFIRMING
    fake.approved = True
    payroll.sync()
    assert desk.plans.get(plan.plan_id).status is PlanStatus.APPROVED


def test_the_webhook_confirms_an_approved_order(mem):
    fake = FakeOrders()
    desk, payroll, _ = wired(mem, paypal_for(fake))
    plan = approve(desk)
    funding = payroll.funding.for_plan(plan.plan_id)
    fake.approved = True
    event = {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": funding["order_id"]}}
    assert payroll.webhook(event) == funding["funding_id"]
    assert desk.plans.get(plan.plan_id).status is PlanStatus.APPROVED


def test_money_taken_for_a_plan_that_goes_stale_at_the_last_moment_is_refunded(mem, monkeypatch):
    fake = FakeOrders()
    desk, payroll, outbox = wired(mem, paypal_for(fake))
    plan = approve(desk)
    funding = payroll.funding.for_plan(plan.plan_id)
    fake.approved = True
    real = desk.plans._stale_reason
    calls = iter([None, "the trip changed meanwhile"])
    monkeypatch.setattr(desk.plans, "_stale_reason", lambda p: next(calls, None) or real(p))
    payroll.funding.capture(funding["funding_id"], APPROVER)
    assert desk.plans.get(plan.plan_id).status is PlanStatus.STALE
    refunded = payroll.funding.get(funding["funding_id"])
    assert refunded["status"] == FundingStatus.REFUNDED and refunded["refund_id"]
    assert any(r.url.path.endswith("/refund") for r in fake.requests)
    assert payroll.for_plan(plan.plan_id) is None, "a stale plan pays nobody"
    assert OUT in crew_of(mem)
    assert any(n.subject.startswith("Refunded") for n in outbox.sent(DESK))


def test_an_accepted_callout_fills_only_after_paypal(mem):
    desk, payroll, _ = wired(mem)
    callout = desk.open("T-1018", Rank.CAPTAIN, "C-1111", by="ananya.iyer")
    accepted = desk.respond(callout.callout_id, LEGAL_CAPTAINS[0], accept=True)
    assert accepted.status is Status.ACCEPTED
    plan = desk.plans.approve(accepted.plan_id, APPROVER)
    assert plan.status is PlanStatus.CONFIRMING
    assert desk.get(callout.callout_id).status is Status.ACCEPTED, "not filled yet"
    funding = payroll.funding.for_plan(plan.plan_id)
    payroll.funding.capture(funding["funding_id"], APPROVER)
    filled = desk.get(callout.callout_id)
    assert filled.status is Status.FILLED and filled.filled_by == LEGAL_CAPTAINS[0]


def test_the_agent_can_approve_but_never_confirm(mem):
    desk, payroll, _ = wired(mem)
    plan = desk.plans.submit(TRIP, OUT, IN, "ananya.iyer", "sick")
    box = ToolBox()
    ctx = Context(mem, Caller(APPROVER, Role.APPROVER), desk=desk, payroll=payroll)
    said = box.call("review_plan", {"plan_id": plan.plan_id, "action": "approve"}, ctx, agent=True)
    assert said["status"] == "confirming" and "PayPal" in said["next_step"]
    funding_id = said["funding_id"]
    refused = box.call("confirm_approval", {"funding_id": funding_id}, ctx, agent=True)
    assert "person at the console" in refused["error"]
    assert "confirm_approval" not in {s["function"]["name"] for s in box.specs(ctx.caller)}
    assert desk.plans.get(plan.plan_id).status is PlanStatus.CONFIRMING

    listed = box.call("approval_payments", {"plan_id": plan.plan_id}, ctx, agent=True)
    assert listed["payments"][0]["status"] == "to pay"

    done = box.call("confirm_approval", {"funding_id": funding_id}, ctx)  # the console
    assert done["plan_status"] == "approved"
