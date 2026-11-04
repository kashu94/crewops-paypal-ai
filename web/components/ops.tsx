"use client";

import { Camera, CameraOff, ChevronDown, Gauge, Inbox, Users } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { tool } from "@/lib/api";
import { hhmm, inr, plural, short, words } from "@/lib/format";
import type { Callout, DelayResult, DeskLoad, GateChangeResult, GatePlan, HoldResult, Notice as NoticeT, Plan, ReportSummary, Risk } from "@/lib/types";
import { useAction } from "@/lib/use-load";

import { useSession } from "./session";
import { Button, control, cx, Empty, Mono, Notice, Pill, SeverityBadge, StatusPill, Table, type PillTone } from "./ui";

const GONE = ["declined", "unavailable", "no answer", "no longer legal", "not approved"];

// -- risk -----------------------------------------------------------------------------------

const RISK_TONE: Record<string, PillTone> = { high: "critical", medium: "pending", low: "ok" };

/** A crew member's risk score. Shown beside a choice, never used to rank it. The drivers open
 * on hover and on click (for touch). */
export function RiskBadge({ risk, compact }: { risk?: Risk | null; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  if (!risk) return null;
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={`Risk ${risk.score} of 100, ${risk.level}`}>
        <Pill tone={RISK_TONE[risk.level] ?? "neutral"}>
          <Gauge size={11} />
          {compact ? risk.score : `Risk ${risk.score}`}
        </Pill>
      </button>
      {open && (
        <span className="animate-rise absolute left-0 top-6 z-40 w-72 rounded-lg border border-line bg-surface p-3 text-left text-xs shadow-[0_12px_28px_rgb(0_0_0/0.14)]">
          <span className="mb-2 flex items-center justify-between font-semibold text-foreground">
            Risk {risk.score}/100 · {risk.level}
            <span className="font-normal text-faint">shown, not ranked</span>
          </span>
          {risk.drivers.length === 0 ? (
            <span className="text-muted">Nothing adds to the risk.</span>
          ) : (
            <span className="flex flex-col gap-1.5">
              {risk.drivers.map((d) => (
                <span key={d.factor} className="flex gap-2">
                  <span className="w-8 shrink-0 text-right font-mono text-pending-ink">+{d.points}</span>
                  <span>
                    <b className="font-medium text-foreground">{words(d.factor)}</b>
                    <span className="block text-muted">{d.detail}</span>
                  </span>
                </span>
              ))}
            </span>
          )}
        </span>
      )}
    </span>
  );
}

