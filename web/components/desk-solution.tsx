"use client";

import { ArrowRight, CheckCheck, Clock, Phone, Plane, Radio, Send, UserPlus, XCircle } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { tool } from "@/lib/api";
import { hm, inr, plural, short } from "@/lib/format";
import type { Callout, Choice, CrewAnalysis, Plan, Report } from "@/lib/types";
import { useAction } from "@/lib/use-load";

import { Confirm } from "./modal";
import { PassengerMessage } from "./passenger-message";
import { RiskBadge } from "./ops";
import { useSession } from "./session";
import { Button, control, cx, Mono, Notice, SeverityBadge, StatusPill, TextArea } from "./ui";

const LIVE_CALLOUT = ["offered", "accepted", "needs_decision", "awaiting_approval"];
const LIVE_PLAN = ["submitted", "approved"];

type Ask = { title: string; body: ReactNode; action: string; run: () => void };

/** One confirmation at a time for the panel: nothing goes to an approver or a crew member
 * without a second click. */
function useAsk() {
  const [ask, setAsk] = useState<Ask | null>(null);
  const dialog = (
    <Confirm
      open={ask !== null}
      title={ask?.title ?? ""}
      action={ask?.action ?? "Confirm"}
      onClose={() => setAsk(null)}
      onConfirm={() => {
        ask?.run();
        setAsk(null);
      }}
    >
      {ask?.body}
    </Confirm>
  );
  return { setAsk, dialog };
}

/** Where one seat stands: the callout and the plan for it. A rejected plan frees the seat. */
function seatState(callouts: Callout[], plans: Plan[], trip: string, replacing: string) {
  const callout = [...callouts].reverse().find((c) => c.trip_id === trip && c.replacing === replacing);
  const plan = [...plans].reverse().find((p) => p.trip_id === trip && p.replacing === replacing);
  const livePlan = plan && LIVE_PLAN.includes(plan.status) ? plan : undefined;
  const liveCallout = callout && LIVE_CALLOUT.includes(callout.status) ? callout : undefined;
  return { callout, plan, livePlan, liveCallout, busy: Boolean(livePlan || liveCallout) };
}

function CalloutLine({ c, mine }: { c: Callout; mine: boolean }) {
  const { busy, run } = useAction();
  const { setAsk, dialog } = useAsk();
  const gone = c.asked.filter((o) => ["declined", "no answer", "unavailable", "not approved", "no longer needed"].includes(o.answer));
  const waiting = c.waiting_for ? c.asked.find((o) => o.crew_id === c.waiting_for) : null;
  const accepted = c.asked.find((o) => o.answer === "accepted");
  const phone = (accept: boolean) =>
    waiting &&
    setAsk({
      title: accept ? `${waiting.name} accepted by phone` : `${waiting.name} declined by phone`,
      body: accept ? (
        <>
          Record that <b>{waiting.name}</b> accepted {c.trip_id} as {c.seat} on the phone. A plan goes to an approver; the roster changes only when it is approved.
        </>
      ) : (
        <>
          Record that <b>{waiting.name}</b> declined on the phone. The seat is offered to the next legal crew member at once.
        </>
      ),
      action: accept ? "Record acceptance" : "Record decline",
      run: () =>
        run(`phone-${c.callout_id}`, () => tool("answer_callout", { callout_id: c.callout_id, accept, crew_id: waiting.crew_id, note: "answered by phone" }), () =>
          accept ? `${waiting.name}'s acceptance recorded: it goes to an approver.` : `${waiting.name}'s decline recorded: the next crew member is being asked.`,
        ),
    });
  return (
    <div className="flex flex-col gap-1 text-xs">
      {gone.map((o) => (
        <span key={o.crew_id} className="flex items-start gap-1.5 text-muted">
          <XCircle size={12} className="mt-px shrink-0 text-critical" />
          <span>
            {o.name}{" "}
            {o.answer === "no answer"
              ? "did not answer in time"
              : o.answer === "unavailable"
                ? "reported they cannot fly"
                : o.answer === "not approved"
                  ? "accepted, not approved"
                  : o.answer === "no longer needed"
                    ? "no longer needed"
                    : "declined"}
            {o.remark ? <i>: “{o.remark}”</i> : null}
          </span>
        </span>
      ))}
      {waiting && (
        <span className="flex flex-wrap items-center gap-1.5 text-pending-ink">
          <Clock size={12} /> Offered to {waiting.name}
          {c.answer_by ? `, answer by ${short(c.answer_by).slice(-5)}` : ""}
          {mine && (
            <span className="ml-1 inline-flex gap-1">
              <button disabled={busy !== null} onClick={() => phone(true)} className="inline-flex items-center gap-1 rounded border border-control bg-surface px-1.5 py-0.5 text-[11px] text-foreground hover:bg-sunken">
                <Phone size={10} /> Accepted
              </button>
              <button disabled={busy !== null} onClick={() => phone(false)} className="inline-flex items-center gap-1 rounded border border-control bg-surface px-1.5 py-0.5 text-[11px] text-foreground hover:bg-sunken">
                <Phone size={10} /> Declined
              </button>
            </span>
          )}
        </span>
      )}
      {accepted && c.status === "accepted" && (
        <span className="flex items-center gap-1.5 text-ok-ink">
          <CheckCheck size={12} /> {accepted.name} accepted: waits for an approver
        </span>
      )}
      {c.status === "needs_decision" && <span className="font-medium text-critical-ink">Nobody left to ask: propose cancelling or flying short on the full report.</span>}
      {c.status === "awaiting_approval" && <span className="text-pending-ink">A cancellation or flying short waits for an approver.</span>}
      {dialog}
    </div>
  );
}

