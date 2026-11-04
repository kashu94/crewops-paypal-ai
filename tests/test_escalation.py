from datetime import timedelta

import pytest
from conftest import SEED_DIR
from test_callout import LEGAL_CAPTAINS, SICK, TRIP, Clock

from crewpay.domain import Rank
from crewpay.engine.watch import scan
from crewpay.ops import SUPERVISOR, CalloutDesk, MemoryOutbox
from crewpay.ops.escalation import escalate
from crewpay.seed import read_meta
from crewpay.store import MemoryStore


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def outbox():
    return MemoryOutbox()


@pytest.fixture
def desk(mem, outbox, clock):
    return CalloutDesk(mem, outbox, clock=clock, docs=mem, four_eyes=True)


def test_a_plan_nobody_approves_reaches_the_supervisor_once(mem, desk, outbox, clock):
    plan = desk.plans.submit(TRIP, SICK, LEGAL_CAPTAINS[0], "ananya.iyer")
    assert escalate(mem, desk, outbox, clock()) == []  # just submitted
    clock.advance(11)
    [sent] = escalate(mem, desk, outbox, clock())
    assert sent.to == SUPERVISOR and sent.ref == plan.plan_id
    assert "submitted by ananya.iyer 11 minutes ago" in sent.body
    assert escalate(mem, desk, outbox, clock()) == []  # once only
    assert outbox.sent(SUPERVISOR) == [sent]


def test_a_callout_with_nobody_left_escalates(mem, desk, outbox, clock):
    callout = desk.open(TRIP, Rank.CAPTAIN, SICK, by="ananya.iyer")
    while desk.get(callout.callout_id).current is not None:
        current = desk.get(callout.callout_id).current
        desk.respond(callout.callout_id, current.crew_id, False)
    clock.advance(12)
    sent = escalate(mem, desk, outbox, clock())
    assert any("has nobody left to ask" in n.subject for n in sent)
    assert all("Nothing has been cancelled" in n.body for n in sent if n.ref.startswith("CO-"))


def test_a_high_watch_problem_nobody_took_up_escalates_until_handled(mem, desk, outbox, clock):
    op = mem.operation()
    issue = next(i for i in scan(op) if i.kind == "illegal")
    raised = clock() - timedelta(minutes=30)
    mem.put_doc("alerted", issue.issue_id, {"issue_id": issue.issue_id, "at": raised.isoformat()})
    # Judge as if the trip were about to report, so the problem is high severity.
    report = op.days_of(issue.trip_id)[0].report
    real = mem.operation
    mem.operation = lambda now=None: real(report - timedelta(hours=3))
    sent = escalate(mem, desk, outbox, clock())
    assert [n.ref for n in sent] == [issue.issue_id]
    assert "nobody has started a callout or a plan" in sent[0].body
