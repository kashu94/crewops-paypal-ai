"""Calls to the models on Amazon Bedrock.

Every model is reached through the Bedrock Converse API, so one client serves the advisor,
the router, the writer and the photo reader. The rest of the system speaks a small chat
protocol (role/content messages, function tools, tool calls with JSON arguments); this module
translates it to Converse and back. Every call:

* names its purpose and an effort hint (kept in the ledger; the models here do not bill for
  reasoning, so it does not change the request);
* asks for structured output by forcing a single tool whose input schema is the form, and
  validates what comes back before anyone uses it;
* retries throttling and server errors (botocore's adaptive retry mode);
* records its token use in a ``Ledger``.

Credentials are never taken from the machine's default AWS profile. The client uses, in this
order: a Bedrock API key (``CREWPAY_BEDROCK_API_KEY``), access keys given for this project
(``CREWPAY_AWS_ACCESS_KEY_ID`` / ``CREWPAY_AWS_SECRET_ACCESS_KEY``), or a named profile
(``CREWPAY_AWS_PROFILE``). With none of them set, model calls are unavailable and every
feature falls back to its template.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from crewpay.settings import Settings

EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
STRUCTURED_TOOL = "submit"
"""The tool a structured reply is forced through."""
IMAGE_FORMATS = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif", "image/webp": "webp"}
DATA_URL = re.compile(r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.S)
STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
}


class ModelError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class ModelUnavailable(ModelError):
    """No Bedrock credentials are configured for this project."""


@dataclass(frozen=True, slots=True)
class Endpoint:
    """One model on Bedrock, by purpose."""

    name: str
    model: str
    region: str = "ap-southeast-2"
    available: bool = True

    @classmethod
    def _of(cls, config: Settings, name: str, model: str) -> Endpoint:
        return cls(name, model, config.bedrock_region, config.has_bedrock)

    @classmethod
    def advisor(cls, config: Settings) -> Endpoint:
        return cls._of(config, "advisor", config.advisor_model)

    @classmethod
    def router(cls, config: Settings) -> Endpoint:
        return cls._of(config, "router", config.router_model)

    @classmethod
    def writer(cls, config: Settings) -> Endpoint:
        return cls._of(config, "writer", config.writer_model)

    @classmethod
    def vision(cls, config: Settings) -> Endpoint:
        return cls._of(config, "vision", config.vision_model)


@dataclass(frozen=True, slots=True)
class Usage:
    endpoint: str
    model: str
    purpose: str
    effort: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    seconds: float
    attempts: int

    @classmethod
    def from_reply(cls, body: dict[str, Any], **fields: Any) -> Usage:
        usage = body.get("usage") or {}
        return cls(
            prompt_tokens=usage.get("inputTokens", 0),
            completion_tokens=usage.get("outputTokens", 0),
            reasoning_tokens=0,
            cached_tokens=usage.get("cacheReadInputTokens") or 0,
            **fields,
        )


class Ledger:
    """Every call's token use, kept in memory and optionally appended to a JSON-lines file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: list[Usage] = []

    def record(self, usage: Usage) -> None:
        with self._lock:
            self._entries.append(usage)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as out:
                    out.write(json.dumps(asdict(usage)) + "\n")

    @property
    def entries(self) -> tuple[Usage, ...]:
        with self._lock:
            return tuple(self._entries)

    def totals(self) -> dict[str, dict[str, int]]:
        """Calls and tokens per model."""
        totals: dict[str, dict[str, int]] = {}
        for u in self.entries:
            t = totals.setdefault(
                u.model, {"calls": 0, "prompt": 0, "completion": 0, "reasoning": 0, "cached": 0}
            )
            t["calls"] += 1
            t["prompt"] += u.prompt_tokens
            t["completion"] += u.completion_tokens
            t["reasoning"] += u.reasoning_tokens
            t["cached"] += u.cached_tokens
        return totals


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    """The model's arguments as sent: JSON text, not yet trusted."""


@dataclass(frozen=True, slots=True)
class Reply:
    content: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    reasoning: str | None = None
    """The model's reasoning text, where the model returns it."""

    def message(self) -> dict[str, Any]:
        """This reply as the assistant turn to send back with the tool results."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments},
                }
                for c in self.tool_calls
            ]
        return message


def _strict(node: Any) -> Any:
    """A JSON schema tightened for structured output: no extra keys, every key required."""
    if isinstance(node, list):
        return [_strict(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strict(v) for k, v in node.items() if k not in ("default", "title")}
    if "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


def response_format(schema: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema a structured reply must fit."""
    return _strict(schema.model_json_schema())


