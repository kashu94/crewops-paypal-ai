"""The HTTP API, and the web console and TV board it serves.

    uv run python -m crewpay.api            # http://localhost:8000

Everything a screen does goes through the same typed tools and role checks the agent uses
(``POST /api/tools/{name}``), so a button can never do what the role could not ask for. The
rest are the things a tool cannot be: asking the agent, speech, photos, the board.

Model-backed requests (asking, speech, photos, the brief) count against a daily allowance,
so a public demo cannot run through the credits.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import threading
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlencode, urlparse

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from crewpay.agent.advisor import Advisor, Answer
from crewpay.agent.build import build_advisor
from crewpay.agent.memory import Conversations
from crewpay.agent.tools import ADMINS, Caller, Context, Role, ToolBox
from crewpay.alexa.linking import AMAZON_REDIRECT_HOSTS, Linked, LinkError, Links
from crewpay.alexa.notifier import AlexaNotifier, AlexaOutbox
from crewpay.alexa.notifier import sender as alexa_sender
from crewpay.alexa.skill import AlexaLinks, Skill
from crewpay.alexa.verify import NotFromAlexa, verified
from crewpay.api.auth import Throttle, caller_from, issue, password_ok
from crewpay.engine.clock import IST, use_clock
from crewpay.engine.clock import now as clock_now
from crewpay.engine.severity import for_issue
from crewpay.engine.watch import scan
from crewpay.models import Endpoint, ModelClient, ModelError
from crewpay.models.media import MediaClient
from crewpay.ops import DESK, SUPERVISOR, CalloutDesk, StoreOutbox
from crewpay.ops.accounts import DEMO_ACCOUNTS, StoreAccounts
from crewpay.ops.alerts import AlertAddresses
from crewpay.ops.brief import Images, MemoryImages, write_brief
from crewpay.ops.desks import DESKS, desk_of
from crewpay.ops.governance import entries, log
from crewpay.ops.messages import (
    handover_facts,
    passenger_facts,
    read_absence,
    summarise_photos,
    write_handover,
    write_passenger_message,
)
from crewpay.ops.push import Push, PushingOutbox
from crewpay.ops.reports import ReportDesk, ReportStatus
from crewpay.ops.roster_upload import MAX_BYTES as ROSTER_LIMIT
from crewpay.pay import PayConfig, PayPalClient, PayPalError, Payroll
from crewpay.pay.payroll import masked
from crewpay.seed import load_dataset, read_meta, shifted
from crewpay.settings import ROOT, Settings, settings
from crewpay.store import MemoryStore, Store, open_store
from crewpay.vision import Kind, ModelReader, read_photo
from crewpay.vision.intake import Status as PhotoStatus

WEB = ROOT / "web" / "out"
SCORECARD = ROOT / "docs" / "scorecard.json"
ROUTER_EVAL = ROOT / "docs" / "router_eval.json"
BRIEF_EVERY_SECONDS = 120
RESEED_AFTER = timedelta(days=7)
AUDIO_LIMIT = 10 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024
LANGUAGES = {"English", "Hindi", "Telugu", "Tamil", "Kannada", "Marathi", "Bengali"}


class ConsoleFiles(StaticFiles):
    """The console, a static export: each page is a folder with an index.html.

    A page is served with or without its trailing slash. Behind some proxies the
    slash can be gone by the time the request arrives, and the usual redirect to add it back
    would then loop.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        page = (Path(str(self.directory)) / path / "index.html").resolve()
        if page.is_file() and page.is_relative_to(Path(str(self.directory)).resolve()):
            return FileResponse(page)
        return await super().get_response(path, scope)


class Services:
    """Everything a request needs, built once per server."""

    def __init__(self, config: Settings, store: Store | MemoryStore | None = None) -> None:
        self.config = config
        self.store = store or open_store(config)
        self.store.live = config.live_clock
        self._seed()
        meta = self.store.meta()
        self.days = {"first": meta.get("first_day"), "last": meta.get("last_day")}
        self.push = Push(self.store)
        # Alexa: linked Echoes chime when a notice reaches the person they belong to.
        self.alexa_links = AlexaLinks(self.store)
        self.alexa = AlexaNotifier(
            self.alexa_links,
            alexa_sender(
                config.alexa_messaging_client_id,
                config.alexa_messaging_client_secret,
                config.alexa_events_stage,
            )
            if config.alexa_messaging_client_id and config.alexa_messaging_client_secret
            else None,
        )
        self.outbox = AlexaOutbox(PushingOutbox(StoreOutbox(self.store), self.push), self.alexa)
        # On the wall clock (as hosted) offers and plans run on real time. On the demo's own
        # clock, they run from the moment the demo data was taken, moving on with real time
        # since the server started, so an offer's "answer by" fits the trips it is for.
        started = datetime.now(UTC)
        as_of = datetime.fromisoformat(self.store.meta()["as_of"])
        clock = (
            (lambda: datetime.now(UTC))
            if config.live_clock
            else (lambda: as_of + (datetime.now(UTC) - started))
        )
        # Everything reads this one clock: the header, offers, plans, PayPal payments, reports
        # and the logs, so every date and time in the console agrees.
        self.clock = clock
        use_clock(clock)
        self.store.clock = clock
        self.desk = CalloutDesk(
            self.store, self.outbox, clock=clock, docs=self.store, four_eyes=True
        )
        self.reports = ReportDesk(self.store, self.outbox, config.seed_dir, clock=clock)
        self.alerts = AlertAddresses(self.store)
        self.payroll = Payroll(
            self.store,
            self.outbox,
            PayPalClient.from_settings(config),
            PayConfig(
                currency=config.paypal_currency,
                fx_inr_per_usd=config.fx_inr_per_usd,
                cap=config.pay_cap,
                receiver=config.sandbox_receiver,
                public_url=config.public_url,
                airlines=_airline_names(config.seed_dir),
            ),
            clock=clock,
        )
        if self.desk.plans is not None:
            self.desk.plans.listeners.append(self.payroll.on_plan)
            self.payroll.funding.attach(self.desk.plans)
        self.accounts = StoreAccounts(self.store)
        self.conversations = Conversations(clock=clock, docs=self.store)
        self.toolbox = ToolBox()
        self.media = MediaClient(config)
        self.reader = ModelReader(ModelClient(Endpoint.vision(config), config=config))
        self.writer = ModelClient(Endpoint.writer(config), config=config)
        self.reasoner = ModelClient(Endpoint.advisor(config), config=config)
        """For short structured readings, such as an absence typed in free text."""
        self.images: Images = MemoryImages()
        self._advisor: Advisor | None = None
        self._lock = threading.Lock()

    def _seed(self, force: bool = False) -> None:
        """Load the demo operation when the store has none, an older shape of it, or (on the
        wall clock) one that started over a week ago, so there is always a week to work in.
        ``force`` starts it again from nothing: every report, plan, callout and message goes."""
        meta = read_meta(self.config.seed_dir)
        stale = force or self.store.is_empty()
        if not stale:
            held = self.store.meta()
            stale = held.get("data_version") != meta.get("data_version")
            if self.config.live_clock and not stale:
                stale = (
                    "shifted_days" not in held
                    or datetime.now(UTC) - datetime.fromisoformat(held["as_of"]) > RESEED_AFTER
                )
        if not stale:
            return
        data = load_dataset(self.config.seed_dir)
        if self.config.live_clock:
            # The data was taken late on the evening before its first day: move it so that
            # evening was yesterday, and the first day is today.
            yesterday = datetime.now(IST).date() - timedelta(days=1)
            data, meta = shifted(data, meta, (yesterday - data.as_of.astimezone(IST).date()).days)
        self.store.load(data, meta)

    @property
    def advisor(self) -> Advisor:
        with self._lock:
            if self._advisor is None:
                self._advisor = build_advisor(self.config, self.store, self.outbox)
                self._advisor.desk = self.desk
                self._advisor.reports = self.reports
                self._advisor.payroll = self.payroll
            return self._advisor

    def context(self, caller: Caller) -> Context:
        return Context(
            self.store,
            caller,
            desk=self.desk,
            reports=self.reports,
            accounts=self.accounts,
            alerts=self.alerts,
            payroll=self.payroll,
        )

    def set_outbox(self, outbox: Any) -> None:
        """Send every notice through ``outbox`` from now on (emails, when deployed)."""
        self.outbox = outbox
        self.desk.outbox = outbox
        if self.desk.plans is not None:
            self.desk.plans.outbox = outbox
        self.reports.outbox = outbox
        self.payroll.outbox = outbox

    def spend(self, what: str) -> None:
        """Count one model-backed request against today's allowance."""
        today = datetime.now(UTC).date().isoformat()
        used = self.store.get_doc("usage", today) or {"count": 0}
        if used["count"] >= self.config.daily_asks:
            raise HTTPException(429, "today's demo allowance is used up; please try tomorrow")
        used["count"] += 1
        used[what] = used.get(what, 0) + 1
        self.store.put_doc("usage", today, used)