/** The ranked ways to fill one seat, each a plan the controller can send to an approver. */
function SeatChoices({ choices, report, trip, replacing, holder, open }: { choices: Choice[]; report: Report; trip: string; replacing: string; holder: string; open: boolean }) {
  const { busy, run } = useAction();
  const { setAsk, dialog } = useAsk();
  if (!choices.length) return <p className="mt-2 text-sm text-critical-ink">No legal crew for this seat: see every choice, including cancelling, on the full report.</p>;
  return (
    <ol className="mt-2 divide-y divide-rule overflow-hidden rounded-lg border border-line">
      {choices.map((c, i) => (
        <li key={c.crew_id} className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm", i === 0 && "bg-accent-soft/40")}>
          <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-foreground font-mono text-[10px] font-semibold text-white">{i + 1}</span>
          <span className="min-w-0 flex-1">
            <b className="font-medium">{c.name}</b> <Mono className="text-faint">{c.crew_id}</Mono>
            <span className="block text-xs text-muted">
              {c.way}
              {c.rank ? ` · ${c.rank}` : ""}
              {c.departure_late_hours ? ` · departs ${hm(c.departure_late_hours)} late` : ""}
            </span>
          </span>
          {c.risk && <RiskBadge risk={c.risk} compact />}
          <span className="w-20 text-right font-mono font-semibold">{inr(c.cost_inr)}</span>
          {open && (
            <Button
              size="sm"
              tone={i === 0 ? "accent" : "secondary"}
              disabled={busy !== null}
              onClick={() =>
                setAsk({
                  title: "Submit this plan?",
                  body: (
                    <>
                      <b>{c.name}</b> ({c.crew_id}) for {holder}&apos;s seat on <Mono>{trip}</Mono>, {c.way.toLowerCase()}, {inr(c.cost_inr)}. It goes to an approver; nothing on the roster
                      changes until they approve it. No callout is made for this seat while it waits.
                    </>
                  ),
                  action: "Submit to an approver",
                  run: () =>
                    run(
                      `plan-${trip}-${replacing}`,
                      async () => {
                        const t = await tool<{ version: number }>("trip_details", { trip_id: trip });
                        return tool<Plan>("assign_crew", {
                          trip_id: trip,
                          replace_crew_id: replacing,
                          with_crew_id: c.crew_id,
                          expected_version: t.version,
                          reason: `cover for ${replacing}`,
                          report_id: report.report_id,
                        });
                      },
                      (p) => `${p.plan_id} sent to an approver.`,
                    ),
                })
              }
            >
              <Send size={12} /> Submit plan
            </Button>
          )}
        </li>
      ))}
      {dialog}
    </ol>
  );
}

