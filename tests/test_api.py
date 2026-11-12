import io
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import SEED_DIR
from fastapi.testclient import TestClient
from test_advisor import FixedRouter, Script
from test_intake import SHARP, Reader, defect, note

import crewpay.api.app as api_module
from crewpay.agent.advisor import Advisor
from crewpay.agent.router import Intent
from crewpay.api.app import Services, create_app
from crewpay.engine.clock import IST
from crewpay.engine.watch import scan
from crewpay.models import ModelError
from crewpay.models.media import Box, Transcript
from crewpay.seed import read_meta
from crewpay.settings import load_settings
from crewpay.store import MemoryStore

BASE = {
    "PROMPT_GUARD_URL": "",
    "CREWPAY_SEED_DIR": str(SEED_DIR),
}
CONTROLLER = {"X-Demo-User": "ananya.iyer", "X-Demo-Role": "controller"}
VIEWER = {"X-Demo-User": "board", "X-Demo-Role": "auditor"}
STATION = {"X-Demo-User": "imran.sheikh", "X-Demo-Role": "reporter"}
ANJALI = {"X-Demo-User": "c1157.reddy", "X-Demo-Role": "crew", "X-Demo-Crew-Id": "C-1157"}


class Media:
    def __init__(self):
        self.heard = []

    def transcribe(self, wav, languages=None):
        self.heard.append(languages)
        return Transcript("Pooja kal bimaar hai", 2100)

    def segment(self, image, mime, phrase):
        return [Box(100, 100, 300, 260, 900, 600, 0.9)]


class Writer:
    def parse(self, *args, **kwargs):
        raise ModelError("offline")


@pytest.fixture
def services(seed, monkeypatch):
    config = load_settings({**BASE, "CREWPAY_DAILY_ASKS": "3"})
    store = MemoryStore()
    store.load(seed, read_meta(SEED_DIR))
    s = Services(config, store)
    s.media = Media()
    s.writer = Writer()
    s._advisor = Advisor(
        store,
        Script("C-1157 is legal for T-1018.", "Second answer."),
        seed.rules,
        router=FixedRouter(Intent.LEGALITY),
        desk=s.desk,
    )
    return s


@pytest.fixture
def client(services):
    return TestClient(create_app(services.config, services))


def test_public_endpoints_and_pages(services, tmp_path, monkeypatch):
    # The console is a static export: a folder per page, each with its index.html.
    (tmp_path / "speaker").mkdir()
    (tmp_path / "index.html").write_text("<title>CrewPay Console</title>")
    (tmp_path / "speaker" / "index.html").write_text("<title>Ops-room speaker</title>")
    monkeypatch.setattr(api_module, "WEB", tmp_path)
    client = TestClient(create_app(services.config, services))
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/config").json()["auth"] == "demo"
    assert "CrewPay Console" in client.get("/").text
    for path in ("/speaker/", "/speaker"):  # with or without the slash, never a redirect
        page = client.get(path, follow_redirects=False)
        assert page.status_code == 200 and "speaker" in page.text
    assert client.get("/../etc/passwd").status_code == 404
    accounts = client.get("/api/accounts/demo").json()["accounts"]
    assert len({a["role"] for a in accounts}) == 8
    assert not any("password" in a for a in accounts)


def test_me_reports_the_identity_role_and_the_operation_clock(client):
    me = client.get("/api/me", headers=ANJALI).json()
    assert (me["user"], me["role"], me["crew_id"]) == ("c1157.reddy", "crew", "C-1157")
    assert "assign_crew" not in me["tools"] and me["clock"].endswith("IST")
    assert client.get("/api/me", headers={"X-Demo-Role": "pilot"}).status_code == 400


def test_tools_go_through_the_same_role_checks(client):
    found = client.post("/api/tools/search_crew", json={"query": "a. nair"}, headers=VIEWER)
    assert found.json()["matches"][0]["crew_id"] == "C-1111"
    refused = client.post("/api/tools/assign_crew", json={}, headers=VIEWER).json()
    assert "cannot use" in refused["error"]