def _airline_names(seed_dir: Path) -> dict[str, str]:
    try:
        rows = json.loads((seed_dir / "airlines.json").read_text())
    except (OSError, ValueError):
        return {}
    return {a["code"]: a["name"] for a in rows if "code" in a and "name" in a}


def _payout_account(s: Services, crew_id: str) -> dict[str, Any]:
    email, why = s.payroll.receiver_for(crew_id)
    own = s.payroll.payee(crew_id)
    return {"email": email, "is": why, "set_at": own["set_at"] if own else None}


def answer_json(answer: Answer, conversation_id: str) -> dict[str, Any]:
    return {
        "conversation_id": conversation_id,
        "text": answer.text,
        "verified": answer.verified,
        "problems": answer.problems,
        "draft": answer.draft,
        "draft_problems": answer.draft_problems,
        "blocked": answer.blocked,
        "route": (
            {
                "intent": str(answer.route.intent),
                "by": answer.route.by,
                "effort": answer.route.effort,
                "tools": list(answer.route.tools),
            }
            if answer.route
            else None
        ),
        "steps": [
            {"tool": s.tool, "arguments": s.arguments, "result": s.result} for s in answer.steps
        ],
        "tokens": answer.tokens,
        "calls": [
            {
                "model": u.model,
                "purpose": u.purpose,
                "effort": u.effort,
                "prompt": u.prompt_tokens,
                "completion": u.completion_tokens,
                "reasoning": u.reasoning_tokens,
                "cached": u.cached_tokens,
                "seconds": u.seconds,
            }
            for u in answer.usage
        ],
    }


OPEN = ("offered", "accepted", "needs_decision", "awaiting_approval")

TONES = (
    ("Escalated", "critical"),
    ("Decision needed", "critical"),
    ("Watch", "critical"),
    ("Approval needed", "pending"),
    ("Sent back", "pending"),
    ("Stale", "pending"),
    ("Offer withdrawn", "pending"),
    ("Back on duty", "ok"),
    ("Declined", "pending"),
    ("Approved", "ok"),
    ("Filled", "ok"),
)
"""How a desk notice reads on the speaker and the screens, from the start of its subject."""


def tone_of(subject: str) -> str:
    return next((tone for prefix, tone in TONES if subject.startswith(prefix)), "info")


