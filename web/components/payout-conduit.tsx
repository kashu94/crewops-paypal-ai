"use client";

/* The flight payout conduit: a cover plan's way from checked, to paid for with PayPal, to the
   crew member paid out, drawn as a lit airway between the plan and the crew card.

   It follows the real records: the plan's status, the approval payment and the crew payout from
   /api/payments. The moment PayPal confirms an approval (announcePaid, or the plan turning from
   confirming to approved) it plays once: the node ignites, a payment pulse runs down the airway,
   and the crew card opens on the payout receipt with its PayPal ids. "Replay payout" plays the
   last real payout again for a retake; with none yet, "Preview" plays a receipt marked as moving
   no money. SVG and transforms only, so it stays light; reduced motion gets the end states. */

import { AnimatePresence, motion, MotionConfig, useReducedMotion } from "framer-motion";
import { Check, Plane, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import { initials, inr } from "@/lib/format";
import { APPROVAL_PAID, type ApprovalPaid } from "@/lib/paid-event";
import type { Plan } from "@/lib/types";
import { useLoad } from "@/lib/use-load";

import { usd, type ApprovalPayment } from "./confirm-paypal";
import { cx } from "./ui";

type Payout = {
  payment_id: string;
  plan_id: string;
  trip_id: string;
  crew_id: string;
  name: string;
  rank: string;
  total_inr: number;
  amount: string;
  currency: string;
  mode: "paypal" | "simulated";
  status: string;
  batch_id: string | null;
  item_id: string | null;
  transaction_id: string | null;
};
type Ledger = { mode: "paypal" | "simulated"; fx_inr_per_usd: number; payments: Payout[]; approvals: ApprovalPayment[] };

type Phase = "ignite" | "transit" | "deliver" | "done";
type Stage = "idle" | "scan" | "confirm" | Phase;
/** What is playing: which plan, a key that restarts every animation, and the plans already
 * known when it started (a plan not among them takes the conduit over once it is done). */
type Show = { planId: string; run: number; preview: boolean; known: string[] };

const IGNITE = 750;
const TRANSIT = 1150;
const DELIVER = 950;

const BLUE = "#38bdf8";
const ICE = "#bae6fd";
const GOLD = "#ffc439";
const GREEN = "#34d399";
const RED = "#f87171";
const SLATE = "#64748b";
const WHITE = "#f8fafc";

/** Rotate and scale SVG parts about their own centre. */
const CENTRE = { transformBox: "fill-box", transformOrigin: "50% 50%" } as const;

// -- the airway ---------------------------------------------------------------------------------

type Pt = [number, number];
const NODE: Pt = [240, 70];
const IN: Pt[] = [[0, 100], [92, 100], [150, 70], [214, 70]]; // the plan, into the node
const OUT: Pt[] = [[266, 70], [330, 70], [388, 100], [480, 100]]; // the payment, out to the crew
const path = (c: Pt[]) => `M ${c[0][0]} ${c[0][1]} C ${c[1][0]} ${c[1][1]}, ${c[2][0]} ${c[2][1]}, ${c[3][0]} ${c[3][1]}`;

function at(c: Pt[], t: number): Pt {
  const u = 1 - t;
  const w = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
  return [w.reduce((s, k, i) => s + k * c[i][0], 0), w.reduce((s, k, i) => s + k * c[i][1], 0)];
}

/** Data packets drifting out of the plan along the inbound airway into the node. */
const PACKETS = Array.from({ length: 9 }, (_, i) => {
  const jitter = ((i * 37) % 11) - 5;
  const points = Array.from({ length: 9 }, (_, k) => {
    const t = k / 8;
    const [x, y] = at(IN, t);
    return [x, y + jitter * (1 - t) * 2.4] as Pt;
  });
  return { xs: points.map((p) => p[0]), ys: points.map((p) => p[1]), delay: i * 0.21, r: 1.4 + ((i * 7) % 3) * 0.45 };
});

/** The burst when PayPal confirms. */
const SPARKS = Array.from({ length: 16 }, (_, i) => {
  const a = (i / 16) * Math.PI * 2;
  const r = 30 + (i % 3) * 11;
  return { x: Math.cos(a) * r, y: Math.sin(a) * r, color: i % 2 ? GOLD : GREEN };
});

/** The payment pulse: a white head with a gold comet tail, layered strokes (no filters). */
const COMET = [
  { width: 16, opacity: 0.16, length: 0.12, color: GOLD },
  { width: 6, opacity: 0.6, length: 0.2, color: GOLD },
  { width: 2.2, opacity: 0.5, length: 0.34, color: ICE },
  { width: 8, opacity: 1, length: 0.004, color: WHITE },
];
const PULSE_EASE = [0.55, 0, 0.35, 1] as const;

// -- what each status means here ----------------------------------------------------------------

type Look = { label: string; color: string; seal?: string };

function payoutLook(p: Payout): Look {
  if (p.mode === "simulated") return { label: "Simulated payout: no PayPal keys on this server", color: SLATE, seal: "SIMULATED · NO MONEY MOVED · " };
  switch (p.status) {
    case "paid":
      return { label: "Paid: PayPal settled it", color: GREEN, seal: "FUNDS TRANSFERRED · VIA PAYPAL · " };
    case "sent":
      return { label: "Sent: PayPal is settling it", color: GOLD, seal: "PAYOUT SENT · VIA PAYPAL · " };
    case "unclaimed":
      return { label: "Sent: waiting for the receiver to claim it", color: GOLD, seal: "PAYOUT SENT · VIA PAYPAL · " };
    case "queued":
      return { label: "Queued for PayPal", color: ICE };
    case "held":
      return { label: "Held: over the cap, a second approver releases it", color: GOLD };
    default:
      return { label: `${p.status.charAt(0).toUpperCase()}${p.status.slice(1)}: see Crew pay`, color: RED };
  }
}

function nodeColor(stage: Stage, payout: Payout | null, preview: boolean): string {
  if (stage === "idle") return SLATE;
  if (stage === "scan") return BLUE;
  if (stage === "confirm" || stage === "ignite") return GOLD;
  if (stage === "done" && !preview && payout) return payoutLook(payout).color === RED ? RED : payout.status === "held" ? GOLD : GREEN;
  return GREEN;
}

function caption(stage: Stage, payout: Payout | null, preview: boolean, name: string): [string, string] {
  switch (stage) {
    case "idle":
      return ["Standing by", "cover appears here once a controller submits it"];
    case "scan":
      return ["Waiting for an approver", "9 duty rules are checked again on approval"];
    case "confirm":
      return ["Waiting for PayPal", "the approver pays for the cover to confirm it"];
    case "ignite":
      return ["Payment captured", "PayPal Checkout confirmed the approval"];
    case "transit":
      return [`Paying ${name}`, "PayPal Payouts"];
    default:
      if (preview) return ["Preview", "no money moved"];
      if (!payout) return ["Creating the payout", "PayPal Payouts"];
      if (payout.mode === "simulated") return ["Simulated payout", "no PayPal keys on this server"];
      return (
        {
          paid: ["Crew paid", "PayPal settled the payout"],
          sent: ["Payout sent", "PayPal is settling it"],
          unclaimed: ["Payout sent", "waiting for the receiver to claim it"],
          queued: ["Payout queued", "on its way to PayPal"],
          held: ["Payout held", "over the cap: a second approver releases it"],
        } as Record<string, [string, string]>
      )[payout.status] ?? ["Payout needs attention", "see Crew pay"];
  }
}

const newest = (a?: string | null, b?: string | null) => String(b ?? "").localeCompare(String(a ?? ""));

function fundingFor(approvals: ApprovalPayment[], planId: string): ApprovalPayment | null {
  const mine = approvals.filter((a) => a.plan_id === planId);
  for (const status of ["paid", "pending", "to pay"]) {
    const hit = mine.filter((a) => a.status === status).at(-1);
    if (hit) return hit;
  }
  return mine.at(-1) ?? null;
}

// -- the conduit ---------------------------------------------------------------------------------

export function PayoutConduit({ plans }: { plans: Plan[] }) {
  return (
    <MotionConfig reducedMotion="user">
      <Conduit plans={plans} />
    </MotionConfig>
  );
}

function Conduit({ plans }: { plans: Plan[] }) {
  const reduce = useReducedMotion() ?? false;
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const ledgerLoad = useLoad(() => api<Ledger>("/api/payments").catch(() => null));
  const ledger = ledgerLoad.data ?? null;
  const reloadLedger = ledgerLoad.reload;

  const [show, setShow] = useState<Show | null>(null);
  const [phase, setPhase] = useState<Phase>("done");
  const timers = useRef<number[]>([]);
  const played = useRef(new Set<string>());
  const seen = useRef<Map<string, string> | null>(null);
  const planIds = useRef<string[]>([]);

  const play = useCallback(
    (planId: string, preview: boolean) => {
      timers.current.forEach((t) => window.clearTimeout(t));
      played.current.add(planId);
      setShow({ planId, preview, run: Date.now(), known: planIds.current });
      if (reduce) {
        setPhase("done");
        timers.current = [];
        return;
      }
      setPhase("ignite");
      timers.current = [
        window.setTimeout(() => setPhase("transit"), IGNITE),
        window.setTimeout(() => setPhase("deliver"), IGNITE + TRANSIT),
        window.setTimeout(() => setPhase("done"), IGNITE + TRANSIT + DELIVER),
      ];
    },
    [reduce],
  );

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  // PayPal confirmed an approval on this page: play at once, with fresh payment records.
  useEffect(() => {
    const on = (e: Event) => {
      const planId = (e as CustomEvent<ApprovalPaid>).detail?.planId;
      if (!planId) return;
      reloadLedger();
      play(planId, false);
    };
    window.addEventListener(APPROVAL_PAID, on);
    return () => window.removeEventListener(APPROVAL_PAID, on);
  }, [play, reloadLedger]);

  // Confirmed some other way (PayPal's return link, a webhook, another tab): play when a plan
  // moves from waiting for PayPal to approved.
  useEffect(() => {
    planIds.current = plans.map((p) => p.plan_id);
    const now = new Map(plans.map((p) => [p.plan_id, p.status]));
    const before = seen.current;
    seen.current = now;
    if (!before) return;
    const moved = plans.find((p) => p.status === "approved" && before.get(p.plan_id) === "confirming" && !played.current.has(p.plan_id));
    if (moved) {
      played.current.add(moved.plan_id);
      window.setTimeout(() => play(moved.plan_id, false), 0);
    }
  }, [plans, play]);

  const confirming = plans.filter((p) => p.status === "confirming").sort((a, b) => newest(a.decided_at, b.decided_at));
  const submitted = plans.filter((p) => p.status === "submitted").sort((a, b) => newest(a.submitted_at, b.submitted_at));
  const approved = plans.filter((p) => p.status === "approved").sort((a, b) => newest(a.decided_at, b.decided_at));
  const waiting = confirming[0] ?? submitted[0] ?? null;

  // What is shown stays on the played plan until it is done and a new plan is waiting.
  const pinned = show ? (plans.find((p) => p.plan_id === show.planId) ?? null) : null;
  const overtaken = phase === "done" && waiting !== null && show !== null && !show.known.includes(waiting.plan_id);
  const playing = pinned !== null && !overtaken;
  const focus = playing ? pinned : (waiting ?? approved[0] ?? null);
  const preview = playing && Boolean(show?.preview);
  const stage: Stage = playing
    ? phase
    : !focus
      ? "idle"
      : focus.status === "submitted"
        ? "scan"
        : focus.status === "confirming"
          ? "confirm"
          : focus.status === "approved"
            ? "done"
            : "idle";
  const run = playing && show ? show.run : 0;

  const payout = focus && ledger ? (ledger.payments.filter((p) => p.plan_id === focus.plan_id).at(-1) ?? null) : null;
  const funding = focus && ledger ? fundingFor(ledger.approvals ?? [], focus.plan_id) : null;
  const fx = ledger?.fx_inr_per_usd ?? null;
  const color = nodeColor(stage, payout, preview);
  const [title, sub] = caption(stage, payout, preview, focus?.name ?? "the crew");

  const replayPlan = approved.find((p) => ledger?.payments.some((x) => x.plan_id === p.plan_id)) ?? null;
  const replay = replayPlan
    ? { planId: replayPlan.plan_id, preview: false, label: "Replay payout", hint: `Plays ${replayPlan.plan_id}'s real payout again` }
    : focus
      ? { planId: focus.plan_id, preview: true, label: "Preview", hint: "Plays the animation as a preview: no money moves" }
      : null;
  const busy = playing && phase !== "done";

  return (
    <section aria-label="Flight payout conduit" className="relative mb-5 overflow-hidden rounded-xl border border-[#16324f] bg-[#040a18] text-[#e6eef8]">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_50%_40%,rgba(56,189,248,0.10),transparent_65%)]" />
      <header className="relative flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-white/10 px-4 py-2.5">
        <Plane size={15} className="text-[#7dd3fc]" aria-hidden />
        <h2 className="text-sm font-semibold tracking-tight text-white">Flight payout conduit</h2>
        <span className="text-xs text-[#8ea3ba]">From checked cover to crew paid, live from PayPal</span>
        {ledger && (
          <span className="rounded-full border border-white/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-[#b9c6d6]">
            {ledger.mode === "paypal" ? "PayPal sandbox" : "Simulated"}
          </span>
        )}
        {replay && (
          <button
            type="button"
            title={replay.hint}
            disabled={busy}
            onClick={() => play(replay.planId, replay.preview)}
            className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-md border border-white/15 bg-white/[0.06] px-2.5 text-xs font-medium text-white transition-colors hover:bg-white/[0.12] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7dd3fc] disabled:opacity-40"
          >
            <RotateCcw size={13} aria-hidden /> {replay.label}
          </button>
        )}
      </header>

      <div className="relative grid items-center gap-3 p-4 lg:grid-cols-[15rem_minmax(0,36rem)_16.5rem] lg:justify-center lg:gap-0">
        <PlanDoc plan={focus} stage={stage} fx={fx} reduce={reduce} />

        <div className="relative w-full">
          <svg viewBox="0 0 480 200" className="block h-auto w-full" aria-hidden>
            <defs>
              <pattern id={`${uid}-grid`} width="16" height="16" patternUnits="userSpaceOnUse">
                <path d="M 16 0 L 0 0 0 16" fill="none" stroke="rgba(125,211,252,0.07)" strokeWidth="1" />
              </pattern>
              <pattern id={`${uid}-major`} width="80" height="80" patternUnits="userSpaceOnUse">
                <path d="M 80 0 L 0 0 0 80" fill="none" stroke="rgba(125,211,252,0.13)" strokeWidth="1" />
              </pattern>
              <radialGradient id={`${uid}-fade`} cx="50%" cy="42%" r="62%">
                <stop offset="0%" stopColor="white" stopOpacity="1" />
                <stop offset="100%" stopColor="white" stopOpacity="0" />
              </radialGradient>
              <mask id={`${uid}-mask`}>
                <rect width="480" height="200" fill={`url(#${uid}-fade)`} />
              </mask>
            </defs>

            {/* The chart: a micro-grid, range rings round the node, faint airways. */}
            <g mask={`url(#${uid}-mask)`}>
              <rect width="480" height="200" fill={`url(#${uid}-grid)`} />
              <rect width="480" height="200" fill={`url(#${uid}-major)`} />
              <circle cx={NODE[0]} cy={NODE[1]} r="52" fill="none" stroke="rgba(56,189,248,0.10)" strokeDasharray="2 4" />
              <circle cx={NODE[0]} cy={NODE[1]} r="84" fill="none" stroke="rgba(56,189,248,0.07)" strokeDasharray="2 6" />
              <path d="M 30 186 C 160 150, 320 150, 450 186" fill="none" stroke="rgba(148,197,255,0.10)" strokeDasharray="1 7" />
              <path d="M 60 18 C 170 40, 310 40, 420 18" fill="none" stroke="rgba(148,197,255,0.08)" strokeDasharray="1 7" />
            </g>

            {/* The airway itself, dashed like a route on a chart. */}
            <path d={path(IN)} fill="none" stroke="rgba(148,197,255,0.35)" strokeWidth="1.2" strokeDasharray="3 5" />
            <path d={path(OUT)} fill="none" stroke="rgba(148,197,255,0.35)" strokeWidth="1.2" strokeDasharray="3 5" />
            <Waypoint at={at(IN, 0.52)} label="RULES" />
            <Waypoint at={at(OUT, 0.48)} label="PAYOUT" />

            {/* Inbound: lit once a plan is in, with packets while it waits for a decision. */}
            {stage !== "idle" && <path d={path(IN)} fill="none" stroke={BLUE} strokeOpacity={stage === "scan" ? 0.6 : 0.35} strokeWidth="1.4" />}
            {stage === "scan" &&
              !reduce &&
              PACKETS.map((p, i) => (
                <motion.g
                  key={i}
                  initial={{ x: p.xs[0], y: p.ys[0], opacity: 0 }}
                  animate={{ x: p.xs, y: p.ys, opacity: [0, 1, 1, 0] }}
                  transition={{
                    x: { duration: 1.9, delay: p.delay, repeat: Infinity, ease: "linear" },
                    y: { duration: 1.9, delay: p.delay, repeat: Infinity, ease: "linear" },
                    opacity: { duration: 1.9, delay: p.delay, repeat: Infinity, times: [0, 0.15, 0.8, 1] },
                  }}
                >
                  <circle r={p.r * 2.6} fill={BLUE} opacity="0.18" />
                  <circle r={p.r} fill={ICE} />
                </motion.g>
              ))}

            {/* Outbound: marching while PayPal is awaited, then the pulse, then lit. */}
            {stage === "confirm" && (
              <motion.path
                d={path(OUT)}
                fill="none"
                stroke={GOLD}
                strokeOpacity="0.55"
                strokeWidth="1.4"
                strokeDasharray="4 6"
                animate={reduce ? undefined : { strokeDashoffset: [0, -20] }}
                transition={{ duration: 1.1, repeat: Infinity, ease: "linear" }}
              />
            )}
            {(stage === "transit" || stage === "deliver" || stage === "done") && (
              <motion.path
                key={`lit-${run}`}
                d={path(OUT)}
                fill="none"
                stroke={stage === "done" ? color : GREEN}
                strokeOpacity="0.85"
                strokeWidth="1.6"
                initial={stage === "transit" ? { pathLength: 0 } : false}
                animate={{ pathLength: 1 }}
                transition={{ duration: TRANSIT / 1000, ease: PULSE_EASE }}
              />
            )}
            {stage === "transit" &&
              COMET.map((c, i) => (
                <motion.path
                  key={`comet-${run}-${i}`}
                  d={path(OUT)}
                  fill="none"
                  stroke={c.color}
                  strokeWidth={c.width}
                  strokeLinecap="round"
                  initial={{ pathLength: c.length, pathOffset: -c.length, opacity: 0 }}
                  animate={{ pathOffset: 1 - c.length, opacity: [0, c.opacity, c.opacity, 0] }}
                  transition={{
                    pathOffset: { duration: TRANSIT / 1000, ease: PULSE_EASE },
                    opacity: { duration: TRANSIT / 1000, times: [0, 0.06, 0.92, 1] },
                  }}
                />
              ))}

            {/* The verification node. */}
            <g>
              <motion.circle
                cx={NODE[0]}
                cy={NODE[1]}
                r="34"
                fill="none"
                stroke={color}
                strokeOpacity="0.4"
                strokeDasharray="2 5"
                style={CENTRE}
                animate={reduce || stage === "idle" ? { rotate: 0 } : { rotate: 360 }}
                transition={{ duration: 16, repeat: Infinity, ease: "linear" }}
              />
              <motion.circle
                cx={NODE[0]}
                cy={NODE[1]}
                r="23"
                fill={color}
                style={CENTRE}
                initial={false}
                animate={{
                  fill: color,
                  fillOpacity: stage === "idle" ? 0.08 : 0.16,
                  scale: reduce || stage === "idle" || stage === "done" ? 1 : [1, stage === "confirm" ? 1.24 : 1.14, 1],
                }}
                transition={{
                  fill: { duration: 0.4 },
                  scale: { duration: stage === "confirm" ? 1.2 : 2.4, repeat: Infinity, ease: "easeInOut" },
                }}
              />
              <motion.circle
                key={`core-${run}`}
                cx={NODE[0]}
                cy={NODE[1]}
                r="13"
                initial={false}
                animate={stage === "ignite" ? { fill: [BLUE, WHITE, GOLD, GREEN] } : { fill: color }}
                transition={stage === "ignite" ? { duration: IGNITE / 1000, times: [0, 0.18, 0.55, 1] } : { duration: 0.4 }}
              />
              <circle cx={NODE[0] - 4} cy={NODE[1] - 4} r="3.6" fill="white" opacity="0.7" />
              <text x={NODE[0]} y={NODE[1] - 44} textAnchor="middle" fontSize="8.5" letterSpacing="2" fill="rgba(186,230,253,0.6)">
                VERIFY
              </text>
            </g>

            {/* The ignite burst. */}
            {stage === "ignite" && (
              <g key={`burst-${run}`}>
                <motion.circle
                  cx={NODE[0]}
                  cy={NODE[1]}
                  r="18"
                  fill="none"
                  stroke={GOLD}
                  strokeWidth="2"
                  style={CENTRE}
                  initial={{ scale: 1, opacity: 0.9 }}
                  animate={{ scale: 3.4, opacity: 0 }}
                  transition={{ duration: 0.8, ease: "easeOut" }}
                />
                {SPARKS.map((s, i) => (
                  <motion.circle
                    key={i}
                    cx={NODE[0]}
                    cy={NODE[1]}
                    r="2.3"
                    fill={s.color}
                    initial={{ x: 0, y: 0, opacity: 1 }}
                    animate={{ x: s.x, y: s.y, opacity: 0 }}
                    transition={{ duration: 0.75, ease: "easeOut", delay: 0.05 }}
                  />
                ))}
              </g>
            )}
          </svg>

          <div className="pointer-events-none absolute inset-x-0 top-[53%] px-2 text-center">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={title} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -4 }} transition={{ duration: 0.2 }}>
                <p className="text-[13px] font-semibold tracking-tight text-white">{title}</p>
                <p className="text-[11px] text-[#8ea3ba]">{sub}</p>
              </motion.div>
            </AnimatePresence>
          </div>
          <p className="sr-only" aria-live="polite">
            {title}: {sub}
          </p>
        </div>

        <CrewCard plan={focus} stage={stage} run={run} payout={payout} funding={funding} fx={fx} preview={preview} reduce={reduce} />
      </div>

      <Tracker stage={stage} payout={payout} preview={preview} simulated={ledger?.mode === "simulated"} />
    </section>
  );
}

