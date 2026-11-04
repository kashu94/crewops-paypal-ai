"""Read an operation from a directory of JSON files."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from crewpay.domain import (
    Airline,
    Certificate,
    Crew,
    DutyHistory,
    Gate,
    Leg,
    Rates,
    Rule,
    Standby,
    Trip,
)
from crewpay.domain.dataset import Dataset

FILES = {
    "airlines": Airline,
    "crew": Crew,
    "certificates": Certificate,
    "histories": DutyHistory,
    "standby": Standby,
    "legs": Leg,
    "trips": Trip,
    "rules": Rule,
    "gates": Gate,
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_meta(directory: Path) -> dict[str, Any]:
    return _read(directory / "meta.json")


def load_dataset(directory: Path) -> Dataset:
    parsed = {
        name: TypeAdapter(tuple[model, ...]).validate_python(_read(directory / f"{name}.json"))
        for name, model in FILES.items()
    }
    meta = read_meta(directory)
    return Dataset(
        **parsed,
        rates=Rates.model_validate(_read(directory / "rates.json")),
        as_of=datetime.fromisoformat(meta["as_of"]),
    )


_DATE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)")


def shifted(data: Dataset, meta: dict[str, Any], days: int) -> tuple[Dataset, dict[str, Any]]:
    """The same operation moved ``days`` whole days later, every date and time with it.

    Duty limits count calendar days and night is a time of day, so moving everything by whole
    days changes no answer: the same captain is illegal on the same trip, a week ahead of
    whenever the demo is opened. Dates inside ids (``SY201-2026-11-05``) move too, so every
    reference still matches.
    """
    if days == 0:
        return data, meta

    def move(text: str) -> str:
        def one(m: re.Match[str]) -> str:
            day = date(int(m[1]), int(m[2]), int(m[3])) + timedelta(days=days)
            return day.isoformat()

        return _DATE.sub(one, text)

    def again[R](records: tuple[R, ...], model: Any) -> tuple[R, ...]:
        raw = json.dumps([r.model_dump(mode="json") for r in records])  # type: ignore[attr-defined]
        return TypeAdapter(tuple[model, ...]).validate_python(json.loads(move(raw)))

    moved = Dataset(
        **{name: again(getattr(data, name), model) for name, model in FILES.items()},
        rates=data.rates,
        as_of=data.as_of + timedelta(days=days),
    )
    return moved, {**json.loads(move(json.dumps(meta))), "shifted_days": days}
