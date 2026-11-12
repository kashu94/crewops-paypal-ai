"""Alert addresses: which email addresses hear about what.

An admin adds an address to one group: the crew desk, the duty supervisor, or one of the
operational teams (``crewpay.ops.teams``). CrewPay keeps the list with the operation; the
notices themselves reach people in the console, on the board and as desktop alerts.
"""

from __future__ import annotations

import re
from typing import Any

from crewpay.engine.clock import now as clock_now
from crewpay.ops.teams import ALERT_GROUPS

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")


class AlertError(ValueError):
    pass


class AlertAddresses:
    def __init__(self, docs: Any) -> None:
        self.docs = docs

    def list(self) -> list[dict]:
        rows = sorted(self.docs.docs("alert_address"), key=lambda r: (r["group"], r["address"]))
        return [
            {
                "address": r["address"],
                "team": r["group"],
                "team_title": ALERT_GROUPS.get(r["group"], r["group"]),
                "added_by": r["added_by"],
                "at": r["at"],
                "status": "kept (no email on this server)",
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
            "at": clock_now().isoformat(),
        }
        self.docs.put_doc("alert_address", key, row)
        return next(r for r in self.list() if (r["team"], r["address"]) == (group, address))

    def remove(self, address: str, group: str) -> None:
        key = f"{group}:{address.strip().lower()}"
        if self.docs.get_doc("alert_address", key) is None:
            raise AlertError(f"{address} is not subscribed to {group}")
        self.docs.delete_doc("alert_address", key)
