"""The Alexa skill: Amazon's request checks, account linking with CrewPay's sign-in, what the
skill says, and the Echo chiming when a notice reaches someone."""

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import SEED_DIR
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from cryptography.x509.verification import Store
from fastapi.testclient import TestClient

import crewpay.api.app as api_module
from crewpay.agent.tools import Caller, Role
from crewpay.alexa import proactive
from crewpay.alexa.linking import Linked, LinkError, Links
from crewpay.alexa.notifier import AlexaNotifier, AlexaOutbox, announcement, recipients
from crewpay.alexa.skill import AlexaLinks, Skill, spoken
from crewpay.alexa.verify import NotFromAlexa, valid_cert_url, verified
from crewpay.api.app import Services, create_app
from crewpay.ops.notify import DESK, MemoryOutbox, Notice
from crewpay.seed import read_meta
from crewpay.settings import ROOT, load_settings
from crewpay.store import MemoryStore

NOW = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
SKILL = "amzn1.ask.skill.test-crewpay"
CERT_URL = "https://s3.amazonaws.com/echo.api/echo-api-cert-12.pem"
REDIRECT = "https://pitangui.amazon.com/api/skill/link/MTEST"
CLIENT, SECRET = "paypal-crewpay-alexa", "a-long-test-secret"


def _cert(subject, issuer, key, signer, *, ca, san=None):
    b = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - timedelta(days=30))
        .not_valid_after(NOW + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False
        )
    )
    if ca:
        b = b.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
    else:
        b = b.add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False),
            critical=True,
        ).add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
    if san:
        b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return b.sign(signer, hashes.SHA256())


@pytest.fixture(scope="module")
def pki():
    root_key, mid_key, leaf_key = (rsa.generate_private_key(65537, 2048) for _ in range(3))
    root = _cert("Test Root", "Test Root", root_key, root_key, ca=True)
    mid = _cert("Test Intermediate", "Test Root", mid_key, root_key, ca=True)

    def leaf_for(name):
        return _cert(name, "Test Intermediate", leaf_key, mid_key, ca=False, san=name)

    return {"root": root, "mid": mid, "leaf_key": leaf_key, "leaf_for": leaf_for}


def envelope(kind="LaunchRequest", token="tok", skill=SKILL, at=NOW, **request):
    return {
        "version": "1.0",
        "context": {
            "System": {
                "application": {"applicationId": skill},
                "user": {
                    "userId": "amzn1.ask.account.MEERA",
                    **({"accessToken": token} if token else {}),
                },
                "apiEndpoint": "https://api.eu.amazonalexa.com",
                "apiAccessToken": "per-request-token",
            }
        },
        "request": {"type": kind, "timestamp": at.strftime("%Y-%m-%dT%H:%M:%SZ"), **request},
    }


def signed(pki, body: bytes, name="echo-api.amazon.com"):
    chain = pki["leaf_for"](name).public_bytes(Encoding.PEM) + pki["mid"].public_bytes(Encoding.PEM)
    sig = pki["leaf_key"].sign(body, padding.PKCS1v15(), hashes.SHA256())
    headers = {"SignatureCertChainUrl": CERT_URL, "Signature-256": base64.b64encode(sig).decode()}
    return headers, (lambda url: chain)


def check(pki, body, headers, fetch, skill=SKILL, now=NOW):
    from crewpay.alexa import verify

    verify._chains.clear()
    return verified(body, headers, skill, fetch=fetch, store=Store([pki["root"]]), now=now)


# -- Amazon's request checks ---------------------------------------------------------------


def test_only_amazons_certificate_urls_are_fetched():
    assert valid_cert_url("https://s3.amazonaws.com/echo.api/echo-api-cert.pem")
    assert valid_cert_url("https://s3.amazonaws.com:443/echo.api/../echo.api/echo-api-cert.pem")
    for bad in (
        "http://s3.amazonaws.com/echo.api/echo-api-cert.pem",
        "https://notamazon.com/echo.api/echo-api-cert.pem",
        "https://s3.amazonaws.com/EcHo.aPi/echo-api-cert.pem",
        "https://s3.amazonaws.com:563/echo.api/echo-api-cert.pem",
        "https://s3.amazonaws.com/invalid.path/echo-api-cert.pem",
        "https://s3.amazonaws.com/echo.api/../invalid/echo-api-cert.pem",
        "https://s3.amazonaws.com/echo.api/echo-api-cert.pem?x=1",
    ):
        assert not valid_cert_url(bad), bad


