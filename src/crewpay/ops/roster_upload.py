"""Roster uploads: the next roster, or corrections to this one, as a file.

Rostering publishes in bulk. An upload is a JSON object with any of these collections, each
record in the same shape as the demo data's files (``data/seed``):

    {"crew": [...], "certificates": [...], "standby": [...], "trips": [...]}

Records replace the ones with the same identity (crew id; crew id and certificate kind; crew
id and standby date; trip id) and anything new is added. An upload never deletes.

The whole merged roster is checked before anything is written, because most mistakes only
show against other records: a trip naming a crew member who does not exist, a leg nobody
flies, two trips putting one person in two places. If anything is wrong nothing is written.
Then the rules engine judges the merged roster, and the upload reports every problem it
would add (someone over a limit, a certificate lapsing on a trip), so rostering sees what it
is about to hand the crew desk. A dry run stops there.

A trip that already exists keeps its history: its new crew list is applied as a roster change
by the person uploading, at the trip's current version, the same way a controller's change is.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError

from crewpay.domain import Certificate, Crew, Standby, Trip
from crewpay.engine import build_operation
from crewpay.engine.watch import scan
from crewpay.store.roster import _key

MAX_BYTES = 2 * 1024 * 1024
COLLECTIONS: dict[str, type] = {
    "crew": Crew,
    "certificates": Certificate,
    "standby": Standby,
    "trips": Trip,
}


class UploadError(ValueError):
    pass


def parse(raw: bytes | str | dict) -> dict[str, list]:
    """The upload's collections, each record validated on its own."""
    if isinstance(raw, (bytes, str)):
        if len(raw) > MAX_BYTES:
            raise UploadError("an upload is at most 2 MB")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as err:
            raise UploadError(f"not JSON: {err}") from err
    if not isinstance(raw, dict) or not raw:
        raise UploadError('an upload is a JSON object of collections, such as {"trips": [...]}')
    unknown = sorted(set(raw) - set(COLLECTIONS))
    if unknown:
        raise UploadError(
            f"unknown collections {', '.join(unknown)}; uploads take {', '.join(COLLECTIONS)}"
        )
    out: dict[str, list] = {}
    for name, rows in raw.items():
        try:
            out[name] = list(TypeAdapter(tuple[COLLECTIONS[name], ...]).validate_python(rows))
        except ValidationError as err:
            first = err.errors()[:3]
            detail = "; ".join(
                f"{name}[{e['loc'][0]}].{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}"
                for e in first
            )
            raise UploadError(detail) from err
    return out


def upload(
    store: Any,
    content: bytes | str | dict,
    by: str,
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        parsed = parse(content)
    except UploadError as err:
        return {
            "status": "rejected",
            "applied": False,
            "errors": [str(err)],
            "counts": {},
            "new_problems": [],
        }

    data = store.dataset()
    counts: dict[str, dict[str, int]] = {}
    merged_data = data
    for name, records in parsed.items():
        current = {_key(name, r) if name != "trips" else r.trip_id: r for r in getattr(data, name)}
        keys = [_key(name, r) if name != "trips" else r.trip_id for r in records]
        if len(set(keys)) != len(keys):
            return {
                "status": "rejected",
                "applied": False,
                "counts": {},
                "new_problems": [],
                "errors": [f"{name}: a record is listed twice"],
            }
        replaced = sum(k in current for k in keys)
        counts[name] = {"added": len(keys) - replaced, "replaced": replaced}
        current.update(dict(zip(keys, records, strict=True)))
        merged_data = replace(merged_data, **{name: tuple(current.values())})

    errors: list[str] = []
    crew_ids = {c.crew_id for c in merged_data.crew}
    legs = {leg.leg_id for leg in merged_data.legs}
    for trip in merged_data.trips:
        for seat in trip.crew:
            if seat.crew_id not in crew_ids:
                errors.append(f"{trip.trip_id}: no crew member {seat.crew_id}")
        errors += [f"{trip.trip_id}: no leg {leg}" for leg in trip.leg_ids if leg not in legs]
    existing_trips = {t.trip_id: t for t in data.trips}
    for trip in parsed.get("trips", []):
        old = existing_trips.get(trip.trip_id)
        if old is not None and (old.days, old.airline, old.tail) != (
            trip.days,
            trip.airline,
            trip.tail,
        ):
            errors.append(f"{trip.trip_id}: an upload can change an existing trip's crew only")
    for name in ("certificates", "standby"):
        errors += [
            f"{name}: no crew member {r.crew_id}"
            for r in parsed.get(name, [])
            if r.crew_id not in crew_ids
        ]
    if not errors:
        try:
            before = store.operation(now)
            after = replace(
                build_operation(merged_data),
                now=before.now,
                unavailable=before.unavailable,
                unavailable_since=before.unavailable_since,
            )
        except Exception as err:  # the engine refuses rosters that contradict themselves
            errors.append(str(err))
    if errors:
        return {
            "status": "rejected",
            "applied": False,
            "counts": counts,
            "errors": errors[:20],
            "new_problems": [],
        }

    known = {i.issue_id for i in scan(before)}
    problems = [
        {
            "crew_id": i.crew_id,
            "name": i.name,
            "trip_id": i.trip_id,
            "kind": i.kind,
            "rules": list(i.rule_ids),
            "detail": i.detail,
        }
        for i in scan(after)
        if i.issue_id not in known
    ]
    result = {
        "status": "accepted",
        "applied": False,
        "counts": counts,
        "errors": [],
        "new_problems": problems,
    }
    if dry_run:
        return result

    for name in ("crew", "certificates", "standby"):
        if name in parsed:
            store.put_records(name, parsed[name])
    if "trips" in parsed:
        existing = {t.trip_id for t in data.trips}
        store.add_trips([t for t in parsed["trips"] if t.trip_id not in existing])
        for trip in parsed["trips"]:
            if trip.trip_id in existing and trip.crew != existing_trips[trip.trip_id].crew:
                _, version = store.trip(trip.trip_id)
                store.change_crew(trip.trip_id, list(trip.crew), version, by, "roster upload")
    return {**result, "applied": True}