def ops_room(s: Services, op: Any, issues: list[Any], callouts: list[Any]) -> dict[str, Any]:
    """What the ops-room wall shows beside the problems: the next day at a glance, each
    controller's load, the departures coming up, standby cover, and the latest messages."""
    horizon = op.now + timedelta(hours=24)
    legs = sorted(
        (leg for leg in op.legs.values() if op.now <= leg.off_block <= horizon),
        key=lambda leg: leg.off_block,
    )
    trip_of = {leg_id: t.trip_id for t in op.trips.values() for leg_id in t.leg_ids}
    troubled = {i.trip_id for i in issues if i.trip_id and i.kind == "illegal"}
    troubled |= {c.trip_id for c in callouts if str(c.status) in OPEN}
    today, tomorrow = op.now.date(), (op.now + timedelta(days=1)).date()
    out_now = {
        u.crew_id
        for u in s.store.unavailability()
        if u.first_day <= tomorrow and u.last_day >= today
    }

    def desk(name: str) -> dict[str, Any]:
        return desks.setdefault(name, {"controller": name, "open": 0, "filled": 0, "to_approve": 0})

    desks: dict[str, dict[str, Any]] = {}
    for c in callouts:
        status = str(c.status)
        desk(c.opened_by)["open"] += status in OPEN
        desk(c.opened_by)["filled"] += status == "filled"
        if status == "awaiting_approval":
            desk(c.opened_by)["to_approve"] += 1
    waiting = s.desk.plans.waiting() if s.desk.plans is not None else []
    for p in waiting:
        desk(p.submitted_by)["to_approve"] += 1

    standby: dict[tuple[str, str], int] = {}
    for crew in op.crew.values():
        for rank in ("Captain", "First Officer", "Purser", "Cabin Crew"):
            standby.setdefault((crew.airline, rank), 0)
        on_call = any(
            d in (today, tomorrow) for sb in op.standby.get(crew.crew_id, ()) for d in sb.dates
        )
        if on_call and crew.crew_id not in out_now:
            standby[(crew.airline, str(crew.rank))] += 1

    return {
        "next_24h": {
            "flights": len(legs),
            "passengers": sum(leg.booked for leg in legs),
            "crew_out": len(out_now),
            "open_callouts": sum(1 for c in callouts if str(c.status) in OPEN),
            "illegal": sum(1 for i in issues if i.kind == "illegal"),
            "fatigue": sum(1 for i in issues if i.kind == "fatigue"),
            "certificates": sum(1 for i in issues if i.kind == "certificate"),
            "plans_waiting": len(waiting),
            "escalated": len(s.store.docs("escalated")),
        },
        "desks": sorted(desks.values(), key=lambda d: (-d["open"], d["controller"])),
        "departures": [
            {
                "flight": leg.flight_no,
                "route": f"{leg.origin}-{leg.destination}",
                "departs": leg.off_block.astimezone(IST).strftime("%d %b %H:%M"),
                "tail": leg.tail,
                "trip_id": trip_of.get(leg.leg_id),
                "booked": leg.booked,
                "state": "affected" if trip_of.get(leg.leg_id) in troubled else "on plan",
            }
            for leg in legs[:10]
        ],
        "standby": [
            {"airline": airline, "rank": rank, "on_call": n}
            for (airline, rank), n in sorted(standby.items())
        ],
        "latest": [
            {
                "subject": n.subject,
                "at": n.at.astimezone(IST).strftime("%d %b %H:%M"),
                "tone": tone_of(n.subject),
            }
            for n in desk_notices(s)[:6]
        ],
    }


PAYPAL = "https://*.paypal.com https://*.paypalobjects.com"
CSP = "; ".join(
    [
        "default-src 'self'",
        # The static console's pages carry inline boot scripts; PayPal's JS SDK and its frames.
        f"script-src 'self' 'unsafe-inline' {PAYPAL}",
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob: {PAYPAL}",
        "font-src 'self' data:",
        f"connect-src 'self' {PAYPAL}",
        f"frame-src {PAYPAL}",
        f"form-action 'self' {PAYPAL}",
        "worker-src 'self'",
        "media-src 'self' blob: data:",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'self'",
    ]
)
"""PayPal's documented sources for the JS SDK and Checkout; everything else is CrewPay's own."""


LINK_STYLE = (
    "body{margin:0;font:16px/1.5 system-ui,sans-serif;background:#0b1630;color:#eef3fa;"
    "display:grid;place-items:center;min-height:100vh}"
    "main{width:min(420px,calc(100% - 32px));background:#13213f;border-radius:16px;padding:28px}"
    "h1{font-size:22px;margin:0 0 6px}p{color:#b8c6dc;margin:0 0 18px}"
    "label{display:block;font-size:14px;color:#b8c6dc;margin:0 0 14px}"
    "select,input{display:block;width:100%;box-sizing:border-box;margin-top:6px;padding:10px;"
    "border-radius:10px;border:1px solid #2d4064;background:#0b1630;color:#eef3fa;font:inherit}"
    "button{width:100%;padding:12px;border:0;border-radius:10px;background:#2563b8;color:#fff;"
    "font:600 16px system-ui,sans-serif;cursor:pointer}.bad{color:#ff8a8a}"
)
"""The Alexa account-linking page's look: plain CSS, no script."""


async def _read_at_most(upload: UploadFile, limit: int) -> bytes:
    """An upload's bytes, reading no more than one past ``limit``: the handler's own size check
    then refuses it, without a large file ever being held in memory."""
    return await upload.read(limit + 1)


EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b")
AMOUNT = re.compile(r"\b\d[\d,]*(\.\d+)?\s?(USD|INR)\b|(₹|\$)\s?\d[\d,]*(\.\d+)?")


def _aloud(text: str) -> str:
    """A notice as the open ops-room speaker may say it: no one's PayPal address or pay."""
    return AMOUNT.sub("an amount", EMAIL.sub("a new PayPal address", text))


def _on_the_wall(offer: dict[str, Any]) -> dict[str, Any]:
    """One crew member asked in a callout, as the open TV board shows it: who and what they
    answered, not what they would be paid or what they wrote back."""
    return {k: v for k, v in offer.items() if k not in ("cost_inr", "remark")}


def desk_notices(s: Services) -> list[Any]:
    """The desk's and the supervisor's notices, newest first."""
    found = s.outbox.sent(DESK) + s.outbox.sent(SUPERVISOR)
    return sorted(found, key=lambda n: n.at, reverse=True)


def desk_name(desk_id: str) -> str:
    found = next((d for d in DESKS if d.desk_id == desk_id), None)
    return found.name if found else desk_id


def waiting_lines(s: Services) -> list[str]:
    """What waits on the desk, one sentence per thing to do, most urgent first: what the
    speaker says when asked "what's waiting?"."""
    lines: list[str] = []
    escalated = {d["ref"]: d["at"] for d in s.store.docs("escalated")}
    callouts = s.desk.all()
    for c in callouts:
        if str(c.status) == "needs_decision":
            line = (
                f"{c.rank} seat on {c.trip_id}: nobody is left to call out ({c.reason}). "
                "A controller proposes cancelling or flying one short, and an approver decides."
            )
            if c.callout_id in escalated:
                at = datetime.fromisoformat(escalated[c.callout_id]).astimezone(IST)
                line += f" Escalated to the duty supervisor at {at:%H:%M}."
            lines.append(line)
    for c in callouts:
        if str(c.status) == "awaiting_approval" and c.proposal is not None:
            lines.append(
                f"An approver must decide: {c.proposal.summary}, "
                f"proposed by {c.proposal.proposed_by}."
            )
    plans = s.desk.plans.waiting() if s.desk.plans is not None else []
    if plans:
        named = "; ".join(f"{p.name} for {p.rank} on {p.trip_id}" for p in plans[:3])
        more = f", and {len(plans) - 3} more" if len(plans) > 3 else ""
        lines.append(
            f"{len(plans)} roster {'change waits' if len(plans) == 1 else 'changes wait'} "
            f"for an approver: {named}{more}."
        )
    reports = s.reports.all()
    signoff = [r for r in reports if r.status is ReportStatus.RESOLVED]
    if signoff:
        lines.append(
            f"{len(signoff)} resolved {'report waits' if len(signoff) == 1 else 'reports wait'} "
            f"for an approver's sign-off: {', '.join(r.title for r in signoff[:3])}."
        )
    working = [r for r in reports if r.status in (ReportStatus.OPEN, ReportStatus.IN_PROGRESS)]
    if working:
        named = "; ".join(f"{r.title} ({r.severity}, {desk_name(r.desk_id)})" for r in working[:3])
        more = f", and {len(working) - 3} more" if len(working) > 3 else ""
        lines.append(
            f"{len(working)} {'disruption is' if len(working) == 1 else 'disruptions are'} "
            f"open: {named}{more}."
        )
    illegal = [i for i in scan(s.store.operation()) if i.kind == "illegal"]
    if illegal:
        named = "; ".join(f"{i.name} on {i.trip_id}, {i.detail.split(';')[0]}" for i in illegal[:3])
        lines.append(
            f"{len(illegal)} rostered crew {'member is' if len(illegal) == 1 else 'members are'} "
            f"not legal: {named}."
        )
    for c in callouts:
        if c.current is not None:
            lines.append(
                f"Callout running for {c.rank} on {c.trip_id}: waiting for {c.current.name} "
                f"to answer by {c.current.expires_at.astimezone(IST):%H:%M}."
            )
    return lines


