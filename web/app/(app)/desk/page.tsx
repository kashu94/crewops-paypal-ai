"use client";

import { ClipboardList, Eye, ListChecks, Radio, TriangleAlert, Users } from "lucide-react";
import { useState } from "react";

import { DeskSolution } from "@/components/desk-solution";
import { Handover } from "@/components/handover";
import { useSession } from "@/components/session";
import { cx, Empty, Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill, SeverityBadge, StatusPill } from "@/components/ui";
import { tool } from "@/lib/api";
import { inr, short } from "@/lib/format";
import { settle } from "@/lib/settle";
import type { Callout, Issue, Report, ReportSummary } from "@/lib/types";
import { BACKDROP } from "@/lib/art";
import { useLoad } from "@/lib/use-load";

const RUNNING = ["offered", "accepted", "needs_decision", "awaiting_approval"];

/** The controller's desk: the disruptions it owns on the left, the one picked worked on the
 * right. Each problem shows once; what the watch finds that nobody has reported yet sits below. */
export default function DeskPage() {
  const { me } = useSession();
  const deskId = me.desk?.desk_id;
  const [picked, setPicked] = useState<string | null>(null);

  const { data, error } = useLoad(
    async () => {
      const [list, issues, callouts] = await Promise.all([
        settle(tool<{ disruptions: ReportSummary[] }>("disruptions", { status: "active", limit: 50 })),
        settle(tool<{ issues: Issue[] }>("open_issues", { limit: 30 })),
        settle(tool<{ callouts: Callout[] }>("callout_status", {})),
      ]);
      return { list, issues, callouts };
    },
    deskId ?? "",
  );

  const all = data?.list.ok ? data.list.value.disruptions ?? [] : [];
  const mine = all.filter((r) => !deskId || r.desk?.desk_id === deskId);
  const others = all.filter((r) => deskId && r.desk?.desk_id !== deskId);
  const chosen = picked && all.some((r) => r.report_id === picked) ? picked : (mine[0] ?? others[0])?.report_id ?? null;

  const detail = useLoad(() => (chosen ? tool<Report>("show_disruption", { report_id: chosen }) : Promise.resolve(null)), chosen ?? "");

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;

  const callouts = data.callouts.ok ? data.callouts.value.callouts ?? [] : [];
  const running = callouts.filter((c) => RUNNING.includes(c.status));
  const reported = new Set(all.flatMap((r) => r.crew_ids ?? []));
  const issues = (data.issues.ok ? data.issues.value.issues ?? [] : []).filter((i) => !reported.has(i.crew_id));
  const waiting = mine.reduce((n, r) => n + (r.plans_waiting ?? 0), 0);
  const signOff = mine.filter((r) => r.status === "resolved").length;

  const Row = ({ r }: { r: ReportSummary }) => (
    <li>
      <button
        onClick={() => setPicked(r.report_id)}
        aria-current={r.report_id === chosen}
        className={cx(
          "flex w-full flex-col gap-1 border-l-[3px] px-4 py-3 text-left transition-colors",
          r.report_id === chosen ? "border-accent bg-accent-soft/60" : "border-transparent hover:bg-sunken",
        )}
      >
        <span className="text-sm font-medium leading-snug">{r.title}</span>
        <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
          <Mono>{r.report_id}</Mono>
          <SeverityBadge severity={r.severity} />
          <StatusPill value={r.status} />
          {(r.seats_open ?? 0) > 0 && <Pill tone="critical">{r.seats_open} open</Pill>}
          {(r.plans_waiting ?? 0) > 0 && <Pill tone="pending">with approver</Pill>}
        </span>
      </button>
    </li>
  );

  return (
    <div className="animate-rise">
      <PageHeader
        eyebrow={me.desk ? `${me.desk.name} · ${me.name || me.user}` : "Crew control"}
        title={me.role === "controller" ? "Your desk" : "The desks"}
        lede="New disruptions come to the desk with the fewest open. Pick one to work it: cover each seat by callout or plan, and an approver decides every change."
        actions={<Handover />}
      />
      <Kpis>
        <Kpi icon={<ClipboardList size={13} />} label="Open on your desk" value={mine.length} tone={mine.length ? "pending" : undefined} />
        <Kpi icon={<Radio size={13} />} label="Callouts running" value={running.length} />
        <Kpi icon={<ListChecks size={13} />} label="Plans with an approver" value={waiting} tone={waiting ? "pending" : undefined} />
        <Kpi icon={<Users size={13} />} label="Resolved, awaiting sign-off" value={signOff} />
      </Kpis>

      <div className="grid gap-5 xl:grid-cols-[minmax(300px,380px)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Panel flush title={me.desk ? `Disruptions on ${me.desk.name}` : "Active disruptions"} icon={<ClipboardList size={15} />} backdrop={BACKDROP}>
            {mine.length === 0 ? <Empty>Nothing on this desk. New reports arrive here.</Empty> : <ul className="divide-y divide-rule">{mine.map((r) => <Row key={r.report_id} r={r} />)}</ul>}
          </Panel>
          {others.length > 0 && (
            <Panel flush title="Other desks" icon={<Eye size={15} />} action="read only">
              <ul className="divide-y divide-rule opacity-80">{others.map((r) => <Row key={r.report_id} r={r} />)}</ul>
            </Panel>
          )}
          {issues.length > 0 && (
            <Panel flush title="Found by the watch" icon={<TriangleAlert size={15} />} action="not reported yet">
              <ul className="divide-y divide-rule">
                {issues.slice(0, 6).map((i) => (
                  <li key={i.issue_id} className="px-4 py-3">
                    <p className="flex flex-wrap items-center gap-1.5 text-sm">
                      <SeverityBadge severity={{ level: i.severity, reasons: i.severity_reasons }} />
                      <b className="font-medium">{i.name}</b>
                      {i.trip_id && <Mono className="text-muted">{i.trip_id}</Mono>}
                      <span className="ml-auto font-mono text-xs text-faint">{short(i.day)}</span>
                    </p>
                    <p className="mt-1 text-xs text-muted">{i.detail}</p>
                    {i.suggested_cover && (
                      <p className="mt-0.5 text-xs">
                        Cheapest legal cover: {i.suggested_cover}, <span className="font-mono">{inr(i.suggested_cost_inr)}</span>
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
        <Panel title="Solution" icon={<ListChecks size={15} />} className="self-start">
          {!chosen ? (
            <Empty>Pick a disruption to work it.</Empty>
          ) : detail.error ? (
            <Notice tone="error">{detail.error}</Notice>
          ) : !detail.data ? (
            <Loading />
          ) : (
            <DeskSolution report={detail.data} />
          )}
        </Panel>
      </div>
    </div>
  );
}
