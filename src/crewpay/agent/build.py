"""Putting the advisor together from the settings."""

from __future__ import annotations

from crewpay.agent.advisor import Advisor
from crewpay.agent.guard import GuardChain, PatternGuard, PromptGuard
from crewpay.agent.router import Router
from crewpay.agent.verify import EvidenceVerifier
from crewpay.models import Endpoint, Ledger, ModelClient
from crewpay.ops import CalloutDesk, Outbox, StoreOutbox
from crewpay.ops.reports import ReportDesk
from crewpay.settings import Settings
from crewpay.store import MemoryStore, Store


def build_advisor(
    config: Settings, store: Store | MemoryStore, outbox: Outbox | None = None
) -> Advisor:
    """The advisor model plans; a smaller, faster model routes, with the advisor model as the
    router's backup.

    Callouts are kept in the store, and their messages too unless another outbox is given,
    so any server can carry on a callout another one started.
    """
    ledger = Ledger(config.usage_log)
    planner = ModelClient(Endpoint.advisor(config), ledger, config=config)
    router_model = ModelClient(Endpoint.router(config), ledger, config=config)
    backup = planner
    guards = [PatternGuard()]
    if config.prompt_guard_url:
        guards.append(PromptGuard(config.prompt_guard_url, config.prompt_guard_token))
    rules = store.dataset().rules
    outbox = outbox or StoreOutbox(store)
    advisor = Advisor(
        store,
        planner,
        rules,
        router=Router(router_model, backup),
        guard=GuardChain(guards),
        verifier=EvidenceVerifier("\n".join(f"{r.rule_id} {r.text}" for r in rules)),
        desk=CalloutDesk(store, outbox, docs=store, four_eyes=True),
    )
    advisor.reports = ReportDesk(store, outbox, config.seed_dir)
    return advisor
