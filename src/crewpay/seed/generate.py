"""Write the demo operation: three regional airlines flying out of Hyderabad for two weeks.

    uv run python -m crewpay.seed.generate [output-dir]

The output is deterministic (fixed random seed), so the committed files can be regenerated and
compared. Crew are rostered with the rules engine itself, one trip at a time, so the published
roster is legal by construction. One problem is then planted on purpose, a certificate that
lapses before a rostered trip, and listed in ``meta.json`` so tests and the demo can find it.

All flying is fictional: the airlines, tails, flight numbers and people are made up.
"""

from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from crewpay.domain import (
    Aircraft,
    AircraftType,
    Airline,
    Certificate,
    CertKind,
    Crew,
    CrewStatus,
    DayHours,
    DutyHistory,
    Gate,
    GateKind,
    Leg,
    Rank,
    Rates,
    Rule,
    Seat,
    Standby,
    Trip,
    TripDay,
)
from crewpay.domain.dataset import Dataset
from crewpay.engine import assess, build_operation
from crewpay.engine.gates import BOARDING
from crewpay.engine.rules import BASE, BLOCK, CERT, DUTY, FDP, NIGHT, RATING, REST, WEEKLY
from crewpay.settings import ROOT

SEED = 2611
START = date(2026, 10, 30)
DAYS = 14
AS_OF = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
HISTORY_DAYS = 28

# Crew names, an initial and a surname, by rank; a few are fixed so the demo reads the same.
CREW_NAMES: dict[str, tuple[str, ...]] = {
    "Captain": (
        "A. Verma",
        "K. Sharma",
        "N. Sen",
        "G. Gupta",
        "S. Rao",
        "R. Kulkarni",
        "N. Reddy",
        "H. Naidu",
        "P. Sen",
        "S. Kapoor",
        "S. Kulkarni",
        "R. Malhotra",
        "V. Chandra",
        "L. Verma",
        "V. Menon",
        "M. Pillai",
        "R. Kapoor",
        "D. Chandra",
        "N. Chandra",
        "S. Malhotra",
        "D. Verma",
        "P. Reddy",
        "A. Iyer",
        "N. Nair",
        "A. Sharma",
    ),
    "First Officer": (
        "K. Chandra",
        "H. Pillai",
        "L. Bose",
        "S. Menon",
        "P. Rao",
        "D. Iyer",
        "I. Reddy",
        "N. Joshi",
        "V. Sharma",
        "V. Reddy",
        "I. Kulkarni",
        "S. Iyer",
        "R. Naidu",
        "L. Gupta",
        "K. Rao",
        "P. Sharma",
        "N. Verma",
        "G. Krishnan",
        "N. Das",
        "N. Gupta",
        "A. Naidu",
        "R. Sharma",
        "S. Reddy",
        "G. Bose",
        "K. Nair",
        "R. Menon",
        "P. Kapoor",
        "A. Kulkarni",
    ),
    "Purser": (
        "D. Rao",
        "R. Verma",
        "A. Gupta",
        "A. Kapoor",
        "H. Malhotra",
        "L. Naidu",
        "R. Joshi",
        "P. Chandra",
        "A. Pillai",
        "R. Nair",
        "A. Sen",
        "L. Sharma",
        "V. Shetty",
        "M. Iyer",
        "M. Naidu",
        "S. Shetty",
        "R. Krishnan",
        "R. Shetty",
        "P. Joshi",
        "K. Krishnan",
        "P. Verma",
        "S. Chandra",
        "S. Naidu",
        "S. Sen",
        "I. Shetty",
    ),
    "Cabin Crew": (
        "N. Menon",
        "P. Pillai",
        "D. Menon",
        "A. Bose",
        "L. Kulkarni",
        "R. Das",
        "R. Pillai",
        "A. Malhotra",
        "M. Menon",
        "D. Sen",
        "N. Kulkarni",
        "K. Joshi",
        "K. Malhotra",
        "V. Kulkarni",
        "M. Reddy",
        "A. Krishnan",
        "H. Reddy",
        "N. Krishnan",
        "R. Sen",
        "H. Joshi",
        "T. Sharma",
        "V. Gupta",
        "R. Gupta",
        "K. Shetty",
        "V. Malhotra",
        "A. Reddy",
        "S. Pillai",
        "P. Naidu",
        "D. Malhotra",
        "S. Sharma",
        "N. Shetty",
        "K. Iyer",
        "K. Bose",
        "I. Pillai",
        "A. Das",
        "P. Menon",
        "V. Verma",
        "I. Krishnan",
        "V. Rao",
        "P. Iyer",
        "D. Bose",
        "P. Malhotra",
        "K. Naidu",
        "L. Joshi",
        "S. Verma",
        "M. Das",
        "R. Rao",
        "P. Shetty",
        "T. Malhotra",
        "V. Pillai",
        "S. Das",
        "S. Bose",
        "P. Kulkarni",
        "R. Bose",
        "P. Gupta",
        "K. Kapoor",
        "V. Nair",
        "S. Joshi",
        "V. Naidu",
    ),
}
NAMED: dict[str, str] = {
    "C-1111": "A. Nair",
    "C-1157": "D. Reddy",
    "C-1167": "R. Iyer",
    "C-1191": "S. Nair",
    "C-1341": "H. Chandra",
    "C-1460": "S. Krishnan",
}
HUB = "HYD"
DATA_VERSION = 7
"""Raised whenever the generated data changes shape, so a deployed store reloads it."""

