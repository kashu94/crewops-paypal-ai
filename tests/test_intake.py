import io
import random
from datetime import timedelta

import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from crewpay.engine import build_operation
from crewpay.engine.clock import IST
from crewpay.models.media import Box
from crewpay.seed import load_dataset
from crewpay.settings import ROOT
from crewpay.vision import Kind, Status, measure, read_photo
from crewpay.vision.intake import (
    CertificateForm,
    Damage,
    DefectForm,
    RosterSwap,
    SceneForm,
    SickNote,
)


@pytest.fixture(scope="module")
def op():
    return build_operation(load_dataset(ROOT / "data" / "seed"))


def photo(blur=0, dim=1.0, size=(900, 600)):
    rng = random.Random(7)
    picture = Image.new("RGB", size, "white")
    pixels = picture.load()
    for x in range(size[0]):
        for y in range(size[1]):
            v = 255 if (x // 12 + y // 12) % 2 else rng.randint(40, 120)
            pixels[x, y] = (v, v, v)
    if blur:
        picture = picture.filter(ImageFilter.GaussianBlur(blur))
    if dim != 1.0:
        picture = ImageEnhance.Brightness(picture).enhance(dim)
    out = io.BytesIO()
    picture.save(out, "JPEG")
    return out.getvalue()


SHARP = photo()


class Reader:
    def __init__(self, form):
        self.form, self.calls = form, 0

    def read(self, image, mime, form_type, instructions):
        self.calls += 1
        assert isinstance(self.form, form_type)
        return self.form


class Locator:
    def __init__(self, *boxes):
        self.boxes, self.phrases = list(boxes), []

    def segment(self, image, mime, phrase):
        self.phrases.append(phrase)
        return self.boxes


def defect(**changes):
    base = dict(
        shows_aircraft=True,
        subject="engine cowling",
        area="engine",
        view="clear",
        view_problems=[],
        tail_number="",
        damage=[Damage(part="cowling", kind="dent", where="centre")],
        damage_phrase="dent",
    )
    return DefectForm(**{**base, **changes})


def note(**changes):
    base = dict(
        is_medical_note=True,
        readable="clear",
        patient_name="A. Nair",
        unfit_from="2026-11-05",
        unfit_to="2026-11-08",
        issued_by="Dr Rao",
    )
    return SickNote(**{**base, **changes})


def test_blurred_dark_and_tiny_photos_are_sent_back_before_any_model_is_asked(op):
    assert measure(SHARP).problems == ()
    for image, problem in (
        (photo(blur=6), "blurred"),
        (photo(dim=0.1), "too dark"),
        (photo(size=(200, 150)), "too small"),
    ):
        reader = Reader(defect())
        result = read_photo(op, Kind.DEFECT, image, "image/jpeg", reader)
        assert result.status is Status.RESHOOT and problem in result.message
        assert reader.calls == 0
    assert read_photo(op, Kind.DEFECT, b"not an image", "image/jpeg", Reader(defect())).status is (
        Status.REJECTED
    )


def test_a_clear_located_defect_is_a_high_confidence_draft(op):
    locator = Locator(Box(100, 100, 300, 260, 900, 600, 0.94))
    result = read_photo(op, Kind.DEFECT, SHARP, "image/jpeg", Reader(defect()), locator, "VT-SYA")
    assert (result.status, result.confidence) == (Status.DRAFT, "high")
    assert result.box.confidence == 0.94 and locator.phrases == ["dent", "damage"]
    assert "Needs engineer confirmation" in result.message
    assert result.signals["damage_located"] is True


def test_a_box_covering_the_whole_photo_does_not_count_as_finding_the_damage(op):
    locator = Locator(Box(0, 0, 900, 600, 900, 600, 0.95))
    result = read_photo(op, Kind.DEFECT, SHARP, "image/jpeg", Reader(defect()), locator)
    assert result.box is None and result.confidence == "medium"


def test_no_visible_damage_is_never_a_clearance(op):
    result = read_photo(op, Kind.DEFECT, SHARP, "image/jpeg", Reader(defect(damage=[])), Locator())
    assert (result.status, result.confidence) == (Status.REJECTED, "low"), "backs nothing up"
    assert "not a clearance" in result.message
    tail = next(iter(op.legs.values())).tail
    plate = read_photo(
        op, Kind.DEFECT, SHARP, "image/jpeg", Reader(defect(damage=[], tail_number=tail))
    )
    assert (plate.status, plate.confidence) == (Status.DRAFT, "low"), "names the aircraft"
    assert plate.signals["tail_seen"] == tail and "not a clearance" in plate.message


def test_gate_photos_must_show_a_gate_problem_or_its_sign(op):
    def gate(**changes):
        base = {
            "shows_airport": True,
            "subject": "gate area",
            "condition": "spillage",
            "view": "clear",
            "detail": "oil spilled across the boarding area",
            "gate_sign": "Gate E-G1",
        }
        return SceneForm(**{**base, **changes})

    spill = read_photo(op, Kind.GATE, SHARP, "image/jpeg", Reader(gate()))
    assert spill.status is Status.DRAFT and spill.signals["gate_seen"] == "E_G1"
    assert "a spillage" in spill.message and "Gate E_G1" in spill.message
    fog = read_photo(op, Kind.GATE, SHARP, "image/jpeg", Reader(gate(condition="fog")))
    assert fog.status is Status.REJECTED and "station disruption" in fog.message
    sign = read_photo(op, Kind.GATE, SHARP, "image/jpeg", Reader(gate(condition="none")))
    assert sign.status is Status.DRAFT and sign.signals["gate_seen"] == "E_G1"
    empty = read_photo(
        op, Kind.GATE, SHARP, "image/jpeg", Reader(gate(condition="none", gate_sign=""))
    )
    assert empty.status is Status.REJECTED
    blurry = read_photo(op, Kind.GATE, SHARP, "image/jpeg", Reader(gate(view="unclear")))
    assert blurry.status is Status.RESHOOT


def test_wrong_subject_unclear_view_and_wrong_aircraft_are_caught(op):
    car = read_photo(
        op,
        Kind.DEFECT,
        SHARP,
        "image/jpeg",
        Reader(defect(shows_aircraft=False, subject="a parked car")),
    )
    assert car.status is Status.REJECTED and "a parked car" in car.message

    unclear = read_photo(
        op,
        Kind.DEFECT,
        SHARP,
        "image/jpeg",
        Reader(defect(view="unclear", view_problems=["panel half hidden"])),
    )
    assert unclear.status is Status.RESHOOT and "panel half hidden" in unclear.message

    other = read_photo(
        op,
        Kind.DEFECT,
        SHARP,
        "image/jpeg",
        Reader(defect(tail_number="VT-KFA")),
        Locator(),
        "VT-SYA",
    )
    assert other.status is Status.CONFIRM and "looks like VT-KFA, not VT-SYA" in other.message

    stranger = read_photo(
        op, Kind.DEFECT, SHARP, "image/jpeg", Reader(defect(tail_number="VT-XYZ")), Locator()
    )
    assert stranger.status is Status.CONFIRM and "not in our fleet" in stranger.message


def test_a_sick_note_becomes_a_proposal_a_person_confirms(op):
    result = read_photo(op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note()))
    assert result.status is Status.DRAFT
    assert result.proposal == {
        "tool": "report_unavailable",
        "arguments": {
            "crew_id": "C-1111",
            "first_day": "2026-11-05",
            "last_day": "2026-11-08",
            "reason": "sick note from Dr Rao",
        },
    }