def test_a_change_moves_the_version_screens_poll(client):
    before = client.get("/api/version", headers=VIEWER).json()
    opened = client.post(
        "/api/tools/start_callout",
        json={"trip_id": "T-1018", "replace_crew_id": "C-1111"},
        headers=CONTROLLER,
    )
    assert opened.json()["waiting_for"] == "C-1157"
    after = client.get("/api/version", headers=VIEWER).json()
    assert after["callouts"] != before["callouts"] and after["notices"] > before["notices"]
    mine = client.get("/api/notices", headers=ANJALI).json()["notices"]
    assert mine[0]["subject"] == "Callout: Captain on T-1018"


def test_asking_returns_the_answer_with_its_trace_and_counts_against_the_allowance(client):
    first = client.post(
        "/api/ask", json={"question": "Is C-1157 legal for T-1018?"}, headers=CONTROLLER
    ).json()
    assert first["text"] == "C-1157 is legal for T-1018." and first["route"]["intent"] == "legality"
    assert first["conversation_id"] and isinstance(first["calls"], list)
    again = client.post(
        "/api/ask",
        json={"question": "And T-1010?", "conversation_id": first["conversation_id"]},
        headers=CONTROLLER,
    ).json()
    assert again["conversation_id"] == first["conversation_id"]
    client.post("/api/voice", files={"audio": ("a.wav", b"RIFF", "audio/wav")}, headers=CONTROLLER)
    over = client.post("/api/ask", json={"question": "One more?"}, headers=CONTROLLER)
    assert over.status_code == 429


def test_voice_passes_only_known_language_hints(client, services):
    r = client.post(
        "/api/voice",
        files={"audio": ("a.wav", b"RIFF", "audio/wav")},
        data={"languages": "Hindi,Klingon,English"},
        headers=CONTROLLER,
    )
    assert r.json() == {"text": "Pooja kal bimaar hai", "seconds": 2.1}
    assert services.media.heard == [["Hindi", "English"]]


def test_photos_come_back_with_status_box_and_proposal(client, services):
    services.reader = Reader(defect())
    r = client.post(
        "/api/intake",
        files={"image": ("d.jpg", SHARP, "image/jpeg")},
        data={"kind": "defect", "tail": "VT-SYA"},
        headers=CONTROLLER,
    ).json()
    assert (r["status"], r["confidence"], r["box"]["confidence"]) == ("draft", "high", 0.9)
    services.reader = Reader(note())
    r = client.post(
        "/api/intake",
        files={"image": ("n.jpg", SHARP, "image/jpeg")},
        data={"kind": "sick_note"},
        headers=CONTROLLER,
    ).json()
    assert r["proposal"]["tool"] == "report_unavailable"
    bad = client.post(
        "/api/intake",
        files={"image": ("x.gif", io.BytesIO(b"GIF89a"), "image/gif")},
        data={"kind": "defect"},
        headers=CONTROLLER,
    )
    assert bad.status_code == 415


def test_the_board_shows_brief_issues_and_callouts(client):
    client.post(
        "/api/tools/start_callout",
        json={"trip_id": "T-1018", "replace_crew_id": "C-1111"},
        headers=CONTROLLER,
    )
    b = client.get("/api/board", headers=VIEWER).json()
    assert b["brief"]["written_by"] == "template"
    assert b["brief"]["mood"] == "critical"
    assert b["issues"][0]["crew_id"] == "C-1111"
    assert b["callouts"][0]["trip_id"] == "T-1018"
    assert b["next_24h"]["open_callouts"] == 1 and b["next_24h"]["illegal"] == 1
    assert len(b["departures"]) == min(10, b["next_24h"]["flights"])
    assert b["desks"] == [{"controller": "ananya.iyer", "open": 1, "filled": 0, "to_approve": 0}]
    sy_captains = [x for x in b["standby"] if (x["airline"], x["rank"]) == ("SY", "Captain")]
    assert len(sy_captains) == 1 and sy_captains[0]["on_call"] >= 1
    assert b["latest"] == []  # the callout messaged the crew member, not the desk
    assert client.get("/api/card/../etc/passwd").status_code == 404


def test_the_speaker_feed_is_public_and_says_how_each_notice_sounds(client):
    client.post(
        "/api/tools/start_callout",
        json={"trip_id": "T-1018", "replace_crew_id": "C-1111"},
        headers=CONTROLLER,
    )
    feed = client.get("/api/speaker").json()
    assert feed["waiting"] == {
        "plans": 0,
        "decisions": 0,
        "proposals": 0,
        "resolutions": 0,
        "disruptions": 0,
    }
    illegal, running = feed["summary"]
    assert illegal.startswith("1 rostered crew member is not legal: A. Nair on T-1018")
    assert running.startswith("Callout running for Captain on T-1018: waiting for D. Reddy")