HUB_GATES = (("G1", "G2", "G3", "G4", "G5"), ("R1", "R2"), ("E_G1",))
"""Contact gates, remote stands and the emergency gate the operation holds at the hub. The
morning wave sends five departures out within an hour, so losing one gate is felt."""
OUTSTATION_GATES = (("G1", "G2"), ("R1",), ("E_G1",))

AIRLINES = (
    Airline(
        code="SY",
        name="Sahyadri Air",
        color="#0f766e",
        hub=HUB,
        fleet=(
            Aircraft(tail="VT-SYA", type=AircraftType.A320, seats=180),
            Aircraft(tail="VT-SYB", type=AircraftType.A320, seats=180),
            Aircraft(tail="VT-SYT", type=AircraftType.ATR72, seats=72),
        ),
    ),
    Airline(
        code="KF",
        name="Kaveri Flyers",
        color="#b45309",
        hub=HUB,
        fleet=(
            Aircraft(tail="VT-KFA", type=AircraftType.A320, seats=186),
            Aircraft(tail="VT-KFB", type=AircraftType.A320, seats=186),
        ),
    ),
    Airline(
        code="GL",
        name="Godavari Link",
        color="#4338ca",
        hub=HUB,
        fleet=(
            Aircraft(tail="VT-GLT", type=AircraftType.ATR72, seats=70),
            Aircraft(tail="VT-GLU", type=AircraftType.ATR72, seats=70),
        ),
    ),
)

BLOCK_HOURS = {
    ("HYD", "DEL"): 2.2,
    ("HYD", "BOM"): 1.4,
    ("HYD", "BLR"): 1.2,
    ("HYD", "MAA"): 1.2,
    ("HYD", "CCU"): 2.0,
    ("HYD", "GOI"): 1.4,
    ("HYD", "VGA"): 0.9,
    ("HYD", "RJA"): 1.1,
    ("HYD", "TIR"): 1.2,
    ("HYD", "VTZ"): 1.3,
}


@dataclass(frozen=True)
class Rotation:
    """What one tail flies: the route for each duty day of a trip, and when it leaves (UTC)."""

    tail: str
    first_flight_no: int
    days: tuple[tuple[time, tuple[str, ...]], ...]
    """(first departure, stations in order) per duty day."""


