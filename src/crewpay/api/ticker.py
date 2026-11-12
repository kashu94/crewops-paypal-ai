"""The background loop: what has to happen even when nobody clicks anything.

Every ``CREWPAY_TICKER_SECONDS`` the server moves callout offers nobody answered to the next
crew member, tells the crew desk once about each new problem the watch finds, escalates to the
duty supervisor what the desk has left untouched, keeps the board's brief fresh, and brings
crew payments up to date with PayPal.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from crewpay.engine.clock import now as clock_now
from crewpay.engine.watch import scan
from crewpay.ops import DESK, Notice
from crewpay.ops.escalation import escalate

log = logging.getLogger("crewpay.ticker")


def tick(s: Any) -> dict[str, int]:
    """One round of the loop over the services ``s``."""
    from crewpay.api.app import refresh_brief

    moved = s.desk.tick()
    raised = {d["issue_id"] for d in s.store.docs("alerted")}
    new = [i for i in scan(s.store.operation()) if i.kind != "certificate"]
    new = [i for i in new if i.issue_id not in raised]
    now = clock_now()
    for issue in new:
        cover = f" Cheapest legal cover: {issue.suggestion}." if issue.suggestion else ""
        where = f" on {issue.trip_id}" if issue.trip_id else ""
        s.outbox.send(
            Notice(
                to=DESK,
                subject=f"Watch: {issue.name}{where}",
                body=f"{issue.name} ({issue.crew_id}){where}: {issue.detail}.{cover}",
                at=now,
                ref=issue.issue_id,
            )
        )
        s.store.put_doc(
            "alerted", issue.issue_id, {"issue_id": issue.issue_id, "at": now.isoformat()}
        )
    escalated = escalate(s.store, s.desk, s.outbox, now, reports=s.reports)
    try:
        briefed = refresh_brief(s)
    except Exception:  # a model outage must not stop the alerts above
        briefed = False
    synced = 0
    payroll = getattr(s, "payroll", None)
    if payroll is not None:
        try:
            synced = payroll.sync()
        except Exception:  # PayPal being slow must not stop the loop
            log.exception("payment sync failed")
    return {
        "offers_moved": len(moved),
        "alerts": len(new),
        "escalated": len(escalated),
        "brief": int(briefed),
        "payments_synced": synced,
    }


def start(s: Any, every: int) -> threading.Event | None:
    """Run ``tick`` every ``every`` seconds on a daemon thread; set the event to stop it."""
    if every <= 0:
        return None
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every):
            try:
                tick(s)
            except Exception:
                log.exception("background tick failed")

    threading.Thread(target=loop, name="crewpay-ticker", daemon=True).start()
    return stop