def test_a_sick_note_that_is_not_one_or_names_nobody_clear_is_not_acted_on(op):
    assert (
        read_photo(
            op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note(is_medical_note=False))
        ).status
        is Status.REJECTED
    )
    assert (
        read_photo(op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note(readable="unclear"))).status
        is Status.RESHOOT
    )
    pooja = read_photo(op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note(patient_name="Nair")))
    assert pooja.status is Status.CONFIRM and len(pooja.crew) > 1 and pooja.proposal is None
    nobody = read_photo(
        op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note(patient_name="Zara Quill"))
    )
    assert nobody.status is Status.CONFIRM and "not in the roster" in nobody.message
    undated = read_photo(op, Kind.SICK_NOTE, SHARP, "image/jpeg", Reader(note(unfit_from="")))
    assert undated.status is Status.CONFIRM and undated.proposal is None


def test_swaps_and_certificates_are_checked_against_the_roster(op):
    swap = RosterSwap(
        is_roster_swap=True,
        readable="clear",
        crew_names=["D. Reddy", "R. Iyer"],
        trip_ids=["T-1018"],
        signed_by_both=False,
    )
    result = read_photo(op, Kind.ROSTER_SWAP, SHARP, "image/jpeg", Reader(swap))
    assert result.status is Status.DRAFT and "not signed by both" in result.message
    bad = swap.model_copy(update={"trip_ids": ["T-9999"]})
    assert read_photo(op, Kind.ROSTER_SWAP, SHARP, "image/jpeg", Reader(bad)).status is (
        Status.CONFIRM
    )
    cert = CertificateForm(
        is_certificate=True,
        readable="clear",
        holder_name="A. Nair",
        kind="medical",
        expires="2027-11-05",
    )
    result = read_photo(op, Kind.CERTIFICATE, SHARP, "image/jpeg", Reader(cert))
    assert "expiring 05 Nov 2027; on file: 05 Nov 2026" in result.message


def board(*flights, readable="clear", is_notice=True):
    from crewpay.vision.intake import DisruptionNotice, FlightStatus

    return DisruptionNotice(
        is_flight_notice=is_notice,
        readable=readable,
        flights=[FlightStatus(**f) for f in flights],
    )


