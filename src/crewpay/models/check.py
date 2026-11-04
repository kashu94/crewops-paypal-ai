"""Check the Bedrock credentials in ``.env`` work: one tiny call to each configured model.

uv run python -m crewpay.models.check
"""

from __future__ import annotations

from crewpay.models.client import Endpoint, ModelClient, ModelError
from crewpay.settings import settings


def main() -> int:
    config = settings()
    failed = 0
    endpoints = (Endpoint.advisor(config), Endpoint.router(config), Endpoint.writer(config))
    for endpoint in endpoints:
        client = ModelClient(endpoint, config=config)
        try:
            reply = client.chat(
                [{"role": "user", "content": "Reply with the single word: ready"}],
                purpose="check",
                effort="minimal",
                max_tokens=20,
            )
        except ModelError as err:
            failed += 1
            print(f"{endpoint.name:8} {endpoint.model}: FAILED, {err}")
            continue
        u = reply.usage
        print(
            f"{endpoint.name:8} {u.model}: {reply.content.strip()!r} "
            f"({u.prompt_tokens} in, {u.completion_tokens} out, {u.seconds:.1f}s)"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