def test_a_genuine_request_passes_and_any_tampering_fails(pki):
    body = json.dumps(envelope()).encode()
    headers, fetch = signed(pki, body)
    assert check(pki, body, headers, fetch)["request"]["type"] == "LaunchRequest"
    with pytest.raises(NotFromAlexa, match="signature"):
        check(pki, body.replace(b"Launch", b"Lunch!"), headers, fetch)
    other_headers, other_fetch = signed(pki, body, name="attacker.example.com")
    with pytest.raises(NotFromAlexa, match="chain"):
        check(pki, body, other_headers, other_fetch)
    with pytest.raises(NotFromAlexa, match="too old"):
        check(pki, body, headers, fetch, now=NOW + timedelta(seconds=200))
    with pytest.raises(NotFromAlexa, match="another skill"):
        check(pki, body, headers, fetch, skill="amzn1.ask.skill.someone-else")
    with pytest.raises(NotFromAlexa, match="missing"):
        check(pki, body, {}, fetch)

    def unreachable(url):
        raise OSError("timed out")

    with pytest.raises(NotFromAlexa, match="unavailable"):
        check(pki, body, headers, unreachable)


# -- Account linking ------------------------------------------------------------------------


def test_a_code_becomes_tokens_once_and_only_for_its_redirect():
    links = Links(CLIENT, SECRET)
    links.check_request(CLIENT, REDIRECT, "code")
    for client, redirect, kind in (
        ("someone-else", REDIRECT, "code"),
        (CLIENT, "https://evil.example.com/cb", "code"),
        (CLIENT, REDIRECT, "token"),
    ):
        with pytest.raises(LinkError):
            links.check_request(client, redirect, kind)
    meera = Linked("meera.bhatt", "approver", None)
    code = links.code(meera, REDIRECT)
    with pytest.raises(LinkError, match="another redirect"):
        links.exchange("authorization_code", code, "https://layla.amazon.com/other")
    tokens = links.exchange("authorization_code", code, REDIRECT)
    assert links.who(str(tokens["access_token"])) == meera
    # An access token is not a refresh token, nor a code, and another secret signs nothing here.
    with pytest.raises(LinkError):
        links.exchange("refresh_token", str(tokens["access_token"]))
    with pytest.raises(LinkError):
        Links(CLIENT, "another-secret").who(str(tokens["access_token"]))
    again = links.exchange("refresh_token", str(tokens["refresh_token"]))
    assert links.who(str(again["access_token"])) == meera


def test_an_expired_code_is_refused(monkeypatch):
    links = Links(CLIENT, SECRET)
    code = links.code(Linked("meera.bhatt", "approver", None), REDIRECT)
    import crewpay.alexa.linking as linking

    later = linking.time.time() + linking.CODE_SECONDS + 5
    monkeypatch.setattr(linking.time, "time", lambda: later)
    with pytest.raises(LinkError, match="expired"):
        links.exchange("authorization_code", code, REDIRECT)


# -- The skill ------------------------------------------------------------------------------

MEERA = Caller("meera.bhatt", Role.APPROVER)


def skill(store=None, ask=None, waiting=None, remind=None):
    links = AlexaLinks(store or MemoryStore())

    def identify(token):
        if token != "tok":
            raise ValueError("not linked")
        return MEERA

    return Skill(
        links,
        identify,
        ask or (lambda who, q: f"You asked: {q}"),
        waiting or (lambda who: []),
        clock=lambda: NOW,
        remind=remind or (lambda *a: None),
    )


def said(out):
    return out["response"]["outputSpeech"]["text"]


def test_an_unlinked_account_is_asked_to_link():
    out = skill().handle(envelope(token=None))
    assert out["response"]["card"] == {"type": "LinkAccount"}
    out = skill().handle(envelope(token="someone-elses"))
    assert out["response"]["card"] == {"type": "LinkAccount"}