ROTATIONS = (
    Rotation("VT-SYA", 201, ((time(1, 30), ("HYD", "DEL", "HYD", "BOM", "HYD")),)),
    Rotation(
        "VT-SYB",
        221,
        (
            (time(2, 0), ("HYD", "BLR", "HYD", "DEL")),
            (time(2, 30), ("DEL", "HYD", "MAA", "HYD")),
        ),
    ),
    Rotation("VT-SYT", 241, ((time(2, 0), ("HYD", "VGA", "HYD", "TIR", "HYD")),)),
    Rotation("VT-KFA", 501, ((time(1, 45), ("HYD", "BOM", "HYD", "GOI", "HYD")),)),
    Rotation("VT-KFB", 521, ((time(14, 30), ("HYD", "DEL", "HYD")),)),
    Rotation("VT-GLT", 701, ((time(1, 45), ("HYD", "RJA", "HYD", "VTZ", "HYD")),)),
    Rotation("VT-GLU", 721, ((time(7, 30), ("HYD", "VGA", "HYD", "TIR", "HYD")),)),
)

# (airline, rank, rating or None for both types, home-base count, outstation count)
COMPLEMENT = (
    ("SY", Rank.CAPTAIN, AircraftType.A320, 7, 1),
    ("SY", Rank.FIRST_OFFICER, AircraftType.A320, 7, 1),
    ("SY", Rank.CAPTAIN, AircraftType.ATR72, 4, 0),
    ("SY", Rank.FIRST_OFFICER, AircraftType.ATR72, 4, 0),
    ("SY", Rank.PURSER, None, 8, 0),
    ("SY", Rank.CABIN_CREW, None, 16, 1),
    ("KF", Rank.CAPTAIN, AircraftType.A320, 6, 1),
    ("KF", Rank.FIRST_OFFICER, AircraftType.A320, 6, 1),
    ("KF", Rank.PURSER, None, 5, 0),
    ("KF", Rank.CABIN_CREW, None, 13, 2),
    ("GL", Rank.CAPTAIN, AircraftType.ATR72, 5, 0),
    ("GL", Rank.FIRST_OFFICER, AircraftType.ATR72, 5, 0),
    ("GL", Rank.PURSER, None, 5, 0),
    ("GL", Rank.CABIN_CREW, None, 6, 0),
)
OUTSTATION = "BOM"
STANDBY_PER_DAY = (
    (Rank.CAPTAIN, 1),
    (Rank.FIRST_OFFICER, 1),
    (Rank.PURSER, 1),
    (Rank.CABIN_CREW, 2),
)

FIRST_NAMES = [
    "Aarav",
    "Aditi",
    "Akash",
    "Ananya",
    "Anil",
    "Anjali",
    "Arjun",
    "Bhavana",
    "Chaitanya",
    "Deepa",
    "Devika",
    "Farhan",
    "Gautam",
    "Harini",
    "Imran",
    "Ishita",
    "Jaya",
    "Kabir",
    "Kavya",
    "Keerthi",
    "Lakshmi",
    "Madhav",
    "Meera",
    "Mohan",
    "Naveen",
    "Neha",
    "Nikhil",
    "Pooja",
    "Pranav",
    "Priya",
    "Rahul",
    "Ramya",
    "Ravi",
    "Rohan",
    "Sahana",
    "Sameer",
    "Sana",
    "Shreya",
    "Siddharth",
    "Sneha",
    "Srinivas",
    "Swathi",
    "Tanvi",
    "Uday",
    "Varun",
    "Vidya",
    "Vikram",
    "Yamini",
    "Zoya",
    "Harsha",
    "Keshav",
    "Lavanya",
    "Manoj",
    "Nandini",
    "Omkar",
    "Pallavi",
    "Rajesh",
    "Saritha",
    "Tarun",
]
SURNAMES = [
    "Rao",
    "Reddy",
    "Naidu",
    "Sharma",
    "Iyer",
    "Menon",
    "Kulkarni",
    "Deshpande",
    "Khan",
    "Varma",
    "Pillai",
    "Joshi",
    "Patil",
    "Gupta",
    "Chowdary",
    "Hegde",
    "Nair",
    "Bhat",
    "Shetty",
    "Qureshi",
    "Banerjee",
    "Das",
    "Mishra",
    "Kapoor",
    "Sastry",
    "Prasad",
    "Murthy",
    "Goud",
    "Ali",
    "Fernandes",
]

