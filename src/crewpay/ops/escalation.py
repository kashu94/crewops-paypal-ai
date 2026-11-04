"""Escalation: what the desk has left untouched goes to the duty supervisor.

Run on the same schedule as the watch. Four things escalate when nobody has acted on them
for ``UNTOUCHED`` (ten minutes):

* a plan still waiting for an approver;
* a callout with nobody left to ask, waiting for the desk's decision or its approval;
* a high-severity watch problem with no callout and no plan on its seat;
* a high-severity disruption report its desk has not started.

Each escalates once. The supervisor's notice says what is waiting, for how long, and on whose
desk, from the same records the screens read.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from crewpay.engine.severity import Level, for_issue
from crewpay.engine.watch import scan
from crewpay.ops.callout import CalloutDesk, Status
from crewpay.ops.notify import SUPERVISOR, Notice, Outbox

UNTOUCHED = timedelta(minutes=10)


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def escalate(
    store: Any,
    desk: CalloutDesk,
    outbox: Outbox,
    now: datetime,
    after: timedelta = UNTOUCHED,
    reports: Any = None,
) -> list[Notice]:
    """Tell the duty supervisor about everything untouched for ``after``. Returns what was sent."""
    done = {d["ref"] for d in store.docs("escalated")}
    due: list[Notice] = []

    def raise_(ref: str, subject: str, body: str) -> None:
        if ref not in done:
            due.append(Notice(SUPERVISOR, f"Escalated: {subject}", body, now, ref))

    if desk.plans is not None:
        for plan in desk.plans.waiting():
            waited = now - plan.submitted_at
            if waited >= after:
                raise_(
                    plan.plan_id,
                    f"{plan.rank} on {plan.trip_id} waits for approval",
                    f"{plan.plan_id} ({plan.summary}, INR {plan.cost:,}) was submitted by "
                    f"{plan.submitted_by} {_minutes(waited)} minutes ago and no approver "
                    "has decided it.",
                )

    for callout in desk.all():
        if callout.status not in (Status.NEEDS_DECISION, Status.AWAITING_APPROVAL):
            continue
        moments = [callout.opened_at] + [o.answered_at for o in callout.offers if o.answered_at]
        if callout.proposal is not None:
            moments.append(callout.proposal.proposed_at)
        waited = now - max(moments)
        if waited >= after:
            what = (
                "waits for an approver"
                if callout.status is Status.AWAITING_APPROVAL
                else "has nobody left to ask"
            )
            raise_(
                f"{callout.callout_id}:{callout.status}",
                f"{callout.rank} on {callout.trip_id} {what}",
                f"Callout {callout.callout_id}, opened by {callout.opened_by}, {what} and has "
                f"not moved for {_minutes(waited)} minutes. Nothing has been cancelled.",
            )

    op = store.operation()
    alerted = {d["issue_id"]: d.get("at") for d in store.docs("alerted")}
    handled = {(c.trip_id, c.vacated_by) for c in desk.all()}
    if desk.plans is not None:
        handled |= {(p.trip_id, p.replacing) for p in desk.plans.all()}
    for issue in scan(op):
        first_seen = alerted.get(issue.issue_id)
        if first_seen is None or (issue.trip_id, issue.crew_id) in handled:
            continue
        waited = now - datetime.fromisoformat(first_seen)
        severity = for_issue(op, issue)
        if severity.level is Level.HIGH and waited >= after:
            raise_(
                issue.issue_id,
                f"{issue.name} on {issue.trip_id}" if issue.trip_id else issue.name,
                f"{issue.name} ({issue.crew_id}): {issue.detail}. Raised {_minutes(waited)} "
                f"minutes ago and nobody has started a callout or a plan "
                f"({'; '.join(severity.reasons)}).",
            )

    for report in reports.all() if reports is not None else []:
        waited = now - report.filed_at
        if str(report.status) == "open" and report.severity == "high" and waited >= after:
            raise_(
                report.report_id,
                f"{report.title} not started",
                f"{report.report_id} ({report.title}) was assigned to {report.desk_id} "
                f"{_minutes(waited)} minutes ago and nobody has started a callout or a plan "
                f"({'; '.join(report.severity_reasons)}).",
            )

    for notice in due:
        outbox.send(notice)
        store.put_doc("escalated", notice.ref, {"ref": notice.ref, "at": now.isoformat()})
    return due
