"use client";

import { useEffect, useState } from "react";

import { Logo } from "@/components/shell";
import { cx } from "@/components/ui";
import { apiUrl, headers, loadConfig } from "@/lib/api";
import type { Board, ReportSummary } from "@/lib/types";

type Wall = Board & { disruptions?: ReportSummary[] };

const GONE = ["declined", "unavailable", "no answer", "no longer legal", "not approved"];
const RANKS = ["Captain", "First Officer", "Purser", "Cabin Crew"];
const MOOD = { calm: "border-l-[#4fd18b] text-[#4fd18b]", busy: "border-l-[#f5b94a] text-[#f5b94a]", critical: "border-l-[#ff6b6b] text-[#ff6b6b]" };

function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-2xl bg-[#111a25] p-5 ring-1 ring-white/5", className)}>
      <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-[0.14em] text-[#8a9bb0]">{title}</h2>
      {children}
    </section>
  );
}

const quiet = (t: string) => <p className="text-[#8a9bb0]">{t}</p>;

/** The ops-room wall: public and read-only, refreshed every 10 seconds. */
export default function BoardPage() {
  const [b, setB] = useState<Wall | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      await loadConfig();
      try {
        const r = await fetch(apiUrl("/api/board"), { headers: headers(), cache: "no-store" });
        if (!r.ok) throw new Error();
        const data = (await r.json()) as Wall;
        if (alive) {
          setB(data);
          setDown(false);
        }
      } catch {
        if (alive) setDown(true);
      }
    };
    load();
    const t = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const n = b?.next_24h;
  const stats: [string, number | string | undefined, string?][] = n
    ? [
        ["Flights next 24h", n.flights],
        ["Passengers", n.passengers.toLocaleString("en-IN")],
        ["Crew out today/tomorrow", n.crew_out, n.crew_out ? "warn" : ""],
        ["Open callouts", n.open_callouts, n.open_callouts ? "warn" : ""],
        ["Illegal", n.illegal, n.illegal ? "bad" : "good"],
        ["Fatigue", n.fatigue, n.fatigue ? "warn" : ""],
        ["Certs due", n.certificates],
        ["To approve", n.plans_waiting ?? 0, n.plans_waiting ? "warn" : ""],
      ]
    : [];
  const airlines = [...new Set((b?.standby ?? []).map((s) => s.airline))];

  return (
    <main className="min-h-screen bg-[#0a0f16] text-[#eaf0f6] [font-size:clamp(15px,1.05vw,22px)]">
      <header className="flex flex-wrap items-center gap-4 border-b border-white/5 px-6 py-4 lg:px-8">
        <Logo size={30} />
        <div>
          <b className="text-[1.15em] font-semibold">CrewPay board</b>
          <p className="text-[0.75em] text-[#8a9bb0]">Ops room · all three airlines</p>
        </div>
        <span className="ml-auto flex items-center gap-2 font-mono text-[0.85em] text-[#8a9bb0]">
          <span className={cx("h-2.5 w-2.5 rounded-full", down ? "bg-[#5f6d7c]" : "bg-[#4fd18b]")} />
          {down ? "Board unavailable" : (b?.clock ?? "Loading…")}
        </span>
      </header>

      {stats.length > 0 && (
        <div className="grid grid-cols-2 gap-px border-b border-white/5 bg-white/5 sm:grid-cols-4 xl:grid-cols-8">
          {stats.map(([label, v, tone]) => (
            <div key={label} className="bg-[#0a0f16] px-5 py-3">
              <b className={cx("block font-mono text-[1.6em] font-semibold leading-tight", tone === "bad" && "text-[#ff6b6b]", tone === "warn" && "text-[#f5b94a]", tone === "good" && "text-[#4fd18b]")}>{v}</b>
              <span className="text-[0.68em] uppercase tracking-[0.1em] text-[#8a9bb0]">{label}</span>
            </div>
          ))}
        </div>
      )}

      <div className="grid gap-5 p-5 lg:grid-cols-[1.1fr_1fr] lg:p-8">
        <div className="flex flex-col gap-5">
          <section className={cx("rounded-2xl border-l-[6px] bg-[#111a25] p-6 ring-1 ring-white/5", b?.brief ? MOOD[b.brief.mood] : "border-l-[#243042]")}>
            {b?.brief ? (
              <>
                <h1 className="text-[1.9em] font-semibold leading-tight">{b.brief.headline}</h1>
                <ul className="mt-3 list-disc space-y-1 pl-5 text-[#d7e0ea]">
                  {b.brief.lines.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
                <p className="mt-3 text-[0.72em] text-[#8a9bb0]">{b.brief.written_by && !["engine", "template"].includes(b.brief.written_by) ? "Written by a model; every figure checked against the engine" : "Built from the engine's facts"}</p>
              </>
            ) : (
              <h1 className="text-[1.6em] font-semibold text-[#d7e0ea]">{b ? "No shift brief yet." : "Loading…"}</h1>
            )}
          </section>

          <Section title="Disruptions">
            {(b?.disruptions ?? []).length
              ? b!.disruptions!.map((r) => (
                  <div key={r.report_id} className="flex flex-wrap items-baseline gap-x-3 border-b border-white/5 py-2 last:border-0">
                    <span className={cx("h-2.5 w-2.5 self-center rounded-full", r.severity?.level === "high" ? "bg-[#ff6b6b]" : r.severity?.level === "medium" ? "bg-[#f5b94a]" : "bg-[#5f6d7c]")} />
                    <b className="font-medium">{r.title}</b>
                    <span className="text-[0.8em] text-[#8a9bb0]">
                      {r.desk?.name ?? ""} · {r.status.replace(/_/g, " ")}
                    </span>
                  </div>
                ))
              : quiet("No disruptions open.")}
          </Section>

          <Section title="Controller desks">
            {(b?.desks ?? []).length
              ? b!.desks!.map((d) => (
                  <div key={d.controller} className="border-b border-white/5 py-2 last:border-0">
                    <b className="font-medium">{d.controller}</b>{" "}
                    <span className="text-[#8a9bb0]">
                      {d.open} open · {d.filled} filled{d.to_approve ? ` · ${d.to_approve} to approve` : ""}
                    </span>
                  </div>
                ))
              : quiet("No callouts in progress.")}
          </Section>

          <Section title="Callouts">
            {(b?.callouts ?? []).length
              ? b!.callouts.map((c) => (
                  <div key={c.callout_id} className="border-b border-white/5 py-2 last:border-0">
                    <b className="font-mono font-medium">{c.trip_id}</b> {c.seat}{" "}
                    <span className={cx("ml-2 rounded-full border px-2 text-[0.7em]", c.status === "filled" ? "text-[#4fd18b]" : c.status === "offered" ? "text-[#8a9bb0]" : "text-[#f5b94a]")}>
                      {c.status.replace(/_/g, " ")}
                    </span>
                    <div className="mt-0.5 text-[0.85em]">
                      {c.asked.map((o, i) => (
                        <span key={o.crew_id}>
                          {i > 0 && " · "}
                          <span className={GONE.includes(o.answer) ? "text-[#5f6d7c] line-through" : ""}>{o.name}</span> <span className="text-[#8a9bb0]">{o.answer}</span>
                        </span>
                      ))}
                    </div>
                  </div>
                ))
              : quiet("None.")}
          </Section>
        </div>

        <div className="flex flex-col gap-5">
          <Section title="What the watch found">
            {(b?.issues ?? []).length
              ? b!.issues.map((i) => (
                  <div key={i.issue_id} className="border-b border-white/5 py-2 last:border-0">
                    <span className={cx("mr-2 rounded-full border px-2 text-[0.7em]", i.kind === "illegal" ? "text-[#ff6b6b]" : i.kind === "fatigue" ? "text-[#f5b94a]" : "text-[#8a9bb0]")}>{i.kind}</span>
                    {i.name} {i.trip_id && <span className="font-mono">· {i.trip_id}</span>}
                    <div className="text-[0.85em] text-[#8a9bb0]">{i.detail}</div>
                  </div>
                ))
              : quiet("Nothing found.")}
          </Section>

          <Section title="Next departures">
            {(b?.departures ?? []).length
              ? b!.departures!.slice(0, 7).map((d) => (
                  <div key={d.flight + d.departs} className="flex flex-wrap items-baseline gap-x-3 border-b border-white/5 py-2 last:border-0">
                    <b className="w-20 font-mono font-medium">{d.flight}</b>
                    <span>{d.route}</span>
                    <span className="text-[0.85em] text-[#8a9bb0]">
                      {d.departs} IST · {d.tail} · {d.booked} booked
                    </span>
                    <span className={cx("ml-auto rounded-full border px-2 text-[0.7em]", d.state === "affected" ? "text-[#ff6b6b]" : "text-[#4fd18b]")}>{d.state}</span>
                  </div>
                ))
              : quiet("None.")}
          </Section>

          <Section title="Standby on call · today and tomorrow">
            {airlines.length ? (
              <table className="w-full text-left">
                <thead>
                  <tr className="text-[0.75em] text-[#8a9bb0]">
                    <th />
                    {RANKS.map((r) => (
                      <th key={r} className="px-2 py-1 font-medium">
                        {r}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {airlines.map((a) => (
                    <tr key={a}>
                      <td className="py-1 font-semibold">{a}</td>
                      {RANKS.map((r) => {
                        const on = b?.standby?.find((x) => x.airline === a && x.rank === r)?.on_call ?? 0;
                        return (
                          <td key={r} className={cx("px-2 py-1 font-mono", !on && "font-bold text-[#ff6b6b]")}>
                            {on}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              quiet("–")
            )}
          </Section>

          <Section title="Latest roster changes">
            {(b?.changes ?? []).length
              ? b!.changes
                  .slice()
                  .reverse()
                  .map((c) => (
                    <div key={`${c.trip_id}-${c.version}`} className="border-b border-white/5 py-2 text-[0.9em] last:border-0">
                      <span className="font-mono">
                        {c.trip_id} v{c.version}
                      </span>
                      : +{c.added.join(", ")} −{c.removed.join(", ")} <span className="text-[#8a9bb0]">by {c.by}, {c.at}</span>
                    </div>
                  ))
              : quiet("None.")}
          </Section>
        </div>
      </div>
    </main>
  );
}
