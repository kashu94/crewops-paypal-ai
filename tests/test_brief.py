import pytest

from crewpay.engine import build_operation
from crewpay.models import ModelError
from crewpay.ops.brief import (
    BriefText,
    facts,
    mood_of,
    write_brief,
)
from crewpay.seed import load_dataset
from crewpay.settings import ROOT


@pytest.fixture(scope="module")
def op():
    return build_operation(load_dataset(ROOT / "data" / "seed"))


class Writer:
    def __init__(self, reply):
        self.reply = reply

    def parse(self, messages, schema, **kwargs):
        if isinstance(self.reply, Exception):
            raise self.reply
        assert schema is BriefText
        return self.reply, None


def test_the_facts_lead_with_what_breaks_a_rule(op):
    f = facts(op, [])
    assert [i["crew_id"] for i in f["illegal"]] == ["C-1111"]
    assert f["illegal"][0]["cost_inr"] == 20840
    assert mood_of(f) == "critical"
    assert mood_of({**f, "illegal": [], "fatigue": []}) == "calm"


def test_a_model_written_brief_is_kept_when_every_figure_checks_out(op):
    good = BriefText(
        headline="Cover T-1018 now",
        lines=["A. Nair (C-1111) cannot fly T-1018; cover C-1157 for INR 20840."],
    )
    brief = write_brief(op, [], Writer(good))
    assert (brief.written_by, brief.verified, brief.lines) == ("model", True, good.lines)


def test_a_brief_with_an_invented_figure_falls_back_to_the_template(op):
    bad = BriefText(headline="Cover T-1018", lines=["Cover C-1157 for INR 19000."])
    brief = write_brief(op, [], Writer(bad))
    assert brief.written_by == "template"
    assert brief.lines[0].startswith("T-1018: A. Nair (C-1111)")
    assert write_brief(op, [], Writer(ModelError("down"))).written_by == "template"
    assert write_brief(op, [], None).headline == "Action needed"