RULES = (
    Rule(
        rule_id=FDP,
        title="Flight duty period",
        text="A flight duty period may not exceed 13 hours, reduced by 30 minutes for each "
        "sector beyond the second.",
        params={"base_hours": 13, "cut_per_extra_sector": 0.5, "free_sectors": 2},
    ),
    Rule(
        rule_id=NIGHT,
        title="Night duty",
        text="A duty that touches 00:00-06:00 India time may not exceed 10 hours and may include "
        "at most two landings in that window.",
        params={
            "night_start_local": 0,
            "night_end_local": 6,
            "max_fdp_hours": 10,
            "max_landings": 2,
        },
    ),
    Rule(
        rule_id=DUTY,
        title="Duty in 7 days",
        text="No more than 60 duty hours in any 7 consecutive calendar days.",
        params={"max_hours": 60, "window_days": 7},
    ),
    Rule(
        rule_id=BLOCK,
        title="Flying in 28 days",
        text="No more than 100 block hours in any 28 consecutive calendar days.",
        params={"max_hours": 100, "window_days": 28},
    ),
    Rule(
        rule_id=REST,
        title="Rest between duties",
        text="Rest before a duty must be at least 12 hours and at least as long as the duty "
        "before it.",
        params={"min_hours": 12},
    ),
    Rule(
        rule_id=WEEKLY,
        title="Weekly rest",
        text="Every 7 consecutive days must include two clear days in a row.",
        params={"clear_days": 2, "window_days": 7},
    ),
    Rule(
        rule_id=RATING,
        title="Type rating",
        text="Crew must be active and rated on the aircraft type, and fly only for their own "
        "airline.",
    ),
    Rule(
        rule_id=CERT,
        title="Certificates",
        text="Licence, medical, recurrent training and safety equipment certificates must all "
        "be valid on each duty date.",
    ),
    Rule(
        rule_id=BASE,
        title="Home base",
        text="Crew are called out from their home base; anyone based elsewhere must be "
        "positioned on a flight that day, at a cost.",
    ),
)

RATES = Rates(
    standby_callout_pilot=16000,
    standby_callout_cabin=8500,
    day_off_callout_pilot=23000,
    day_off_callout_cabin=11500,
    positioning=7500,
    delay_per_hour=6200,
    cancellation_per_leg=320000,
    hotel_night=4800,
    service_allowance_pilot=420,
    service_allowance_cabin=130,
    travel_per_10_minutes=160,
)


def _round5(moment: datetime) -> datetime:
    minutes = (moment.minute + 2) // 5 * 5
    return moment.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minutes)


def _block(a: str, b: str) -> float:
    return BLOCK_HOURS.get((a, b)) or BLOCK_HOURS[(b, a)]