def test_launch_greets_by_name_and_asks_for_notifications_once():
    out = skill().handle(envelope())
    assert said(out).startswith("Welcome to CrewPay, Meera.")
    assert out["response"]["card"]["type"] == "AskForPermissionsConsent"
    granted = envelope()
    granted["context"]["System"]["user"]["permissions"] = {"consentToken": "yes"}
    assert "card" not in skill().handle(granted)["response"]


def test_whats_waiting_reads_the_most_urgent_three():
    lines = [f"Approve: plan {n}." for n in range(5)]
    out = skill(waiting=lambda who: lines).handle(
        envelope("IntentRequest", intent={"name": "WhatsWaitingIntent"})
    )
    text = said(out)
    assert text.startswith("You have 5 things waiting. The 3 most urgent:")
    assert "plan 2" in text and "plan 3" not in text
    empty = skill().handle(envelope("IntentRequest", intent={"name": "WhatsWaitingIntent"}))
    assert said(empty) == "Nothing is waiting for you right now."


def test_a_question_keeps_its_opening_word_and_is_said_aloud():
    asked = []

    def ask(who, q):
        asked.append((who, q))
        return "R. Iyer (C-1167) covers T-1001 on 2026-10-30 for ₹31,160, paid as 370.95 USD."

    out = skill(ask=ask).handle(
        envelope(
            "IntentRequest",
            intent={"name": "AskWhoIntent", "slots": {"question": {"value": "covers T 1001"}}},
        )
    )
    assert asked == [(MEERA, "who covers T 1001")]
    assert said(out) == (
        "R. Iyer (C 1167) covers T 1001 on 30 October for 31,160 rupees, paid as $370.95."
    )
    assert spoken("due 2026-02-30") == "due 2026-02-30"


def test_every_locale_model_has_the_intents_the_skill_handles():
    from crewpay.alexa.skill import QUESTION_STARTS, SAID

    folder = ROOT / "alexa" / "skill-package" / "interactionModels" / "custom"
    handled = {"WhatsWaitingIntent", "AskCrewPayIntent", *QUESTION_STARTS, *SAID}
    for model in folder.glob("*.json"):
        lm = json.loads(model.read_text())["interactionModel"]["languageModel"]
        assert lm["invocationName"] == "crew pay"
        names = {i["name"] for i in lm["intents"]}
        assert handled <= names, model.name
    manifest = json.loads((ROOT / "alexa" / "skill-package" / "skill.json").read_text())
    assert manifest["manifest"]["apis"]["custom"]["endpoint"]["uri"].endswith("/api/alexa")


def test_permission_changes_and_disabling_are_remembered():
    store = MemoryStore()
    s = skill(store)
    s.handle(envelope())  # a request from a linked account keeps the link
    changed = envelope(
        "AlexaSkillEvent.ProactiveSubscriptionChanged",
        token=None,
        body={"subscriptions": [{"eventName": "AMAZON.MessageAlert.Activated"}]},
    )
    s.handle(changed)
    (link,) = AlexaLinks(store).all()
    assert link["user"] == "meera.bhatt" and link["role"] == "approver"
    assert link["notifications"] is True
    s.handle(envelope("AlexaSkillEvent.SkillDisabled", token=None))
    assert AlexaLinks(store).all() == []


def test_the_echo_says_what_happened_through_a_reminder():
    set_ = []
    s = skill(remind=lambda endpoint, token, body: set_.append((endpoint, token, body)))
    s.handle(
        envelope(
            "Messaging.MessageReceived",
            token=None,
            message={"announce": "CrewPay. Approval needed: Captain on T-1001.", "locale": "en-IN"},
        )
    )
    ((endpoint, token, body),) = set_
    assert endpoint == "https://api.eu.amazonalexa.com" and token == "per-request-token"
    assert body["trigger"]["offsetInSeconds"] == proactive.REMINDER_DELAY_SECONDS
    text = body["alertInfo"]["spokenInfo"]["content"][0]["text"]
    assert text == "CrewPay. Approval needed: Captain on T 1001."