def test_a_departures_board_turns_delays_into_crew_checks(op):
    leg = min(
        (leg for leg in op.legs.values() if leg.flight_no == "SY221" and leg.off_block > op.now),
        key=lambda leg: leg.off_block,
    )
    notice = board(
        {"flight_no": "SY221", "status": "delayed", "new_time": "", "delay_minutes": 150},
        {"flight_no": "KF 502", "status": "gate_change", "new_time": "", "delay_minutes": 0},
        {"flight_no": "6E123", "status": "cancelled", "new_time": "", "delay_minutes": 0},
    )
    result = read_photo(op, Kind.DISRUPTION, SHARP, "image/jpeg", Reader(notice))
    assert result.status is Status.DRAFT
    trip = next(t.trip_id for t in op.trips.values() if leg.leg_id in t.leg_ids)
    assert result.proposal == {
        "tool": "delay_impact",
        "arguments": {"trip_id": trip, "hours": 2.5, "day": leg.day.isoformat()},
    }
    assert "KF502 gate change: no crew impact" in result.message
    assert "Not ours: 6E123" in result.message


def test_a_gate_change_on_a_board_is_checked_against_who_holds_the_gate(op):
    leg = min(
        (leg for leg in op.legs.values() if leg.flight_no == "SY201" and leg.off_block > op.now),
        key=lambda leg: leg.off_block,
    )
    other = next(
        x
        for x in op.legs.values()
        if x.origin == "HYD" and x.off_block == leg.off_block + timedelta(minutes=15)
    )
    notice = board(
        {
            "flight_no": "SY201",
            "status": "gate_change",
            "new_time": "",
            "delay_minutes": 0,
            "new_gate": f"Gate {other.gate}",
        }
    )
    result = read_photo(op, Kind.DISRUPTION, SHARP, "image/jpeg", Reader(notice))
    assert f"held by {other.flight_no}" in result.message
    assert result.proposal == {
        "tool": "gate_change",
        "arguments": {"leg_id": leg.leg_id, "gate": other.gate},
    }


def test_a_new_departure_time_is_turned_into_minutes_late(op):
    leg = min(
        (leg for leg in op.legs.values() if leg.flight_no == "SY221" and leg.off_block > op.now),
        key=lambda leg: leg.off_block,
    )
    scheduled = leg.off_block.astimezone(IST)
    later = (scheduled.hour * 60 + scheduled.minute + 95) % (24 * 60)
    notice = board(
        {
            "flight_no": "SY221",
            "status": "delayed",
            "new_time": f"{later // 60:02d}:{later % 60:02d}",
            "delay_minutes": 0,
        }
    )
    result = read_photo(op, Kind.DISRUPTION, SHARP, "image/jpeg", Reader(notice))
    assert result.form["matched"][0]["delay_minutes"] == 95


def test_a_notice_that_is_not_one_or_shows_none_of_our_flights(op):
    assert (
        read_photo(op, Kind.DISRUPTION, SHARP, "image/jpeg", Reader(board(is_notice=False))).status
        is Status.REJECTED
    )
    none = board({"flight_no": "AI101", "status": "delayed", "new_time": "", "delay_minutes": 30})
    result = read_photo(op, Kind.DISRUPTION, SHARP, "image/jpeg", Reader(none))
    assert result.status is Status.CONFIRM and "AI101" in result.message


def test_a_photo_of_the_conditions_backs_a_closure(op):

    def scene(**changes):
        base = {
            "shows_airport": True,
            "subject": "apron",
            "condition": "flooding",
            "view": "clear",
            "detail": "apron under standing water",
        }
        return SceneForm(**{**base, **changes})

    unmarked = read_photo(op, Kind.SCENE, SHARP, "image/jpeg", Reader(scene()))
    assert (unmarked.status, unmarked.confidence) == (Status.DRAFT, "medium")
    locator = Locator(Box(100, 300, 800, 580, 900, 600, 0.82))
    flooded = read_photo(op, Kind.SCENE, SHARP, "image/jpeg", Reader(scene()), locator)
    assert (flooded.status, flooded.confidence) == (Status.DRAFT, "high")
    assert locator.phrases == ["flood water"] and flooded.box is not None
    assert flooded.message.startswith("Shows flooding: apron under standing water. Marked")
    cat = read_photo(
        op, Kind.SCENE, SHARP, "image/jpeg", Reader(scene(shows_airport=False, subject="a cat"))
    )
    assert cat.status is Status.REJECTED and "a cat" in cat.message
    calm = read_photo(op, Kind.SCENE, SHARP, "image/jpeg", Reader(scene(condition="none")))
    assert (calm.status, calm.confidence) == (Status.DRAFT, "low")


def test_white_paper_is_not_glare():
    page = Image.new("RGB", (900, 600), "white")
    pen = ImageDraw.Draw(page)
    for y in range(40, 560, 40):
        pen.line((40, y, 860, y), fill=(30, 30, 30), width=1)
    out = io.BytesIO()
    page.save(out, "JPEG")
    assert "washed out" in " ".join(measure(out.getvalue()).problems)
    assert measure(out.getvalue(), paper=True).problems == ()
