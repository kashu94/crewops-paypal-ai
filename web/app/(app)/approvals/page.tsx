"use client";

import { CheckCheck, History, ListChecks, Radio, ShieldCheck, Undo2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { CalloutList, PlanList } from "@/components/ops";
import { Part } from "@/components/part";
import { useSession } from "@/components/session";
import { Handover } from "@/components/handover";
import { Button, control, cx, Empty, Loading, Mono, Notice, PageHeader, Panel, SeverityBadge } from "@/components/ui";
import { tool } from "@/lib/api";
import { plural } from "@/lib/format";
import { settle } from "@/lib/settle";
import type { Callout, Plan, ReportSummary } from "@/lib/types";
import { useAction, useLoad } from "@/lib/use-load";

function Resolution({ r, canDecide }: { r: ReportSummary; canDecide: boolean }) {
  const { flash } = useSession();
  const { busy, run } = useAction();
  const [note, setNote] = useState("");
  return (
    <li className="px-4 py-3 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={`/disruptions/view/?id=${encodeURIComponent(r.report_id)}`} className="text-sm font-medium hover:text-accent">
          {r.title}
        </Link>
        <SeverityBadge severity={r.severity} />
        <Mono className="ml-auto text-xs text-faint">{r.report_id}</Mono>
      </div>
      <p className="mt-0.5 text-xs text-muted">
        {r.desk ? `${r.desk.name} (${r.desk.controller})` : "No desk"} says it is resolved. Filed by {r.filed_by}, {r.filed_at}.
      </p>
      {r.resolution_note && <p className="mt-1.5 text-sm">{r.resolution_note}</p>}
      {(r.changes ?? []).length > 0 && (
        <div className="mt-1.5 rounded-md border border-line bg-sunken px-3 py-2 text-[13px]">
          <b className="block font-medium">Confirming changes these flights for everyone:</b>
          <ul className="mt-0.5 list-disc pl-5 text-muted">
            {(r.changes ?? []).map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      )}
      {canDecide && (
        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={busy !== null} onClick={() => run("confirm", () => tool("confirm_resolution", { report_id: r.report_id }), () => `${r.report_id} closed.`)}>
            <CheckCheck size={14} /> Confirm and close
          </Button>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What is still missing" className={cx(control, "h-7 min-w-40 flex-1 text-[13px]")} />
          <Button
            size="sm"
            tone="secondary"
            disabled={busy !== null}
            onClick={() => {
              if (note.trim().length < 3) return flash("Say what is still missing.", "pending");
              run("return", () => tool("return_resolution", { report_id: r.report_id, note: note.trim() }), () => `${r.report_id} returned to the desk.`);
            }}
          >
            <Undo2 size={14} /> Return to desk
          </Button>
        </div>
      )}
    </li>
  );
}

export default function ApprovalsPage() {
  const { me, can } = useSession();
  const { data, error } = useLoad(async () => {
    const [plans, callouts, resolved] = await Promise.all([
      settle(tool<{ plans: Plan[] }>("plan_status", { waiting_only: false })),
      settle(tool<{ callouts: Callout[] }>("callout_status", {})),
      can("disruptions") ? settle(tool<{ disruptions: ReportSummary[] }>("disruptions", { status: "resolved", limit: 30 })) : Promise.resolve(null),
    ]);
    return { plans, callouts, resolved };
  });
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;

  const all = data.plans.ok ? data.plans.value.plans ?? [] : [];
  const waiting = all.filter((p) => p.status === "submitted");
  const decided = all.filter((p) => p.status !== "submitted");
  const desk = data.callouts.ok ? (data.callouts.value.callouts ?? []).filter((c) => ["needs_decision", "awaiting_approval"].includes(c.status)) : [];
  const resolutions = data.resolved?.ok ? data.resolved.value.disruptions ?? [] : [];
  const approver = me.role === "approver";
  const nothing = data.plans.ok && !waiting.length && !desk.length && !resolutions.length;

  return (
    <div className="animate-rise">
      <PageHeader
        title="Approvals"
        lede="Every roster change needs two people: a controller submits it, an approver decides it. The rules are checked again at approval; a plan made against an older roster goes stale and changes nothing. A disruption is closed only when an approver confirms the desk's resolution."
        actions={<Handover />}
      />
      {!approver && (
        <Notice className="mb-5" icon={<ShieldCheck size={15} />}>
          {me.role === "controller" ? "You can see what waits and withdraw your own plans; an approver decides them." : "Read only: an approver decides these."}
        </Notice>
      )}
      {nothing && (
        <Notice tone="ok" className="mb-5">
          Nothing is waiting for a second pair of eyes.
        </Notice>
      )}
      <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Panel flush title="Plans waiting for an approver" icon={<ListChecks size={15} />} action={plural(waiting.length, "plan")}>
            <Part of={data.plans}>{() => <PlanList plans={waiting} empty="No plans waiting." />}</Part>
          </Panel>
          {data.resolved && (
            <Panel flush title="Resolutions to confirm" icon={<CheckCheck size={15} />} action={plural(resolutions.length, "report")}>
              <Part of={data.resolved}>
                {() =>
                  resolutions.length ? (
                    <ul className="divide-y divide-rule">
                      {resolutions.map((r) => (
                        <Resolution key={r.report_id} r={r} canDecide={approver && can("confirm_resolution")} />
                      ))}
                    </ul>
                  ) : (
                    <Empty>No desk is waiting for sign-off.</Empty>
                  )
                }
              </Part>
            </Panel>
          )}
          {desk.length > 0 && (
            <Panel flush title="Callouts with nobody left to ask" icon={<Radio size={15} />} action="never cancelled automatically">
              <CalloutList callouts={desk} />
            </Panel>
          )}
        </div>
        <Panel flush title="Decided" icon={<History size={15} />} className="self-start">
          <Part of={data.plans}>{() => <PlanList plans={decided.slice(0, 12)} empty="No decisions yet." />}</Part>
        </Panel>
      </div>
    </div>
  );
}