# -- Notifications --------------------------------------------------------------------------


def _links(store):
    links = AlexaLinks(store)
    for alexa_user, who in (
        ("A-ANANYA", Caller("ananya.iyer", Role.CONTROLLER)),
        ("A-MEERA", MEERA),
        ("A-IYER", Caller("c1167.iyer", Role.CREW, "C-1167")),
        ("A-QUIET", Caller("karan.mehta", Role.APPROVER)),
    ):
        links.save(alexa_user, "https://api.amazonalexa.com", who, NOW, "en-IN")
        links.notifications(alexa_user, "https://api.amazonalexa.com", alexa_user != "A-QUIET", NOW)
    return links


def test_only_the_people_a_notice_is_for_are_chimed():
    links = _links(MemoryStore()).all()

    def notice(to, subject):
        return Notice(to, subject, "body", NOW, "ref")

    def who(n):
        return sorted(link["user"] for link in recipients(n, links))

    assert who(notice(DESK, "New disruption: Sick: A. Verma")) == ["ananya.iyer"]
    assert who(notice(DESK, "Approval needed: Captain on T-1001")) == ["meera.bhatt"]
    assert who(notice(DESK, "Watch: medical lapses")) == []
    assert who(notice("C-1167", "Callout: Captain on T-1001")) == ["c1167.iyer"]
    assert who(notice("C-1157", "Callout: Captain on T-1001")) == []


def test_a_notice_is_kept_even_when_alexa_fails_and_the_words_go_with_the_chime():
    store = MemoryStore()
    _links(store)
    sent = []

    def send(link, now, text):
        sent.append((link["user"], text))
        raise RuntimeError("Alexa is down")

    inner = MemoryOutbox()
    outbox = AlexaOutbox(inner, AlexaNotifier(AlexaLinks(store), send), background=False)
    n = Notice(
        DESK,
        "New disruption: Sick: A. Verma, 30 Oct",
        "Crew unavailable reported by c1123.verma. Assigned to Desk 1 (Ananya Iyer); severity "
        "high (1 seat to fill; reports in 6 hours; 541 passengers booked).",
        NOW,
        "R-1",
    )
    outbox.send(n)
    assert inner.sent() == [n]
    words = (
        "CrewPay, high severity. New on your desk: Sick: A. Verma, 30 Oct. "
        "Reports in 6 hours, 541 passengers booked."
    )
    assert sent == [("ananya.iyer", words)]
    # Anything else: its subject and first sentence, and nobody's sign-in name read out.
    other = Notice(
        "C-1167", "Offer withdrawn: Captain on T-1001", "c1157.reddy took it. X.", NOW, "x"
    )
    assert announcement(other) == "CrewPay. Offer withdrawn: Captain on T 1001. someone took it."


def test_a_notification_is_a_message_alert_to_the_persons_region():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        if url == proactive.LWA_URL:
            return 200, json.dumps({"access_token": "lwa-token", "expires_in": 3600}).encode()
        return 202, b""

    tokens = proactive.Tokens("client", "secret", post=post)
    event = proactive.message_alert("A-MEERA", NOW)
    assert event["event"]["payload"]["messageGroup"]["creator"]["name"] == "CrewPay"
    proactive.send(tokens, "https://api.eu.amazonalexa.com", event, "development", post=post)
    proactive.send(tokens, "https://api.eu.amazonalexa.com", event, "development", post=post)
    urls = [u for u, _, _ in calls]
    assert urls.count(proactive.LWA_URL) == 1
    assert urls[-1] == "https://api.eu.amazonalexa.com/v1/proactiveEvents/stages/development"
    with pytest.raises(proactive.ProactiveError):
        proactive.events_url("https://evil.example.com", "development")


# -- Through the server ---------------------------------------------------------------------


@pytest.fixture
def alexa_client(seed, monkeypatch):
    config = load_settings(
        {
            "PROMPT_GUARD_URL": "",
            "CREWPAY_SEED_DIR": str(SEED_DIR),
            "CREWPAY_DEMO_PASSWORD": "open-sesame",
            "CREWPAY_ALEXA_SKILL_ID": SKILL,
            "CREWPAY_ALEXA_CLIENT_ID": CLIENT,
            "CREWPAY_ALEXA_CLIENT_SECRET": SECRET,
        }
    )
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    services = Services(config, store)
    return TestClient(create_app(config, services), follow_redirects=False), services


