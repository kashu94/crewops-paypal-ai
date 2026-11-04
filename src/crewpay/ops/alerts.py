"""Alert addresses: which email addresses hear about what.

An admin adds an address to one group: the crew desk, the duty supervisor, or one of the
operational teams (``crewpay.ops.teams``). CrewPay keeps the list with the operation; the
notices themselves reach people in the console, on the board and as desktop alerts.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from typing import Any

from crewpay.ops.teams import ALERT_GROUPS

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


class AlertError(ValueError):
    pass


class AlertAddresses:
    def __init__(self, docs: Any, sns: Any = None, topic_arn: str = "") -> None:
        self.docs, self.sns, self.topic_arn = docs, sns, topic_arn

    def _status(self, row: dict) -> str:
        if self.sns is None or not row.get("subscription"):
            return "kept (no email on this server)"
        try:
            attrs = self.sns.get_subscription_attributes(SubscriptionArn=row["subscription"])
        except Exception:  # deleted by its owner, or the topic was rebuilt
            return "not subscribed"
        pending = attrs["Attributes"].get("PendingConfirmation") == "true"
        return "waiting for confirmation" if pending else "confirmed"

    def list(self) -> list[dict]:
        rows = sorted(self.docs.docs("alert_address"), key=lambda r: (r["group"], r["address"]))
        return [
            {
                "address": r["address"],
                "team": r["group"],
                "team_title": ALERT_GROUPS.get(r["group"], r["group"]),
                "added_by": r["added_by"],
                "at": r["at"],
                "status": self._status(r),
            }
            for r in rows
        ]

    def add(self, address: str, group: str, by: str) -> dict:
        address = address.strip().lower()
        if not EMAIL.match(address):
            raise AlertError(f"{address!r} is not an email address")
        if group not in ALERT_GROUPS:
            raise AlertError(f"no group {group!r}; groups are {', '.join(ALERT_GROUPS)}")
        key = f"{group}:{address}"
        if self.docs.get_doc("alert_address", key) is not None:
            raise AlertError(f"{address} already hears {ALERT_GROUPS[group]} alerts")
        row = {
            "address": address,
            "group": group,
            "added_by": by,
            "at": datetime.now(UTC).isoformat(),
            "subscription": None,
        }
        if self.sns is not None and self.topic_arn:
            reply = self.sns.subscribe(
                TopicArn=self.topic_arn,
                Protocol="email",
                Endpoint=address,
                Attributes={"FilterPolicy": f'{{"group": ["{group}"]}}'},
                ReturnSubscriptionArn=True,
            )
            row["subscription"] = reply["SubscriptionArn"]
        self.docs.put_doc("alert_address", key, row)
        return next(r for r in self.list() if (r["team"], r["address"]) == (group, address))

    def remove(self, address: str, group: str) -> None:
        key = f"{group}:{address.strip().lower()}"
        row = self.docs.get_doc("alert_address", key)
        if row is None:
            raise AlertError(f"{address} is not subscribed to {group}")
        if self.sns is not None and row.get("subscription"):
            # Already gone, perhaps; the record goes either way.
            with contextlib.suppress(Exception):
                self.sns.unsubscribe(SubscriptionArn=row["subscription"])
        self.docs.delete_doc("alert_address", key)
