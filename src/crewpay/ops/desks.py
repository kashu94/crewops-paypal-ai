"""Controller desks: who works which disruption.

Three controllers share the operation, one desk each. A new disruption goes to the desk with
the fewest open ones (ties go to the fewest overall, then desk order), so the load evens out
and the person reporting never picks. Any desk can read any disruption; only the owning
desk's controller plans it. Cover is still checked across desks, because two disruptions on
different desks can want the same standby.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Desk:
    desk_id: str
    name: str
    controller: str
    """The controller's username."""
    controller_name: str


DESKS: tuple[Desk, ...] = (
    Desk("desk-1", "Desk 1", "ananya.iyer", "Ananya Iyer"),
    Desk("desk-2", "Desk 2", "rohit.malhotra", "Rohit Malhotra"),
    Desk("desk-3", "Desk 3", "divya.rao", "Divya Rao"),
)


def desk(desk_id: str | None) -> Desk | None:
    return next((d for d in DESKS if d.desk_id == desk_id), None)


def desk_of(user: str) -> Desk | None:
    """The desk ``user`` controls, if any."""
    return next((d for d in DESKS if d.controller == user), None)


def assign_desk(open_counts: Mapping[str, int], totals: Mapping[str, int]) -> Desk:
    """Fewest open, then fewest overall, then desk order."""
    return min(
        DESKS,
        key=lambda d: (open_counts.get(d.desk_id, 0), totals.get(d.desk_id, 0), d.desk_id),
    )
