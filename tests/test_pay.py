import json

import httpx
import pytest
from conftest import SEED_DIR
from fastapi.testclient import TestClient
from test_callout import LEGAL_CAPTAINS, SICK, TRIP, Clock

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.api.app import Services, create_app
from crewpay.ops import DESK, CalloutDesk, MemoryOutbox
from crewpay.pay import CREW_PAY, PayConfig, PayError, PayPalClient, Payroll, PayStatus
from crewpay.pay.payroll import to_usd
from crewpay.seed import read_meta
from crewpay.settings import load_settings
from crewpay.store import MemoryStore

FIRST, SECOND = LEGAL_CAPTAINS[:2]


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def outbox():
    return MemoryOutbox()


def wired(mem, outbox, paypal=None, **config):
    desk = CalloutDesk(mem, outbox, clock=Clock(), docs=mem, four_eyes=True)
    payroll = Payroll(mem, outbox, paypal, PayConfig(**config))
    desk.plans.listeners.append(payroll.on_plan)
    return desk, payroll


def approve(desk, crew_id=FIRST):
    plan = desk.plans.submit(TRIP, SICK, crew_id, "ananya.iyer", "sick")
    return desk.plans.approve(plan.plan_id, "meera.bhatt")


class FakePayPal:
    """Stands in for the PayPal API, as a httpx transport that records every request."""

    def __init__(self, item_status="SUCCESS", fail_create=False):
        self.requests = []
        self.item_status = item_status
        self.fail_create = fail_create
        self.tokens = 0

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/v1/oauth2/token":
            self.tokens += 1
            return httpx.Response(200, json={"access_token": "A21", "expires_in": 32400})
        if path == "/v1/payments/payouts" and request.method == "POST":
            if self.fail_create:
                return httpx.Response(
                    422, json={"name": "INSUFFICIENT_FUNDS", "message": "Sender has no funds"}
                )
            body = json.loads(request.content)
            batch = body["sender_batch_header"]["sender_batch_id"]
            self.sent = body
            return httpx.Response(
                201,
                json={"batch_header": {"payout_batch_id": f"B-{batch}", "batch_status": "PENDING"}},
            )
        if path.startswith("/v1/payments/payouts/"):
            sender_item = self.sent["items"][0]["sender_item_id"]
            return httpx.Response(
                200,
                json={
                    "batch_header": {"batch_status": "SUCCESS"},
                    "items": [
                        {
                            "payout_item_id": "ITEM-1",
                            "transaction_id": "TX-9",
                            "transaction_status": self.item_status,
                            "payout_item_fee": {"value": "0.25", "currency": "USD"},
                            "payout_item": {"sender_item_id": sender_item},
                        }
                    ],
                },
            )
        if path.endswith("/cancel"):
            return httpx.Response(200, json={"transaction_status": "RETURNED"})
        if path == "/v1/notifications/verify-webhook-signature":
            ok = request.headers.get("x-test-genuine") == "1" or (
                json.loads(request.content)["transmission_sig"] == "good"
            )
            return httpx.Response(200, json={"verification_status": "SUCCESS" if ok else "FAILURE"})
        return httpx.Response(404, json={"message": "unknown"})


def client_for(fake):
    return PayPalClient(
        "id",
        "secret",
        "https://api-m.sandbox.paypal.com",
        http=httpx.Client(transport=httpx.MockTransport(fake)),
        sleep=lambda s: None,
    )


def test_approving_cover_pays_the_crew_their_engine_costed_lines(mem, outbox):
    desk, payroll = wired(mem, outbox)
    plan = approve(desk)
    payment = payroll.for_plan(plan.plan_id)
    assert payment is not None and payment["mode"] == "simulated"
    paid_lines = {line["label"]: line["inr"] for line in payment["lines"]}
    assert set(paid_lines) <= set(CREW_PAY) and "callout" in paid_lines
    assert paid_lines == {k: v for k, v in plan.breakdown.items() if k in CREW_PAY and v}
    assert payment["total_inr"] == sum(paid_lines.values())
    assert payment["amount"] == to_usd(payment["total_inr"], 84.0)
    assert payment["status"] == PayStatus.SENT and payment["approved_by"] == "meera.bhatt"

    assert payroll.sync() == 1
    paid = payroll.get(payment["payment_id"])
    assert paid["status"] == PayStatus.PAID
    assert outbox.sent(FIRST)[-1].subject.startswith("Paid: ")
    assert payroll.sync() == 0, "a settled payment is not asked about again"