def test_an_approval_needs_an_approver_through_the_api(client):
    trip = client.post("/api/tools/trip_details", json={"trip_id": "T-1018"}, headers=CONTROLLER)
    version = trip.json()["version"]
    plan = client.post(
        "/api/tools/assign_crew",
        json={
            "trip_id": "T-1018",
            "replace_crew_id": "C-1111",
            "with_crew_id": "C-1157",
            "expected_version": version,
            "reason": "medical lapses",
        },
        headers=CONTROLLER,
    ).json()
    assert plan["status"] == "submitted"
    feed = client.get("/api/speaker").json()
    assert feed["waiting"]["plans"] == 1
    assert feed["items"][0]["tone"] == "pending"
    reddy = {"X-Demo-User": "rohit.malhotra", "X-Demo-Role": "controller"}
    review = {"plan_id": plan["plan_id"], "action": "approve"}
    refused = client.post("/api/tools/review_plan", json=review, headers=reddy).json()
    assert "only an approver" in refused["error"]
    nair = {"X-Demo-User": "meera.bhatt", "X-Demo-Role": "approver"}
    done = client.post("/api/tools/review_plan", json=review, headers=nair).json()
    assert done["status"] == "confirming" and "PayPal" in done["next_step"]
    assert done["replacing_name"], "the approver sees who is replaced by name, not only id"
    confirm = {"funding_id": done["funding_id"]}
    other = client.post("/api/tools/confirm_approval", json=confirm, headers=reddy).json()
    assert "cannot use" in other["error"]
    paid = client.post("/api/tools/confirm_approval", json=confirm, headers=nair).json()
    assert paid["status"] == "paid" and paid["plan_status"] == "approved"
    log = client.get("/api/log/activity", headers=CONTROLLER).json()["entries"]
    assert [e["action"] for e in log[:3]] == ["confirm_approval", "review_plan", "assign_crew"]


def test_only_an_admin_reads_the_security_log(client):
    assert client.get("/api/log/security", headers=CONTROLLER).status_code == 403
    crew = {"X-Demo-User": "c1157.reddy", "X-Demo-Role": "crew", "X-Demo-Crew-Id": "C-1157"}
    assert client.get("/api/log/activity", headers=crew).status_code == 403
    admin = {"X-Demo-User": "priya.desai", "X-Demo-Role": "admin"}
    assert client.get("/api/log/security", headers=admin).json() == {"entries": []}


def test_on_the_wall_clock_the_demo_starts_today(monkeypatch):
    s = Services(load_settings({**BASE, "CREWPAY_LIVE_CLOCK": "true"}), MemoryStore())
    today = datetime.now(IST).date()
    assert s.days["first"] == today.isoformat()
    op = s.store.operation()
    assert abs((op.now - datetime.now(UTC)).total_seconds()) < 60
    [illegal] = [i for i in scan(op) if i.kind == "illegal"]
    assert (illegal.crew_id, illegal.trip_id) == ("C-1111", "T-1018")
    assert illegal.day == today + timedelta(days=6), "the same problem, the same days ahead"
    assert s.store.meta()["shifted_days"] == (today - date(2026, 10, 30)).days


def test_a_checked_photo_is_kept_with_the_report_it_backs(client, services):
    services.reader = Reader(defect())
    photo = {"image": ("d.jpg", SHARP, "image/jpeg")}
    kept = client.post(
        "/api/intake",
        files=photo,
        data={"kind": "defect", "tail": "VT-SYA", "keep": "true"},
        headers=STATION,
    ).json()
    fault = {
        "kind": "aircraft_technical",
        "tail": "VT-SYA",
        "start": "2026-11-05T05:00",
        "end": "2026-11-05T09:00",
    }
    reddy = {"X-Demo-User": "lakshmi.hegde", "X-Demo-Role": "reporter"}
    theirs = {**fault, "evidence_ids": [kept["evidence_id"]]}
    assert (
        "no photo"
        in client.post("/api/tools/report_disruption", json=theirs, headers=reddy).json()["error"]
    )
    report = client.post("/api/tools/report_disruption", json=theirs, headers=STATION).json()
    assert report["evidence"] == {
        "level": "high",
        "label": "Photo checked, high confidence",
        "photos": 1,
    }
    assert report["details"]["evidence"][0]["message"].startswith("Draft defect report")
    assert client.get(f"/api/evidence/{kept['evidence_id']}", headers=CONTROLLER).content == SHARP
    assert client.get(f"/api/evidence/{kept['evidence_id']}", headers=ANJALI).status_code == 403
    bare = client.post(
        "/api/tools/report_disruption", json={**fault, "tail": "VT-SYB"}, headers=STATION
    ).json()
    assert bare["evidence"]["label"] == "No photo"
    assert "maintenance confirms" in bare["details"]["evidence_note"]