function Waypoint({ at: [x, y], label }: { at: Pt; label: string }) {
  return (
    <g>
      <path d={`M ${x} ${y - 4} L ${x + 4} ${y} L ${x} ${y + 4} L ${x - 4} ${y} Z`} fill="#040a18" stroke="rgba(186,230,253,0.6)" strokeWidth="1" />
      <text x={x} y={y - 9} textAnchor="middle" fontSize="7.5" letterSpacing="1.6" fill="rgba(186,230,253,0.5)">
        {label}
      </text>
    </g>
  );
}

// -- the plan, as a document being read ----------------------------------------------------------

function PlanDoc({ plan, stage, fx, reduce }: { plan: Plan | null; stage: Stage; fx: number | null; reduce: boolean }) {
  if (!plan)
    return (
      <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-white/15 p-4 text-center text-xs text-[#8ea3ba]">
        No cover plan waiting.
      </div>
    );
  const status =
    plan.status === "submitted"
      ? `Submitted by ${plan.submitted_by}`
      : plan.status === "confirming"
        ? `Approved by ${plan.decided_by ?? "an approver"}, waiting for PayPal`
        : plan.status === "approved"
          ? `Approved by ${plan.decided_by ?? "an approver"} and paid with PayPal`
          : `${plan.status.charAt(0).toUpperCase()}${plan.status.slice(1)}`;
  return (
    <div className="relative min-h-56 overflow-hidden rounded-lg border border-white/12 bg-white/[0.035] p-3.5">
      <div className="flex items-center justify-between gap-2 text-[10px] font-medium uppercase tracking-[0.16em] text-[#7dd3fc]/80">
        <span>Cover plan</span>
        <span className="font-mono normal-case tracking-normal text-[#8ea3ba]">{plan.plan_id}</span>
      </div>
      <p className="mt-2 font-mono text-[15px] font-semibold text-white">
        {plan.trip_id} <span className="font-sans text-[13px] font-normal text-[#b9c6d6]">· {plan.seat}</span>
      </p>
      <dl className="mt-2 grid grid-cols-[3.4rem_1fr] gap-x-2 gap-y-1 text-[12px]">
        <Row k="Out">
          {plan.replacing_name ? <>{plan.replacing_name} </> : null}
          <span className={plan.replacing_name ? "font-mono text-[#8ea3ba]" : "font-mono"}>{plan.replacing}</span>
        </Row>
        <Row k="In">
          {plan.name} <span className="font-mono text-[#8ea3ba]">{plan.crew_id}</span>
        </Row>
        <Row k="Cost">
          <span className="font-mono">{inr(plan.cost_inr)}</span>
          {fx ? <span className="text-[#8ea3ba]"> · {usd(plan.cost_inr / fx)}</span> : null}
        </Row>
        <Row k="Rules">
          <span className="inline-flex items-center gap-1">
            <Check size={12} className="text-[#34d399]" aria-hidden /> 9 duty rules checked
          </span>
        </Row>
      </dl>
      <p className="mt-2.5 border-t border-white/10 pt-2 text-[11px] text-[#8ea3ba]">{status}</p>
      <span aria-hidden className="absolute -right-1 top-1/2 hidden h-2 w-2 -translate-y-1/2 rounded-full bg-[#7dd3fc] lg:block shadow-[0_0_10px_2px_rgba(56,189,248,0.7)]" />
      {stage === "scan" && !reduce && (
        <motion.div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          initial={{ y: "0%" }}
          animate={{ y: ["0%", "100%"] }}
          transition={{ duration: 2.4, repeat: Infinity, repeatType: "reverse", ease: "easeInOut" }}
        >
          <div className="absolute inset-x-0 -top-10 h-10 bg-[linear-gradient(to_bottom,transparent,rgba(56,189,248,0.16))]" />
          <div className="absolute inset-x-0 top-0 h-px bg-[#7dd3fc] shadow-[0_0_12px_2px_rgba(56,189,248,0.8)]" />
        </motion.div>
      )}
    </div>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-[#8ea3ba]">{k}</dt>
      <dd className="min-w-0 truncate text-[#e6eef8]">{children}</dd>
    </>
  );
}

// -- the crew member, and what PayPal says -------------------------------------------------------

function CrewCard({
  plan,
  stage,
  run,
  payout,
  funding,
  fx,
  preview,
  reduce,
}: {
  plan: Plan | null;
  stage: Stage;
  run: number;
  payout: Payout | null;
  funding: ApprovalPayment | null;
  fx: number | null;
  preview: boolean;
  reduce: boolean;
}) {
  if (!plan)
    return (
      <div className="flex min-h-56 flex-col items-center justify-center rounded-lg border border-dashed border-white/15 p-4 text-center text-xs text-[#8ea3ba]">
        Nobody to pay yet.
      </div>
    );
  const landed = stage === "deliver" || stage === "done";
  return (
    <motion.div
      key={`card-${run}`}
      className="relative min-h-56 overflow-hidden rounded-lg border border-white/12 bg-white/[0.035] p-3.5"
      animate={stage === "deliver" && !reduce ? { scale: [1, 1.035, 0.995, 1] } : { scale: 1 }}
      transition={{ duration: 0.7, ease: "easeOut" }}
    >
      {stage === "deliver" && !reduce && (
        <>
          <motion.span
            aria-hidden
            className="pointer-events-none absolute -left-10 top-1/2 -mt-10 h-20 w-20 rounded-full border-2 border-[#34d399]/70"
            initial={{ scale: 0.2, opacity: 0.9 }}
            animate={{ scale: 4.4, opacity: 0 }}
            transition={{ duration: 1.1, ease: "easeOut" }}
          />
          <motion.span
            aria-hidden
            className="pointer-events-none absolute -left-10 top-1/2 -mt-10 h-20 w-20 rounded-full border border-[#ffc439]/60"
            initial={{ scale: 0.2, opacity: 0.8 }}
            animate={{ scale: 3.4, opacity: 0 }}
            transition={{ duration: 1.1, ease: "easeOut", delay: 0.18 }}
          />
          <motion.span
            aria-hidden
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_0%_50%,rgba(52,211,153,0.28),transparent_65%)]"
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0.2] }}
            transition={{ duration: 1.2 }}
          />
        </>
      )}
      <span
        aria-hidden
        className={cx(
          "absolute -left-1 top-1/2 hidden h-2 w-2 -translate-y-1/2 rounded-full lg:block",
          landed ? "bg-[#34d399] shadow-[0_0_10px_2px_rgba(52,211,153,0.7)]" : "bg-white/25",
        )}
      />

      <div className="relative flex items-center gap-2.5">
        <span
          className={cx(
            "grid h-9 w-9 shrink-0 place-items-center rounded-full border text-[12px] font-semibold",
            landed ? "border-[#34d399]/60 bg-[#34d399]/15 text-white" : "border-white/20 bg-white/5 text-[#b9c6d6]",
          )}
        >
          {initials(plan.name)}
        </span>
        <div className="min-w-0">
          <p className="truncate text-[14px] font-semibold text-white">{plan.name}</p>
          <p className="truncate text-[11px] text-[#8ea3ba]">
            <span className="font-mono">{plan.crew_id}</span> · covering <span className="font-mono">{plan.trip_id}</span>
          </p>
        </div>
      </div>

      <div className="relative mt-3">
        <AnimatePresence mode="wait" initial={false}>
          {landed ? (
            <motion.div
              key={`receipt-${run}`}
              initial={{ opacity: 0, y: 10, filter: "blur(6px)", clipPath: "inset(0 0 100% 0 round 8px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)", clipPath: "inset(0 0 0% 0 round 8px)" }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.6, ease: [0.2, 0.8, 0.2, 1] }}
            >
              <Receipt plan={plan} payout={payout} funding={funding} fx={fx} preview={preview} spin={!reduce} />
            </motion.div>
          ) : (
            <motion.div key="monitor" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }}>
              <Monitor stage={stage} funding={funding} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

