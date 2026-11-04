"use client";

import { Activity, ShieldAlert } from "lucide-react";

import { Part } from "@/components/part";
import { useSession } from "@/components/session";
import { Empty, Loading, Notice, PageHeader, Panel, Pill, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { words } from "@/lib/format";
import { settle } from "@/lib/settle";
import { useLoad } from "@/lib/use-load";

type Entry = { at: string; by: string; role: string; action: string; detail: string };
type Refusal = { at: string; by: string; source?: string; question: string; reason: string };

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
};

const SECURITY = ["auditor", "admin", "super_admin"];

export default function ActivityPage() {
  const { me } = useSession();
  const security = SECURITY.includes(me.role);
  const { data, error } = useLoad(async () => {
    const [activity, refused] = await Promise.all([
      settle(api<{ entries: Entry[] }>("/api/log/activity?limit=80")),
      security ? settle(api<{ entries: Refusal[] }>("/api/log/security?limit=40")) : Promise.resolve(null),
    ]);
    return { activity, refused };
  });
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;
  return (
    <div className="animate-rise">
      <PageHeader title="Activity" lede="Every change made through CrewPay: who, in which role, when and what. Reading is never logged; changing always is." />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Panel flush title="Changes" icon={<Activity size={15} />} action={data.activity.ok ? `${data.activity.value.entries.length} latest` : undefined}>
          <Part of={data.activity}>
            {(v) =>
              v.entries.length ? (
                <Table>
                  <thead>
                    <tr>
                      <th>When (IST)</th>
                      <th>Who</th>
                      <th>What</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.entries.map((e, i) => (
                      <tr key={i}>
                        <td className="whitespace-nowrap font-mono text-xs">{when(e.at)}</td>
                        <td>
                          {e.by} <span className="block text-xs text-faint">{words(e.role)}</span>
                        </td>
                        <td>
                          <b className="font-medium">{words(e.action)}</b>
                          <span className="block text-xs text-muted">{e.detail}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              ) : (
                <Empty>No changes yet.</Empty>
              )
            }
          </Part>
        </Panel>
        <Panel flush title="Refused questions" icon={<ShieldAlert size={15} />} action={security ? "security log" : undefined} className="self-start">
          {data.refused ? (
            <Part of={data.refused}>
              {(v) =>
                v.entries.length ? (
                  <ul className="divide-y divide-rule">
                    {v.entries.map((e, i) => (
                      <li key={i} className="px-4 py-3 sm:px-5">
                        <div className="flex flex-wrap items-center gap-2">
                          <Pill tone="critical">{e.source || "refused"}</Pill>
                          <b className="text-sm font-medium">{e.by}</b>
                          <span className="ml-auto font-mono text-xs text-faint">{when(e.at)}</span>
                        </div>
                        <p className="mt-1 text-sm">“{e.question}”</p>
                        <p className="text-xs text-faint">{e.reason}</p>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No refused questions.</Empty>
                )
              }
            </Part>
          ) : (
            <Empty>Only auditors and admins read the security log.</Empty>
          )}
        </Panel>
      </div>
    </div>
  );
}
