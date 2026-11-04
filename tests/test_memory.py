import io
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SEED_DIR
from test_advisor import FixedRouter, Script

from crewpay.agent import Caller, Role
from crewpay.agent.advisor import Advisor
from crewpay.agent.build import build_advisor
from crewpay.agent.chat import run
from crewpay.agent.guard import GuardChain, PatternGuard, PromptGuard
from crewpay.agent.memory import MAX_TURNS, Conversations
from crewpay.agent.router import Intent
from crewpay.seed import read_meta
from crewpay.settings import load_settings
from crewpay.store import MemoryStore

CONTROLLER = Caller("ananya.iyer", Role.CONTROLLER)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


def advisor(mem, seed, planner):
    return Advisor(mem, planner, seed.rules, router=FixedRouter(Intent.LEGALITY))


def test_a_follow_up_sees_earlier_turns_and_who_was_discussed(mem, seed):
    planner = Script(
        [("search_crew", {"query": "a. nair"})],
        [("check_legality", {"crew_id": "C-1111", "trip_id": "T-1018"})],
        "No: C-1111 fails FDTL-08 on T-1018.",
        "Also not legal.",
    )
    chats = Conversations()
    first, _ = chats.ask(advisor(mem, seed, planner), "Is Pooja legal for T-1018?", CONTROLLER)
    second, _ = chats.ask(advisor(mem, seed, planner), "And for T-1010?", CONTROLLER, first.id)

    assert second.id == first.id
    sent = planner.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[2]["content"] == "No: C-1111 fails FDTL-08 on T-1018."
    assert sent[0]["content"].endswith(
        "Earlier in this conversation: already discussed T-1018, C-1111 (A. Nair)."
    )
    assert second.focus[0] == "T-1010"


def test_only_the_last_turns_are_kept(mem, seed):
    planner = Script(*[f"answer {i}" for i in range(MAX_TURNS + 2)])
    chats = Conversations()
    conversation_id = None
    for i in range(MAX_TURNS + 2):
        convo, _ = chats.ask(advisor(mem, seed, planner), f"q{i}", CONTROLLER, conversation_id)
        conversation_id = convo.id
    history = convo.history()
    assert len(history) == 2 * MAX_TURNS and history[0]["content"] == "q2"


def test_another_caller_never_sees_someone_elses_conversation(mem, seed):
    chats = Conversations()
    mine, _ = chats.ask(advisor(mem, seed, Script("ok")), "Is C-1157 legal?", CONTROLLER)
    other = Caller("rohit.malhotra", Role.CONTROLLER)
    theirs = chats.get(mine.id, other)
    assert theirs.id != mine.id and theirs.turns == []


def test_idle_conversations_are_dropped():
    now = [datetime(2026, 11, 1, 3, 0, tzinfo=UTC)]
    chats = Conversations(clock=lambda: now[0])
    convo = chats.get(None, CONTROLLER)
    convo.updated = now[0]
    now[0] += timedelta(minutes=31)
    assert chats.get(convo.id, CONTROLLER).id != convo.id


def test_build_uses_pattern_screening_and_adds_prompt_guard_when_configured(mem):
    base = {
        "CREWPAY_BEDROCK_API_KEY": "k",
        "PROMPT_GUARD_URL": "",
        "CREWPAY_SEED_DIR": str(SEED_DIR),
    }
    built = build_advisor(load_settings(base), mem)
    assert isinstance(built.guard, GuardChain)
    assert [type(g) for g in built.guard.guards] == [PatternGuard]
    assert built.router.model.endpoint.name == "router"
    assert built.router.backup is built.planner

    config = load_settings({**base, "PROMPT_GUARD_URL": "https://g"})
    built = build_advisor(config, mem)
    assert [type(g) for g in built.guard.guards] == [PatternGuard, PromptGuard]
    assert "FDTL-08" in built.verifier.reference


def test_the_chat_loop_answers_and_starts_over_on_request(mem, seed):
    planner = Script("C-1157 is legal.", "Fresh start.")
    out = io.StringIO()
    run(
        advisor(mem, seed, planner),
        CONTROLLER,
        io.StringIO("Is C-1157 legal?\n/new\nHi\n/quit\n"),
        out,
    )
    text = out.getvalue()
    assert "crewpay> C-1157 is legal." in text and "(new conversation)" in text
    assert len(planner.requests[1]["messages"]) == 2  # system + the new question only
