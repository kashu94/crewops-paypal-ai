"""Runtime settings.

Read from the environment, with a `.env` file in the repository root filling any gaps. A value
exported in the shell always wins over the file, so a one-off override needs no edit.

CrewPay keeps its operation in memory (the demo data, reloaded on start) and reaches only two
outside services: Amazon Bedrock for the models and the PayPal sandbox for payments. Both use
credentials named for this project alone (``CREWPAY_*`` and ``PAYPAL_*``); the machine's
default AWS profile is never read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(os.environ.get("CREWPAY_ROOT") or Path(__file__).resolve().parents[2])
"""The repository root, or where the deployed bundle unpacks (``CREWPAY_ROOT``)."""
PAYPAL_SANDBOX = "https://api-m.sandbox.paypal.com"


def _read_dotenv(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    # utf-8-sig: Windows editors may start the file with a byte-order mark
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _flag(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class Settings:
    # -- Amazon Bedrock ---------------------------------------------------------------------
    bedrock_api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_profile: str
    """A named profile created for CrewPay; empty never falls back to ``default``."""
    bedrock_region: str
    advisor_model: str
    router_model: str
    writer_model: str
    vision_model: str
    # -- PayPal -----------------------------------------------------------------------------
    paypal_client_id: str
    paypal_client_secret: str
    paypal_base_url: str
    paypal_webhook_id: str
    paypal_currency: str
    pay_cap: float
    """The most one payment may be, in ``paypal_currency``, before a second approver must
    release it."""
    fx_inr_per_usd: float
    """The demo exchange rate: costs are in rupees, payouts in ``paypal_currency``."""
    sandbox_receiver: str
    """A sandbox personal account every payout goes to (empty: each crew member's address)."""
    # -- the app ----------------------------------------------------------------------------
    stage: str
    auth: str
    """Always ``demo``: callers pick a demo identity."""
    store: str
    """Always ``memory``."""
    usage_log: Path | None
    daily_asks: int
    """Model-backed requests allowed per day across all users, to protect the credits."""
    seed_dir: Path
    live_clock: bool
    """Run the operation on the wall clock, with the demo dates moved to start today. Off, it
    stays at the moment the demo data was taken (tests, the scorecard)."""
    ticker_seconds: int
    """How often the background loop moves callouts on, escalates and syncs payments; 0 off."""
    prompt_guard_url: str
    prompt_guard_token: str

    @property
    def has_bedrock(self) -> bool:
        return bool(
            self.bedrock_api_key
            or (self.aws_access_key_id and self.aws_secret_access_key)
            or self.aws_profile
        )

    @property
    def has_paypal(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    merged = {**_read_dotenv(ROOT / ".env"), **(env if env is not None else os.environ)}

    def get(key: str, default: str = "") -> str:
        return merged.get(key) or default

    profile = get("CREWPAY_AWS_PROFILE")
    if profile == "default":
        raise ValueError("CREWPAY_AWS_PROFILE must name a profile made for CrewPay, not default")
    paypal_base = get("PAYPAL_BASE_URL", PAYPAL_SANDBOX).rstrip("/")
    if "sandbox" not in paypal_base and not _flag(get("PAYPAL_ALLOW_LIVE", "false")):
        raise ValueError("PAYPAL_BASE_URL must be the PayPal sandbox")
    lite = "global.amazon.nova-2-lite-v1:0"
    return Settings(
        bedrock_api_key=get("CREWPAY_BEDROCK_API_KEY"),
        aws_access_key_id=get("CREWPAY_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=get("CREWPAY_AWS_SECRET_ACCESS_KEY"),
        aws_profile=profile,
        bedrock_region=get("CREWPAY_BEDROCK_REGION", "ap-southeast-2"),
        advisor_model=get("CREWPAY_ADVISOR_MODEL", lite),
        router_model=get("CREWPAY_ROUTER_MODEL", "amazon.nova-micro-v1:0"),
        writer_model=get("CREWPAY_WRITER_MODEL", lite),
        vision_model=get("CREWPAY_VISION_MODEL", lite),
        paypal_client_id=get("PAYPAL_CLIENT_ID"),
        paypal_client_secret=get("PAYPAL_CLIENT_SECRET"),
        paypal_base_url=paypal_base,
        paypal_webhook_id=get("PAYPAL_WEBHOOK_ID"),
        paypal_currency=get("CREWPAY_CURRENCY", "USD"),
        pay_cap=float(get("CREWPAY_PAY_CAP", "500")),
        fx_inr_per_usd=float(get("CREWPAY_FX_INR_PER_USD", "84")),
        sandbox_receiver=get("PAYPAL_SANDBOX_RECEIVER"),
        stage=get("CREWPAY_STAGE", "dev"),
        auth="demo",
        store="memory",
        usage_log=Path(get("CREWPAY_USAGE_LOG")) if get("CREWPAY_USAGE_LOG") else None,
        daily_asks=int(get("CREWPAY_DAILY_ASKS", "300")),
        seed_dir=Path(get("CREWPAY_SEED_DIR", str(ROOT / "data" / "seed"))),
        live_clock=_flag(get("CREWPAY_LIVE_CLOCK", "false")),
        ticker_seconds=int(get("CREWPAY_TICKER_SECONDS", "60")),
        prompt_guard_url=get("PROMPT_GUARD_URL"),
        prompt_guard_token=get("PROMPT_GUARD_TOKEN"),
    )


@lru_cache(maxsize=1)
def settings() -> Settings:
    return load_settings()
