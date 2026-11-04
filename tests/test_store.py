from datetime import UTC, datetime, timedelta

import pytest
from conftest import SEED_DIR
from pydantic import ValidationError

from crewpay.domain import Rank, Seat
from crewpay.engine import NotFound, assess
from crewpay.seed import read_meta
from crewpay.store import MemoryStore, StaleTrip


def first_trip(store):
    trip_id = sorted(store.dataset().trips, key=lambda t: t.first_day.report)[-1].trip_id
    return store.trip(trip_id)


def test_the_operation_reads_back_exactly_as_loaded(store, seed):
    data = store.dataset()
    assert sorted(data.trips, key=lambda t: t.trip_id) == sorted(
        seed.trips, key=lambda t: t.trip_id
    )
    for kind in ("airlines", "crew", "certificates", "histories", "standby", "legs", "rules"):
        loaded = sorted(r.model_dump_json() for r in getattr(data, kind))
        assert loaded == sorted(r.model_dump_json() for r in getattr(seed, kind)), kind
    assert data.rates == seed.rates
    assert data.as_of == seed.as_of
    assert store.meta()["known_issues"] == read_meta(SEED_DIR)["known_issues"]
    assert not store.is_empty()


def test_an_empty_table_is_empty_and_a_reload_replaces_everything(empty, seed):
    assert empty.is_empty()
    empty.load(seed, read_meta(SEED_DIR))
    trip, version = first_trip(empty)
    empty.change_crew(trip.trip_id, trip.crew[:-1], version, "ananya.iyer", "test")
    empty.load(seed, read_meta(SEED_DIR))
    assert empty.trip(trip.trip_id) == (trip, 1)
    assert empty.changes() == []
    assert empty.roster_version() == 1


def test_the_operation_is_rebuilt_only_after_a_roster_change(store):
    first = store.operation()
    assert store.operation() is first
    trip, version = first_trip(store)
    store.change_crew(trip.trip_id, trip.crew[:-1], version, "c.rao", "test")
    assert store.operation() is not first


def test_now_moves_forward_but_never_back(store):
    op = store.operation()
    later = op.now + timedelta(days=3)
    assert store.operation(later).now == later
    assert store.operation(op.now - timedelta(days=1)).now == op.now


def test_a_crew_change_is_versioned_and_recorded(store):
    trip, version = first_trip(store)
    sick = trip.holders(Rank.FIRST_OFFICER)[0]
    op = store.operation()
    spare = next(
        c.crew_id
        for c in op.crew_of(trip.airline, Rank.FIRST_OFFICER)
        if c.crew_id != sick and assess(op, c.crew_id, trip.trip_id).legal
    )
    crew = [s if s.crew_id != sick else Seat(crew_id=spare, rank=s.rank) for s in trip.crew]
    at = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)

    new_version = store.change_crew(trip.trip_id, crew, version, "ananya.iyer", "sick call", at=at)

    assert new_version == version + 1
    changed, now_version = store.trip(trip.trip_id)
    assert now_version == new_version
    assert spare in {s.crew_id for s in changed.crew}
    [change] = store.changes(trip.trip_id)
    assert (change.actor, change.reason, change.at) == ("ananya.iyer", "sick call", at)
    assert change.added == (spare,) and change.removed == (sick,)
    assert any(d.trip_id == trip.trip_id for d in store.operation().state(spare).duties)


def test_a_stale_version_is_refused_and_nothing_changes(store):
    trip, version = first_trip(store)
    store.change_crew(trip.trip_id, trip.crew[:-1], version, "ananya.iyer", "first")
    with pytest.raises(StaleTrip) as err:
        store.change_crew(trip.trip_id, trip.crew, version, "rohit.malhotra", "second")
    assert err.value.actual == version + 1
    assert len(store.changes(trip.trip_id)) == 1
    assert store.trip(trip.trip_id)[0].crew == trip.crew[:-1]
    assert store.roster_version() == 2


def test_changes_come_back_oldest_first(store):
    trips = sorted(store.dataset().trips, key=lambda t: t.trip_id)[:2]
    start = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    for minutes, trip in ((5, trips[0]), (0, trips[1])):
        store.change_crew(
            trip.trip_id,
            trip.crew[:-1],
            1,
            "ananya.iyer",
            "x",
            at=start + timedelta(minutes=minutes),
        )
    assert [c.trip_id for c in store.changes()] == [trips[1].trip_id, trips[0].trip_id]


def test_an_invalid_crew_is_refused(store):
    trip, version = first_trip(store)
    with pytest.raises(ValidationError):
        store.change_crew(trip.trip_id, [{"crew_id": "C-1", "rank": "Pilot"}], version, "x", "y")
    assert store.trip(trip.trip_id) == (trip, version)
    assert store.changes() == []


def test_unknown_trips_are_not_found(store):
    with pytest.raises(NotFound):
        store.trip("T-9999")
    with pytest.raises(NotFound):
        store.change_crew("T-9999", [], 1, "x", "y")


def test_a_change_racing_another_is_refused_by_the_table(store, monkeypatch):
    if isinstance(store, MemoryStore):
        pytest.skip("the memory store changes trips under a lock, so reads cannot go stale")
    trip, version = first_trip(store)
    store.change_crew(trip.trip_id, trip.crew[:-1], version, "ananya.iyer", "first")
    # The second controller read the trip just before the first change landed.
    reads = iter([(trip, version)])
    read = store.trip
    monkeypatch.setattr(store, "trip", lambda trip_id: next(reads, None) or read(trip_id))
    with pytest.raises(StaleTrip) as err:
        store.change_crew(trip.trip_id, trip.crew, version, "rohit.malhotra", "second")
    assert err.value.actual == version + 1
    assert [c.actor for c in store.changes()] == ["ananya.iyer"]
    assert store.roster_version() == 2
