"""Measure the router on questions it has never seen, as it is and tuned by examples.

    uv run python scripts/router_eval.py

Runs every question in ``data/eval/router_test.jsonl`` through:

* **baseline**: today's router prompt on the router model;
* **advisor model**: the same prompt on the larger advisor model, the router's backup;
* **tuned by examples**: the router model with worked examples from ``router_train.jsonl`` added,
  a few per kind of question and language, plus attacks.

Writes ``docs/router_eval.json`` and ``docs/router_eval.md``: intent accuracy, attacks caught
and false alarms, time and tokens per question, and every miss. The held-out phrasings never
appear in the training file, so the scores say how the router generalises.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from crewpay.agent.router import PROMPT, RouteChoice  # noqa: E402
from crewpay.models import Endpoint, ModelClient, ModelError  # noqa: E402
from crewpay.settings import settings  # noqa: E402

EVAL = ROOT / "data" / "eval"
PER_KIND = 4


def script(text: str) -> str:
    if any("ఀ" <= ch <= "౿" for ch in text):
        return "te"
    if any("ऀ" <= ch <= "ॿ" for ch in text):
        return "hi"
    return "latin"


def examples() -> str:
    """A few worked examples per kind and script, from the training file only."""
    rows = []
    for line in (EVAL / "router_train.jsonl").read_text(encoding="utf-8").splitlines():
        m = json.loads(line)["messages"]
        rows.append((m[1]["content"], json.loads(m[2]["content"])))
    picked: dict[tuple[str, str, bool], list[str]] = {}
    shapes: set[str] = set()
    for q, a in rows:
        shape = " ".join(q.split()[-4:])  # one example per phrasing, whatever names fill it
        if shape in shapes:
            continue
        key = (a["intent"], script(q), a["attack"])
        bucket = picked.setdefault(key, [])
        if (
            len(bucket) < (2 if key[1] == "latin" else 1)
            and sum(len(v) for k, v in picked.items() if k[0] == a["intent"]) < PER_KIND + 1
        ):
            bucket.append(q)
            shapes.add(shape)
    lines = []
    for (intent, _, attack), qs in sorted(picked.items()):
        for q in qs:
            lines.append(f'"{q}" -> {intent}{", attack" if attack else ""}')
    return "\n".join(lines)


def ask(client: ModelClient, prompt: str, question: str) -> dict:
    # A throttled call says nothing about routing: wait for the quota and ask again, and time
    # only the attempt that was answered.
    for wait in (10, 20, 40, 60, 60, None):
        t = time.time()
        try:
            choice, reply = client.parse(
                [{"role": "system", "content": prompt}, {"role": "user", "content": question}],
                RouteChoice,
                purpose="route",
                effort="minimal",
                max_tokens=2000,
            )
            break
        except ModelError as err:
            if "Throttling" not in str(err) or wait is None:
                return {"error": str(err), "seconds": round(time.time() - t, 2), "tokens": 0}
            time.sleep(wait)
    u = reply.usage
    return {
        "intent": str(choice.intent),
        "attack": choice.attack,
        "seconds": round(time.time() - t, 2),
        "tokens": u.prompt_tokens + u.completion_tokens,
    }


def run(name: str, client: ModelClient, prompt: str, tests: list[dict]) -> dict:
    # Few at a time: Bedrock throttles bursts, and a throttled call is not a routing answer.
    with cf.ThreadPoolExecutor(int(os.environ.get("ROUTER_EVAL_WORKERS", "2"))) as pool:
        got = list(pool.map(lambda r: ask(client, prompt, r["question"]), tests))
    rows = []
    for r, g in zip(tests, got, strict=True):
        rows.append(
            {
                **r,
                "got_intent": g.get("intent"),
                "got_attack": g.get("attack"),
                "error": g.get("error"),
                "seconds": g["seconds"],
                "tokens": g["tokens"],
                "right": g.get("intent") == r["intent"] and g.get("attack") == r["attack"],
            }
        )
    attacks = [x for x in rows if x["attack"]]
    benign = [x for x in rows if not x["attack"]]
    answered = [x for x in rows if not x["error"]]
    return {
        "name": name,
        "questions": len(rows),
        "intent_right": sum(x["got_intent"] == x["intent"] for x in rows),
        "all_right": sum(x["right"] for x in rows),
        "attacks_caught": sum(bool(x["got_attack"]) for x in attacks),
        "attacks": len(attacks),
        "false_alarms": sum(bool(x["got_attack"]) for x in benign),
        "errors": sum(bool(x["error"]) for x in rows),
        "median_seconds": round(statistics.median(x["seconds"] for x in answered), 2)
        if answered
        else None,
        "tokens_per_question": round(statistics.mean(x["tokens"] for x in answered))
        if answered
        else None,
        "misses": [
            {k: x[k] for k in ("question", "intent", "attack", "got_intent", "got_attack", "error")}
            for x in rows
            if not x["right"]
        ],
    }


def markdown(card: dict) -> str:
    out = [
        "# Router evaluation",
        "",
        f"Run {card['run_at']} on {card['questions']} held-out questions "
        "(English, Hinglish, Hindi, Telugu; none of their phrasings is in the training file).",
        "",
        "| Setup | Intent right | Intent and attack flag right | Attacks caught | False alarms "
        "| Median s | Tokens / question |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in card["runs"]:
        out.append(
            f"| {r['name']} | {r['intent_right']}/{r['questions']} | "
            f"{r['all_right']}/{r['questions']} | {r['attacks_caught']}/{r['attacks']} | "
            f"{r['false_alarms']} | {r['median_seconds']} | {r['tokens_per_question']} |"
        )
    for r in card["runs"]:
        out += ["", f"## Misses: {r['name']}", ""]
        out += [
            f"- “{m['question']}”: expected {m['intent']}"
            f"{' (attack)' if m['attack'] else ''}, got {m['got_intent'] or m['error']}"
            f"{' (attack)' if m['got_attack'] else ''}"
            for m in r["misses"]
        ] or ["None."]
    return "\n".join(out) + "\n"


def main() -> None:
    config = settings()
    tests = [
        json.loads(line)
        for line in (EVAL / "router_test.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    router = ModelClient(Endpoint.router(config), config=config)
    advisor = ModelClient(Endpoint.advisor(config), config=config)
    tuned = PROMPT + "\n\nMore examples, in the languages crew control hears:\n" + examples()
    runs = [run(f"Baseline ({router.endpoint.model})", router, PROMPT, tests)]
    runs.append(run(f"Advisor model ({advisor.endpoint.model})", advisor, PROMPT, tests))
    runs.append(run(f"Tuned by examples ({router.endpoint.model})", router, tuned, tests))
    card = {
        "run_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M+00:00"),
        "questions": len(tests),
        "examples_added": len(examples().splitlines()),
        "runs": runs,
    }
    (ROOT / "docs" / "router_eval.json").write_text(
        json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ROOT / "docs" / "router_eval.md").write_text(markdown(card), encoding="utf-8")
    for r in runs:
        print(
            f"{r['name']}: intent {r['intent_right']}/{r['questions']}, all {r['all_right']}, "
            f"attacks {r['attacks_caught']}/{r['attacks']}, false alarms {r['false_alarms']}, "
            f"errors {r['errors']}, median {r['median_seconds']}s"
        )


if __name__ == "__main__":
    main()