def test_a_note_that_does_not_match_the_report_is_flagged(client, services):
    services.reader = Reader(note())
    kept = client.post(
        "/api/intake",
        files={"image": ("n.jpg", SHARP, "image/jpeg")},
        data={"kind": "sick_note", "keep": "true"},
        headers=STATION,
    ).json()
    form = services.store.get_doc("evidence", kept["evidence_id"])
    [crew_id] = form["crew_ids"]
    first, last = form["days"]

    def sick(crew, day_one, day_two):
        return client.post(
            "/api/tools/report_disruption",
            json={
                "kind": "crew_unavailable",
                "crew_ids": [crew],
                "first_day": day_one,
                "last_day": day_two,
                "evidence_ids": [kept["evidence_id"]],
            },
            headers=STATION,
        ).json()["details"]["evidence"][0]

    same = sick(crew_id, first, last)
    assert same["check"] == "The photo matches the person and the dates reported."
    assert "Confirm to record" not in same["message"]
    other = client.post(
        "/api/tools/report_disruption",
        json={
            "kind": "crew_unavailable",
            "crew_ids": ["C-1157"],
            "first_day": first,
            "last_day": last,
            "evidence_ids": [kept["evidence_id"]],
        },
        headers=STATION,
    ).json()
    assert other["error"].startswith(f"The photo names {crew_id}")


def test_a_certificate_that_names_the_person_reported_is_kept_not_refused():
    from datetime import date

    from crewpay.agent.desk_tools import NOT_ON_REPORT, FileReport, Kind, _photo_matches

    lapse = FileReport(
        kind=Kind.CERTIFICATION_LAPSE,
        crew_ids=["C-1302"],
        cert="recurrent_training",
        first_day=date(2026, 11, 9),
    )
    same = _photo_matches(lapse, {"crew_ids": ["C-1302"]})
    assert same == "The photo names the person reported." and not same.endswith(NOT_ON_REPORT)
    other = _photo_matches(lapse, {"crew_ids": ["C-1157"]})
    assert other is not None and other.endswith(NOT_ON_REPORT)


def test_a_passenger_message_names_the_flights_and_new_times(client):
    delay = {"kind": "flight_delay", "trip_id": "T-1018", "hours": 2}
    report = client.post("/api/tools/report_disruption", json=delay, headers=STATION).json()
    msg = client.post(
        f"/api/reports/{report['report_id']}/passenger-message",
        json={"language": "Hindi"},
        headers=CONTROLLER,
    ).json()
    first = msg["facts"]["flights"][0]
    assert first["flight"] in msg["sms"] and first["now"] in msg["sms"]
    assert msg["written_by"] == "template" and msg["verified"] is True
    assert len(msg["sms"]) <= 300
    assert (
        client.post(
            f"/api/reports/{report['report_id']}/passenger-message", json={}, headers=ANJALI
        ).status_code
        == 403
    )


def test_the_handover_says_what_each_desk_still_holds(client):
    client.post(
        "/api/tools/report_disruption",
        json={"kind": "flight_delay", "trip_id": "T-1018", "hours": 2},
        headers=STATION,
    )
    note = client.get("/api/handover", headers=CONTROLLER).json()
    assert note["headline"] == "1 disruption carried over"
    assert any(line.startswith("Desk 1") for line in note["desks"])
    assert client.get("/api/handover", headers=STATION).status_code == 403