class Generator:
    def __init__(self, seed: int = SEED) -> None:
        self.rng = random.Random(seed)
        self.aircraft = {a.tail: (a, airline) for airline in AIRLINES for a in airline.fleet}
        self.legs: list[Leg] = []
        self.trips: list[Trip] = []
        self.crew: list[Crew] = []
        self.certs: list[Certificate] = []
        self.histories: list[DutyHistory] = []
        self.standby: list[Standby] = []
        self.known_issues: list[dict[str, str]] = []
        self.gate_list: list[Gate] = []
        self._names: set[str] = set()

    # -- schedule ---------------------------------------------------------------------------

    def schedule(self) -> None:
        trip_no = 1001
        for rotation in ROTATIONS:
            aircraft, airline = self.aircraft[rotation.tail]
            turn = timedelta(minutes=40 if aircraft.type is AircraftType.A320 else 30)
            span = len(rotation.days)
            for offset in range(0, DAYS - span + 1, span):
                trip_days = []
                flight_no = rotation.first_flight_no
                for i, (first_dep, stations) in enumerate(rotation.days):
                    day = START + timedelta(days=offset + i)
                    off = datetime.combine(day, first_dep, UTC)
                    leg_ids = []
                    for a, b in zip(stations, stations[1:], strict=False):
                        hours = _block(a, b)
                        on = _round5(off + timedelta(hours=hours))
                        number = f"{airline.code}{flight_no}"
                        flight_no += 1
                        leg = Leg(
                            leg_id=f"{number}-{day.isoformat()}",
                            flight_no=number,
                            day=day,
                            origin=a,
                            destination=b,
                            off_block=off,
                            on_block=on,
                            tail=rotation.tail,
                            aircraft_type=aircraft.type,
                            seats=aircraft.seats,
                            booked=round(aircraft.seats * self.rng.uniform(0.55, 0.97)),
                        )
                        self.legs.append(leg)
                        leg_ids.append(leg.leg_id)
                        off = on + turn
                    first = next(x for x in self.legs if x.leg_id == leg_ids[0])
                    last = next(x for x in self.legs if x.leg_id == leg_ids[-1])
                    trip_days.append(
                        TripDay(
                            day=day,
                            legs=tuple(leg_ids),
                            report=first.off_block - timedelta(hours=1),
                            release=last.on_block + timedelta(minutes=30),
                        )
                    )
                self.trips.append(
                    Trip(
                        trip_id=f"T-{trip_no}",
                        airline=airline.code,
                        tail=rotation.tail,
                        days=tuple(trip_days),
                        crew=(),
                    )
                )
                trip_no += 1
        self.trips.sort(key=lambda t: (t.first_day.report, t.trip_id))

    # -- people -----------------------------------------------------------------------------

    def _name(self) -> str:
        while True:
            name = f"{self.rng.choice(FIRST_NAMES)} {self.rng.choice(SURNAMES)}"
            if name not in self._names:
                self._names.add(name)
                return name

    def people(self) -> None:
        number = 1100
        for airline, rank, rating, at_hub, away in COMPLEMENT:
            for i in range(at_hub + away):
                number += self.rng.randint(3, 17)
                crew_id = f"C-{number}"
                if rank.is_pilot:
                    years = (
                        self.rng.randint(9, 27) if rank is Rank.CAPTAIN else self.rng.randint(1, 12)
                    )
                    ratings = (rating,)
                else:
                    years = self.rng.randint(1, 18) + (5 if rank is Rank.PURSER else 0)
                    ratings = tuple(
                        sorted({t.type for t in self._fleet(airline)}, key=lambda t: t.value)
                    )
                self.crew.append(
                    Crew(
                        crew_id=crew_id,
                        name=self._name(),
                        rank=rank,
                        airline=airline,
                        base=HUB if i < at_hub else OUTSTATION,
                        ratings=ratings,
                        years_of_service=years,
                        minutes_to_airport=self.rng.randrange(20, 95, 5),
                        command_qualified=rank is Rank.FIRST_OFFICER
                        and years >= 8
                        and self.rng.random() < 0.6,
                        right_seat_qualified=rank is Rank.CAPTAIN and self.rng.random() < 0.4,
                    )
                )
                for kind in CertKind:
                    expires = START + timedelta(days=self.rng.randint(120, 900))
                    self.certs.append(Certificate(crew_id=crew_id, kind=kind, expires=expires))
        # A few people who are around but cannot fly this fortnight.
        statuses = (CrewStatus.LEAVE, CrewStatus.TRAINING, CrewStatus.LEAVE)
        for crew, status in zip(self.rng.sample(self.crew, 3), statuses, strict=True):
            self.crew[self.crew.index(crew)] = crew.model_copy(update={"status": status})
        # Certificates that lapse inside the fortnight, so some candidates fail on them.
        # Only one is planted here (beside the captain in ``plant_known_issue``); the other draws
        # are still made, so the rest of the operation comes out the same.
        lapses = [
            (crew.crew_id, self.rng.choice(list(CertKind)), self.rng.randint(2, 11))
            for crew in self.rng.sample(self.crew, 6)
        ]
        crew_id, kind, days = max(lapses, key=lambda x: x[2])
        self._set_expiry(crew_id, kind, START + timedelta(days=days))

    def name_crew(self) -> None:
        """Name everyone from ``CREW_NAMES`` by rank, in crew id order. The names drawn while
        making them are dropped, so the rest of the operation comes out the same."""
        left = {rank: list(names) for rank, names in CREW_NAMES.items()}
        for i, crew in enumerate(self.crew):
            name = NAMED.get(crew.crew_id) or left[str(crew.rank)].pop(0)
            self.crew[i] = crew.model_copy(update={"name": name})

    def _fleet(self, code: str) -> tuple[Aircraft, ...]:
        return next(a.fleet for a in AIRLINES if a.code == code)

    def _set_expiry(self, crew_id: str, kind: CertKind, expires: date) -> None:
        for i, cert in enumerate(self.certs):
            if cert.crew_id == crew_id and cert.kind is kind:
                self.certs[i] = cert.model_copy(update={"expires": expires})

    # -- what they flew before --------------------------------------------------------------

    def history(self) -> None:
        heavy = {c.crew_id for c in self.rng.sample(self.crew, 8)}
        first = START - timedelta(days=HISTORY_DAYS)
        for crew in self.crew:
            phase = self.rng.randrange(7)
            days: list[DayHours] = []
            for n in range(HISTORY_DAYS):
                day = first + timedelta(days=n)
                # four days on, three off
                working = crew.status is CrewStatus.ACTIVE and (n + phase) % 7 < 4
                duty = round(self.rng.uniform(6, 10), 2) if working else 0.0
                block = round(duty * self.rng.uniform(0.45, 0.6), 2)
                days.append(DayHours(day=day, duty_hours=duty, block_hours=block))
            total = sum(d.block_hours for d in days)
            target = self.rng.uniform(91, 96) if crew.crew_id in heavy else min(total, 84)
            if total:
                scale = target / total
                days = [
                    d.model_copy(update={"block_hours": round(d.block_hours * scale, 2)})
                    for d in days
                ]
            last_worked = next((d for d in reversed(days) if d.duty_hours), None)
            if last_worked:
                release = datetime.combine(last_worked.day, time(11, 0), UTC)
                rest_until = release + timedelta(hours=max(12.0, last_worked.duty_hours))
            else:
                rest_until = datetime.combine(first, time(0), UTC)
            self.histories.append(
                DutyHistory(crew_id=crew.crew_id, rest_until=rest_until, days=tuple(days))
            )

    # -- rostering --------------------------------------------------------------------------

    def _dataset(self) -> Dataset:
        return Dataset(
            airlines=AIRLINES,
            crew=tuple(self.crew),
            certificates=tuple(self.certs),
            histories=tuple(self.histories),
            standby=tuple(self.standby),
            legs=tuple(self.legs),
            trips=tuple(self.trips),
            rules=RULES,
            rates=RATES,
            as_of=AS_OF,
            gates=tuple(self.gate_list),
        )

    def _needs(self, trip: Trip) -> list[Rank]:
        aircraft, _ = self.aircraft[trip.tail]
        cabin = math.ceil(aircraft.seats / 50)
        return [Rank.CAPTAIN, Rank.FIRST_OFFICER, Rank.PURSER] + [Rank.CABIN_CREW] * (cabin - 1)

    @staticmethod
    def _preference(state, day: date) -> tuple[int, float]:
        """Continue a block of duty days before starting someone fresh, the way rosters are
        built, then spread the hours. Scattered single duties use up everyone's weekly rest."""
        continuing = (day - timedelta(days=1)) in state.working_days()
        return (0 if continuing else 1, state.duty_between(day - timedelta(days=6), day))

    def roster(self) -> None:
        for index, trip in enumerate(self.trips):
            op = build_operation(self._dataset())
            first = trip.first_day.day
            chosen: list[Seat] = []
            for rank in self._needs(trip):
                taken = {s.crew_id for s in chosen}
                pool = [
                    c
                    for c in self.crew
                    if c.airline == trip.airline
                    and c.rank is rank
                    and c.base == HUB
                    and c.crew_id not in taken
                ]
                self.rng.shuffle(pool)
                pool.sort(key=lambda c: self._preference(op.crew[c.crew_id], first))
                pick = next((c for c in pool if assess(op, c.crew_id, trip.trip_id).legal), None)
                if pick is None:
                    raise RuntimeError(f"no legal {rank} for {trip.trip_id} on {first}")
                chosen.append(Seat(crew_id=pick.crew_id, rank=rank))
            self.trips[index] = trip.model_copy(update={"crew": tuple(chosen)})

    def standby_duty(self) -> None:
        """Each day, the airline keeps one of each rank (two cabin crew) on standby at the hub,
        chosen from people with no trip that day. Early and late windows alternate."""
        op = build_operation(self._dataset())
        windows = ((time(0, 0), time(8, 0)), (time(8, 0), time(16, 0)))
        for n in range(DAYS):
            day = START + timedelta(days=n)
            for airline in AIRLINES:
                for rank, count in STANDBY_PER_DAY:
                    free = [
                        c
                        for c in self.crew
                        if c.airline == airline.code
                        and c.rank is rank
                        and c.base == HUB
                        and c.status is CrewStatus.ACTIVE
                        and day not in op.crew[c.crew_id].working_days()
                    ]
                    for i, c in enumerate(self.rng.sample(free, min(count, len(free)))):
                        start, end = windows[(n + i) % 2]
                        self.standby.append(
                            Standby(
                                crew_id=c.crew_id,
                                base=HUB,
                                dates=(day,),
                                window_start=start,
                                window_end=end,
                            )
                        )

    def plant_known_issue(self) -> None:
        """A captain whose medical lapses two days before a trip they are rostered on: the kind
        of problem a roster audit has to catch before the day."""
        target_day = START + timedelta(days=8)
        trip = next(
            t
            for t in self.trips
            if t.first_day.day == target_day and self.aircraft[t.tail][0].type is AircraftType.A320
        )
        captain = trip.holders(Rank.CAPTAIN)[0]
        lapse = target_day - timedelta(days=2)
        self._set_expiry(captain, CertKind.MEDICAL, lapse)
        for t in self.trips:
            day = t.first_day.day
            if captain in t.holders(Rank.CAPTAIN) and t.days[-1].day > lapse:
                self.known_issues.append(
                    {
                        "crew_id": captain,
                        "trip_id": t.trip_id,
                        "rule_id": CERT,
                        "note": f"medical lapses {lapse.isoformat()}, rostered on {t.trip_id} "
                        f"on {day.isoformat()}",
                    }
                )

    def gates(self) -> None:
        """Give every departure the first gate free for its boarding window, earliest first."""
        stations = sorted(
            {leg.origin for leg in self.legs} | {leg.destination for leg in self.legs}
        )
        for station in stations:
            contact, remote, emergency = HUB_GATES if station == HUB else OUTSTATION_GATES
            self.gate_list += [
                Gate(station=station, gate=g, kind=GateKind.CONTACT) for g in contact
            ]
            self.gate_list += [Gate(station=station, gate=g, kind=GateKind.REMOTE) for g in remote]
            self.gate_list += [
                Gate(station=station, gate=g, kind=GateKind.EMERGENCY) for g in emergency
            ]
            held: dict[str, list[tuple[datetime, datetime]]] = {g: [] for g in contact + remote}
            leaving = sorted(
                (i for i, leg in enumerate(self.legs) if leg.origin == station),
                key=lambda i: (self.legs[i].off_block, self.legs[i].leg_id),
            )
            for i in leaving:
                leg = self.legs[i]
                start, end = leg.off_block - BOARDING, leg.off_block
                gate = next(
                    g for g, slots in held.items() if all(e <= start or end <= s for s, e in slots)
                )
                held[gate].append((start, end))
                self.legs[i] = leg.model_copy(update={"gate": gate})

    def build(self) -> Dataset:
        self.schedule()
        self.gates()
        self.people()
        self.name_crew()
        self.history()
        self.roster()
        self.standby_duty()
        self.plant_known_issue()
        return self._dataset()