function Absence({ report, mine }: { report: Report; mine: boolean }) {
  const { busy, run } = useAction();
  const { setAsk, dialog } = useAsk();
  const a = report.analysis as unknown as CrewAnalysis | null;
  const seats = a?.seats ?? [];
  const working = ["open", "in_progress"].includes(report.status);
  return (
    <>
      <ul className="flex flex-col gap-3">
        {seats.map((s) => {
          const st = seatState(report.callouts, report.plans, s.trip_id, s.replacing);
          const open = mine && working && !st.busy && !s.started;
          return (
            <li key={`${s.trip_id}-${s.replacing}`} className={cx("rounded-xl border p-3.5", st.busy ? "border-line bg-sunken/50" : "border-line bg-surface")}>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Mono className="font-semibold">{s.trip_id}</Mono>
                <span className="font-medium">{s.rank}</span>
                <span className="text-muted">
                  for {s.name} <Mono className="text-faint">{s.replacing}</Mono>
                </span>
                <span className="ml-auto font-mono text-xs text-faint">reports {short(s.reports_at)}</span>
              </div>
              {st.livePlan ? (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <StatusPill value={st.livePlan.status} />
                  <b className="font-medium">{st.livePlan.name}</b>
                  <Mono className="text-faint">{st.livePlan.crew_id}</Mono>
                  <span className="text-xs text-muted">{st.livePlan.status === "submitted" ? `plan ${st.livePlan.plan_id} waits for an approver` : "approved: on the roster"}</span>
                  <span className="ml-auto font-mono font-semibold">{inr(st.livePlan.cost_inr)}</span>
                </div>
              ) : (
                <SeatChoices choices={s.choices ?? []} report={report} trip={s.trip_id} replacing={s.replacing} holder={s.name} open={open} />
              )}
              <div className="mt-2 flex flex-col gap-1.5">
                {st.callout && <CalloutLine c={st.callout} mine={mine} />}
                {st.plan && !st.livePlan && (
                  <span className="text-xs text-muted">
                    Plan <Mono>{st.plan.plan_id}</Mono> for {st.plan.name} was {st.plan.status.replace("_", " ")}
                    {st.plan.decision_note ? `: ${st.plan.decision_note}` : ""}. Choose again below or call out.
                  </span>
                )}
              </div>
              {mine && working && !s.started && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    tone="secondary"
                    disabled={busy !== null || st.busy}
                    title={st.busy ? "A callout or plan is already in hand for this seat" : "Offer the seat to legal crew, cheapest first"}
                    onClick={() =>
                      setAsk({
                        title: "Start a callout?",
                        body: (
                          <>
                            The {s.rank} seat on <Mono>{s.trip_id}</Mono> is offered to the cheapest legal crew member, then the next, until someone accepts. Each has 20 minutes to answer.
                          </>
                        ),
                        action: "Start the callout",
                        run: () =>
                          run(`co-${s.trip_id}-${s.replacing}`, () => tool("start_callout", { trip_id: s.trip_id, replace_crew_id: s.replacing, report_id: report.report_id }), () => `Callout started for ${s.trip_id}.`),
                      })
                    }
                  >
                    <Radio size={13} />{" "}
                    {st.livePlan?.status === "approved" ? "Covered" : st.livePlan ? "With an approver" : st.liveCallout ? "Callout running" : "Start callout"}
                  </Button>
                  <Link href={`/cover/?trip=${s.trip_id}&crew=${s.replacing}&report=${encodeURIComponent(report.report_id)}`}>
                    <Button size="sm" tone="ghost">
                      <UserPlus size={13} /> Every choice
                    </Button>
                  </Link>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {report.can?.plan && seats.length > 1 && (
        <Button
          disabled={busy !== null}
          onClick={() =>
            setAsk({
              title: "Submit the cheapest plan for every free seat?",
              body: <>Each seat not already in hand gets the cheapest combination that uses nobody twice: {inr(a?.plan?.total_cost ?? 0)} in all. Each plan goes to an approver.</>,
              action: "Submit to an approver",
              run: () =>
                run(
                  "plan",
                  () => tool<{ plans: unknown[]; skipped?: { seat: string; why: string }[] }>("submit_report_plan", { report_id: report.report_id }),
                  (r) =>
                    r.plans?.length
                      ? `${plural(r.plans.length, "plan")} sent to an approver.`
                      : { text: `Nothing submitted: ${(r.skipped ?? []).map((x) => `${x.seat} ${x.why}`).join("; ") || "every seat is already in hand"}.`, tone: "pending" },
                ),
            })
          }
          className="self-start"
        >
          <Send size={14} /> Submit the cheapest plan for every free seat
        </Button>
      )}
      {dialog}
    </>
  );
}

type AircraftOption = { way: string; summary: string; cost_inr: number; tail?: string; note?: string };
type Move = { flight: string; leg_id: string; from_gate: string; to_gate: string; to: string; departs: string; passengers: number; other_free_gates?: string[] };

/** Choosing how an operational disruption is handled: the choice goes to an approver as the
 * resolution, and the approver confirms it or sends it back. */
function Operational({ report, mine }: { report: Report; mine: boolean }) {
  const { busy, run } = useAction();
  const { setAsk, dialog } = useAsk();
  const a = (report.analysis ?? {}) as { options?: AircraftOption[]; moves?: Move[]; flights_taken?: string[] };
  const [gates, setGates] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const working = ["open", "in_progress"].includes(report.status);
  const resolve = (text: string, title: string, choice: Record<string, unknown> = {}, changes: string[] = []) =>
    setAsk({
      title,
      body: (
        <>
          <p>{text}</p>
          {changes.length > 0 && (
            <>
              <p className="mt-2 font-medium">When an approver approves it:</p>
              <ul className="mt-1 list-disc pl-5">
                {changes.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-2 text-muted">
            It goes to an approver, who confirms it and closes the report, or sends it back.{changes.length > 0 && " Nothing changes on the flights until then."}
          </p>
        </>
      ),
      action: "Send to an approver",
      run: () => run("resolve", () => tool("close_disruption", { report_id: report.report_id, note: text, ...choice }), () => "Sent to an approver for sign-off."),
    });
  const aircraftChanges = (o: AircraftOption) =>
    o.way === "swap" || o.way === "spare"
      ? (a.flights_taken ?? []).map((f) => `${f} flies on ${o.tail}`)
      : o.way === "cancel"
        ? (a.flights_taken ?? []).map((f) => `${f} cancelled`)
        : [];

  if (report.status === "resolved")
    return (
      <Notice tone="pending">
        Waiting for an approver to sign off: {report.resolution_note}
        {(report.changes ?? []).length > 0 && <span className="mt-1 block">On approval: {(report.changes ?? []).join("; ")}.</span>}
      </Notice>
    );

  return (
    <div className="flex flex-col gap-3">
      {report.kind === "aircraft_technical" && (a.options ?? []).length > 0 && (
        <>
          <p className="text-xs text-muted">
            Ways to fly {(a.flights_taken ?? []).join(", ")}, out and back, best first:
          </p>
          <ol className="divide-y divide-rule overflow-hidden rounded-lg border border-line">
            {(a.options ?? []).map((o, i) => (
              <li key={o.summary} className={cx("flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5 text-sm", i === 0 && "bg-accent-soft/40", o.way === "cancel" && "bg-sunken/60")}>
                <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-foreground font-mono text-[10px] font-semibold text-white">{i + 1}</span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 font-medium">
                    <Plane size={13} className="text-faint" /> {o.summary}
                  </span>
                  {o.note && <span className="block text-xs text-muted">{o.note}</span>}
                </span>
                <span className={cx("w-24 text-right font-mono font-semibold", o.way === "cancel" && "text-critical")}>{inr(o.cost_inr)}</span>
                {mine && working && (
                  <Button size="sm" tone={i === 0 ? "accent" : "secondary"} disabled={busy !== null} onClick={() => resolve(`Plan: ${o.summary}.`, "Submit this plan?", { option: i }, aircraftChanges(o))}>
                    <Send size={12} /> Submit plan
                  </Button>
                )}
              </li>
            ))}
          </ol>
        </>
      )}
      {report.kind === "gate_closure" && (a.moves ?? []).length > 0 && (
        <>
          <p className="text-xs text-muted">Where each flight boards instead. Pick a gate for each, then submit them as one plan.</p>
          <ul className="divide-y divide-rule overflow-hidden rounded-lg border border-line">
            {(a.moves ?? []).map((m) => {
              const choices = [m.to_gate, ...(m.other_free_gates ?? [])];
              return (
                <li key={m.leg_id} className="flex flex-wrap items-center gap-3 px-3 py-2.5 text-sm">
                  <Mono className="font-semibold">{m.flight}</Mono>
                  <span className="text-muted">departs {short(m.departs).slice(-5)} · {m.passengers} pax · from gate {m.from_gate}</span>
                  <select
                    aria-label={`New gate for ${m.flight}`}
                    disabled={!mine || !working}
                    value={gates[m.leg_id] ?? m.to_gate}
                    onChange={(e) => setGates((g) => ({ ...g, [m.leg_id]: e.target.value }))}
                    className={cx(control.replace("w-full", ""), "ml-auto h-8 w-40")}
                  >
                    {choices.map((g) => (
                      <option key={g} value={g}>
                        Gate {g}
                        {g === m.to_gate && m.to !== "contact" ? ` (${m.to})` : ""}
                      </option>
                    ))}
                  </select>
                </li>
              );
            })}
          </ul>
          {mine && working && (
            <Button
              className="self-start"
              disabled={busy !== null}
              onClick={() => {
                const moved = (a.moves ?? []).map((m) => `${m.flight} boards at gate ${gates[m.leg_id] ?? m.to_gate}`);
                resolve(`Plan: ${moved.join("; ")}.`, "Submit these gates?", { gates: Object.fromEntries((a.moves ?? []).map((m) => [m.leg_id, gates[m.leg_id] ?? m.to_gate])) }, moved);
              }}
            >
              <Send size={14} /> Submit the gates
            </Button>
          )}
        </>
      )}
      {mine && working && !["aircraft_technical"].includes(report.kind) && !(report.kind === "gate_closure" && (a.moves ?? []).length) && (
        <div className="flex flex-col gap-2">
          <TextArea label="What was done" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Such as: gate change announced, passengers informed" />
          <Button tone="secondary" className="self-start" disabled={busy !== null || note.trim().length < 3} onClick={() => resolve(note.trim(), "Mark it resolved?")}>
            <CheckCheck size={14} /> Mark resolved
          </Button>
        </div>
      )}
      {dialog}
    </div>
  );
}

/** The selected disruption's solution: every seat or flight with its ranked options and where
 * it stands, and only the actions this controller can take now. */
export function DeskSolution({ report, header = true }: { report: Report; header?: boolean }) {
  const { me } = useSession();
  const mine = me.desk?.desk_id === report.desk?.desk_id && me.role === "controller";
  const crew = ["crew_unavailable", "planned_leave", "certification_lapse"].includes(report.kind);
  const link = `/disruptions/view/?id=${encodeURIComponent(report.report_id)}`;

  return (
    <div className="flex flex-col gap-4">
      {header && (
        <>
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1">
              <p className="text-xs text-muted">
                <Mono>{report.report_id}</Mono> · {report.label ?? report.kind} · filed by {report.filed_by}
              </p>
              <h2 className="mt-0.5 text-lg font-semibold leading-snug">{report.title}</h2>
            </div>
            <SeverityBadge severity={report.severity} />
            <StatusPill value={report.status} />
          </div>
          <p className="text-sm text-muted">{report.summary}</p>
        </>
      )}
      {!mine && report.desk && me.role === "controller" && (
        <Notice tone="pending">
          With {report.desk.name} ({report.desk.controller}). You can read it; planning it is theirs.
        </Notice>
      )}
      {report.status === "resolved" && crew && <Notice tone="pending">Waiting for an approver to sign off: {report.resolution_note}</Notice>}
      {!crew && header && me.role !== "crew" && (
        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-sunken px-3 py-2 text-xs text-muted">
          <span className="flex-1">Passengers on these flights need to hear about it.</span>
          <PassengerMessage reportId={report.report_id} />
        </div>
      )}
      {crew ? <Absence report={report} mine={mine} /> : <Operational report={report} mine={mine} />}
      {crew && mine && report.can?.close && (report.seats_open ?? 1) === 0 && <ResolveCrew report={report} />}
      {header && (
        <Link href={link} className="ml-auto inline-flex items-center gap-1 text-sm text-accent hover:underline">
          Full report <ArrowRight size={13} />
        </Link>
      )}
    </div>
  );
}

function ResolveCrew({ report }: { report: Report }) {
  const { busy, run } = useAction();
  const { setAsk, dialog } = useAsk();
  const approved = report.plans.filter((p) => p.status === "approved").length;
  const waiting = report.plans.filter((p) => p.status === "submitted").length;
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-ok-soft px-3 py-2.5 text-sm text-ok-ink">
      <CheckCheck size={15} />
      <span className="flex-1">
        Every seat is in hand{waiting ? `; ${plural(waiting, "plan")} still with an approver` : ""}.
      </span>
      <Button
        size="sm"
        disabled={busy !== null || waiting > 0}
        title={waiting ? "Wait for the approver's decision first" : undefined}
        onClick={() =>
          setAsk({
            title: "Mark it resolved?",
            body: <>Every seat is covered ({plural(approved, "approved plan")}). An approver confirms the resolution and closes the report.</>,
            action: "Send to an approver",
            run: () => run("close", () => tool("close_disruption", { report_id: report.report_id, note: `Every seat covered: ${plural(approved, "approved plan")}.` }), () => "Sent to an approver for sign-off."),
          })
        }
      >
        Mark resolved
      </Button>
      {dialog}
    </div>
  );
}