function Monitor({ stage, funding }: { stage: Stage; funding: ApprovalPayment | null }) {
  const money = funding ? usd(funding.amount, funding.currency) : null;
  const approval: [string, string] =
    stage === "confirm"
      ? [`Waiting${money ? ` for ${money}` : ""}`, GOLD]
      : stage === "ignite" || stage === "transit"
        ? [`Captured${money ? `: ${money}` : ""}`, GREEN]
        : ["Opens when the plan is approved", SLATE];
  const crew: [string, string] = stage === "transit" ? ["Sending through PayPal Payouts", GOLD] : ["After PayPal confirms the approval", SLATE];
  return (
    <div className="rounded-md border border-white/10 bg-black/20 p-2.5">
      <p className="text-[10px] font-medium uppercase tracking-[0.16em] text-[#7dd3fc]/80">PayPal status</p>
      <ul className="mt-1.5 space-y-1.5 text-[12px]">
        <Line k="Approval payment" v={approval[0]} color={approval[1]} />
        <Line k="Crew payout" v={crew[0]} color={crew[1]} />
      </ul>
    </div>
  );
}

function Line({ k, v, color }: { k: string; v: string; color: string }) {
  return (
    <li>
      <span className="block text-[11px] text-[#8ea3ba]">{k}</span>
      <span className="flex items-center gap-1.5 text-[#e6eef8]">
        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color, boxShadow: `0 0 8px ${color}` }} />
        {v}
      </span>
    </li>
  );
}

