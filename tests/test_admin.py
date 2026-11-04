import base64
import hashlib
import hmac
import json

import pytest
from conftest import SEED_DIR
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crewpay.agent import Caller, Context, Role, ToolBox
from crewpay.engine.risk import RiskLevel, crew_risk
from crewpay.ops import DESK, MemoryOutbox, Notice
from crewpay.ops.accounts import StoreAccounts
from crewpay.ops.alerts import AlertAddresses
from crewpay.ops.push import Push, PushingOutbox, allowed, b64u, unb64u
from crewpay.seed import read_meta
from crewpay.store import MemoryStore

MENON = Caller("priya.desai", Role.ADMIN)
KAPOOR = Caller("vikram.saxena", Role.SUPER_ADMIN)
IYER = Caller("ananya.iyer", Role.CONTROLLER)


@pytest.fixture
def mem(seed):
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    return store


@pytest.fixture
def ctx(mem):
    accounts, alerts = StoreAccounts(mem), AlertAddresses(mem)
    return lambda caller: Context(mem, caller, accounts=accounts, alerts=alerts)


box = ToolBox()


# -- accounts --------------------------------------------------------------------------------


def test_admins_manage_accounts_but_only_a_super_admin_manages_admins(ctx):
    names = {a["username"] for a in box.call("team_accounts", {}, ctx(MENON))["accounts"]}
    assert {"ananya.iyer", "meera.bhatt", "vikram.saxena", "ops.speaker"} <= names

    made = box.call("create_account", {"username": "p.das", "role": "controller"}, ctx(MENON))
    assert len(made["temporary_password"]) == 12
    crew = box.call("create_account", {"username": "new.crew", "role": "crew"}, ctx(MENON))
    assert "crew id" in crew["error"]
    raise_ = box.call("create_account", {"username": "x.admin", "role": "admin"}, ctx(MENON))
    assert "only a super admin" in raise_["error"]
    assert box.call("create_account", {"username": "x.admin", "role": "admin"}, ctx(KAPOOR))[
        "temporary_password"
    ]

    off = box.call("set_account", {"username": "p.das", "enabled": False}, ctx(MENON))
    assert off["enabled"] is False
    other_admin = box.call("reset_password", {"username": "vikram.saxena"}, ctx(MENON))
    assert "only a super admin" in other_admin["error"]
    own = box.call("set_account", {"username": "priya.desai", "role": "super_admin"}, ctx(MENON))
    assert "your own account" in own["error"]
    reset = box.call("reset_password", {"username": "ananya.iyer"}, ctx(MENON))
    assert reset["temporary_password"]
    status = {
        a["username"]: a["status"] for a in box.call("team_accounts", {}, ctx(MENON))["accounts"]
    }
    assert status["ananya.iyer"] == "must change password"
    assert "cannot use" in box.call("team_accounts", {}, ctx(IYER))["error"]


# -- alert addresses -------------------------------------------------------------------------


def test_alert_addresses_subscribe_to_one_group(mem, ctx):
    added = box.call(
        "add_alert_address", {"address": "Gates@Example.com", "team": "gate_control"}, ctx(MENON)
    )
    assert added["address"] == "gates@example.com" and added["team_title"]
    again = box.call(
        "add_alert_address", {"address": "gates@example.com", "team": "gate_control"}, ctx(MENON)
    )
    assert "already" in again["error"]
    assert (
        "no group"
        in box.call("add_alert_address", {"address": "a@b.co", "team": "catering"}, ctx(MENON))[
            "error"
        ]
    )


# -- desktop alerts ------------------------------------------------------------------------


def _hmac(key, data):
    return hmac.new(key, data, hashlib.sha256).digest()


