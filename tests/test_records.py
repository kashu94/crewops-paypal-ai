from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from crewpay.domain import Crew, Leg, Rank, Rates, Standby, TripDay
from crewpay.settings import load_settings


def leg(**overrides):
    fields = {
        "leg_id": "SY215-2026-11-03",
        "flight_no": "SY215",
        "day": "2026-11-03",
        "origin": "HYD",
        "destination": "BOM",
        "off_block": "2026-11-03T01:00:00Z",
        "on_block": "2026-11-03T02:30:00+00:00",
        "tail": "VT-SYA",
        "aircraft_type": "A320",
        "seats": 180,
        "booked": 150,
    }
    return Leg.model_validate({**fields, **overrides})


def test_leg_converts_offsets_to_utc_and_computes_block():
    parsed = leg(off_block="2026-11-03T06:30:00+05:30")
    assert parsed.off_block == datetime(2026, 11, 3, 1, 0, tzinfo=UTC)
    assert parsed.block_hours == 1.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"on_block": "2026-11-03T00:30:00Z"},
        {"booked": 181},
        {"origin": "hyd"},
        {"off_block": "2026-11-03T01:00:00"},
    ],
)
def test_leg_rejects_bad_records(overrides):
    with pytest.raises(ValidationError):
        leg(**overrides)


def test_trip_day_needs_release_after_report():
    with pytest.raises(ValidationError):
        TripDay.model_validate(
            {
                "day": "2026-11-03",
                "legs": ["SY215-2026-11-03"],
                "report": "2026-11-03T05:00:00Z",
                "release": "2026-11-03T04:00:00Z",
            }
        )


def test_crew_id_shape_and_rank_flags():
    with pytest.raises(ValidationError):
        Crew(
            crew_id="1042",
            name="R. Rao",
            rank=Rank.CAPTAIN,
            airline="SY",
            base="HYD",
            ratings=("A320",),
            years_of_service=10,
            minutes_to_airport=40,
        )
    assert Rank.FIRST_OFFICER.is_pilot and Rank.PURSER.is_cabin


def test_standby_window_covers_only_its_dates_and_hours():
    standby = Standby(
        crew_id="C-1001",
        base="HYD",
        dates=(date(2026, 11, 3),),
        window_start=time(0, 0),
        window_end=time(8, 0),
    )
    assert standby.covers(datetime(2026, 11, 3, 7, 59, tzinfo=UTC))
    assert not standby.covers(datetime(2026, 11, 3, 8, 1, tzinfo=UTC))
    assert not standby.covers(datetime(2026, 11, 4, 1, 0, tzinfo=UTC))


def test_rates_round_travel_up_to_the_next_ten_minutes():
    rates = Rates(
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
    assert rates.travel(41) == 750
    assert rates.callout(Rank.PURSER, on_standby=True) == 8000
    assert rates.service_allowance(Rank.CAPTAIN, 12) == 4800


def test_shell_environment_wins_over_defaults():
    loaded = load_settings(
        {"CREWPAY_ADVISOR_MODEL": "us.amazon.nova-pro-v1:0", "CREWPAY_BEDROCK_API_KEY": "k"}
    )
    assert loaded.advisor_model == "us.amazon.nova-pro-v1:0"
    assert loaded.has_bedrock
    assert loaded.paypal_base_url == "https://api-m.sandbox.paypal.com"