def test_a_plan_is_paid_once_and_rejected_plans_pay_nothing(mem, outbox):
    desk, payroll = wired(mem, outbox)
    plan = approve(desk)
    assert payroll.on_plan(plan) is None
    assert len(payroll.all()) == 1
    second = desk.plans.submit(TRIP, FIRST, SECOND, "ananya.iyer", "swap back")
    desk.plans.reject(second.plan_id, "meera.bhatt", "not needed")
    assert len(payroll.all()) == 1


def test_over_the_cap_waits_for_a_second_approver(mem, outbox):
    desk, payroll = wired(mem, outbox, cap=1.0)
    plan = approve(desk)
    payment = payroll.for_plan(plan.plan_id)
    assert payment["status"] == PayStatus.HELD and payment["batch_id"] is None
    assert any("Payment held" in n.subject for n in outbox.sent(DESK))

    with pytest.raises(PayError, match="cannot also release"):
        payroll.release(payment["payment_id"], "meera.bhatt")
    released = payroll.release(payment["payment_id"], "vikram.rao")
    assert released["status"] == PayStatus.SENT and released["released_by"] == "vikram.rao"
    with pytest.raises(PayError, match="not held"):
        payroll.release(payment["payment_id"], "vikram.rao")


def test_payouts_go_to_paypal_named_after_the_payment_so_retries_never_pay_twice(mem, outbox):
    fake = FakePayPal()
    desk, payroll = wired(mem, outbox, client_for(fake), receiver="crew@personal.example.com")
    payment = payroll.for_plan(approve(desk).plan_id)

    create = next(r for r in fake.requests if r.url.path == "/v1/payments/payouts")
    body = json.loads(create.content)
    assert body["sender_batch_header"]["sender_batch_id"] == payment["payment_id"]
    assert create.headers["paypal-request-id"] == payment["payment_id"]
    assert create.headers["authorization"] == "Bearer A21"
    [item] = body["items"]
    assert item["receiver"] == "crew@personal.example.com"
    assert item["amount"] == {"value": payment["amount"], "currency": "USD"}
    assert item["sender_item_id"] == payment["payment_id"]
    assert payment["batch_id"] == f"B-{payment['payment_id']}"

    payroll.sync()
    paid = payroll.get(payment["payment_id"])
    assert (paid["status"], paid["item_id"], paid["transaction_id"], paid["fee"]) == (
        PayStatus.PAID,
        "ITEM-1",
        "TX-9",
        "0.25",
    )
    assert fake.tokens == 1, "the access token is reused until it expires"


def test_a_refused_payout_is_failed_told_to_the_desk_and_can_be_sent_again(mem, outbox):
    fake = FakePayPal(fail_create=True)
    desk, payroll = wired(mem, outbox, client_for(fake))
    payment = payroll.for_plan(approve(desk).plan_id)
    assert payment["status"] == PayStatus.FAILED and "no funds" in payment["error"]
    assert any("Payment failed" in n.subject for n in outbox.sent(DESK))

    fake.fail_create = False
    again = payroll.send(payroll.get(payment["payment_id"]))
    assert again["status"] == PayStatus.SENT and again["error"] is None


def test_unclaimed_payments_can_be_taken_back(mem, outbox):
    fake = FakePayPal(item_status="UNCLAIMED")
    desk, payroll = wired(mem, outbox, client_for(fake))
    payment = payroll.for_plan(approve(desk).plan_id)
    payroll.sync()
    assert payroll.get(payment["payment_id"])["status"] == PayStatus.UNCLAIMED
    done = payroll.cancel(payment["payment_id"], "meera.bhatt", "wrong address")
    assert done["status"] == PayStatus.CANCELLED
    assert any(r.url.path == "/v1/payments/payouts-item/ITEM-1/cancel" for r in fake.requests)
    with pytest.raises(PayError, match="only an unclaimed"):
        payroll.cancel(payment["payment_id"], "meera.bhatt", "again")


def test_webhook_events_move_the_payment(mem, outbox):
    desk, payroll = wired(mem, outbox)
    payment = payroll.for_plan(approve(desk).plan_id)
    event = {
        "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED",
        "resource": {
            "payout_item_id": "ITEM-7",
            "transaction_id": "TX-7",
            "transaction_status": "SUCCESS",
            "payout_item": {"sender_item_id": payment["payment_id"]},
        },
    }
    assert payroll.webhook(event) == payment["payment_id"]
    assert payroll.get(payment["payment_id"])["status"] == PayStatus.PAID
    assert payroll.webhook({"event_type": "CHECKOUT.ORDER.APPROVED"}) is None