def refresh_brief(s: Services, force: bool = False) -> bool:
    """Write a new shift brief when the roster or the callouts have moved, at most every two
    minutes. Returns whether one was written."""
    callouts = s.desk.all()
    signature = f"{s.store.roster_version()}:" + ",".join(
        f"{c.callout_id}={c.status}" for c in callouts
    )
    cached = s.store.get_doc("brief", "current")
    now = datetime.now(UTC)
    if cached and not force:
        age = (now - datetime.fromisoformat(cached["at"])).total_seconds()
        if cached["signature"] == signature or age < BRIEF_EVERY_SECONDS:
            return False
    s.spend("brief")
    brief = write_brief(s.store.operation(), callouts, s.writer)
    s.store.put_doc(
        "brief", "current", {"signature": signature, "at": now.isoformat(), "brief": asdict(brief)}
    )
    return True


def create_app(config: Settings | None = None, services: Services | None = None) -> FastAPI:
    config = config or settings()
    app = FastAPI(title="CrewPay", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def secure_headers(request: Request, call_next: Any) -> Response:
        """Browser protections on every response: only PayPal may add scripts, frames and
        calls besides CrewPay itself; nobody may frame the console; no type sniffing."""
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        if not request.url.path.startswith("/api/docs"):  # Swagger UI loads from a CDN
            h.setdefault("Content-Security-Policy", CSP)
        if request.headers.get("x-forwarded-proto", request.url.scheme) == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    state: dict[str, Services] = {}
    if services is not None:
        state["services"] = services

    def svc() -> Services:
        if "services" not in state:
            state["services"] = Services(config)
        return state["services"]

    def caller(request: Request) -> Caller:
        return caller_from(request, config)

    @app.exception_handler(ModelError)
    async def model_down(_request: Request, err: ModelError) -> JSONResponse:
        return JSONResponse({"detail": f"a model is unavailable: {err}"}, status_code=503)

    # -- public ---------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def public_config() -> dict[str, Any]:
        return {
            "auth": config.auth,
            "stage": config.stage,
            "models": config.has_bedrock,
            "paypal": config.has_paypal,
            "password": bool(config.demo_password),
            # The client id is public: the PayPal button in the browser needs it.
            "paypal_client_id": config.paypal_client_id or None,
            "currency": config.paypal_currency,
        }

    # -- reading --------------------------------------------------------------------------

    @app.get("/api/me")
    def me(who: Caller = Depends(caller)) -> dict[str, Any]:
        s = svc()
        op = s.store.operation()
        demo = next((a for a in DEMO_ACCOUNTS if a.user == who.user), None)
        owns = desk_of(who.user) if who.role is Role.CONTROLLER else None
        name = op.state(who.crew_id).name if who.crew_id else (demo.label if demo else who.user)
        return {
            "user": who.user,
            "name": name.split(",")[0],
            "role": str(who.role),
            "crew_id": who.crew_id,
            "desk": {"desk_id": owns.desk_id, "name": owns.name} if owns else None,
            "clock": op.now.astimezone(IST).strftime("%a %d %b %Y, %H:%M IST"),
            "days": s.days,
            "tools": [t.name for t in s.toolbox.available(who)],
            "push": s.push.subscribed(who.user),
        }

    throttle = Throttle()

    @app.post("/api/login")
    async def login(request: Request) -> dict[str, Any]:
        """Sign in as one of the demo accounts with the shared demo password."""
        try:
            body = await request.json()
        except ValueError as err:
            raise HTTPException(400, "not JSON") from err
        user = str(body.get("user", "")).strip()
        account = next((a for a in DEMO_ACCOUNTS if a.user == user), None)
        if account is None:
            raise HTTPException(404, f"no account {user!r}")
        if not config.demo_password:
            return {"token": None, "user": user}
        # Per address and account: behind a proxy many people can share one address, and one
        # of them guessing must not lock everyone else out.
        where = f"{request.client.host if request.client else 'unknown'}:{user}"
        throttle.check(where)
        if not password_ok(config, str(body.get("password", ""))):
            throttle.miss(where)
            log(svc().store, "security", by=user, role=str(account.role), action="wrong password")
            raise HTTPException(401, "That is not the demo password.")
        log(svc().store, "activity", by=user, role=str(account.role), action="signed in")
        return {"token": issue(config, account.user, account.role, account.crew_id), "user": user}

    @app.get("/api/accounts/demo")
    def demo_accounts() -> dict[str, Any]:
        """The demo accounts, for the sign-in page. Never their passwords."""
        return {
            "accounts": [
                {"user": a.user, "role": str(a.role), "crew_id": a.crew_id, "label": a.label}
                for a in DEMO_ACCOUNTS
            ]
        }

    @app.get("/api/version")
    def version(_who: Caller = Depends(caller)) -> dict[str, Any]:
        """Cheap to call every couple of seconds: screens refresh when a number moves."""
        s = svc()
        callouts = s.store.docs("callout")
        reports = s.store.docs("report")
        return {
            "roster": s.store.roster_version(),
            "callouts": hash(json.dumps(callouts, sort_keys=True)) % 10**9,
            "plans": hash(json.dumps(s.store.docs("plan"), sort_keys=True)) % 10**9,
            "reports": hash(json.dumps(reports, sort_keys=True)) % 10**9,
            "notices": len(s.store.docs("notice")),
        }

    @app.post("/api/tools/{name}")
    def tool(
        name: str, arguments: dict[str, Any] = Body(default={}), who: Caller = Depends(caller)
    ) -> dict[str, Any]:
        s = svc()
        return s.toolbox.call(name, arguments, s.context(who))

    @app.get("/api/notices")
    def notices(limit: int = 30, who: Caller = Depends(caller)) -> dict[str, Any]:
        s = svc()
        mine = {DESK, SUPERVISOR} if who.role is not Role.CREW else {who.crew_id}
        if who.role is Role.REPORTER:
            mine = {DESK}
        found = [n for n in s.outbox.sent() if n.to in mine]
        return {
            "notices": [
                {**asdict(n), "at": n.at.astimezone(IST).strftime("%d %b %H:%M IST")}
                for n in found[-limit:][::-1]
            ]
        }

    # -- the agent ------------------------------------------------------------------------

    @app.post("/api/ask")
    def ask(
        question: str = Body(..., embed=True, min_length=2, max_length=2000),
        conversation_id: str | None = Body(None, embed=True),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        s = svc()
        s.spend("ask")
        conversation, answer = s.conversations.ask(s.advisor, question, who, conversation_id)
        return answer_json(answer, conversation.id)

    @app.post("/api/voice")
    async def voice(
        audio: UploadFile = File(...),
        languages: str = Form("English"),
        _who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        data = await _read_at_most(audio, AUDIO_LIMIT)
        if len(data) > AUDIO_LIMIT:
            raise HTTPException(413, "keep recordings under a minute")
        hints = [x.strip() for x in languages.split(",") if x.strip() in LANGUAGES]
        s = svc()
        s.spend("voice")
        transcript = s.media.transcribe(data, hints or None)
        return {"text": transcript.text, "seconds": round(transcript.duration_ms / 1000, 1)}

    @app.post("/api/intake")
    async def intake(
        image: UploadFile = File(...),
        kind: Kind = Form(...),
        tail: str | None = Form(None),
        keep: bool = Form(False),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        """Check a photo. With ``keep``, a photo that passes is kept as evidence for a report
        and its ``evidence_id`` returned, to pass to report_disruption."""
        data = await _read_at_most(image, IMAGE_LIMIT)
        if len(data) > IMAGE_LIMIT:
            raise HTTPException(413, "send a photo under 8 MB")
        mime = image.content_type or "image/jpeg"
        if mime not in ("image/jpeg", "image/png", "image/webp"):
            raise HTTPException(415, "send a JPEG, PNG or WebP photo")
        s = svc()
        s.spend("intake")
        result = read_photo(
            s.store.operation(), kind, data, mime, s.reader, s.media, (tail or None)
        )
        out = asdict(result)
        out["kind"], out["status"] = str(result.kind), str(result.status)
        if keep and result.status in (PhotoStatus.DRAFT, PhotoStatus.CONFIRM):
            evidence_id = uuid.uuid4().hex[:12]
            s.images.put(f"evidence/{evidence_id}", data)
            s.store.put_doc(
                "evidence",
                evidence_id,
                {
                    "evidence_id": evidence_id,
                    "by": who.user,
                    "kind": str(result.kind),
                    "status": str(result.status),
                    "message": result.message,
                    "confidence": result.confidence,
                    "description": (result.suggestion or {}).get("description", ""),
                    "severity": (result.suggestion or {}).get("severity"),
                    "seen": result.signals.get("tail_seen") or result.signals.get("gate_seen"),
                    "crew_ids": [c["crew_id"] for c in result.crew],
                    "days": [
                        (result.proposal or {}).get("arguments", {}).get(k)
                        for k in ("first_day", "last_day")
                    ],
                    "mime": mime,
                    "at": clock_now().isoformat(),
                },
            )
            out["evidence_id"] = evidence_id
        return out

    @app.post("/api/intake/summary")
    def intake_summary(
        evidence_ids: list[str] = Body(..., embed=True, max_length=5),
        subject: str = Body("", embed=True, max_length=80),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        """One description, and the highest severity, for a report from its checked photos."""
        s = svc()
        docs = []
        for evidence_id in evidence_ids:
            doc = s.store.get_doc("evidence", evidence_id) if evidence_id.isalnum() else None
            if doc is None or doc["by"] != who.user:
                raise HTTPException(404, f"no photo {evidence_id} of yours")
            docs.append(doc)
        if len(docs) > 1:
            s.spend("photo summary")
        rank = {"low": 1, "medium": 2, "high": 3}
        severity = max((d.get("severity") or "low" for d in docs), key=rank.get, default=None)
        return {
            "description": summarise_photos(
                subject, [d.get("description") or d["message"] for d in docs], s.writer
            ),
            "severity": severity,
        }

    @app.get("/api/evidence/{evidence_id}")
    def evidence(evidence_id: str, who: Caller = Depends(caller)) -> Response:
        """A photo kept with a report: for the desk roles, and for whoever took it."""
        s = svc()
        doc = s.store.get_doc("evidence", evidence_id) if evidence_id.isalnum() else None
        if doc is None:
            raise HTTPException(404)
        if who.role in (Role.CREW, Role.REPORTER, Role.SPEAKER) and doc["by"] != who.user:
            raise HTTPException(403, "only the desk and whoever took it can see this photo")
        data = s.images.get(f"evidence/{evidence_id}")
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type=doc.get("mime", "image/jpeg"))

    # -- words for people outside the desk ----------------------------------------------

    def _tool(s: Services, who: Caller, name: str, args: dict[str, Any]) -> dict[str, Any]:
        out = s.toolbox.call(name, args, s.context(who))
        if "error" in out:
            raise HTTPException(403 if "cannot use" in out["error"] else 400, out["error"])
        return out

    @app.post("/api/reports/{report_id}/passenger-message")
    def passenger_message(
        report_id: str,
        language: str = Body("English", embed=True),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        """The SMS and app notice customer care sends about a report, drafted by the writer model
        and checked against the engine's facts. Nothing is sent; a person copies it."""
        if who.role in (Role.CREW, Role.SPEAKER):
            raise HTTPException(403, f"the {who.role} role cannot draft passenger messages")
        s = svc()
        report = _tool(s, who, "show_disruption", {"report_id": report_id})
        try:
            f = passenger_facts(s.store.operation(), report)
            if not f["flights"]:
                raise ValueError("this report changes no passenger's flight")
        except (ValueError, KeyError) as err:
            raise HTTPException(400, str(err)) from err
        s.spend("message")
        return {**write_passenger_message(f, s.writer, language), "facts": f}

    @app.get("/api/handover")
    def handover(who: Caller = Depends(caller)) -> dict[str, Any]:
        """The shift handover: what each desk closed and still holds, and what the next shift
        must do first, written by the writer model from the reports, plans and callouts."""
        if who.role not in (
            Role.CONTROLLER,
            Role.APPROVER,
            Role.AUDITOR,
            Role.ADMIN,
            Role.SUPER_ADMIN,
        ):
            raise HTTPException(403, f"the {who.role} role cannot read the handover")
        s = svc()
        reports = _tool(s, who, "disruptions", {"status": "all", "limit": 100})["disruptions"]
        plans = _tool(s, who, "plan_status", {}).get("plans", [])
        callouts = _tool(s, who, "callout_status", {}).get("callouts", [])
        f = handover_facts(reports, plans, callouts)
        s.spend("handover")
        return {**write_handover(f, s.writer), "facts": f}

    @app.post("/api/absence/understand")
    def understand_absence(
        text: str = Body(..., embed=True, min_length=3, max_length=600),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        """Turn "kal se do din bukhar hai" into the fields of a report the person confirms."""
        s = svc()
        s.spend("voice")
        today = s.store.operation().now.astimezone(IST).date()
        try:
            return read_absence(text, today, s.reasoner)
        except ModelError as err:
            raise HTTPException(503, f"could not read that just now: {err}") from err

    @app.get("/api/log/{kind}")
    def activity_log(kind: str, limit: int = 50, who: Caller = Depends(caller)) -> dict:
        """Who changed what (desk roles), and which questions were refused (admins only)."""
        if kind not in ("activity", "security"):
            raise HTTPException(404, "no such log")
        readers = (
            {Role.ADMIN, Role.SUPER_ADMIN, Role.AUDITOR}
            if kind == "security"
            else {Role.CONTROLLER, Role.APPROVER, Role.AUDITOR, Role.ADMIN, Role.SUPER_ADMIN}
        )
        if who.role not in readers:
            raise HTTPException(403, f"the {who.role} role cannot read the {kind} log")
        return {"entries": entries(svc().store, kind, min(limit, 200))}

    # -- desktop alerts and roster files --------------------------------------------------

    @app.get("/api/push/key")
    def push_key(_who: Caller = Depends(caller)) -> dict[str, str]:
        return {"public_key": svc().push.public_key()}

    @app.post("/api/admin/reset")
    def reset_demo(who: Caller = Depends(caller)) -> dict[str, Any]:
        """A clean slate: the demo operation as it starts, today. Accounts are not touched."""
        if who.role is not Role.SUPER_ADMIN:
            raise HTTPException(403, "only a super admin can reset the demo")
        s = svc()
        s._seed(force=True)
        log(s.store, "activity", by=who.user, role=str(who.role), action="reset the demo")
        return {"reset": True, "days": s.store.meta().get("first_day")}

    @app.post("/api/push/subscribe")
    def push_subscribe(
        subscription: dict[str, Any] = Body(...), who: Caller = Depends(caller)
    ) -> dict[str, bool]:
        try:
            svc().push.subscribe(subscription, who.user, str(who.role), who.crew_id)
        except ValueError as err:
            raise HTTPException(400, str(err)) from err
        return {"subscribed": True}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(
        endpoint: str = Body(..., embed=True), _who: Caller = Depends(caller)
    ) -> dict[str, bool]:
        svc().push.unsubscribe(endpoint)
        return {"subscribed": False}

    @app.post("/api/roster/upload")
    async def roster_upload(
        file: UploadFile = File(...),
        dry_run: bool = Form(True),
        who: Caller = Depends(caller),
    ) -> dict[str, Any]:
        data = await _read_at_most(file, ROSTER_LIMIT)
        if len(data) > ROSTER_LIMIT:
            raise HTTPException(413, "an upload is at most 2 MB")
        try:
            content = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise HTTPException(400, f"not a JSON file: {err}") from err
        s = svc()
        return s.toolbox.call(
            "upload_roster", {"content": content, "dry_run": dry_run}, s.context(who)
        )

    # -- the board ------------------------------------------------------------------------

    @app.get("/api/board")
    def board(request: Request) -> dict[str, Any]:
        """Read-only, so a TV on the wall needs no sign-in. Only a signed-in desk user (or the
        schedule) can trigger a fresh brief, so an open screen never spends model credits."""
        s = svc()
        try:
            who: Caller | None = caller_from(request, config)
        except HTTPException:
            who = None
        if who is not None and who.role is not Role.CREW:
            refresh_brief(s)
        op = s.store.operation()
        cached = s.store.get_doc("brief", "current")
        ctx = s.context(Caller("board", Role.AUDITOR))
        issues = scan(op)
        callouts = s.desk.all()
        return {
            "clock": op.now.astimezone(IST).strftime("%a %d %b, %H:%M IST"),
            "brief": cached["brief"] if cached else None,
            "issues": [
                {
                    **asdict(i),
                    "day": i.day.isoformat(),
                    "severity": str(grade.level),
                    "severity_reasons": list(grade.reasons),
                }
                for i, grade in sorted(
                    ((i, for_issue(op, i)) for i in issues), key=lambda p: p[1].level.rank
                )[:12]
            ],
            "callouts": [
                {**c, "asked": [_on_the_wall(o) for o in c.get("asked", [])]}
                for c in s.toolbox.call("callout_status", {}, ctx).get("callouts", [])
            ],
            "changes": s.toolbox.call("roster_changes", {"limit": 5}, ctx).get("changes", []),
            "disruptions": s.toolbox.call("disruptions", {"limit": 8}, ctx).get("disruptions", []),
            "desk_load": s.reports.load(),
            **ops_room(s, op, issues, callouts),
        }

    @app.get("/api/speaker")
    def speaker() -> dict[str, Any]:
        """The ops-room speaker's feed: the desk's notices, newest first, each with how it
        should sound. Read-only and public like the board; it never spends model credits."""
        s = svc()
        op = s.store.operation()
        waiting = s.desk.plans.waiting() if s.desk.plans is not None else []
        return {
            "clock": op.now.astimezone(IST).strftime("%a %d %b, %H:%M IST"),
            "waiting": {
                "plans": len(waiting),
                "decisions": sum(1 for c in s.desk.all() if str(c.status) == "needs_decision"),
                "proposals": sum(1 for c in s.desk.all() if str(c.status) == "awaiting_approval"),
                "resolutions": sum(1 for r in s.reports.all() if r.status is ReportStatus.RESOLVED),
                "disruptions": sum(
                    1
                    for r in s.reports.all()
                    if r.status in (ReportStatus.OPEN, ReportStatus.IN_PROGRESS)
                ),
            },
            "summary": waiting_lines(s),
            "items": [
                {
                    "id": f"{n.ref}:{n.at.isoformat()}:{n.subject}",
                    "at": n.at.astimezone(IST).strftime("%H:%M"),
                    "to": n.to,
                    "tone": tone_of(n.subject),
                    "subject": _aloud(n.subject),
                    "body": _aloud(n.body),
                }
                for n in desk_notices(s)[:25]
            ],
        }

    @app.get("/api/card/{key:path}")
    def card(key: str) -> Response:
        if not key.startswith("cards/") or ".." in key:
            raise HTTPException(404)
        data = svc().images.get(key)
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    # -- crew pay -------------------------------------------------------------------------

    @app.get("/api/payments")
    def payments(who: Caller = Depends(caller)) -> dict[str, Any]:
        """The payment ledger for the console: crew see their own, crew control all of it."""
        if who.role is Role.SPEAKER or who.role is Role.REPORTER:
            raise HTTPException(403, f"the {who.role} role cannot see crew pay")
        s = svc()
        rows = s.payroll.all()
        approvals = s.payroll.funding.all()
        if who.role is Role.CREW:
            rows = [p for p in rows if p["crew_id"] == who.crew_id]
            approvals = []
        elif who.role not in ADMINS:
            # Where someone is paid is theirs and the admins' to see.
            rows = [{**p, "receiver": masked(p.get("receiver"))} for p in rows]
        return {
            **s.payroll.totals(),
            "cap": s.payroll.config.cap,
            "fx_inr_per_usd": s.payroll.config.fx_inr_per_usd,
            "payments": rows,
            "approvals": approvals,
            "payout_account": _payout_account(s, who.crew_id) if who.crew_id else None,
        }

    @app.post("/api/payments/sync")
    def payments_sync(who: Caller = Depends(caller)) -> dict[str, int]:
        if who.role not in (Role.CONTROLLER, Role.APPROVER, Role.ADMIN, Role.SUPER_ADMIN):
            raise HTTPException(403, f"the {who.role} role cannot refresh payments")
        return {"changed": svc().payroll.sync()}

    webhook_lock = threading.Lock()

    @app.post("/api/paypal/webhook")
    async def paypal_webhook(request: Request) -> dict[str, Any]:
        """PayPal's notices about payout items and approval payments. Each is checked with
        PayPal before it counts."""
        s = svc()
        try:
            event = await request.json()
        except ValueError as err:
            raise HTTPException(400, "not JSON") from err
        paypal = s.payroll.paypal
        if paypal is None or not config.paypal_webhook_id:
            raise HTTPException(404, "PayPal webhooks are not set up on this server")
        signed = ("paypal-transmission-id", "paypal-transmission-sig", "paypal-cert-url")
        if not all(request.headers.get(h) for h in signed):
            raise HTTPException(400, "not signed by PayPal")
        try:
            genuine = paypal.verify_webhook(dict(request.headers), event, config.paypal_webhook_id)
        except PayPalError as err:
            raise HTTPException(502, f"could not verify with PayPal: {err}") from err
        if not genuine:
            raise HTTPException(400, "the signature did not verify")
        # PayPal delivers an event again until it gets a 2xx, and a signed event can be sent
        # again by anyone who saw it: each event id counts once.
        event_id = str(event.get("id") or request.headers.get("paypal-transmission-id"))
        with webhook_lock:
            if s.store.get_doc("paypal_events", event_id) is not None:
                return {"received": True, "duplicate": True}
            s.store.put_doc(
                "paypal_events",
                event_id,
                {"type": str(event.get("event_type", "")), "at": datetime.now(UTC).isoformat()},
            )
        moved = s.payroll.webhook(event)
        log(
            s.store,
            "activity",
            by="paypal",
            role="webhook",
            action=str(event.get("event_type", "")),
            detail=moved or "no matching payment",
        )
        return {"received": True, "payment_id": moved}

    @app.get("/api/router-eval")
    def router_eval() -> Any:
        if not ROUTER_EVAL.is_file():
            raise HTTPException(404, "no router evaluation yet")
        return json.loads(ROUTER_EVAL.read_text(encoding="utf-8"))

    @app.get("/api/scorecard")
    def scorecard() -> Any:
        if not SCORECARD.is_file():
            raise HTTPException(404, "no scorecard yet")
        return json.loads(SCORECARD.read_text(encoding="utf-8"))

    # -- Alexa ----------------------------------------------------------------------------

    links = Links(config.alexa_client_id, config.alexa_client_secret, config.alexa_redirect_urls)

    def alexa_caller(token: str) -> Caller:
        who = links.who(token)
        account = next((a for a in DEMO_ACCOUNTS if a.user == who.user), None)
        if account is None:
            raise LinkError("invalid_grant", "that account is gone")
        return Caller(account.user, account.role, account.crew_id)

    def alexa_skill(s: Services) -> Skill:
        def ask(who: Caller, question: str) -> str:
            s.spend("alexa")
            _conversation, answer = s.conversations.ask(s.advisor, question, who, None)
            # The room's Echo is heard by everyone in it: no one's pay or PayPal address.
            return _aloud(answer.text) if who.role is Role.SPEAKER else answer.text

        def waiting(who: Caller) -> list[str]:
            if who.role is Role.SPEAKER:
                return [_aloud(line) for line in waiting_lines(s)]
            found = s.toolbox.call("my_queue", {"limit": 10}, s.context(who))
            return [
                f"{item['title']}{', ' + item['detail'] if item.get('detail') else ''}."
                for item in found.get("items", [])
            ]

        room = Caller("ops.speaker", Role.SPEAKER)
        return Skill(
            s.alexa_links, alexa_caller, ask, waiting, clock=lambda: datetime.now(UTC), room=room
        )

    @app.post("/api/alexa")
    async def alexa(request: Request) -> Any:
        """The Alexa skill's endpoint. Anything not signed by Alexa, for this skill, within
        the last 150 seconds is refused before it is read."""
        body = await request.body()
        try:
            envelope = verified(body, dict(request.headers), config.alexa_skill_id)
        except NotFromAlexa as err:
            log(svc().store, "security", by="alexa", role="skill", action=f"refused: {err}")
            return JSONResponse({"error": "not a valid Alexa request"}, status_code=400)
        try:
            return alexa_skill(svc()).handle(envelope)
        except Exception:
            logging.getLogger(__name__).exception("Alexa request failed")
            return {
                "version": "1.0",
                "response": {
                    "outputSpeech": {
                        "type": "PlainText",
                        "text": "Sorry, CrewPay couldn't answer that just now. Please try again.",
                    },
                    "shouldEndSession": True,
                },
            }

    def link_page(fields: dict[str, str], problem: str = "", status: int = 200) -> Response:
        """CrewPay's sign-in for Alexa account linking: plain HTML, no script."""
        options = "".join(
            f'<option value="{html.escape(a.user)}">{html.escape(a.label)} '
            f"({html.escape(str(a.role).replace('_', ' '))})</option>"
            for a in DEMO_ACCOUNTS
            if a.role is not Role.SPEAKER
        )
        hidden = "".join(
            f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in fields.items()
        )
        password = (
            '<label>Demo password<input type="password" name="password" required '
            'autocomplete="current-password"></label>'
            if config.demo_password
            else ""
        )
        note = f'<p class="bad">{html.escape(problem)}</p>' if problem else ""
        page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link CrewPay to Alexa</title>
<style>{LINK_STYLE}</style>
</head><body><main><h1>Link CrewPay to Alexa</h1>
<p>Pick your CrewPay account. Alexa will answer as you, with your role's access, and can tell
you when something reaches you.</p>{note}
<form method="post" action="/api/alexa/link">{hidden}
<label>Account<select name="user">{options}</select></label>{password}
<button type="submit">Link with Alexa</button></form></main></body></html>"""
        response = Response(page, status_code=status, media_type="text/html")
        hosts = {f"https://{urlparse(u).netloc}" for u in links.redirect_urls}
        allowed = " ".join(sorted(hosts | set(AMAZON_REDIRECT_HOSTS)))
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"form-action 'self' {allowed}; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    def _link_fields(source: Any) -> dict[str, str]:
        return {
            k: str(source.get(k) or "")
            for k in ("client_id", "redirect_uri", "state", "response_type", "scope")
        }

    @app.get("/api/alexa/link")
    def alexa_link_form(request: Request) -> Response:
        fields = _link_fields(request.query_params)
        try:
            links.check_request(
                fields["client_id"], fields["redirect_uri"], fields["response_type"]
            )
        except LinkError as err:
            return Response(
                f"Alexa account linking refused: {err}",
                status_code=400,
                media_type="text/plain",
            )
        return link_page(fields)

    @app.post("/api/alexa/link")
    async def alexa_link_submit(request: Request) -> Response:
        form = await request.form()
        fields = _link_fields(form)
        try:
            links.check_request(
                fields["client_id"], fields["redirect_uri"], fields["response_type"]
            )
        except LinkError as err:
            return Response(
                f"Alexa account linking refused: {err}",
                status_code=400,
                media_type="text/plain",
            )
        user = str(form.get("user") or "")
        account = next((a for a in DEMO_ACCOUNTS if a.user == user), None)
        if account is None:
            return link_page(fields, "Pick one of the accounts.", 400)
        if config.demo_password:
            where = f"{request.client.host if request.client else 'unknown'}:{user}"
            try:
                throttle.check(where)
            except HTTPException as err:
                return link_page(fields, str(err.detail), 429)
            if not password_ok(config, str(form.get("password") or "")):
                throttle.miss(where)
                log(
                    svc().store,
                    "security",
                    by=user,
                    role=str(account.role),
                    action="wrong password (Alexa linking)",
                )
                return link_page(fields, "That is not the demo password.", 401)
        code = links.code(
            Linked(account.user, account.role.value, account.crew_id), fields["redirect_uri"]
        )
        log(svc().store, "activity", by=user, role=str(account.role), action="linked Alexa")
        query = urlencode({"state": fields["state"], "code": code})
        joiner = "&" if "?" in fields["redirect_uri"] else "?"
        return Response(
            status_code=302, headers={"Location": f"{fields['redirect_uri']}{joiner}{query}"}
        )

    @app.post("/api/alexa/token")
    async def alexa_token(request: Request) -> Any:
        """The token step of account linking: a code or a refresh token for new tokens."""
        form = await request.form()
        client_id = str(form.get("client_id") or "")
        client_secret = str(form.get("client_secret") or "")
        basic = request.headers.get("authorization", "")
        if basic.lower().startswith("basic "):
            try:
                pair = base64.b64decode(basic[6:].strip()).decode()
                client_id, _, client_secret = pair.partition(":")
                client_id, client_secret = unquote_plus(client_id), unquote_plus(client_secret)
            except ValueError:
                return JSONResponse({"error": "invalid_client"}, status_code=401)
        if not links.client_ok(client_id, client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        grant = str(form.get("grant_type") or "")
        value = str(form.get("code") or form.get("refresh_token") or "")
        try:
            tokens = links.exchange(grant, value, str(form.get("redirect_uri") or ""))
        except LinkError as err:
            return JSONResponse(
                {"error": err.error, "error_description": str(err)}, status_code=400
            )
        return JSONResponse(tokens, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    # -- the pages ------------------------------------------------------------------------

    if WEB.is_dir():
        app.mount("/", ConsoleFiles(directory=WEB, html=True), name="console")

    return app


def main() -> None:
    import argparse
    import os

    import uvicorn

    from crewpay.api.ticker import start

    # Render (and most hosts) say which port to listen on in PORT
    hosted = "PORT" in os.environ
    parser = argparse.ArgumentParser(description="Run the CrewPay API and web console.")
    parser.add_argument("--host", default="0.0.0.0" if hosted else "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    config = settings()
    if hosted and not config.demo_password:
        # Without it anyone could say which role they are in a header, super admin included.
        raise SystemExit("CREWPAY_DEMO_PASSWORD must be set on a hosted server")
    services = Services(config)
    start(services, config.ticker_seconds)
    uvicorn.run(
        create_app(config, services),
        host=args.host,
        port=args.port,
        # Hosted, the platform's proxy says who the client is in X-Forwarded-For.
        proxy_headers=True,
        forwarded_allow_ips="*" if hosted else "127.0.0.1",
    )


if __name__ == "__main__":
    main()