def test_an_absence_said_aloud_becomes_fields_to_confirm(client, services):
    from crewpay.ops.messages import AbsenceReading

    class Reads:
        def parse(self, *args, **kwargs):
            return (
                AbsenceReading(
                    understood=True,
                    first_day="2026-10-30",
                    last_day="2026-10-31",
                    reason_kind="sick",
                    since="",
                    note="fever since last night",
                ),
                None,
            )

    services.reasoner = Reads()
    got = client.post(
        "/api/absence/understand", json={"text": "kal se do din bukhar hai"}, headers=ANJALI
    ).json()
    assert (got["first_day"], got["last_day"], got["reason_kind"]) == (
        "2026-10-30",
        "2026-10-31",
        "sick",
    )
    assert got["heard"] == "kal se do din bukhar hai" and "since" not in got


def test_a_super_admin_resets_the_demo_to_a_clean_slate(client, services):
    client.post(
        "/api/tools/report_disruption",
        json={"kind": "flight_delay", "trip_id": "T-1018", "hours": 2},
        headers=STATION,
    )
    assert services.reports.all()
    admin = {"X-Demo-User": "priya.desai", "X-Demo-Role": "admin"}
    assert client.post("/api/admin/reset", headers=admin).status_code == 403
    root = {"X-Demo-User": "vikram.saxena", "X-Demo-Role": "super_admin"}
    assert client.post("/api/admin/reset", headers=root).json()["reset"] is True
    assert services.reports.all() == [] and services.desk.all() == []
    assert [
        e["action"] for e in client.get("/api/log/activity", headers=CONTROLLER).json()["entries"]
    ] == ["reset the demo"]


def test_paying_and_approving_share_one_lock(services):
    """An approval opens a PayPal funding while holding the plans; a capture confirms the plan
    while holding the funding. Two locks taken in opposite orders could deadlock the server."""
    assert services.payroll.funding._lock is services.desk.plans._lock


def test_the_open_ops_room_feeds_never_show_anyones_pay_or_paypal_address(client):
    from crewpay.api.app import _aloud, _on_the_wall

    assert _aloud("x pointed C-1111's payouts to ravi.k@personal.example.") == (
        "x pointed C-1111's payouts to a new PayPal address."
    )
    assert "an amount" in _aloud("A. Nair is owed 335.43 USD for T-1018")
    assert _on_the_wall({"name": "D. Reddy", "cost_inr": 28000, "remark": "x", "answer": "no"}) == {
        "name": "D. Reddy",
        "answer": "no",
    }
    board = client.get("/api/board")
    assert board.status_code == 200
    for c in board.json()["callouts"]:
        assert all("cost_inr" not in o for o in c["asked"])


def test_every_response_carries_browser_protections(client):
    r = client.get("/api/config")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert "https://*.paypal.com" in r.headers["content-security-policy"]


def test_an_accepted_seat_shows_as_waiting_for_approval_until_it_goes_through(client):
    """The crew member who says yes sees the trip's days as waiting for an approver; once the
    approval is paid for, the seat is theirs and the days are an ordinary duty."""
    opened = client.post(
        "/api/tools/start_callout",
        json={"trip_id": "T-1001", "replace_crew_id": "C-1123"},
        headers=CONTROLLER,
    ).json()
    first = opened["waiting_for"]
    crew = {"X-Demo-User": "c", "X-Demo-Role": "crew", "X-Demo-Crew-Id": first}
    said = client.post(
        "/api/tools/answer_callout",
        json={"callout_id": opened["callout_id"], "accept": True},
        headers=crew,
    ).json()
    mine = client.post("/api/tools/callout_status", json={}, headers=crew).json()
    earlier = mine["earlier_offers"][0]
    assert earlier["outcome"] == "accepted" and earlier["waiting_for_approval"] is True
    assert earlier["days"] and all(len(d) == 10 for d in earlier["days"])

    nair = {"X-Demo-User": "meera.bhatt", "X-Demo-Role": "approver"}
    review = {"plan_id": said["plan_id"], "action": "approve"}
    done = client.post("/api/tools/review_plan", json=review, headers=nair).json()
    client.post(
        "/api/tools/confirm_approval", json={"funding_id": done["funding_id"]}, headers=nair
    )
    after = client.post("/api/tools/callout_status", json={}, headers=crew).json()
    assert after["earlier_offers"][0]["waiting_for_approval"] is False
    assert after["earlier_offers"][0]["seat_now"] == "filled by you"
