import base64
import json

import pytest
from pydantic import BaseModel

from crewpay.models import Endpoint, Ledger, ModelClient, ModelError, ModelUnavailable
from crewpay.models.client import STRUCTURED_TOOL, to_converse
from crewpay.settings import load_settings

ENDPOINT = Endpoint("advisor", "us.amazon.nova-2-lite-v1:0")


def reply(*blocks, stop="end_turn", usage=None):
    return {
        "output": {"message": {"role": "assistant", "content": list(blocks)}},
        "stopReason": stop,
        "usage": usage or {"inputTokens": 17, "outputTokens": 61, "cacheReadInputTokens": 8},
        "ResponseMetadata": {"RetryAttempts": 0},
    }


class Runtime:
    """Stands in for the bedrock-runtime client: answers in turn and keeps every request."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []

    def converse(self, **request):
        self.sent.append(request)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_for(*answers, ledger=None):
    runtime = Runtime(*answers)
    return ModelClient(ENDPOINT, ledger=ledger or Ledger(), runtime=runtime), runtime


class Intent(BaseModel):
    intent: str
    crew_ids: list[str]


def test_a_call_sends_system_and_turns_and_records_usage():
    client, runtime = client_for(reply({"text": "ready"}))
    result = client.chat(
        [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}],
        purpose="check",
        effort="minimal",
        max_tokens=50,
    )
    [request] = runtime.sent
    assert request == {
        "modelId": "us.amazon.nova-2-lite-v1:0",
        "system": [{"text": "be brief"}],
        "messages": [{"role": "user", "content": [{"text": "hi"}]}],
        "inferenceConfig": {"maxTokens": 50},
    }
    assert result.content == "ready" and result.finish_reason == "stop"
    assert client.ledger.totals()["us.amazon.nova-2-lite-v1:0"] == {
        "calls": 1,
        "prompt": 17,
        "completion": 61,
        "reasoning": 0,
        "cached": 8,
    }


def test_tool_calls_come_back_and_round_trip_as_converse_turns():
    use = {"toolUse": {"toolUseId": "t1", "name": "find_cover", "input": {"trip_id": "T-1018"}}}
    client, runtime = client_for(reply({"text": "Looking."}, use, stop="tool_use"))
    tools = [{"type": "function", "function": {"name": "find_cover", "parameters": {}}}]
    result = client.chat(
        [{"role": "user", "content": "cover T-1018?"}],
        purpose="plan",
        tools=tools,
        tool_choice="auto",
    )
    assert result.finish_reason == "tool_calls"
    [call] = result.tool_calls
    assert (call.name, json.loads(call.arguments)) == ("find_cover", {"trip_id": "T-1018"})
    assert runtime.sent[0]["toolConfig"]["toolChoice"] == {"auto": {}}

    _, turns = to_converse(
        [
            {"role": "user", "content": "cover T-1018?"},
            result.message(),
            {"role": "tool", "tool_call_id": "t1", "content": '{"ok": true}'},
        ]
    )
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"][1] == use
    assert turns[2]["content"] == [
        {"toolResult": {"toolUseId": "t1", "content": [{"text": '{"ok": true}'}]}}
    ]


def test_structured_replies_are_forced_through_one_tool_and_validated():
    form = {
        "toolUse": {
            "toolUseId": "f",
            "name": STRUCTURED_TOOL,
            "input": {"intent": "cover", "crew_ids": ["C-1"]},
        }
    }
    client, runtime = client_for(reply(form, stop="tool_use"))
    parsed, _ = client.parse([{"role": "user", "content": "x"}], Intent, purpose="route")
    assert parsed == Intent(intent="cover", crew_ids=["C-1"])
    config = runtime.sent[0]["toolConfig"]
    assert config["toolChoice"] == {"tool": {"name": STRUCTURED_TOOL}}
    schema = config["tools"][0]["toolSpec"]["inputSchema"]["json"]
    assert schema["additionalProperties"] is False and schema["required"] == ["intent", "crew_ids"]

    bad = {"toolUse": {"toolUseId": "f", "name": STRUCTURED_TOOL, "input": {"intent": 3}}}
    client, _ = client_for(reply(bad))
    with pytest.raises(ModelError, match="did not match Intent"):
        client.parse([{"role": "user", "content": "x"}], Intent, purpose="route")


def test_images_go_as_bytes():
    png = b"\x89PNG fake"
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    _, turns = to_converse(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read this"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ]
    )
    assert turns[0]["content"][1] == {"image": {"format": "png", "source": {"bytes": png}}}


def test_bedrock_failures_become_model_errors():
    client, _ = client_for(RuntimeError("throttled"))
    with pytest.raises(ModelError, match="throttled"):
        client.chat([{"role": "user", "content": "hi"}], purpose="check")


def test_no_credentials_or_an_unknown_effort_fails_before_any_call():
    config = load_settings({"CREWPAY_SEED_DIR": "x"})
    assert not config.has_bedrock
    offline = ModelClient(Endpoint.advisor(config), config=config)
    with pytest.raises(ModelUnavailable):
        offline.chat([{"role": "user", "content": "hi"}], purpose="check")
    client, runtime = client_for()
    with pytest.raises(ValueError):
        client.chat([{"role": "user", "content": "hi"}], purpose="check", effort="huge")
    assert runtime.sent == []


def test_the_default_profile_is_refused():
    with pytest.raises(ValueError, match="default"):
        load_settings({"CREWPAY_AWS_PROFILE": "default"})


def test_only_the_paypal_sandbox_is_allowed():
    with pytest.raises(ValueError, match="sandbox"):
        load_settings({"PAYPAL_BASE_URL": "https://api-m.paypal.com"})


def test_the_ledger_appends_to_a_file(tmp_path):
    path = tmp_path / "usage.jsonl"
    client, _ = client_for(reply({"text": "a"}), reply({"text": "b"}), ledger=Ledger(path))
    client.chat([{"role": "user", "content": "1"}], purpose="one")
    client.chat([{"role": "user", "content": "2"}], purpose="two")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["purpose"] for r in rows] == ["one", "two"]