# -- translation to and from Converse ---------------------------------------------------------


def _image_block(url: str) -> dict[str, Any]:
    match = DATA_URL.match(url)
    if not match or match["mime"] not in IMAGE_FORMATS:
        raise ModelError("only base64 data URLs of PNG, JPEG, GIF or WebP images can be sent")
    return {
        "image": {
            "format": IMAGE_FORMATS[match["mime"]],
            "source": {"bytes": base64.b64decode(match["data"])},
        }
    }


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content:
        kind = part.get("type")
        if kind == "text" and part.get("text", "").strip():
            blocks.append({"text": part["text"]})
        elif kind == "image_url":
            blocks.append(_image_block(part["image_url"]["url"]))
    return blocks


def _arguments(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def to_converse(
    messages: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The chat messages as Converse ``system`` and ``messages``: tool results become user
    turns, and turns from the same side are merged, since Converse wants them to alternate."""
    system: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []

    def add(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"].extend(blocks)
        else:
            turns.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role in ("system", "developer"):
            system.extend(_content_blocks(m.get("content")))
        elif role == "user":
            add("user", _content_blocks(m.get("content")))
        elif role == "assistant":
            blocks = _content_blocks(m.get("content"))
            for call in m.get("tool_calls") or ():
                fn = call["function"]
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": call["id"],
                            "name": fn["name"],
                            "input": _arguments(fn.get("arguments", "")),
                        }
                    }
                )
            add("assistant", blocks)
        elif role == "tool":
            text = m.get("content")
            if not isinstance(text, str):
                text = json.dumps(text)
            add(
                "user",
                [
                    {
                        "toolResult": {
                            "toolUseId": m["tool_call_id"],
                            "content": [{"text": text or "(empty)"}],
                        }
                    }
                ],
            )
    if turns and turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"text": "(continue)"}]})
    return system, turns


def tool_config(
    tools: Sequence[dict[str, Any]], tool_choice: str | dict[str, Any] | None
) -> dict[str, Any]:
    specs = []
    for t in tools:
        fn = t.get("function", t)
        specs.append(
            {
                "toolSpec": {
                    "name": fn["name"],
                    "description": fn.get("description") or fn["name"],
                    "inputSchema": {
                        "json": fn.get("parameters") or {"type": "object", "properties": {}}
                    },
                }
            }
        )
    config: dict[str, Any] = {"tools": specs}
    if tool_choice == "required":
        config["toolChoice"] = {"any": {}}
    elif isinstance(tool_choice, dict):
        config["toolChoice"] = {"tool": {"name": tool_choice["function"]["name"]}}
    elif tool_choice == "auto":
        config["toolChoice"] = {"auto": {}}
    return config


def _session(config: Settings) -> Any:
    """A boto3 session from this project's own credentials, never the default profile."""
    import boto3

    if config.bedrock_api_key:
        # Bedrock API keys are bearer tokens; botocore picks them up for the Bedrock services
        # from this variable, ahead of any signing credentials.
        import os

        os.environ["AWS_BEARER_TOKEN_BEDROCK"] = config.bedrock_api_key
        return boto3.Session(
            aws_access_key_id="unused", aws_secret_access_key="unused", region_name=None
        )
    if config.aws_access_key_id and config.aws_secret_access_key:
        return boto3.Session(
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
    if config.aws_profile:
        return boto3.Session(profile_name=config.aws_profile)
    raise ModelUnavailable("no Bedrock credentials are configured for CrewPay")


def bedrock_runtime(config: Settings) -> Any:
    from botocore.config import Config

    return _session(config).client(
        "bedrock-runtime",
        region_name=config.bedrock_region,
        config=Config(
            read_timeout=120, connect_timeout=10, retries={"max_attempts": 4, "mode": "adaptive"}
        ),
    )


@dataclass
class ModelClient:
    endpoint: Endpoint
    ledger: Ledger = field(default_factory=Ledger)
    runtime: Any = None
    """A ``bedrock-runtime`` client, or anything with its ``converse`` method (tests)."""
    config: Settings | None = None
    """Where to find credentials when ``runtime`` is built on first use."""

    def _client(self) -> Any:
        if self.runtime is None:
            if self.config is None or not self.endpoint.available:
                raise ModelUnavailable(f"no Bedrock credentials for the {self.endpoint.name} model")
            self.runtime = bedrock_runtime(self.config)
        return self.runtime

    def chat(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        purpose: str,
        effort: str = "low",
        tools: Sequence[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        schema: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Reply:
        if effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}, not {effort!r}")
        if self.runtime is None and not self.endpoint.available:
            raise ModelUnavailable(f"no Bedrock credentials for the {self.endpoint.name} model")
        system, turns = to_converse(messages)
        request: dict[str, Any] = {"modelId": self.endpoint.model, "messages": turns}
        if system:
            request["system"] = system
        if schema is not None:
            request["toolConfig"] = {
                "tools": [
                    {
                        "toolSpec": {
                            "name": STRUCTURED_TOOL,
                            "description": f"Submit the {schema.__name__} form. Always call "
                            "this tool exactly once with every field filled.",
                            "inputSchema": {"json": response_format(schema)},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": STRUCTURED_TOOL}},
            }
        elif tools:
            request["toolConfig"] = tool_config(tools, tool_choice)
        elif any(
            "toolUse" in b or "toolResult" in b for t in turns for b in t["content"]
        ):  # Converse needs the tools declared to read a conversation that used them
            raise ModelError("a conversation with tool calls needs its tools")
        inference: dict[str, Any] = {}
        if max_tokens is not None:
            inference["maxTokens"] = min(max_tokens, 10_000)
        if temperature is not None:
            inference["temperature"] = temperature
        if inference:
            request["inferenceConfig"] = inference

        started = time.monotonic()
        client = self._client()
        try:
            body = client.converse(**request)
        except ModelError:
            raise
        except Exception as err:  # botocore raises its own families; report them as one
            response = getattr(err, "response", None) or {}
            status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            raise ModelError(
                f"Bedrock {self.endpoint.name} model failed: {err}", status=status, body=str(err)
            ) from err
        attempts = 1 + int(((body.get("ResponseMetadata") or {}).get("RetryAttempts")) or 0)
        usage = Usage.from_reply(
            body,
            endpoint=self.endpoint.name,
            model=self.endpoint.model,
            purpose=purpose,
            effort=effort,
            seconds=round(time.monotonic() - started, 3),
            attempts=attempts,
        )
        self.ledger.record(usage)
        try:
            blocks = body["output"]["message"]["content"]
        except (KeyError, TypeError) as err:
            raise ModelError(
                "reply had no message", body=json.dumps(body, default=str)[:2000]
            ) from err

        texts: list[str] = []
        calls: list[ToolCall] = []
        reasoning: list[str] = []
        for block in blocks:
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                use = block["toolUse"]
                calls.append(
                    ToolCall(
                        id=use["toolUseId"],
                        name=use["name"],
                        arguments=json.dumps(use.get("input") or {}),
                    )
                )
            elif "reasoningContent" in block:
                text = (block["reasoningContent"].get("reasoningText") or {}).get("text")
                if text:
                    reasoning.append(text)
        finish = STOP_REASONS.get(body.get("stopReason", ""), body.get("stopReason", ""))
        if schema is not None:
            # the form arrives as the forced tool's input: hand it on as the reply text
            submitted = [c for c in calls if c.name == STRUCTURED_TOOL]
            content = submitted[0].arguments if submitted else _json_in("".join(texts))
            return Reply(content, (), "stop", usage, "\n".join(reasoning) or None)
        return Reply(
            content="".join(texts),
            tool_calls=tuple(calls),
            finish_reason=finish,
            usage=usage,
            reasoning="\n".join(reasoning) or None,
        )

    def parse[M: BaseModel](
        self,
        messages: Sequence[dict[str, Any]],
        schema: type[M],
        *,
        purpose: str,
        effort: str = "low",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[M, Reply]:
        """A reply that must fit ``schema``, validated before it is returned."""
        reply = self.chat(
            messages,
            purpose=purpose,
            effort=effort,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            return schema.model_validate_json(reply.content), reply
        except ValidationError as err:
            raise ModelError(
                f"reply did not match {schema.__name__}", body=reply.content[:2000]
            ) from err


def _json_in(text: str) -> str:
    """The JSON object in a text reply, for a model that answered in words instead of calling
    the form tool."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text
