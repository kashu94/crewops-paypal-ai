"""Buying the positioning seat with PayPal Checkout when cover must fly in first."""

import json

import httpx
import pytest
from conftest import SEED_DIR
from test_callout import Clock

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox
from crewpay.pay import PayConfig, PayPalClient, Payroll, SeatError, SeatStatus
from crewpay.pay.payroll import CREW_PAY, to_usd
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

TRIP, OUT, IN = "T-1015", "C-1140", "C-1177"
"""S. Rao, based elsewhere, covers the captain of T-1015 by flying in on KF502."""


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


class FakeOrders:
    """The PayPal Orders API as a httpx transport; ``approved`` is the payer's click."""

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
        if path == "/v2/checkout/orders":
            self.body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "id": "5O190127TN364715T",
                    "status": "PAYER_ACTION_REQUIRED",
                    "links": [
                        {"rel": "self", "href": "https://x/self"},
                        {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/x"},
                    ],
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


def wired(mem, paypal=None, **config):
    outbox = MemoryOutbox()
    desk = CalloutDesk(mem, outbox, clock=Clock(), docs=mem, four_eyes=True)
    payroll = Payroll(mem, outbox, paypal, PayConfig(airlines={"KF": "Kaveri Flyers"}, **config))
    desk.plans.listeners.append(payroll.on_plan)
    return desk, payroll, outbox


def approve(desk):
    plan = desk.plans.submit(TRIP, OUT, IN, "ananya.iyer", "sick")
    assert plan.positioning and plan.positioning["flight_no"].startswith("KF")
    return desk.plans.approve(plan.plan_id, "meera.bhatt")


def paypal_for(fake):
    return PayPalClient(
        "id",
        "secret",
        "https://api-m.sandbox.paypal.com",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=lambda s: None,
    )


def test_approving_cover_that_positions_opens_a_seat_to_pay(mem):
    desk, payroll, outbox = wired(mem)
    plan = approve(desk)
    seat = payroll.seats.for_plan(plan.plan_id)
    assert seat["status"] == SeatStatus.TO_PAY and seat["mode"] == "simulated"
    assert seat["airline"] == "Kaveri Flyers" and seat["flight_no"] == plan.positioning["flight_no"]
    assert seat["inr"] == plan.breakdown["positioning"]
    assert seat["amount"] == to_usd(seat["inr"], 84.0) and seat["order_id"].startswith("SIM-")
    assert any(n.subject.startswith("Seat to pay") for n in outbox.sent(DESK))

    payment = payroll.for_plan(plan.plan_id)
    assert {x["label"] for x in payment["lines"]} <= set(CREW_PAY)
    assert "positioning" in {x["label"] for x in payment["airline_costs"]}, "not paid to crew"

    paid = payroll.seats.capture(seat["seat_id"], "meera.bhatt")
    assert paid["status"] == SeatStatus.PAID and paid["capture_id"].startswith("SIMC-")
    assert outbox.sent(IN)[-1].subject.startswith("Seat booked")
    assert payroll.seats.capture(seat["seat_id"], "meera.bhatt")["status"] == SeatStatus.PAID
    with pytest.raises(SeatError):
        payroll.seats.cancel(seat["seat_id"], "meera.bhatt", "too late")


def test_the_order_is_made_by_the_server_and_captured_only_once_approved(mem):
    fake = FakeOrders()
    desk, payroll, _ = wired(mem, paypal_for(fake), seller="seats@kaveri.example")
    seat = payroll.seats.for_plan(approve(desk).plan_id)

    unit = fake.body["purchase_units"][0]
    assert fake.body["intent"] == "CAPTURE"
    assert unit["amount"]["value"] == seat["amount"] and unit["reference_id"] == seat["seat_id"]
    assert unit["payee"] == {"email_address": "seats@kaveri.example"}
    create = next(r for r in fake.requests if r.url.path == "/v2/checkout/orders")
    assert create.headers["PayPal-Request-Id"].startswith(f"{seat['seat_id']}-order-")
    assert seat["order_id"] == "5O190127TN364715T"
    assert seat["approve_url"].startswith("https://www.sandbox.paypal.com")

    with pytest.raises(SeatError, match="not approved"):
        payroll.seats.capture(seat["seat_id"], "meera.bhatt", seat["order_id"])
    with pytest.raises(SeatError, match="not the order"):
        payroll.seats.capture(seat["seat_id"], "meera.bhatt", "SOME-OTHER-ORDER")

    fake.approved = True
    paid = payroll.seats.capture(seat["seat_id"], "meera.bhatt", seat["order_id"])
    assert paid["status"] == SeatStatus.PAID and paid["capture_id"] == "3C679366HH908993F"
    capture = next(r for r in fake.requests if r.url.path.endswith("/capture"))
    assert capture.headers["PayPal-Request-Id"] == f"{seat['seat_id']}-capture"


def test_an_order_approved_through_the_link_is_captured_by_the_status_check(mem):
    fake = FakeOrders()
    desk, payroll, _ = wired(mem, paypal_for(fake))
    seat = payroll.seats.for_plan(approve(desk).plan_id)
    assert payroll.sync() == 0
    fake.approved = True
    assert payroll.sync() >= 1
    assert payroll.seats.get(seat["seat_id"])["status"] == SeatStatus.PAID


def test_webhooks_capture_an_approved_order_and_record_a_refund(mem):
    fake = FakeOrders()
    desk, payroll, outbox = wired(mem, paypal_for(fake))
    seat = payroll.seats.for_plan(approve(desk).plan_id)
    fake.approved = True
    approved = {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": seat["order_id"]}}
    assert payroll.webhook(approved) == seat["seat_id"]
    assert payroll.seats.get(seat["seat_id"])["status"] == SeatStatus.PAID

    refund = {
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {
            "id": "3C679366HH908993F",
            "status": "REFUNDED",
            "supplementary_data": {"related_ids": {"order_id": seat["order_id"]}},
        },
    }
    assert payroll.webhook(refund) == seat["seat_id"]
    assert payroll.seats.get(seat["seat_id"])["status"] == SeatStatus.REFUNDED
    assert any(n.subject.startswith("Seat refunded") for n in outbox.sent(DESK))
    assert (
        payroll.webhook({"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": "X"}}) is None
    )


def test_seat_tools_follow_the_roles(mem):
    desk, payroll, _ = wired(mem)
    seat = payroll.seats.for_plan(approve(desk).plan_id)
    box = ToolBox()

    def ctx(caller):
        return Context(mem, caller, desk=desk, payroll=payroll)

    controller = ctx(Caller("ananya.iyer", Role.CONTROLLER))
    listed = box.call("positioning_seats", {}, controller)
    assert listed["count"] == 1 and listed["seats"][0]["paypal_order_id"].startswith("SIM-")
    refused = box.call("pay_seat", {"seat_id": seat["seat_id"]}, controller)
    assert "cannot use" in refused["error"]

    assert box.call("positioning_seats", {}, ctx(Caller("c", Role.CREW, IN)))["count"] == 1
    assert box.call("positioning_seats", {}, ctx(Caller("c", Role.CREW, OUT)))["count"] == 0

    approver = ctx(Caller("karan.mehta", Role.APPROVER))
    assert box.call("pay_seat", {"seat_id": seat["seat_id"]}, approver)["status"] == "paid"
    late = box.call("cancel_seat", {"seat_id": seat["seat_id"], "reason": "not needed"}, approver)
    assert "unpaid" in late["error"]


def test_an_order_paypal_failed_to_create_is_made_again_by_the_status_check(mem):
    fake = FakeOrders()
    real = fake.__call__
    down = {"on": True}

    def flaky(request):
        if down["on"] and request.url.path == "/v2/checkout/orders":
            return httpx.Response(400, json={"name": "INVALID_REQUEST", "message": "try later"})
        return real(request)

    desk, payroll, outbox = wired(mem, paypal_for(flaky))
    seat = payroll.seats.for_plan(approve(desk).plan_id)
    assert seat["status"] == SeatStatus.FAILED and seat["order_id"] is None
    assert any(n.subject.startswith("Seat order failed") for n in outbox.sent(DESK))
    down["on"] = False
    payroll.sync()
    again = payroll.seats.get(seat["seat_id"])
    assert again["status"] == SeatStatus.TO_PAY and again["order_id"] == "5O190127TN364715T"
