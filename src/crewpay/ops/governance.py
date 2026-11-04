"""Governance: who changed what, which questions were refused, and the duty limits.

* **Activity.** Every change made through a tool (a plan submitted or approved, a callout
  answered, an unavailability reported) is logged with who made it, their role and when.
* **Security.** Every question the assistant refused, and why, is logged for the admin.
* **Duty limits.** An admin may make a limit stricter than the regulator's (a shorter duty
  day, longer rest). Relaxing one, back towards the regulator's figure, needs a super admin,
  the way loosening a company fatigue limit needs a senior manager's signature. No one can
  take a limit past the regulator's figure. Each change needs a reason and is logged; the
  engine applies it at once.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from crewpay.domain import Rule

Direction = Literal["lower", "higher"]

STRICTER: dict[tuple[str, str], Direction] = {
    ("FDTL-01", "base_hours"): "lower",
    ("FDTL-02", "max_fdp_hours"): "lower",
    ("FDTL-02", "max_landings"): "lower",
    ("FDTL-03", "max_hours"): "lower",
    ("FDTL-04", "max_hours"): "lower",
    ("FDTL-05", "min_hours"): "higher",
    ("FDTL-06", "clear_days"): "higher",
}
"""The limits an admin may change, and which way is stricter."""


class LimitError(ValueError):
    pass


def _key(at: datetime) -> str:
    return f"{at.isoformat()}#{time.time_ns():020d}#{uuid.uuid4().hex[:4]}"


def log(docs: Any, kind: Literal["activity", "security"], **entry: Any) -> None:
    at = datetime.now(UTC)
    docs.put_doc(kind, _key(at), {"at": at.isoformat(), **entry})


def entries(docs: Any, kind: str, limit: int = 50) -> list[dict[str, Any]]:
    return sorted(docs.docs(kind), key=lambda d: d["at"], reverse=True)[:limit]


@dataclass(frozen=True, slots=True)
class Limit:
    rule_id: str
    title: str
    param: str
    value: float
    regulator: float
    stricter: Direction

    @property
    def tightened(self) -> bool:
        return self.value != self.regulator


def limits(store: Any) -> list[Limit]:
    rules = {r.rule_id: r for r in store.dataset().rules}
    set_before = {d["key"]: d for d in store.docs("rule_limit")}
    out = []
    for (rule_id, param), way in STRICTER.items():
        rule = rules.get(rule_id)
        if rule is None or param not in rule.params:
            continue
        value = rule.params[param]
        regulator = set_before.get(f"{rule_id}:{param}", {}).get("regulator", value)
        out.append(Limit(rule_id, rule.title, param, value, regulator, way))
    return out


def relaxes(limit: Limit, value: float) -> bool:
    """Whether moving ``limit`` to ``value`` makes the rule less strict than it is now."""
    return value > limit.value if limit.stricter == "lower" else value < limit.value


def set_limit(
    store: Any,
    rule_id: str,
    param: str,
    value: float,
    by: str,
    reason: str,
    may_relax: bool = False,
    role: str = "admin",
) -> tuple[Limit, Rule]:
    """Change one limit, never less strict than the regulator's. Only a caller who
    ``may_relax`` can make it less strict than it is now. Returns it and the rule."""
    if len(reason.strip()) < 5:
        raise LimitError("give the reason for the change")
    current = next((x for x in limits(store) if (x.rule_id, x.param) == (rule_id, param)), None)
    if current is None:
        editable = ", ".join(f"{r} {p}" for r, p in STRICTER)
        raise LimitError(f"{rule_id} {param} cannot be changed here (only: {editable})")
    looser = value > current.regulator if current.stricter == "lower" else value < current.regulator
    if looser:
        raise LimitError(
            f"{value:g} is less strict than the regulator's {current.regulator:g}: limits can "
            "only be made stricter"
        )
    if value <= 0:
        raise LimitError("a limit must be positive")
    if relaxes(current, value) and not may_relax:
        raise LimitError(
            f"{value:g} relaxes {rule_id} {param} from {current.value:g}: only a super admin "
            "can relax a limit"
        )
    rule = next(r for r in store.dataset().rules if r.rule_id == rule_id)
    changed = rule.model_copy(update={"params": {**rule.params, param: value}})
    store.set_rule(changed)
    key = f"{rule_id}:{param}"
    store.put_doc(
        "rule_limit",
        key,
        {
            "key": key,
            "regulator": current.regulator,
            "value": value,
            "by": by,
            "reason": reason.strip(),
            "at": datetime.now(UTC).isoformat(),
        },
    )
    log(
        store,
        "activity",
        by=by,
        role=role,
        action="set_rule_limit",
        detail=f"{rule_id} {param}: {current.value:g} to {value:g} "
        f"(regulator {current.regulator:g}): {reason.strip()}",
    )
    return Limit(rule_id, rule.title, param, value, current.regulator, current.stricter), changed
