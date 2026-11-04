import pytest

from crewpay.seed import load_dataset, read_meta
from crewpay.settings import ROOT
from crewpay.store import MemoryStore

SEED_DIR = ROOT / "data" / "seed"


@pytest.fixture(scope="session")
def seed():
    return load_dataset(SEED_DIR)


@pytest.fixture
def empty():
    """An empty store."""
    return MemoryStore()


@pytest.fixture
def store(empty, seed):
    empty.load(seed, read_meta(SEED_DIR))
    return empty