def _dump(records: Any) -> Any:
    return [r.model_dump(mode="json") for r in records]


STATIONS = ("HYD", "DEL", "BOM", "BLR", "MAA", "GOI", "VGA", "TIR", "RJA", "VTZ")
ON_DUTY = {
    "gate_control": (
        "Ravi Teja",
        "Neha Bansal",
        "Arjun Pillai",
        "Kavya Shetty",
        "Senthil Kumar",
        "Rohan Naik",
        "Srinivas Rao",
        "Lakshmi Prasanna",
        "Harsha Varma",
        "Bhavana Reddy",
    ),
    "station_ops": (
        "Faizan Qadri",
        "Meera Joshi",
        "Vikram Desai",
        "Shreya Hegde",
        "Karthik Raman",
        "Anita Fernandes",
        "Prakash Babu",
        "Deepika Naidu",
        "Suresh Chandra",
        "Swathi Kiran",
    ),
}
NETWORK_DESKS = (
    ("flight_info", "Nisha Kapoor", "+91 40 6612 3100"),
    ("ops_monitoring", "Venkat Subramanian", "+91 40 6612 3200"),
    ("maintenance_control", "Imtiaz Ahmed", "+91 40 6612 3300"),
    ("crew_records", "Padma Iyengar", "+91 40 6612 3400"),
    ("customer_care", "Rahul Mehta", "+91 40 6612 3500"),
)


