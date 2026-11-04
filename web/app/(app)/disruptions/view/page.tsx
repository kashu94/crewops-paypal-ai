"use client";

import {
  Camera,
  ArrowLeft,
  Building2,
  CheckCheck,
  ChevronDown,
  ClipboardList,
  Cpu,
  History,
  ListChecks,
  Phone,
  Radio,
  Shuffle,
  Undo2,
  Users,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState, type ReactNode } from "react";

import { CalloutList, DelayView, EvidenceBadge, GateChangeView, GatePlanView, HoldView, PlanList, RiskBadge } from "@/components/ops";
import { DeskSolution } from "@/components/desk-solution";
import { PassengerMessage } from "@/components/passenger-message";
import { EvidencePhoto } from "@/components/photo-intake";
import { useSession } from "@/components/session";
import { Button, cx, Dl, Empty, Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill, Select, SeverityBadge, StatusPill, Table, TextArea } from "@/components/ui";
import { tool } from "@/lib/api";
import { hhmm, inr, kindLabel, plural, short, words } from "@/lib/format";
import type { CrewAnalysis, DelayResult, DeskLoad, EvidenceItem, GateChangeResult, GatePlan, HoldResult, Report } from "@/lib/types";
import { useAction, useLoad } from "@/lib/use-load";

const STEPS = [
  ["open", "Reported"],
  ["in_progress", "Being worked"],
  ["resolved", "Resolved by the desk"],
  ["closed", "Confirmed and closed"],
] as const;