function Receipt({
  plan,
  payout,
  funding,
  fx,
  preview,
  spin,
}: {
  plan: Plan;
  payout: Payout | null;
  funding: ApprovalPayment | null;
  fx: number | null;
  preview: boolean;
  spin: boolean;
}) {
  const look: Look = preview
    ? { label: "Preview: no money moved", color: SLATE, seal: "PREVIEW · NO MONEY MOVED · " }
    : payout
      ? payoutLook(payout)
      : { label: "Creating the PayPal payout…", color: ICE };
  const amount = payout ? usd(payout.amount, payout.currency) : fx ? usd(plan.cost_inr / fx) : inr(plan.cost_inr);
  const rupees = payout ? payout.total_inr : plan.cost_inr;
  const ids: [string, string | null | undefined][] = preview
    ? []
    : [
        ["Payout item", payout?.item_id ?? payout?.batch_id],
        ["Transaction", payout?.transaction_id],
        ["Approval capture", funding?.paypal_capture_id],
      ];
  return (
    <div className="rounded-md border p-2.5" style={{ borderColor: `${look.color}55`, background: `linear-gradient(160deg, ${look.color}14, transparent 60%)` }}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-[0.16em]" style={{ color: look.color }}>
            Payout receipt
          </p>
          <p className="mt-0.5 font-mono text-[20px] font-semibold leading-tight text-white">{amount}</p>
          <p className="text-[11px] text-[#8ea3ba]">
            {inr(rupees)}
            {fx ? ` at ₹${fx} per $` : ""}
          </p>
        </div>
        {look.seal && <Seal text={look.seal} color={look.color} spin={spin} />}
      </div>
      <dl className="mt-2 space-y-0.5 text-[11px]">
        {ids
          .filter(([, v]) => v)
          .map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <dt className="w-[5.6rem] shrink-0 text-[#8ea3ba]">{k}</dt>
              <dd className="min-w-0 truncate font-mono text-[#e6eef8]" title={v ?? ""}>
                {v}
              </dd>
            </div>
          ))}
      </dl>
      <p className="mt-2 flex items-center gap-1.5 text-[11.5px] text-[#e6eef8]">
        <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: look.color, boxShadow: `0 0 8px ${look.color}` }} />
        {look.label}
      </p>
    </div>
  );
}

