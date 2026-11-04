"""Small hand-built operations for engine tests.

One airline (SY) with an A320 and an ATR, a hub at HYD and an outstation at BOM. Every test
starts from ``Ops()`` and adds just the crew, trips and history it needs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from crewpay.domain import (
    Aircraft,
    AircraftType,
    Airline,
    Certificate,
    CertKind,
    Crew,
    DayHours,
    DutyHistory,
    Leg,
    Rank,
    Rates,
    Seat,
    Standby,
    Trip,
    TripDay,
)
from crewpay.domain.dataset import Dataset
from crewpay.engine import build_operation

DAY = date(2026, 11, 3)
AS_OF = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

RATES = Rates(
    standby_callout_pilot=15000,
    standby_callout_cabin=8000,
    day_off_callout_pilot=22000,
    day_off_callout_cabin=11000,
    positioning=7000,
    delay_per_hour=6000,
    cancellation_per_leg=300000,
    hotel_night=4500,
    service_allowance_pilot=400,
    service_allowance_cabin=120,
    travel_per_10_minutes=150,
)

AIRLINES = (
    Airline(
        code="SY",
        name="Sahyadri Air",
        color="#0f766e",
        hub="HYD",
        fleet=(
            Aircraft(tail="VT-SYA", type=AircraftType.A320, seats=180),
            Aircraft(tail="VT-SYT", type=AircraftType.ATR72, seats=72),
        ),
    ),
    Airline(
        code="KF",
        name="Kaveri Flyers",
        color="#b45309",
        hub="HYD",
        fleet=(Aircraft(tail="VT-KFA", type=AircraftType.A320, seats=180),),
    ),
)


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), UTC)


class Ops:
    def __init__(self) -> None:
        self.crew: list[Crew] = []
        self.certs: list[Certificate] = []
        self.histories: list[DutyHistory] = []
        self.standby: list[Standby] = []
        self.legs: list[Leg] = []
        self.trips: list[Trip] = []
        self._n = 0

    def person(
        self,
        crew_id: str,
        rank: Rank = Rank.CAPTAIN,
        base: str = "HYD",
        airline: str = "SY",
        ratings: tuple[AircraftType, ...] = (AircraftType.A320,),
        cert_expiry: date = date(2028, 1, 1),
        **extra,
    ) -> Ops:
        self.crew.append(
            Crew(
                crew_id=crew_id,
                name=f"Crew {crew_id}",
                rank=rank,
                airline=airline,
                base=base,
                ratings=ratings,
                years_of_service=extra.pop("years", 10),
                minutes_to_airport=extra.pop("minutes", 40),
                **extra,
            )
        )
        for kind in CertKind:
            self.certs.append(Certificate(crew_id=crew_id, kind=kind, expires=cert_expiry))
        return self

    def history(
        self, crew_id: str, rest_until: datetime, days: dict[date, tuple[float, float]]
    ) -> Ops:
        self.histories.append(
            DutyHistory(
                crew_id=crew_id,
                rest_until=rest_until,
                days=tuple(
                    DayHours(day=k, duty_hours=d, block_hours=b) for k, (d, b) in days.items()
                ),
            )
        )
        return self

    def on_standby(self, crew_id: str, day: date, start: time, end: time, base="HYD") -> Ops:
        self.standby.append(
            Standby(crew_id=crew_id, base=base, dates=(day,), window_start=start, window_end=end)
        )
        return self

    def leg(
        self,
        day: date,
        origin: str,
        destination: str,
        off: datetime,
        hours: float,
        tail: str = "VT-SYA",
        flight_no: str | None = None,
    ) -> str:
        self._n += 1
        flight_no = flight_no or f"SY{100 + self._n}"
        leg_id = f"{flight_no}-{day.isoformat()}"
        kind = AircraftType.ATR72 if tail.endswith("T") else AircraftType.A320
        self.legs.append(
            Leg(
                leg_id=leg_id,
                flight_no=flight_no,
                day=day,
                origin=origin,
                destination=destination,
                off_block=off,
                on_block=off + timedelta(hours=hours),
                tail=tail,
                aircraft_type=kind,
                seats=72 if kind is AircraftType.ATR72 else 180,
                booked=60,
            )
        )
        return leg_id

    def trip(
        self,
        trip_id: str,
        days: list[tuple[date, list[str]]],
        crew: list[tuple[str, Rank]] = (),
        airline: str = "SY",
    ) -> Ops:
        legs = {leg.leg_id: leg for leg in self.legs}
        trip_days = []
        for day, leg_ids in days:
            flown = [legs[x] for x in leg_ids]
            trip_days.append(
                TripDay(
                    day=day,
                    legs=tuple(leg_ids),
                    report=flown[0].off_block - timedelta(hours=1),
                    release=flown[-1].on_block + timedelta(minutes=30),
                )
            )
        self.trips.append(
            Trip(
                trip_id=trip_id,
                airline=airline,
                tail=legs[days[0][1][0]].tail,
                days=tuple(trip_days),
                crew=tuple(Seat(crew_id=c, rank=r) for c, r in crew),
            )
        )
        return self

    def day_trip(
        self,
        trip_id: str,
        day: date = DAY,
        start: datetime | None = None,
        sectors: int = 2,
        hours: float = 1.5,
        turn: float = 0.75,
        origin: str = "HYD",
        tail: str = "VT-SYA",
        crew: list[tuple[str, Rank]] = (),
        airline: str = "SY",
    ) -> Ops:
        """A there-and-back day from ``origin`` with ``sectors`` legs."""
        off = start or at(day, 2)
        leg_ids = []
        here, there = origin, "BOM" if origin != "BOM" else "HYD"
        for _ in range(sectors):
            leg_ids.append(self.leg(day, here, there, off, hours, tail))
            off += timedelta(hours=hours + turn)
            here, there = there, here
        return self.trip(trip_id, [(day, leg_ids)], crew, airline)

    def dataset(self, as_of: datetime = AS_OF) -> Dataset:
        return Dataset(
            airlines=AIRLINES,
            crew=tuple(self.crew),
            certificates=tuple(self.certs),
            histories=tuple(self.histories),
            standby=tuple(self.standby),
            legs=tuple(self.legs),
            trips=tuple(self.trips),
            rules=(),
            rates=RATES,
            as_of=as_of,
        )

    def build(self, as_of: datetime = AS_OF):
        return build_operation(self.dataset(as_of))