/** The drivers of a risk score as a list, for a profile. */
export function RiskDetail({ risk }: { risk?: Risk | null }) {
  if (!risk) return <p className="text-sm text-muted">No risk score from the server yet.</p>;
  const bar = risk.level === "high" ? "bg-critical" : risk.level === "medium" ? "bg-pending" : "bg-ok";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end gap-3">
        <span className="text-3xl font-semibold tabular-nums leading-none">{risk.score}</span>
        <span className="pb-0.5 text-sm text-muted">of 100 · {risk.level}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-sunken">
        <i className={cx("block h-full rounded-full", bar)} style={{ width: `${risk.score}%` }} />
      </div>
      <ul className="flex flex-col gap-2 text-sm">
        {risk.drivers.map((d) => (
          <li key={d.factor} className="flex gap-3">
            <span className="w-9 shrink-0 text-right font-mono text-xs leading-5 text-pending-ink">+{d.points}</span>
            <span>
              <span className="font-medium">{words(d.factor)}</span>
              <span className="block text-xs text-muted">{d.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-faint">Risk is shown to help a person weigh a choice. Cover is never ranked by it.</p>
    </div>
  );
}

// -- callouts --------------------------------------------------------------------------------

const ANSWER_TONE = (a: string): PillTone => (a === "accepted" ? "ok" : GONE.includes(a) ? "critical" : a === "waiting" ? "accent" : "neutral");

export function CalloutRow({ c }: { c: Callout }) {
  const { me, can } = useSession();
  const { busy, run } = useAction();
  const decide = (action: string) =>
    run(`${c.callout_id}|${action}`, () => tool("decide_callout", { callout_id: c.callout_id, action }), () => `${c.callout_id}: ${words(action)} sent.`);
  const controller = me.role === "controller" && can("decide_callout");
  const approver = me.role === "approver" && can("decide_callout");
  return (
    <li className="px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="font-semibold">{c.trip_id}</Mono>
        <span className="text-sm text-muted">
          {c.seat} replacing <Mono>{c.replacing}</Mono>
        </span>
        <StatusPill value={c.status} />
        {c.report_id && (
          <Link href={`/disruptions/view/?id=${encodeURIComponent(c.report_id)}`} className="text-xs text-accent hover:underline">
            <Mono>{c.report_id}</Mono>
          </Link>
        )}
        {c.answer_by && <span className="ml-auto font-mono text-xs text-faint">answer by {hhmm(c.answer_by)}</span>}
      </div>
      <ul className="mt-2 flex flex-col gap-1">
        {c.asked.map((o) => {
          const gone = GONE.includes(o.answer);
          return (
            <li key={o.crew_id} className="flex items-baseline gap-2 text-sm">
              <span className={cx("min-w-0 truncate", gone && "text-faint line-through")}>
                {o.name} <Mono className="text-faint">{o.crew_id}</Mono>
              </span>
              <Pill tone={ANSWER_TONE(o.answer)}>{o.answer}</Pill>
              <span className="ml-auto font-mono text-xs text-muted">{inr(o.cost_inr)}</span>
            </li>
          );
        })}
      </ul>
      {c.waiting_for_approval && c.plan_id && (
        <p className="mt-2 text-xs text-muted">
          Accepted; waiting for an approver on <Link className="text-accent hover:underline" href="/approvals/"><Mono>{c.plan_id}</Mono></Link>.
        </p>
      )}
      {c.to_arrange && <p className="mt-2 text-xs text-pending-ink">To arrange: {c.to_arrange.join("; ")}</p>}
      {c.proposal && (
        <p className="mt-2 text-xs text-muted">
          Proposed by {c.proposal.proposed_by}: {c.proposal.summary}
          {c.proposal.approved_by ? `, approved by ${c.proposal.approved_by}` : ""}.
        </p>
      )}
      {c.note && <p className="mt-1 text-xs text-muted">{c.note}</p>}
      {(controller && c.status === "needs_decision" && c.desk_options?.length) || (approver && c.status === "awaiting_approval") ? (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {controller &&
            c.status === "needs_decision" &&
            c.desk_options?.map((o) => (
              <Button key={o.way} size="sm" tone="secondary" disabled={busy !== null} onClick={() => decide(`propose_${o.way}`)}>
                Propose: {o.summary} · {inr(o.cost_inr)}
              </Button>
            ))}
          {approver && c.status === "awaiting_approval" && (
            <>
              <Button size="sm" disabled={busy !== null} onClick={() => decide("approve")}>
                Approve
              </Button>
              <Button size="sm" tone="secondary" disabled={busy !== null} onClick={() => decide("reject")}>
                Send back
              </Button>
            </>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function CalloutList({ callouts, empty = "No callouts." }: { callouts: Callout[]; empty?: string }) {
  if (!callouts.length) return <Empty icon={<Users size={20} />}>{empty}</Empty>;
  return <ul className="divide-y divide-rule">{callouts.map((c) => <CalloutRow key={c.callout_id} c={c} />)}</ul>;
}

// -- plans ----------------------------------------------------------------------------------

export function PlanRow({ p }: { p: Plan }) {
  const { me, can, flash } = useSession();
  const { busy, run } = useAction();
  const [note, setNote] = useState("");
  const mine = me.user === p.submitted_by;
  const approver = me.role === "approver" && can("review_plan");
  const review = (action: string) => {
    if ((action === "reject" || action === "send_back") && note.trim().length < 3) {
      flash(action === "reject" ? "Say why it is rejected." : "Say what to change.", "pending");
      return;
    }
    return run(
      `${p.plan_id}|${action}`,
      () => tool<Plan>("review_plan", { plan_id: p.plan_id, action, note: note.trim() }),
      (r) => (r.status === "stale" ? `Not applied: ${r.decision_note ?? "the trip changed"}` : `${p.plan_id} ${words(r.status)}.`),
    );
  };
  return (
    <li className="px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="font-semibold">{p.trip_id}</Mono>
        <span className="text-sm text-muted">{p.seat}</span>
        <StatusPill value={p.status} />
        {p.report_id && (
          <Link href={`/disruptions/view/?id=${encodeURIComponent(p.report_id)}`} className="text-xs text-accent hover:underline">
            <Mono>{p.report_id}</Mono>
          </Link>
        )}
        <Mono className="ml-auto text-xs text-faint">{p.plan_id}</Mono>
      </div>
      <p className="mt-1 text-sm">
        <b className="font-medium">{p.name}</b> <Mono className="text-faint">{p.crew_id}</Mono> replaces <Mono>{p.replacing}</Mono> ·{" "}
        <span className="font-mono">{inr(p.cost_inr)}</span>
      </p>
      <p className="text-xs text-muted">
        Submitted by {p.submitted_by} at {short(p.submitted_at)}
        {p.source.startsWith("CO-") ? ` from callout ${p.source}` : p.source.startsWith("R-") && !p.report_id ? ` for report ${p.source}` : ""}
        {p.note_from_submitter ? `: “${p.note_from_submitter}”` : ""}
      </p>
      {p.to_arrange && <p className="mt-1 text-xs text-pending-ink">To arrange: {p.to_arrange.join("; ")}</p>}
      {p.decided_by && (
        <p className="mt-1 text-xs">
          {words(p.status)} by {p.decided_by}
          {p.decision_note ? `: ${p.decision_note}` : ""}
        </p>
      )}
      {p.status === "submitted" && approver && !mine && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy !== null} onClick={() => review("approve")}>
            Approve
          </Button>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reason or note" className={cx(control, "h-7 min-w-40 flex-1 text-[13px]")} />
          <Button size="sm" tone="secondary" disabled={busy !== null} onClick={() => review("send_back")}>
            Send back
          </Button>
          <Button size="sm" tone="danger-quiet" disabled={busy !== null} onClick={() => review("reject")}>
            Reject
          </Button>
        </div>
      )}
      {p.status === "submitted" && mine && can("review_plan") && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">You submitted this; an approver decides it.</span>
          <Button size="sm" tone="secondary" disabled={busy !== null} onClick={() => review("withdraw")}>
            Withdraw
          </Button>
        </div>
      )}
    </li>
  );
}

export function PlanList({ plans, empty = "No plans." }: { plans: Plan[]; empty?: string }) {
  if (!plans.length) return <Empty icon={<Inbox size={20} />}>{empty}</Empty>;
  return <ul className="divide-y divide-rule">{plans.map((p) => <PlanRow key={p.plan_id} p={p} />)}</ul>;
}

// -- the watch's issues ----------------------------------------------------------------------

// -- messages --------------------------------------------------------------------------------

export function NoticeList({ notices, empty = "No messages yet." }: { notices: NoticeT[]; empty?: string }) {
  if (!notices.length) return <Empty icon={<Inbox size={20} />}>{empty}</Empty>;
  return (
    <ul className="divide-y divide-rule">
      {notices.map((n, i) => (
        <li key={`${n.at}-${i}`} className="px-4 py-3 sm:px-5">
          <div className="flex items-baseline gap-2">
            <b className="text-sm font-medium">{n.subject}</b>
            <span className="ml-auto shrink-0 font-mono text-xs text-faint">{n.at}</span>
          </div>
          <p className="mt-0.5 text-[13px] text-muted">{n.body}</p>
        </li>
      ))}
    </ul>
  );
}

// -- disruption reports ----------------------------------------------------------------------

const EVIDENCE_TONE = { high: "ok", read: "ok", medium: "pending", low: "critical", none: "neutral" } as const;

/** How far a report is backed by a photo. */
export function EvidenceBadge({ evidence }: { evidence?: ReportSummary["evidence"] }) {
  if (!evidence) return null;
  return (
    <Pill tone={EVIDENCE_TONE[evidence.level] ?? "neutral"} title={evidence.label}>
      {evidence.level === "none" ? <CameraOff size={11} /> : <Camera size={11} />}
      {evidence.level === "none" ? "no photo" : evidence.level === "read" ? "photo" : `photo · ${evidence.level}`}
    </Pill>
  );
}

export function ReportRows({ reports, empty = "No disruption reports here." }: { reports: ReportSummary[]; empty?: ReactNode }) {
  const { me } = useSession();
  if (!reports.length) return <Empty icon={<Inbox size={20} />}>{empty}</Empty>;
  return (
    <ul className="divide-y divide-rule">
      {reports.map((r) => (
        <li key={r.report_id}>
          <Link href={`/disruptions/view/?id=${encodeURIComponent(r.report_id)}`} className="group flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-3 transition-colors hover:bg-sunken sm:px-5">
            <span className="min-w-0 flex-1 basis-64">
              <span className="block truncate text-sm font-medium group-hover:text-accent">{r.title}</span>
              <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                <Mono>{r.report_id}</Mono>
                <span>· {words(r.kind)}</span>
                <span>
                  · {r.filed_by}, {r.filed_at}
                </span>
                {r.desk && (
                  <span className={cx("rounded px-1.5 font-medium", me.desk?.desk_id === r.desk.desk_id ? "bg-pending-soft text-pending-ink" : "bg-sunken")}>
                    {r.desk.name}
                  </span>
                )}
              </span>
            </span>
            <span className="flex flex-wrap items-center gap-1.5">
              {(r.seats_open ?? 0) > 0 && <Pill tone="critical">{plural(r.seats_open ?? 0, "seat")} open</Pill>}
              {(r.plans_waiting ?? 0) > 0 && <Pill tone="pending">{plural(r.plans_waiting ?? 0, "plan")} waiting</Pill>}
              <EvidenceBadge evidence={r.evidence} />
              <SeverityBadge severity={r.severity} />
              <StatusPill value={r.status} />
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function DeskLoadTable({ desks }: { desks: DeskLoad[] }) {
  const { me } = useSession();
  const max = Math.max(1, ...desks.map((d) => d.open + d.in_progress + d.resolved));
  return (
    <ul className="flex flex-col gap-3">
      {desks.map((d) => {
        const active = d.open + d.in_progress + d.resolved;
        return (
          <li key={d.desk_id}>
            <div className="flex items-baseline gap-2 text-sm">
              <b className={cx("font-medium", me.desk?.desk_id === d.desk_id && "text-accent")}>{d.name}</b>
              <span className="text-xs text-muted">{d.controller}</span>
              <span className="ml-auto font-mono text-xs text-muted">
                {active} active · {d.total} total
              </span>
            </div>
            <div className="mt-1.5 flex h-2.5 overflow-hidden rounded-full bg-sunken" title={`${d.open} open, ${d.in_progress} in progress, ${d.resolved} awaiting sign-off`}>
              <i className="block h-full bg-critical" style={{ width: `${(d.open / max) * 100}%` }} />
              <i className="block h-full bg-accent" style={{ width: `${(d.in_progress / max) * 100}%` }} />
              <i className="block h-full bg-pending" style={{ width: `${(d.resolved / max) * 100}%` }} />
            </div>
          </li>
        );
      })}
      <li className="flex flex-wrap gap-3 pt-1 text-xs text-muted">
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-critical" /> open</span>
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-accent" /> in progress</span>
        <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-pending" /> awaiting sign-off</span>
      </li>
    </ul>
  );
}

// -- what a disruption does --------------------------------------------------------------------

export function DelayView({ r }: { r: DelayResult }) {
  const over = r.crew_over_a_limit ?? [];
  return (
    <div className="flex flex-col gap-3">
      {over.length ? (
        <Notice tone="error">
          <b>{plural(over.length, "crew member")} over a limit</b> if <Mono>{r.trip_id}</Mono> leaves {r.delay_hours}h late on {short(r.day)}.
        </Notice>
      ) : (
        <Notice tone="ok">
          Everyone on <Mono>{r.trip_id}</Mono> stays legal {r.delay_hours}h late.
        </Notice>
      )}
      {over.length > 0 && (
        <div className="rounded-md border border-line">
          <Table>
            <thead>
              <tr>
                <th>Crew</th>
                <th>Rules broken</th>
              </tr>
            </thead>
            <tbody>
              {over.map((b) => (
                <tr key={b.crew_id}>
                  <td>
                    {b.name} <Mono className="text-faint">{b.crew_id}</Mono>
                    {b.rank && <span className="block text-xs text-muted">{b.rank}</span>}
                  </td>
                  <td>
                    <span className="flex flex-wrap gap-1">{b.rules.map((x) => <Pill key={x} tone="critical">{x}</Pill>)}</span>
                    {b.details && <span className="mt-1 block text-xs text-muted">{b.details.join("; ")}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {r.crew_illegal_even_on_time && <p className="text-xs text-muted">Illegal even on time (another problem): {r.crew_illegal_even_on_time.join(", ")}</p>}
      <p className="text-xs text-muted">
        {r.passengers} passengers · delay cost <span className="font-mono">{inr(r.delay_cost_inr)}</span>
      </p>
    </div>
  );
}

export function GatePlanView({ r }: { r: GatePlan }) {
  const moves = r.moves ?? [];
  const bus = moves.filter((m) => m.delay_minutes);
  return (
    <div className="flex flex-col gap-3">
      {bus.length ? (
        <Notice tone="pending">
          <b>{plural(bus.length, "flight")} {bus.length === 1 ? "boards" : "board"} by bus</b> from a remote stand; the extra time is checked against crew duty.
        </Notice>
      ) : moves.length ? (
        <Notice tone="ok">Every flight keeps a gate; no delay.</Notice>
      ) : (
        <Notice>No departure holds those gates in that window.</Notice>
      )}
      {moves.length > 0 && (
        <div className="rounded-md border border-line">
          <Table>
            <thead>
              <tr>
                <th>Flight</th>
                <th>From</th>
                <th>To</th>
                <th className="text-right">Departs</th>
                <th className="text-right">Pax</th>
              </tr>
            </thead>
            <tbody>
              {moves.map((m) => (
                <tr key={m.leg_id}>
                  <td>
                    <Mono>{m.flight}</Mono>
                    {m.trip_id && <Mono className="ml-1 text-faint">{m.trip_id}</Mono>}
                  </td>
                  <td>{m.from_gate}</td>
                  <td>
                    <Pill tone={m.to === "remote" ? "critical" : m.to === "emergency" ? "pending" : "ok"}>
                      {m.to_gate} · {m.to}
                    </Pill>
                    {m.other_free_gates && <span className="mt-0.5 block text-xs text-faint">also free: {m.other_free_gates.join(", ")}</span>}
                  </td>
                  <td className="text-right font-mono">
                    {hhmm(m.departs)}
                    {m.delay_minutes > 0 && <Pill tone="critical" className="ml-1">+{m.delay_minutes}m bus</Pill>}
                  </td>
                  <td className="text-right font-mono">{m.passengers}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-xs text-muted">
        {r.flights_moved} moved · {r.passengers_moved} passengers
      </p>
      {r.crew_impact?.length > 0 && (
        <div className="flex flex-col gap-3 border-t border-rule pt-3">
          <h3 className="text-sm font-semibold">Crew duty</h3>
          {r.crew_impact.map((d) => (
            <DelayView key={`${d.trip_id}-${d.day}`} r={d} />
          ))}
        </div>
      )}
    </div>
  );
}

/** 95 -> "1h 35m", 8100 -> "5d 15h". */
function lateBy(minutes: number) {
  if (minutes < 60) return `${minutes}m`;
  const d = Math.floor(minutes / 1440);
  const h = Math.floor((minutes % 1440) / 60);
  const m = minutes % 60;
  return d ? `${d}d ${h}h` : m ? `${h}h ${m}m` : `${h}h`;
}

export function HoldView({ r }: { r: HoldResult }) {
  const flights = r.flights_held ?? [];
  return (
    <div className="flex flex-col gap-3">
      <Notice tone={r.crew_over_a_limit ? "error" : "ok"}>
        <b>
          {plural(flights.length, "flight")} held, {r.passengers} passengers.
        </b>{" "}
        {r.crew_over_a_limit ? `${plural(r.crew_over_a_limit, "crew member")} pushed over a limit.` : "No crew pushed over a limit."}
      </Notice>
      {flights.length > 0 && (
        <div className="rounded-md border border-line">
          <Table>
            <thead>
              <tr>
                <th>Flight</th>
                <th>Route</th>
                <th className="text-right">Planned</th>
                <th className="text-right">Leaves</th>
                <th className="text-right">Late</th>
              </tr>
            </thead>
            <tbody>
              {flights.map((f) => (
                <tr key={f.leg_id}>
                  <td><Mono>{f.flight}</Mono></td>
                  <td>{f.route}</td>
                  <td className="text-right font-mono">{hhmm(f.planned)}</td>
                  <td className="text-right font-mono">
                    {f.leaves.slice(0, 10) !== f.planned.slice(0, 10) && <span className="text-muted">{short(f.leaves.slice(0, 10))} </span>}
                    {hhmm(f.leaves)}
                  </td>
                  <td className="text-right font-mono">{lateBy(f.late_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
      {(r.trips ?? []).map((t) => (
        <details key={`${t.trip_id}-${t.day}`} className="rounded-md border border-line">
          <summary className="flex items-center gap-2 px-3 py-2 text-sm">
            <ChevronDown size={14} className="text-faint" />
            <Mono className="font-medium">{t.trip_id}</Mono> on {short(t.day)}
            {t.crew_over_a_limit.length > 0 && <Pill tone="critical">{plural(t.crew_over_a_limit.length, "over a limit", "over a limit")}</Pill>}
          </summary>
          <div className="border-t border-rule p-3">
            <DelayView r={t} />
          </div>
        </details>
      ))}
    </div>
  );
}

export function GateChangeView({ r }: { r: GateChangeResult }) {
  return r.free ? (
    <Notice tone="ok">
      Gate <b>{r.wanted_gate}</b> is free for <Mono>{r.flight}</Mono>&apos;s boarding window at {r.station}. Move it from {r.current_gate}.
    </Notice>
  ) : (
    <div className="flex flex-col gap-2">
      <Notice tone="error">
        Gate <b>{r.wanted_gate}</b> is held by {(r.held_by ?? []).join(", ")} while <Mono>{r.flight}</Mono> boards.
      </Notice>
      {r.free_gates?.length ? (
        <p className="flex flex-wrap items-center gap-1.5 text-sm">
          Free instead: {r.free_gates.map((g) => <Pill key={g} tone="ok">{g}</Pill>)}
        </p>
      ) : (
        <p className="text-sm text-muted">No ordinary gate is free for the whole window.</p>
      )}
    </div>
  );
}
