"use client";

import { LayoutGrid, ListChecks, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DeskLoadTable, ReportRows } from "@/components/ops";
import { Part } from "@/components/part";
import { useSession } from "@/components/session";
import { Button, Loading, Notice, PageHeader, Panel, Segmented } from "@/components/ui";
import { tool } from "@/lib/api";
import { settle } from "@/lib/settle";
import type { DeskLoad, ReportStatus, ReportSummary } from "@/lib/types";
import { BACKDROP } from "@/lib/art";
import { useLoad } from "@/lib/use-load";

type StatusFilter = "active" | ReportStatus | "all";

export default function DisruptionsPage() {
  const { me, can } = useSession();
  const wide = can("disruptions");
  const [status, setStatus] = useState<StatusFilter>("active");
  const [desk, setDesk] = useState<string>(me.role === "controller" && me.desk ? me.desk.desk_id : "all");

  const { data, error, loading } = useLoad(
    async () => {
      if (!wide) {
        const mine = await settle(tool<{ disruptions: ReportSummary[] }>("my_reports"));
        return { list: mine, load: null, counts: null };
      }
      const [list, load] = await Promise.all([
        settle(tool<{ disruptions: ReportSummary[]; counts?: Record<string, number> }>("disruptions", { status, ...(desk !== "all" ? { desk_id: desk } : {}), limit: 100 })),
        can("desk_load") ? settle(tool<{ desks: DeskLoad[] }>("desk_load")) : Promise.resolve(null),
      ]);
      return { list, load, counts: list.ok ? (list.value.counts ?? null) : null };
    },
    `${status}|${desk}|${wide}`,
  );

  if (error) return <Notice tone="error">{error}</Notice>;

  const desks = data?.load?.ok ? data.load.value.desks ?? [] : [];
  const counts = data?.counts ?? {};
  const active = (counts.open ?? 0) + (counts.in_progress ?? 0) + (counts.resolved ?? 0);
  const all = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <div className="animate-rise">
      <PageHeader
        title={wide ? "Disruptions" : "My reports"}
        lede={
          wide
            ? "Every disruption reported, by desk and state. Open one for its full record: what it breaks, the plan, who was told and every step it took."
            : "The reports you filed and where each stands. Open one to follow it to the end."
        }
        actions={
          can("report_disruption") && (
            <Link href="/report/">
              <Button>
                <TriangleAlert size={15} /> Report a disruption
              </Button>
            </Link>
          )
        }
      />
      {wide && (
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Segmented
            label="State"
            value={status}
            onChange={setStatus}
            options={[
              { value: "active", label: "Active", count: data?.counts ? active : undefined },
              { value: "open", label: "Open", count: counts.open },
              { value: "in_progress", label: "In progress", count: counts.in_progress },
              { value: "resolved", label: "Awaiting sign-off", count: counts.resolved },
              { value: "closed", label: "Closed", count: counts.closed },
              { value: "revoked", label: "Revoked", count: counts.revoked },
              { value: "all", label: "All", count: data?.counts ? all : undefined },
            ]}
          />
          <Segmented
            label="Desk"
            value={desk}
            onChange={setDesk}
            options={[
              { value: "all", label: "Every desk" },
              ...(desks.length ? desks : me.desk ? [{ desk_id: me.desk.desk_id, name: me.desk.name }] : []).map((d) => ({
                value: d.desk_id,
                label: me.desk?.desk_id === d.desk_id ? `${d.name} (mine)` : d.name,
              })),
            ]}
          />
        </div>
      )}
      <div className={wide && data?.load ? "grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]" : ""}>
        <Panel flush title={wide ? "Reports" : "Filed by you"} icon={<ListChecks size={15} />} className={loading && data ? "opacity-70" : ""} backdrop={BACKDROP}>
          {!data ? <Loading /> : <Part of={data.list}>{(v) => <ReportRows reports={v.disruptions ?? []} empty="Nothing here with these filters." />}</Part>}
        </Panel>
        {wide && data?.load && (
          <Panel title="Desk load" icon={<LayoutGrid size={15} />} className="self-start">
            <Part of={data.load}>{(v) => <DeskLoadTable desks={v.desks ?? []} />}</Part>
          </Panel>
        )}
      </div>
    </div>
  );
}