def test_crew_see_only_their_own_payments_and_only_approvers_release(mem, outbox):
    desk, payroll = wired(mem, outbox, cap=1.0)
    payment = payroll.for_plan(approve(desk).plan_id)
    box = ToolBox()

    def ctx(caller):
        return Context(mem, caller, desk=desk, payroll=payroll)

    own = box.call("crew_payments", {}, ctx(Caller("c", Role.CREW, FIRST)))
    assert [p["payment_id"] for p in own["payments"]] == [payment["payment_id"]]
    other = box.call("crew_payments", {}, ctx(Caller("c", Role.CREW, SECOND)))
    assert other["payments"] == []
    peek = box.call(
        "payment_status", {"payment_id": payment["payment_id"]}, ctx(Caller("c", Role.CREW, SECOND))
    )
    assert "own payments" in peek["error"]

    controller = ctx(Caller("ananya.iyer", Role.CONTROLLER))
    refused = box.call("release_payment", {"payment_id": payment["payment_id"]}, controller)
    assert "cannot use" in refused["error"]
    same = box.call(
        "release_payment",
        {"payment_id": payment["payment_id"]},
        ctx(Caller("meera.bhatt", Role.APPROVER)),
    )
    assert "cannot also release" in same["error"]
    done = box.call(
        "release_payment",
        {"payment_id": payment["payment_id"]},
        ctx(Caller("vikram.rao", Role.APPROVER)),
    )
    assert done["status"] == "sent" and done["pay_lines_inr"]["callout"] > 0