function Seal({ text, color, spin }: { text: string; color: string; spin: boolean }) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  return (
    <svg viewBox="0 0 80 80" className="h-[3.75rem] w-[3.75rem] shrink-0" aria-hidden>
      <defs>
        <path id={`${id}-ring`} d="M 40 40 m -29 0 a 29 29 0 1 1 58 0 a 29 29 0 1 1 -58 0" />
      </defs>
      <circle cx="40" cy="40" r="38" fill={color} fillOpacity="0.07" stroke={color} strokeOpacity="0.45" />
      <circle cx="40" cy="40" r="20" fill={color} fillOpacity="0.16" stroke={color} strokeOpacity="0.8" />
      <motion.g style={CENTRE} animate={spin ? { rotate: 360 } : { rotate: 0 }} transition={{ duration: 18, repeat: Infinity, ease: "linear" }}>
        <text fontSize="6.6" fontWeight="600" fill={color} letterSpacing="1">
          <textPath href={`#${id}-ring`} textLength="178" lengthAdjust="spacing">
            {text}
          </textPath>
        </text>
      </motion.g>
      <path d="M 32 40.5 l 5.5 5.5 l 11 -12" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// -- where the plan has got to --------------------------------------------------------------------

function Tracker({ stage, payout, preview, simulated }: { stage: Stage; payout: Payout | null; preview: boolean; simulated: boolean }) {
  const paidOut = !preview && payout?.mode === "paypal" && payout.status === "paid";
  const reached = { idle: -1, scan: 0, confirm: 1, ignite: 1, transit: 2, deliver: 2, done: 2 }[stage];
  // A preview never ticks "paid out": only PayPal saying so does.
  const done = (i: number) => i < reached || (i === reached && (stage === "ignite" || (i === 2 && paidOut)));
  const steps = ["Cover checked", simulated ? "Paid with PayPal (simulated)" : "Paid with PayPal", simulated ? "Crew paid out (simulated)" : "Crew paid out"];
  return (
    <ol className="relative flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-white/10 px-4 py-2.5 text-[11.5px]">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-2">
          {i > 0 && <span aria-hidden className={cx("h-px w-6 sm:w-10", i <= reached ? "bg-[#7dd3fc]/60" : "bg-white/15")} />}
          <span
            aria-hidden
            className={cx(
              "grid h-4 w-4 place-items-center rounded-full border",
              done(i) ? "border-[#34d399] bg-[#34d399]/20" : i === reached ? "border-[#7dd3fc] bg-[#7dd3fc]/15" : "border-white/20",
            )}
          >
            {done(i) && <Check size={10} className="text-[#34d399]" />}
          </span>
          <span className={i <= reached ? "text-white" : "text-[#8ea3ba]"}>{s}</span>
        </li>
      ))}
    </ol>
  );
}
