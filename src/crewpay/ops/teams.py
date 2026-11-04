"""The teams told about a disruption besides crew control, and who is on duty in each.

A disruption rarely stops at the crew desk. A gate closure is the gate controllers' problem
first; an aircraft fault is maintenance control's; passengers on a late flight hear it from
customer care. Each team has a fixed job, and which teams a report needs follows from its
kind alone, so the list is the same every time.

Who is on duty, per station or for the whole network, is demo data
(``data/seed/contacts.json``). Email addresses are not in there: an admin subscribes them to
a team's alerts (``crewpay.ops.alerts``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

NETWORK = "NETWORK"
"""Station of a team that serves every station from the operations centre."""


class Team(StrEnum):
    GATE_CONTROL = "gate_control"
    STATION_OPS = "station_ops"
    FLIGHT_INFO = "flight_info"
    OPS_MONITORING = "ops_monitoring"
    MAINTENANCE_CONTROL = "maintenance_control"
    CREW_RECORDS = "crew_records"
    CUSTOMER_CARE = "customer_care"


@dataclass(frozen=True, slots=True)
class TeamInfo:
    title: str
    job: str
    per_station: bool


TEAMS: dict[Team, TeamInfo] = {
    Team.GATE_CONTROL: TeamInfo(
        "Gate and stand control",
        "allocates gates and stands and moves departures off a closed gate",
        per_station=True,
    ),
    Team.STATION_OPS: TeamInfo(
        "Station duty manager",
        "runs the station: ground handling, boarding staff and passengers at the airport",
        per_station=True,
    ),
    Team.FLIGHT_INFO: TeamInfo(
        "Flight information",
        "updates the display screens, the website and the app with new times and gates",
        per_station=False,
    ),
    Team.OPS_MONITORING: TeamInfo(
        "Operations monitoring",
        "follows every flight from the operations centre and tells the captain",
        per_station=False,
    ),
    Team.MAINTENANCE_CONTROL: TeamInfo(
        "Maintenance control",
        "decides when an aircraft with a fault can fly again",
        per_station=False,
    ),
    Team.CREW_RECORDS: TeamInfo(
        "Training and crew records",
        "books licence, medical and training renewals and tells the crew member",
        per_station=False,
    ),
    Team.CUSTOMER_CARE: TeamInfo(
        "Customer care",
        "tells booked passengers by text and app message, and rebooks them",
        per_station=False,
    ),
}

ALERT_GROUPS: dict[str, str] = {
    "desk": "Crew desk (controllers on duty)",
    "supervisor": "Duty supervisor",
    **{str(t): info.title for t, info in TEAMS.items()},
}
"""Everyone an admin can subscribe an email address for."""

TEAMS_FOR_KIND: dict[str, tuple[Team, ...]] = {
    "crew_unavailable": (Team.STATION_OPS,),
    "planned_leave": (Team.CREW_RECORDS,),
    "certification_lapse": (Team.CREW_RECORDS,),
    "flight_delay": (
        Team.STATION_OPS,
        Team.OPS_MONITORING,
        Team.FLIGHT_INFO,
        Team.CUSTOMER_CARE,
    ),
    "aircraft_technical": (
        Team.MAINTENANCE_CONTROL,
        Team.OPS_MONITORING,
        Team.FLIGHT_INFO,
        Team.CUSTOMER_CARE,
    ),
    "station_disruption": (
        Team.STATION_OPS,
        Team.OPS_MONITORING,
        Team.FLIGHT_INFO,
        Team.CUSTOMER_CARE,
    ),
    "gate_closure": (Team.GATE_CONTROL, Team.STATION_OPS, Team.FLIGHT_INFO),
    "gate_change": (Team.GATE_CONTROL, Team.STATION_OPS, Team.FLIGHT_INFO),
}


@dataclass(frozen=True, slots=True)
class Contact:
    team: Team
    station: str
    name: str
    phone: str


@lru_cache(maxsize=4)
def contacts(seed_dir: Path) -> tuple[Contact, ...]:
    path = seed_dir / "contacts.json"
    if not path.is_file():
        return ()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(Contact(Team(r["team"]), r["station"], r["name"], r["phone"]) for r in rows)


def on_duty(seed_dir: Path, team: Team, station: str | None) -> Contact | None:
    """Who answers for ``team`` at ``station``; the network desk for a network team."""
    where = station if TEAMS[team].per_station else NETWORK
    return next((c for c in contacts(seed_dir) if c.team is team and c.station == where), None)


def teams_to_tell(seed_dir: Path, kind: str, stations: list[str]) -> list[dict]:
    """The teams a report of ``kind`` touching ``stations`` needs, each with who is on duty."""
    out = []
    for team in TEAMS_FOR_KIND.get(kind, ()):
        info = TEAMS[team]
        for station in (stations or [None]) if info.per_station else [None]:
            contact = on_duty(seed_dir, team, station)
            out.append(
                {
                    "team": str(team),
                    "title": info.title,
                    "job": info.job,
                    "station": station,
                    "contact": (
                        {"name": contact.name, "phone": contact.phone} if contact else None
                    ),
                }
            )
    return out
