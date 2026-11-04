from datetime import UTC, datetime

from test_advisor import FixedRouter, Script

from crewpay.agent import Caller, Role
from crewpay.agent.advisor import Advisor
from crewpay.agent.memory import Conversations
from crewpay.agent.router import Intent
from crewpay.domain import Rank
from crewpay.ops import DESK, CalloutDesk, Notice, Status, StoreOutbox

TRIP, SICK = "T-1018", "C-1111"
NOW = datetime(2026, 10, 29, 18, 30, tzinfo=UTC)


def test_documents_round_trip_in_both_stores(store):
    store.put_doc("thing", "b", {"n": 2, "nested": {"ok": True}})
    store.put_doc("thing", "a", {"n": 1})
    assert store.get_doc("thing", "b") == {"n": 2, "nested": {"ok": True}}
    assert [d["n"] for d in store.docs("thing")] == [1, 2]
    store.delete_doc("thing", "a")
    assert store.get_doc("thing", "a") is None and len(store.docs("thing")) == 1
    assert store.docs("other") == []


def test_a_callout_carries_on_from_another_server(store):
    first = CalloutDesk(store, StoreOutbox(store), clock=lambda: NOW, docs=store)
    callout = first.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")
    asked = callout.current.crew_id

    second = CalloutDesk(store, StoreOutbox(store), clock=lambda: NOW, docs=store)
    assert [c.callout_id for c in second.all()] == [callout.callout_id]
    second.respond(callout.callout_id, asked, accept=False)

    again = first.get(callout.callout_id)  # the first server reads the change
    assert again.offers[0].answer == "declined" and again.current.crew_id != asked
    assert first.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer").callout_id == callout.callout_id
    assert again.status is Status.OFFERED


def test_notices_are_kept_in_order_for_every_server(store):
    outbox = StoreOutbox(store)
    outbox.send(Notice(DESK, "first", "a", NOW, "x"))
    outbox.send(Notice("C-1157", "second", "b", NOW.replace(minute=31), "x"))
    assert [n.subject for n in StoreOutbox(store).sent()] == ["first", "second"]
    assert [n.subject for n in outbox.sent("C-1157")] == ["second"]


def test_a_follow_up_finds_its_conversation_on_another_server(store, seed):
    caller = Caller("ananya.iyer", Role.CONTROLLER)
    planner = Script("C-1157 is legal for T-1018.", "Yes, also for T-1010.")
    advisor = Advisor(store, planner, seed.rules, router=FixedRouter(Intent.LEGALITY))
    convo, _ = Conversations(docs=store).ask(advisor, "Is C-1157 legal for T-1018?", caller)

    elsewhere = Conversations(docs=store)
    again, _ = elsewhere.ask(advisor, "And for T-1010?", caller, convo.id)
    assert again.id == convo.id
    assert planner.requests[-1]["messages"][2]["content"] == "C-1157 is legal for T-1018."
    assert elsewhere.get(convo.id, Caller("rohit.malhotra", Role.CONTROLLER)).id != convo.id
