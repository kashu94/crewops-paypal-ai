"""The database: the operation's records, the roster and every change made to it.

CrewPay keeps the operation in memory, loaded with the demo data on start."""

from crewpay.settings import Settings
from crewpay.store.memory import MemoryStore
from crewpay.store.roster import RosterChange, StaleTrip

Store = MemoryStore
"""The one store there is."""


def open_store(config: Settings) -> MemoryStore:
    """A memory store loaded with the demo data."""
    from crewpay.seed import load_dataset, read_meta

    store = MemoryStore()
    store.load(load_dataset(config.seed_dir), read_meta(config.seed_dir))
    return store


__all__ = ["MemoryStore", "RosterChange", "StaleTrip", "Store", "open_store"]