def test_the_webhook_endpoint_needs_paypal_to_vouch_for_each_event(seed):
    fake = FakePayPal()
    config = load_settings(
        {
            "CREWPAY_SEED_DIR": str(SEED_DIR),
            "PAYPAL_CLIENT_ID": "id",
            "PAYPAL_CLIENT_SECRET": "secret",
            "PAYPAL_WEBHOOK_ID": "WH-1",
        }
    )
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    s = Services(config, store)
    s.payroll.paypal = client_for(fake)
    client = TestClient(create_app(config, s))

    event = {"event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "resource": {}}
    sent = {"paypal-transmission-id": "T-1", "paypal-cert-url": "https://api.paypal.com/c"}
    unsigned = client.post("/api/paypal/webhook", json=event)
    assert unsigned.status_code == 400 and not fake.requests
    forged = client.post(
        "/api/paypal/webhook", json=event, headers={**sent, "paypal-transmission-sig": "x"}
    )
    assert forged.status_code == 400
    genuine = client.post(
        "/api/paypal/webhook", json=event, headers={**sent, "paypal-transmission-sig": "good"}
    )
    assert genuine.status_code == 200 and genuine.json()["payment_id"] is None
    verify = json.loads(fake.requests[-1].content)
    assert verify["webhook_id"] == "WH-1" and verify["webhook_event"] == event

    ledger = client.get(
        "/api/payments", headers={"X-Demo-User": "meera.bhatt", "X-Demo-Role": "approver"}
    )
    assert ledger.status_code == 200 and ledger.json()["mode"] == "paypal"
    station = client.get(
        "/api/payments", headers={"X-Demo-User": "imran.sheikh", "X-Demo-Role": "reporter"}
    )
    assert station.status_code == 403


def test_crew_choose_where_they_are_paid_and_everyone_is_told(mem, outbox):
    desk, payroll = wired(mem, outbox, receiver="demo-crew@personal.example")
    box = ToolBox()

    def ctx(caller):
        return Context(mem, caller, desk=desk, payroll=payroll)

    me = ctx(Caller("c", Role.CREW, FIRST))
    before = box.call("payout_account", {}, me)
    assert before["next_payout_goes_to"] == "demo-crew@personal.example"

    bad = box.call("set_payout_email", {"email": "not an email"}, me)
    assert "not an email" in bad["error"]
    other = box.call("set_payout_email", {"email": "x@y.example", "crew_id": SECOND}, me)
    assert "themselves" in other["error"]
    controller = ctx(Caller("ananya.iyer", Role.CONTROLLER))
    refused = box.call("set_payout_email", {"email": "x@y.example", "crew_id": FIRST}, controller)
    assert "cannot use" in refused["error"]

    done = box.call("set_payout_email", {"email": "Ravi.K@personal.example"}, me)
    assert done["next_payout_goes_to"] == "Ravi.K@personal.example"
    assert outbox.sent(FIRST)[-1].subject == "Your PayPal address for pay changed"
    assert any(n.subject == f"Payout address changed: {FIRST}" for n in outbox.sent(DESK))
    peek = box.call("payout_account", {"crew_id": FIRST}, ctx(Caller("c", Role.CREW, SECOND)))
    assert "own record" in peek["error"]

    payment = payroll.for_plan(approve(desk).plan_id)
    assert payment["receiver"] == "Ravi.K@personal.example"
    assert payment["receiver_is"] == "the PayPal account the crew member chose"


def test_a_replayed_payout_notice_never_undoes_a_returned_payment(mem, outbox):
    desk, payroll = wired(mem, outbox)
    payment = payroll.for_plan(approve(desk).plan_id)

    def event(status):
        return {
            "event_type": f"PAYMENT.PAYOUTS-ITEM.{status}",
            "resource": {
                "payout_item_id": "ITEM-7",
                "transaction_status": status,
                "payout_item": {"sender_item_id": payment["payment_id"]},
            },
        }

    payroll.webhook(event("SUCCESS"))
    payroll.webhook(event("RETURNED"))
    assert payroll.get(payment["payment_id"])["status"] == PayStatus.RETURNED
    paid_notices = len([n for n in outbox.sent(FIRST) if n.subject.startswith("Paid")])
    payroll.webhook(event("SUCCESS"))
    assert payroll.get(payment["payment_id"])["status"] == PayStatus.RETURNED
    assert len([n for n in outbox.sent(FIRST) if n.subject.startswith("Paid")]) == paid_notices


def test_each_paypal_event_counts_once(seed):
    fake = FakePayPal()
    config = load_settings(
        {
            "CREWPAY_SEED_DIR": str(SEED_DIR),
            "PAYPAL_CLIENT_ID": "id",
            "PAYPAL_CLIENT_SECRET": "secret",
            "PAYPAL_WEBHOOK_ID": "WH-1",
        }
    )
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    s = Services(config, store)
    s.payroll.paypal = client_for(fake)
    client = TestClient(create_app(config, s))
    event = {"id": "WH-EVT-1", "event_type": "PAYMENT.PAYOUTS-ITEM.SUCCEEDED", "resource": {}}
    signed = {
        "paypal-transmission-id": "T-1",
        "paypal-cert-url": "https://api.paypal.com/c",
        "paypal-transmission-sig": "good",
    }
    first = client.post("/api/paypal/webhook", json=event, headers=signed)
    again = client.post("/api/paypal/webhook", json=event, headers=signed)
    assert first.status_code == again.status_code == 200
    assert "duplicate" not in first.json() and again.json()["duplicate"] is True


def test_staff_see_a_masked_payout_address_and_the_crew_member_the_whole_one(mem, outbox):
    desk, payroll = wired(mem, outbox, receiver="demo-crew@personal.example")
    payment = payroll.for_plan(approve(desk).plan_id)
    box = ToolBox()

    def seen(caller):
        ctx = Context(mem, caller, desk=desk, payroll=payroll)
        return box.call("payment_status", {"payment_id": payment["payment_id"]}, ctx)["receiver"]

    assert seen(Caller("c", Role.CREW, FIRST)) == "demo-crew@personal.example"
    assert seen(Caller("vikram.rao", Role.APPROVER)) == "d***@personal.example"


def test_a_payout_paypal_never_answered_is_retried_not_marked_failed(mem, outbox):
    """No answer means PayPal may already have the batch: it stays queued and the status check
    sends it again under the same batch id, which PayPal never pays twice."""
    fake = FakePayPal()
    down = {"on": True}

    def flaky(request):
        if down["on"] and request.url.path == "/v1/payments/payouts" and request.method == "POST":
            raise httpx.ConnectError("timed out")
        return fake(request)

    paypal = PayPalClient(
        "id",
        "secret",
        "https://api-m.sandbox.paypal.com",
        http=httpx.Client(transport=httpx.MockTransport(flaky)),
        sleep=lambda s: None,
    )
    desk, payroll = wired(mem, outbox, paypal)
    payment = payroll.for_plan(approve(desk).plan_id)
    assert payment["status"] == PayStatus.QUEUED and payment["retry"]
    down["on"] = False
    payroll.sync()
    sent = payroll.get(payment["payment_id"])
    assert sent["status"] in (PayStatus.SENT, PayStatus.PAID) and "retry" not in sent
    assert fake.sent["sender_batch_header"]["sender_batch_id"] == payment["payment_id"]
