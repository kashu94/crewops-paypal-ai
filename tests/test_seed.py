import json
import math
from collections import defaultdict

import pytest

from crewpay.domain import Rank
from crewpay.engine import assess, build_operation
from crewpay.engine.rules import check_fdp, check_night
from crewpay.seed import load_dataset, read_meta
from crewpay.seed.generate import write
from crewpay.settings import ROOT

SEED_DIR = ROOT / "data" / "seed"


@pytest.fixture(scope="module")
def data():
    return load_dataset(SEED_DIR)


@pytest.fixture(scope="module")
def op(data):
    return build_operation(data)


def test_committed_files_match_the_generator(tmp_path):
    write(tmp_path)
    for path in sorted(SEED_DIR.glob("*.json")):
        assert json.loads(path.read_text()) == json.loads((tmp_path / path.name).read_text()), (
            f"{path.name} is stale: run `uv run python -m crewpay.seed.generate`"
        )


def test_every_rostered_seat_is_legal_except_the_known_issues(data, op):
    known = {
        (i["crew_id"], i["trip_id"]): i["rule_id"] for i in read_meta(SEED_DIR)["known_issues"]
    }
    assert known
    for trip in data.trips:
        for seat in trip.crew:
            result = assess(op, seat.crew_id, trip.trip_id)
            if (seat.crew_id, trip.trip_id) in known:
                assert [f.rule_id for f in result.blockers] == [known[seat.crew_id, trip.trip_id]]
            else:
                assert result.legal, (trip.trip_id, seat.crew_id, result.blockers)


def test_every_duty_day_fits_the_period_and_night_limits_for_anyone(op):
    for days in op.duty_days.values():
        for day in days:
            assert not check_fdp(day, op.book).failed
            assert not check_night(day, op.book).failed


def test_every_trip_has_two_pilots_a_purser_and_one_cabin_crew_per_fifty_seats(data):
    seats = {a.tail: a.seats for airline in data.airlines for a in airline.fleet}
    for trip in data.trips:
        ranks = [s.rank for s in trip.crew]
        assert ranks.count(Rank.CAPTAIN) == 1 and ranks.count(Rank.FIRST_OFFICER) == 1
        cabin = sum(r.is_cabin for r in ranks)
        assert cabin == math.ceil(seats[trip.tail] / 50)
        assert len({s.crew_id for s in trip.crew}) == len(trip.crew)


def test_crew_fly_only_for_their_airline(data):
    airline = {c.crew_id: c.airline for c in data.crew}
    for trip in data.trips:
        assert {airline[s.crew_id] for s in trip.crew} == {trip.airline}


def test_each_aircraft_flies_a_continuous_line(data):
    by_tail = defaultdict(list)
    for leg in data.legs:
        by_tail[leg.tail].append(leg)
    for legs in by_tail.values():
        legs.sort(key=lambda leg: leg.off_block)
        for prev, nxt in zip(legs, legs[1:], strict=False):
            assert prev.destination == nxt.origin, (prev.leg_id, nxt.leg_id)
            assert prev.on_block < nxt.off_block


def test_trips_cover_every_leg_once(data):
    flown = [leg for trip in data.trips for leg in trip.leg_ids]
    assert sorted(flown) == sorted(leg.leg_id for leg in data.legs)


def test_standby_crew_have_no_trip_that_day(data, op):
    for s in data.standby:
        assert not set(s.dates) & op.crew[s.crew_id].working_days()