function Lifecycle({ status }: { status: string }) {
  if (status === "revoked")
    return (
      <Notice tone="error" icon={<XCircle size={15} />}>
        Revoked by an admin. Any absence it recorded has been withdrawn; nothing more happens to it.
      </Notice>
    );
  const at = STEPS.findIndex(([s]) => s === status);
  return (
    <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Where the report stands">
      {STEPS.map(([s, label], i) => {
        const done = i < at || status === "closed";
        const now = i === at && status !== "closed";
        return (
          <li key={s} className={cx("rounded-md border px-3 py-2", now ? "border-accent bg-accent-soft" : done ? "border-line bg-surface" : "border-dashed border-line bg-sunken")}>
            <span className="flex items-center gap-2">
              <span className={cx("grid h-5 w-5 place-items-center rounded-full font-mono text-[11px] font-semibold", done ? "bg-ok text-white" : now ? "bg-accent text-white" : "bg-active text-muted")}>
                {done ? "✓" : i + 1}
              </span>
              <span className={cx("text-[13px]", now ? "font-semibold text-accent-ink" : done ? "text-foreground" : "text-muted")}>{label}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function CrewWork({ a }: { a: CrewAnalysis }) {
  const fills = a.plan?.fills ?? [];
  const unfilled = a.plan?.unfilled ?? [];
  const fillFor = (trip: string, replacing: string) => fills.find((f) => f.trip_id === trip && f.replacing === replacing);
  const whyFor = (trip: string, replacing: string) => unfilled.find((u) => u.trip_id === trip && u.replacing === replacing);
  return (
    <>
      <Panel title="Who is out" icon={<Users size={15} />} action={plural(a.crew?.length ?? 0, "person", "people")}>
        <div className="flex flex-wrap gap-2">
          {(a.crew ?? []).map((c) => (
            <span key={c.crew_id} className={cx("inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm", c.back_on_duty ? "border-[#b9e6c9] bg-ok-soft" : "border-line bg-sunken")}>
              <b className={cx("font-medium", c.back_on_duty && "line-through decoration-ok/60")}>{c.name}</b> <Mono className="text-faint">{c.crew_id}</Mono> <span className="text-xs text-muted">{c.rank}</span>
              {c.back_on_duty && <Pill tone="ok">back on duty</Pill>}
            </span>
          ))}
        </div>
      </Panel>
      <Panel
        flush
        title="Seats and the joint plan"
        icon={<Cpu size={15} />}
        action={<>covered together · nobody fills two · total <span className="font-mono text-foreground">{inr(a.plan?.total_cost)}</span></>}
      >
        {(a.seats ?? []).length === 0 ? (
          <Empty>No rostered seats in those dates.</Empty>
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Trip</th>
                <th>Seat</th>
                <th>Cover</th>
                <th>Risk</th>
                <th className="text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {a.seats.map((s) => {
                const f = fillFor(s.trip_id, s.replacing);
                const why = whyFor(s.trip_id, s.replacing);
                return (
                  <tr key={`${s.trip_id}-${s.replacing}-${s.day}`}>
                    <td>
                      <Mono className="font-medium">{s.trip_id}</Mono>
                      <span className="block text-xs text-muted">
                        {short(s.day)} · reports {hhmm(s.reports_at)}
                      </span>
                    </td>
                    <td>
                      {s.rank}
                      <span className="block text-xs text-muted">
                        for {s.name} <Mono>{s.replacing}</Mono>
                      </span>
                    </td>
                    <td>
                      {f ? (
                        <>
                          <b className="font-medium">{f.name}</b> <Mono className="text-faint">{f.crew_id}</Mono>
                          <span className="block text-xs text-muted">{f.summary}</span>
                          {f.in_hand && (
                            <Pill tone="accent" className="mt-1">
                              in hand: <Mono>{f.in_hand}</Mono>
                            </Pill>
                          )}
                        </>
                      ) : (
                        <>
                          <Pill tone="critical">no legal crew</Pill>
                          {why && <span className="mt-0.5 block text-xs text-muted">{why.why}</span>}
                        </>
                      )}
                    </td>
                    <td>{f?.risk ? <RiskBadge risk={f.risk} compact /> : <span className="text-faint">–</span>}</td>
                    <td className="text-right font-mono">{f ? inr(f.cost) : "–"}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
        {(a.seats_open ?? 0) > 0 && <p className="border-t border-rule px-4 py-2 text-xs text-critical">{plural(a.seats_open, "seat")} still open.</p>}
      </Panel>
    </>
  );
}

function Analysis({ report }: { report: Report }) {
  const a = report.analysis as Record<string, unknown> | null;
  if (!a) return <Panel title="Analysis" icon={<Cpu size={15} />}><Empty>Not worked out yet.</Empty></Panel>;
  if (typeof a.error === "string") return <Panel title="Analysis" icon={<Cpu size={15} />}><Notice tone="error">{a.error}</Notice></Panel>;
  const k = report.kind;
  if (["crew_unavailable", "planned_leave", "certification_lapse"].includes(k) && Array.isArray(a.seats)) return <CrewWork a={a as unknown as CrewAnalysis} />;
  let body: ReactNode;
  if (k === "flight_delay" && Array.isArray(a.crew_over_a_limit)) {
    body = (
      <div className="flex flex-col gap-4">
        <DelayView r={a as unknown as DelayResult} />
        {a.gates ? (
          <div className="border-t border-rule pt-4">
            <h3 className="mb-2 text-sm font-semibold">At the gate</h3>
            <GatePlanView r={a.gates as GatePlan} />
          </div>
        ) : null}
      </div>
    );
  } else if ((k === "aircraft_technical" || k === "station_disruption") && Array.isArray(a.flights_held)) body = <HoldView r={a as unknown as HoldResult} />;
  else if (k === "gate_closure" && Array.isArray(a.moves)) body = <GatePlanView r={a as unknown as GatePlan} />;
  else if (k === "gate_change" && "free" in a) body = <GateChangeView r={a as unknown as GateChangeResult} />;
  else body = <pre className="max-h-96 overflow-auto rounded-md bg-sunken p-3 font-mono text-xs">{JSON.stringify(a, null, 2)}</pre>;
  return (
    <Panel title={typeof a.what === "string" ? a.what : "What it does"} icon={<Cpu size={15} />}>
      {body}
    </Panel>
  );
}

function Figures({ report }: { report: Report }) {
  const a = (report.analysis ?? {}) as Record<string, unknown>;
  const k = report.kind;
  if (["crew_unavailable", "planned_leave", "certification_lapse"].includes(k)) {
    const c = a as Partial<CrewAnalysis>;
    return (
      <Kpis>
        <Kpi label="Crew out" value={c.crew?.length ?? 0} />
        <Kpi label="Seats left empty" value={c.seats?.length ?? 0} />
        <Kpi label="Still open" value={c.seats_open ?? 0} tone={c.seats_open ? "critical" : "ok"} />
        <Kpi label="Plans waiting" value={report.plans.filter((p) => p.status === "submitted").length} tone="pending" />
        <Kpi label="Joint plan cost" value={<span className="font-mono text-xl">{inr(c.plan?.total_cost)}</span>} />
      </Kpis>
    );
  }
  const flights = Array.isArray(a.flights_held) ? a.flights_held.length : Array.isArray(a.moves) ? a.moves.length : undefined;
  const over = Array.isArray(a.crew_over_a_limit) ? a.crew_over_a_limit.length : typeof a.crew_over_a_limit === "number" ? a.crew_over_a_limit : undefined;
  const pax = typeof a.passengers === "number" ? a.passengers : typeof a.passengers_moved === "number" ? a.passengers_moved : undefined;
  return (
    <Kpis>
      {flights !== undefined && <Kpi label="Flights affected" value={flights} />}
      {over !== undefined && <Kpi label="Crew over a limit" value={over} tone={over ? "critical" : "ok"} />}
      {pax !== undefined && <Kpi label="Passengers" value={pax.toLocaleString("en-IN")} />}
      <Kpi label="Plans" value={report.plans.length} />
      <Kpi label="Callouts" value={report.callouts.length} />
    </Kpis>
  );
}

function Actions({ report }: { report: Report }) {
  const { flash } = useSession();
  const { busy, run } = useAction();
  const [note, setNote] = useState("");
  const [desk, setDesk] = useState("");
  const can = report.can ?? {};
  const desks = useLoad(() => (can.reassign ? tool<{ desks: DeskLoad[] }>("desk_load").then((d) => d.desks) : Promise.resolve([] as DeskLoad[])), String(Boolean(can.reassign)));
  const id = report.report_id;
  const needNote = (what: string) => {
    if (note.trim().length < 3) {
      flash(what, "pending");
      return false;
    }
    return true;
  };
  const any = Boolean(can.confirm || can.return || can.revoke || can.reassign);
  if (!any)
    return (
      <p className="text-sm text-muted">
        {report.status === "closed" || report.status === "revoked" ? "Nothing more to do: this report is finished." : "Nothing for you to do here; it is shown read-only."}
      </p>
    );
  const desk_options = desks.data ?? [];
  return (
    <div className="flex flex-col gap-3">
      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="A note for the plan, the resolution, a return or a reassignment" />
      {can.confirm && (
        <Button disabled={busy !== null} onClick={() => run("confirm", () => tool("confirm_resolution", { report_id: id }), () => `${id} closed.`)}>
          <CheckCheck size={14} /> Confirm and close
        </Button>
      )}
      {can.return && (
        <Button
          tone="secondary"
          disabled={busy !== null}
          onClick={() => needNote("Say what is still missing.") && run("return", () => tool("return_resolution", { report_id: id, note: note.trim() }), () => "Returned to the desk.")}
        >
          <Undo2 size={14} /> Return to the desk
        </Button>
      )}
      {can.reassign && (
        <div className="flex flex-col gap-2 rounded-md border border-line p-3">
          <Select label="Move to desk" value={desk} onChange={(e) => setDesk(e.target.value)}>
            <option value="">Choose a desk…</option>
            {desk_options
              .filter((d) => d.desk_id !== report.desk?.desk_id)
              .map((d) => (
                <option key={d.desk_id} value={d.desk_id}>
                  {d.name} · {d.controller} ({d.open + d.in_progress} active)
                </option>
              ))}
          </Select>
          {desks.error && <p className="text-xs text-critical">{desks.error}</p>}
          <Button
            tone="secondary"
            size="sm"
            disabled={busy !== null || !desk}
            onClick={() => needNote("Say why it moves.") && run("reassign", () => tool("reassign_disruption", { report_id: id, desk_id: desk, note: note.trim() }), () => "Moved to another desk.")}
          >
            <Shuffle size={14} /> Reassign
          </Button>
        </div>
      )}
      {can.revoke && (
        <Button
          tone="danger-quiet"
          disabled={busy !== null}
          onClick={() => {
            if (!needNote("Say why it is revoked.")) return;
            if (!window.confirm(`Revoke ${id}? Any absence it recorded is withdrawn.`)) return;
            run("revoke", () => tool("revoke_report", { report_id: id, reason: note.trim() }), () => `${id} revoked.`);
          }}
        >
          <XCircle size={14} /> Revoke the report
        </Button>
      )}
    </div>
  );
}

export default function DisruptionView() {
  const id = useSearchParams().get("id") ?? "";
  const { me, can } = useSession();
  const { data: r, error } = useLoad(() => (id ? tool<Report>("show_disruption", { report_id: id }) : Promise.reject(new Error("No report id in the address."))), id);
  const back = (
    <Link href={can("disruptions") ? "/disruptions/" : me.role === "crew" ? "/crew/" : "/disruptions/"} className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-foreground">
      <ArrowLeft size={14} /> {can("disruptions") ? "Disruptions" : me.role === "crew" ? "My roster" : "My reports"}
    </Link>
  );
  if (error)
    return (
      <div>
        {back}
        <Notice tone="error">{error}</Notice>
      </div>
    );
  if (!r)
    return (
      <div>
        {back}
        <Loading />
      </div>
    );
  const yours = me.desk && r.desk?.desk_id === me.desk.desk_id;
  const details = Object.entries(r.details ?? {}).filter(
    ([k, v]) => !["evidence", "evidence_note"].includes(k) && v !== null && v !== "" && !(Array.isArray(v) && !v.length),
  );
  const photos = (r.details?.evidence as EvidenceItem[] | undefined) ?? [];
  const photoNote = r.details?.evidence_note as string | undefined;

  return (
    <div className="animate-rise">
      {back}
      <PageHeader
        eyebrow={
          <span className="flex flex-wrap items-center gap-2">
            <Mono>{r.report_id}</Mono> · {kindLabel(r.kind)} · filed by {r.filed_by}
            {r.filed_role ? ` (${words(r.filed_role)})` : ""}, {r.filed_at}
          </span>
        }
        title={
          <span className="flex flex-wrap items-center gap-2.5">
            {r.title}
            <SeverityBadge severity={r.severity} />
            <StatusPill value={r.status} />
          </span>
        }
        lede={r.summary}
        actions={!["crew_unavailable", "planned_leave", "certification_lapse"].includes(r.kind) && me.role !== "crew" ? <PassengerMessage reportId={r.report_id} /> : undefined}
      />
      <div className="mb-5 flex flex-col gap-3">
        {r.desk && (
          <Notice tone={yours ? "info" : "pending"} icon={<Building2 size={15} />}>
            {yours ? (
              <>On your desk. You work it below; an approver approves each change and confirms the resolution.</>
            ) : (
              <>
                With <b>{r.desk.name}</b> ({r.desk.controller}).{me.role === "controller" ? " You can read it; planning it is theirs." : ""}
              </>
            )}
          </Notice>
        )}
        {r.severity && r.severity.reasons?.length > 0 && (
          <p className="text-xs text-muted">
            <b className="font-semibold text-foreground">Why {r.severity.level} severity:</b> {r.severity.reasons.join("; ")}.
          </p>
        )}
        {r.resolution_note && (
          <Notice tone={r.status === "closed" ? "ok" : "pending"}>
            <b>Resolution:</b> {r.resolution_note}
            {(r.changes ?? []).length > 0 && (
              <span className="mt-1 block">
                {r.status === "closed" ? "Flights changed" : "Flights change when approved"}: {(r.changes ?? []).join("; ")}.
              </span>
            )}
          </Notice>
        )}
        <Lifecycle status={r.status} />
      </div>

      <Figures report={r} />

      {(yours || r.status === "resolved") && (
        <Panel title="Work it" icon={<ClipboardList size={15} />} className="mb-5">
          <DeskSolution report={r} header={false} />
        </Panel>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-5">
          <Analysis report={r} />
          {(r.plans.length > 0 || r.can.plan) && (
            <Panel flush title="Plans" icon={<ListChecks size={15} />} action={plural(r.plans.length, "plan")}>
              <PlanList plans={r.plans} empty="No plans yet. Submit the joint plan or a plan from Find cover." />
            </Panel>
          )}
          {r.callouts.length > 0 && (
            <Panel flush title="Callouts" icon={<Radio size={15} />}>
              <CalloutList callouts={r.callouts} />
            </Panel>
          )}
          <Panel flush title="Teams to tell" icon={<Phone size={15} />} action={plural(r.teams?.length ?? 0, "team")}>
            {(r.teams ?? []).length === 0 ? (
              <Empty>Nobody else needs telling.</Empty>
            ) : (
              <ul className="grid divide-y divide-rule sm:grid-cols-2 sm:divide-y-0">
                {r.teams.map((t) => (
                  <li key={t.team + (t.station ?? "")} className="flex flex-col gap-0.5 border-rule px-4 py-3 sm:border-b sm:px-5 sm:odd:border-r">
                    <span className="flex items-center gap-2 text-sm font-medium">
                      {t.title}
                      {t.station && <Pill>{t.station}</Pill>}
                    </span>
                    <span className="text-xs text-muted">{t.job}</span>
                    {t.contact ? (
                      <span className="mt-1 text-[13px]">
                        {t.contact.name} ·{" "}
                        <a className="font-mono text-accent hover:underline" href={`tel:${t.contact.phone.replace(/\s/g, "")}`}>
                          {t.contact.phone}
                        </a>
                      </span>
                    ) : (
                      <span className="mt-1 text-xs text-faint">No contact on file</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
        <aside className="flex min-w-0 flex-col gap-5">
          <Panel title="Actions" icon={<ClipboardList size={15} />}>
            <Actions report={r} />
          </Panel>
          <Panel title="Evidence" icon={<Camera size={15} />} action={<EvidenceBadge evidence={r.evidence} />}>
            {photos.length === 0 ? (
              <p className="text-sm text-muted">{photoNote ?? "Filed without a photo. The desk confirms it with the reporter or the team on the ground."}</p>
            ) : (
              <ul className="flex flex-col gap-4">
                {photos.map((p) => (
                  <li key={p.evidence_id} className="flex flex-col gap-2">
                    <EvidencePhoto id={p.evidence_id} alt={p.message} />
                    <p className="text-[13px] leading-relaxed">{p.message}</p>
                    {p.check && (
                      <Notice tone={/^The photo (matches|names the person)/.test(p.check) ? "ok" : "pending"} className="text-[13px]">
                        {p.check}
                      </Notice>
                    )}
                    <p className="text-xs text-faint">
                      {words(p.kind)} · checked when it was added{p.confidence ? ` · ${p.confidence} confidence` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          {details.length > 0 && (
            <Panel title="As filed" icon={<ChevronDown size={15} />}>
              <Dl rows={details.map(([k, v]) => [words(k), <span key={k} className="font-mono text-[13px]">{Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}</span>])} />
            </Panel>
          )}
          <Panel title="History" icon={<History size={15} />}>
            {(r.history ?? []).length === 0 ? (
              <p className="text-sm text-muted">Nothing yet.</p>
            ) : (
              <ol className="relative flex flex-col gap-4 border-l border-line pl-4">
                {r.history.map((h, i) => (
                  <li key={i} className="relative">
                    <span className={cx("absolute -left-[21px] top-1 h-2.5 w-2.5 rounded-full ring-2 ring-surface", i === r.history.length - 1 ? "bg-accent" : "bg-control")} />
                    <p className="text-sm font-medium">{h.what}</p>
                    <p className="text-xs text-muted">
                      {h.by} · <span className="font-mono">{h.at}</span>
                    </p>
                    {h.note && <p className="mt-0.5 text-[13px] text-foreground">“{h.note}”</p>}
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </aside>
      </div>
    </div>
  );
}
