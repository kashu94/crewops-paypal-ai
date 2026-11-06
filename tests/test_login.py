"""One shared demo password, checked by the server: the identity comes only from the token."""

import pytest
from conftest import SEED_DIR
from fastapi.testclient import TestClient

from crewpay.api.app import create_app
from crewpay.settings import load_settings

BASE = {"CREWPAY_SEED_DIR": str(SEED_DIR), "CREWPAY_TICKER_SECONDS": "0"}
FAKE_APPROVER = {"X-Demo-User": "meera.bhatt", "X-Demo-Role": "approver"}


@pytest.fixture
def locked():
    return TestClient(create_app(load_settings({**BASE, "CREWPAY_DEMO_PASSWORD": "fly-safe-26"})))


def test_with_a_password_the_demo_headers_get_nobody_in(locked):
    assert locked.get("/api/config").json()["password"] is True
    assert locked.get("/api/me", headers=FAKE_APPROVER).status_code == 401
    assert locked.get("/api/health").status_code == 200
    assert locked.get("/api/accounts/demo").status_code == 200, "the picker still lists who to be"


def test_the_right_password_signs_in_as_that_account_only(locked):
    wrong = locked.post("/api/login", json={"user": "meera.bhatt", "password": "guess"})
    assert wrong.status_code == 401
    assert (
        locked.post("/api/login", json={"user": "nobody", "password": "fly-safe-26"}).status_code
        == 404
    )

    token = locked.post(
        "/api/login", json={"user": "meera.bhatt", "password": "fly-safe-26"}
    ).json()["token"]
    me = locked.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert (me["user"], me["role"]) == ("meera.bhatt", "approver")

    forged = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    assert locked.get("/api/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # a token outranks the headers: they cannot change who is calling
    crew = locked.post("/api/login", json={"user": "c1157.reddy", "password": "fly-safe-26"}).json()
    me = locked.get(
        "/api/me", headers={"Authorization": f"Bearer {crew['token']}", **FAKE_APPROVER}
    ).json()
    assert me["role"] == "crew"


def test_guessing_is_slowed_down(locked):
    for _ in range(5):
        locked.post("/api/login", json={"user": "meera.bhatt", "password": "nope"})
    again = locked.post("/api/login", json={"user": "meera.bhatt", "password": "fly-safe-26"})
    assert again.status_code == 429


def test_without_a_password_the_demo_stays_open():
    open_ = TestClient(create_app(load_settings(BASE)))
    assert open_.get("/api/config").json()["password"] is False
    assert open_.post("/api/login", json={"user": "meera.bhatt"}).json()["token"] is None
    assert open_.get("/api/me", headers=FAKE_APPROVER).json()["role"] == "approver"