def contacts() -> list[dict[str, str]]:
    """Who is on duty in each operational team: one per station for the station teams, one
    at the operations centre for the rest. Made-up names and numbers."""
    out = []
    for team, names in ON_DUTY.items():
        prefix = "98" if team == "gate_control" else "97"
        for station, name in zip(STATIONS, names, strict=True):
            n = len(out)
            out.append(
                {
                    "team": team,
                    "station": station,
                    "name": name,
                    "phone": f"+91 {prefix}{n % 10}4{n % 7} {31000 + n * 731:05d}",
                }
            )
    out += [
        {"team": team, "station": "NETWORK", "name": name, "phone": phone}
        for team, name, phone in NETWORK_DESKS
    ]
    return out


def write(directory: Path, seed: int = SEED) -> Dataset:
    generator = Generator(seed)
    data = generator.build()
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, Any] = {
        "airlines": _dump(data.airlines),
        "crew": _dump(data.crew),
        "certificates": _dump(data.certificates),
        "histories": _dump(data.histories),
        "standby": _dump(data.standby),
        "legs": _dump(data.legs),
        "trips": _dump(data.trips),
        "rules": _dump(data.rules),
        "rates": data.rates.model_dump(mode="json"),
        "gates": _dump(data.gates),
        "contacts": contacts(),
        "meta": {
            "data_version": DATA_VERSION,
            "as_of": AS_OF.isoformat(),
            "first_day": START.isoformat(),
            "last_day": (START + timedelta(days=DAYS - 1)).isoformat(),
            "seed": seed,
            "known_issues": generator.known_issues,
        },
    }
    for name, content in files.items():
        text = json.dumps(content, indent=1, ensure_ascii=False) + "\n"
        (directory / f"{name}.json").write_text(text, encoding="utf-8")
    return data


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "seed"
    data = write(target)
    print(
        f"wrote {len(data.trips)} trips, {len(data.legs)} legs and {len(data.crew)} crew "
        f"to {target}"
    )


if __name__ == "__main__":
    main()