def test_the_endpoint_refuses_anything_alexa_did_not_sign(alexa_client):
    client, _ = alexa_client
    r = client.post("/api/alexa", content=json.dumps(envelope()).encode())
    assert r.status_code == 400


def test_linking_through_the_sign_in_then_asking_whats_waiting(alexa_client, monkeypatch):
    client, services = alexa_client
    query = {
        "client_id": CLIENT,
        "redirect_uri": REDIRECT,
        "state": "xyz",
        "response_type": "code",
        "scope": "crewpay",
    }
    page = client.get("/api/alexa/link", params=query)
    assert page.status_code == 200 and "Link CrewPay to Alexa" in page.text
    assert "form-action 'self'" in page.headers["content-security-policy"]
    assert "https://pitangui.amazon.com" in page.headers["content-security-policy"]
    assert client.get("/api/alexa/link", params={**query, "redirect_uri": "https://x.io/"}).is_error
    wrong = client.post("/api/alexa/link", data={**query, "user": "meera.bhatt", "password": "no"})
    assert wrong.status_code == 401
    ok = client.post(
        "/api/alexa/link", data={**query, "user": "meera.bhatt", "password": "open-sesame"}
    )
    assert ok.status_code == 302
    back = urlparse(ok.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    got = parse_qs(back.query)
    assert got["state"] == ["xyz"]
    basic = base64.b64encode(f"{CLIENT}:{SECRET}".encode()).decode()
    bad = client.post(
        "/api/alexa/token",
        data={"grant_type": "authorization_code", "code": got["code"][0]},
        headers={"Authorization": "Basic " + base64.b64encode(b"x:y").decode()},
    )
    assert bad.status_code == 401
    tokens = client.post(
        "/api/alexa/token",
        data={
            "grant_type": "authorization_code",
            "code": got["code"][0],
            "redirect_uri": REDIRECT,
        },
        headers={"Authorization": f"Basic {basic}"},
    ).json()
    assert tokens["token_type"] == "Bearer" and tokens["refresh_token"]

    # Alexa's signature is checked by verify (tested above); here the envelope is taken as is.
    monkeypatch.setattr(api_module, "verified", lambda body, headers, skill: json.loads(body))
    asked = envelope(
        "IntentRequest", token=tokens["access_token"], intent={"name": "WhatsWaitingIntent"}
    )
    out = client.post("/api/alexa", content=json.dumps(asked).encode()).json()
    assert out["response"]["outputSpeech"]["text"]
    (link,) = services.alexa_links.all()
    assert link["user"] == "meera.bhatt"
    hello = client.post("/api/alexa", content=json.dumps(envelope(token="forged")).encode())
    assert hello.json()["response"]["card"] == {"type": "LinkAccount"}


def test_an_unlinked_echo_speaks_for_the_ops_room_and_still_offers_linking():
    room = Caller("ops.speaker", Role.SPEAKER)
    asked = []

    def waiting(who):
        return ["Approve: Captain on T 1001."] if who is room else []

    def ask(who, q):
        asked.append(who)
        return "Nothing urgent."

    s = Skill(
        AlexaLinks(MemoryStore()),
        lambda token: (_ for _ in ()).throw(ValueError("not linked")),
        ask,
        waiting,
        clock=lambda: NOW,
        room=room,
    )
    hello = s.handle(envelope(token=None))
    assert "speaks for the ops room" in said(hello)
    assert hello["response"]["card"] == {"type": "LinkAccount"}
    out = s.handle(envelope("IntentRequest", token=None, intent={"name": "WhatsWaitingIntent"}))
    assert said(out) == "The desk has 1 thing waiting. Approve: Captain on T 1001."
    s.handle(
        envelope(
            "IntentRequest",
            token=None,
            intent={"name": "AskIsIntent", "slots": {"question": {"value": "anything late"}}},
        )
    )
    assert asked == [room]