def decrypt(body: bytes, browser: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with a push message (RFC 8291)."""
    salt, keylen = body[:16], body[20]
    server_public = body[21 : 21 + keylen]
    ua_public = browser.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
    shared = browser.exchange(ec.ECDH(), peer)
    ikm = _hmac(_hmac(auth, shared), b"WebPush: info\x00" + ua_public + server_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, body[21 + keylen :], None)
    return plain.rstrip(b"\x00")[:-1]


def test_desktop_alerts_reach_the_right_browsers_encrypted(mem):
    browser = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    keys = {
        "p256dh": b64u(
            browser.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        ),
        "auth": b64u(auth),
    }
    posted = []
    push = Push(mem, post=lambda url, body, headers: posted.append((url, body, headers)) or 201)
    desk_browser = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys}
    push.subscribe(desk_browser, "ananya.iyer", "controller", None)
    with pytest.raises(ValueError):
        push.subscribe(
            {"endpoint": "https://evil.example/x", "keys": keys}, "ananya.iyer", "x", None
        )
    assert allowed("https://updates.push.services.mozilla.com/wpush/v2/x")

    outbox = PushingOutbox(MemoryOutbox(), push)
    at = mem.operation().now
    outbox.send(Notice(DESK, "Approval needed", "A plan waits", at, "PL-1"))
    outbox.send(Notice("C-1157", "Callout offer", "for you", at, "CO-1"))  # no crew browser
    assert len(posted) == 1 and len(outbox.sent()) == 2
    url, body, headers = posted[0]
    assert json.loads(decrypt(body, browser, auth))["title"] == "Approval needed"
    assert headers["Content-Encoding"] == "aes128gcm"
    token = headers["Authorization"].split("t=")[1].split(",")[0]
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert len(unb64u(push.public_key())) == 65
    assert Push(mem).public_key() == push.public_key(), "the key pair is kept"


def test_a_browser_that_unsubscribed_is_forgotten(mem):
    push = Push(mem, post=lambda *a: 410)
    keys = {"p256dh": b64u(b"\x04" + bytes(64)), "auth": b64u(bytes(16))}
    push.subscribe(
        {"endpoint": "https://fcm.googleapis.com/x", "keys": keys},
        "ananya.iyer",
        "controller",
        None,
    )
    real = ec.generate_private_key(ec.SECP256R1()).public_key()
    keys["p256dh"] = b64u(
        real.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    )
    push.subscribe(
        {"endpoint": "https://fcm.googleapis.com/x", "keys": keys},
        "ananya.iyer",
        "controller",
        None,
    )
    push.send(Notice(DESK, "s", "b", mem.operation().now, "r"))
    assert not push.subscribed("ananya.iyer")


# -- risk ----------------------------------------------------------------------------------


def test_risk_scores_say_what_drives_them_and_never_rank_cover(mem):
    op = mem.operation()
    scores = {c: crew_risk(op, c) for c in op.crew}
    top = max(scores.values(), key=lambda r: r.score)
    assert top.score == sum(d.points for d in top.drivers) and top.drivers
    assert top.level in (RiskLevel.HIGH, RiskLevel.MEDIUM)

    first = crew_risk(op, "C-1157").score
    mem.mark_unavailable(
        "C-1157", op.now.date(), op.now.date(), "fever", "ananya.iyer", kind="sick"
    )
    again = crew_risk(mem.operation(), "C-1157", mem.unavailability())
    assert again.score == min(100, first + 15)
    assert any(d.factor == "reported unavailable" for d in again.drivers)

    cover = box.call(
        "find_cover",
        {"trip_id": "T-1018", "rank": "Captain", "vacated_by": "C-1111"},
        Context(mem, IYER),
    )
    crewed = [c for c in cover["choices"] if c.get("crew_id")]
    costs = [c["cost_inr"] for c in crewed]
    assert costs == sorted(costs), "cost alone orders the choices"
    assert all("risk" in c for c in crewed)


def test_crew_see_only_their_own_risk(mem):
    me = Caller("c1157.reddy", Role.CREW, "C-1157")
    mine = box.call("crew_risk", {"crew_id": "C-1111"}, Context(mem, me))["crew"]
    assert [r["crew_id"] for r in mine] == ["C-1157"]


# -- roster upload ---------------------------------------------------------------------------


def test_a_roster_upload_is_checked_whole_before_anything_is_written(mem):
    trip = next(t for t in mem.dataset().trips if t.trip_id == "T-1018").model_dump(mode="json")
    bad = {"trips": [{**trip, "crew": [{"crew_id": "C-9999", "rank": "Captain"}]}]}
    refused = box.call("upload_roster", {"content": bad, "dry_run": False}, Context(mem, MENON))
    assert refused["status"] == "rejected" and "C-9999" in refused["errors"][0]

    swapped = [
        {"crew_id": "C-1157", "rank": "Captain"} if s["crew_id"] == "C-1111" else s
        for s in trip["crew"]
    ]
    new_trip = {**trip, "trip_id": "T-1999", "crew": swapped}
    content = {"trips": [{**trip, "crew": swapped}, new_trip]}
    before = mem.roster_version()
    dry = box.call("upload_roster", {"content": content}, Context(mem, MENON))
    assert dry["status"] == "accepted" and not dry["applied"]
    assert dry["counts"]["trips"] == {"added": 1, "replaced": 1}
    assert any(p["trip_id"] == "T-1999" for p in dry["new_problems"]), (
        "putting the same captain on two trips the same days is a new problem"
    )
    assert mem.roster_version() == before

    done = box.call("upload_roster", {"content": content, "dry_run": False}, Context(mem, MENON))
    assert done["applied"]
    assert "C-1157" in {s.crew_id for s in mem.trip("T-1018")[0].crew}
    assert mem.trip("T-1999")[1] == 1
    assert mem.changes("T-1018")[-1].actor == "priya.desai"
    assert (
        "cannot use" in box.call("upload_roster", {"content": content}, Context(mem, IYER))["error"]
    )
