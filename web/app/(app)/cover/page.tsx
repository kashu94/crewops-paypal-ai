"use client";

import { Ban, BatteryLow, ClipboardList, Filter, HelpCircle, Plane, Radio, Search, Send } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Confirm } from "@/components/modal";
import { RiskBadge } from "@/components/ops";
import { useSession } from "@/components/session";
import { Button, control, cx, Input, Label, Loading, Mono, Notice, PageHeader, Panel, Pill, Spinner } from "@/components/ui";
import { message, tool } from "@/lib/api";
import { hhmm, hm, inr, plural, short } from "@/lib/format";
import type { Callout, Choice, CoverResult, Plan, TripDetails } from "@/lib/types";
import { useAction, useLoad } from "@/lib/use-load";

const OPEN_CALLOUT = ["offered", "accepted", "needs_decision", "awaiting_approval"];
const CALLOUT_NOW: Record<string, string> = {
  offered: "Callout running: waiting for an answer",
  accepted: "Accepted: the plan waits for an approver",
  needs_decision: "Nobody accepted: the desk must decide",
  awaiting_approval: "A proposal waits for an approver",
};

const PICK: Record<string, string> = { cheapest: "cheapest", least_delay: "least delay", keeps_standby: "keeps standby free" };

function initials(name?: string) {
  return (name ?? "?")
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("");
}

function Stat({ label, children, tone }: { label: string; children: React.ReactNode; tone?: "ok" | "pending" }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-[0.06em] text-faint">{label}</dt>
      <dd className={cx("mt-0.5 font-mono text-[15px] font-semibold", tone === "ok" && "text-ok", tone === "pending" && "text-pending")}>{children}</dd>
    </div>
  );
}

/** One way to fill the seat. The first is shown large: it is what the engine would do. */
function ChoiceCard({ c, top, tags, saving, action }: { c: Choice; top: boolean; tags: string[]; saving?: number; action: React.ReactNode }) {
  const late = c.departure_late_hours ?? 0;
  const breakdown = Object.entries(c.cost_breakdown ?? {})
    .map(([k, v]) => `${k} ${inr(v)}`)
    .join(" + ");
  return (
    <article
      className={cx(
        "rounded-xl border transition-shadow hover:shadow-[0_6px_20px_rgb(20_40_70/0.08)]",
        top ? "border-accent/40 bg-[linear-gradient(135deg,var(--accent-soft),var(--surface)_60%)] p-4 sm:p-5" : "border-line bg-surface p-3.5 sm:px-4",
      )}
    >
      <div className="flex flex-wrap items-start gap-3">
        <span className="relative shrink-0">
          <span className={cx("grid place-items-center rounded-full font-semibold", top ? "h-12 w-12 bg-accent text-base text-white" : "h-10 w-10 bg-accent-soft text-sm text-accent")}>
            {initials(c.name)}
          </span>
          <span className="absolute -bottom-1 -right-1 grid h-5 min-w-5 place-items-center rounded-full bg-foreground px-1 font-mono text-[10px] font-semibold text-white ring-2 ring-surface">
            {c.position}
          </span>
        </span>
        <div className="min-w-0 flex-1">
          {top && <p className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-accent">Recommended</p>}
          <h3 className={cx("font-semibold leading-snug", top ? "text-lg" : "text-[15px]")}>
            {c.rank} {c.name} <span className="whitespace-nowrap font-mono text-[0.85em] font-normal text-muted">{c.crew_id}</span>
          </h3>
          <p className="mt-0.5 text-[13px] text-muted">
            {c.way}
            {c.base ? ` · based ${c.base}` : ""}
          </p>
          {tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {tags.map((x) => (
                <Pill key={x} tone="accent">
                  {x}
                </Pill>
              ))}
            </div>
          )}
        </div>
        <div className="flex w-full shrink-0 items-center justify-between gap-2 sm:ml-auto sm:w-auto sm:flex-col sm:items-end">
          <span className={cx("font-mono font-semibold tracking-tight", top ? "text-2xl" : "text-lg")} title={breakdown}>
            {inr(c.cost_inr)}
          </span>
          {action}
        </div>
      </div>
      <dl className={cx("mt-3 grid grid-cols-2 gap-3 border-t border-rule pt-3 sm:grid-cols-4", !top && "mt-2.5 pt-2.5")}>
        <Stat label="Risk">{c.risk ? <RiskBadge risk={c.risk} /> : "–"}</Stat>
        <Stat label="To the airport">{c.minutes_to_airport ? `${c.minutes_to_airport} min` : "–"}</Stat>
        <Stat label="Departure" tone={late ? "pending" : "ok"}>
          {late ? `${hm(late)} late` : "on time"}
        </Stat>
        {top && saving ? <Stat label="Saves vs cancelling">{inr(saving)}</Stat> : <Stat label="Cost made of">{<span className="block truncate font-sans text-xs font-normal text-muted" title={breakdown}>{breakdown || "–"}</span>}</Stat>}
      </dl>
      {((c.fatigue ?? []).length > 0 || (c.to_arrange ?? []).length > 0) && (
        <ul className="mt-3 flex flex-col gap-1.5">
          {(c.fatigue ?? []).map((f) => (
            <li key={f.detail} className="flex items-start gap-2 rounded-md bg-pending-soft px-2.5 py-1.5 text-xs text-pending-ink">
              <BatteryLow size={13} className="mt-px shrink-0" /> <span><b className="font-semibold capitalize">{f.severity} fatigue:</b> {f.detail}</span>
            </li>
          ))}
          {(c.to_arrange ?? []).map((a) => (
            <li key={a} className="flex items-start gap-2 rounded-md bg-sunken px-2.5 py-1.5 text-xs text-muted">
              <ClipboardList size={13} className="mt-px shrink-0" /> <span><b className="font-semibold text-foreground">To arrange:</b> {a}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

/** How many could sit in the seat, and how many each check left. */
function Funnel({ r }: { r: CoverResult }) {
  const steps = [{ stage: "could sit in the seat", left: r.considered, dropped: 0, reason: "Same rank, or qualified to fly it" }, ...r.funnel];
  return (
    <div className="border-b border-rule px-4 py-4 sm:px-5">
      <p className="mb-2.5 text-xs text-muted">Who is left after each check</p>
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
        {steps.map((s, i) => (
          <li key={s.stage} title={s.reason} className="rounded-lg bg-sunken px-3 py-2">
            <div className="flex items-baseline justify-between gap-1">
              <b className={cx("font-mono text-lg", i === steps.length - 1 && "text-accent")}>{s.left}</b>
              {s.dropped > 0 && <span className="font-mono text-xs text-critical">−{s.dropped}</span>}
            </div>
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-line">
              <i className={cx("block h-full rounded-full", i === steps.length - 1 ? "bg-accent" : "bg-[#9fb3c8]")} style={{ width: `${r.considered ? (s.left / r.considered) * 100 : 0}%` }} />
            </div>
            <span className="mt-1 block text-[11px] leading-tight text-muted">{s.stage}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

function WhyNot({ trip, rank, crew }: { trip: string; rank: string; crew: string }) {
  const [out, setOut] = useState<React.ReactNode>(null);
  async function ask(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const id = String(new FormData(e.currentTarget).get("crew") ?? "").trim().toUpperCase();
    setOut(<span className="text-muted">Checking…</span>);
    try {
      const w = await tool<CoverResult>("find_cover", { trip_id: trip, rank, vacated_by: crew, explain: [id], limit: 1 });
      const x = w.why_not?.[id];
      setOut(
        !x ? (
          "No answer."
        ) : typeof x === "string" ? (
          x
        ) : (
          <>
            <b>Out at “{x.stage}”</b> <span className="text-muted">({x.rules.join(", ")})</span>
            <ul className="mt-1 list-disc pl-5">
              {x.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </>
        ),
      );
    } catch (err) {
      setOut(<span className="text-critical">{message(err)}</span>);
    }
  }
  return (
    <Panel title="Why not someone?" icon={<HelpCircle size={15} />}>
      <form onSubmit={ask} className="flex items-end gap-2">
        <Input label="Crew id" name="crew" placeholder="C-1123" pattern="[Cc]-\d{4}" required className="flex-1" />
        <Button tone="secondary">Explain</Button>
      </form>
      {out && <div className="mt-3 text-sm">{out}</div>}
    </Panel>
  );
}

export default function CoverPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { can } = useSession();
  const { busy, run } = useAction();
  const [ask, setAsk] = useState<{ title: string; body: React.ReactNode; action: string; run: () => void } | null>(null);
  const trip = (params.get("trip") || "T-1018").toUpperCase();
  const crewParam = params.get("crew") || "";
  const report = params.get("report") || "";
  const [tripInput, setTripInput] = useState(trip);

  const go = (t: string, c = "") => {
    const q = new URLSearchParams({ trip: t, ...(c ? { crew: c } : {}), ...(report ? { report } : {}) });
    router.replace(`/cover/?${q}`);
  };

  const { data, error, loading } = useLoad(async () => {
    const details = await tool<TripDetails>("trip_details", { trip_id: trip });
    const seat = details.crew.find((c) => c.crew_id === crewParam) ?? details.crew[0];
    if (!seat) return { details, seat: null, cover: null, coverError: "Nobody is rostered on this trip.", running: null, planned: null };
    const callouts = await tool<{ callouts: Callout[] }>("callout_status", {}).catch(() => ({ callouts: [] as Callout[] }));
    const running = (callouts.callouts ?? []).find((c) => c.trip_id === trip && c.replacing === seat.crew_id && OPEN_CALLOUT.includes(c.status)) ?? null;
    const plans = await tool<{ plans: Plan[] }>("plan_status", {}).catch(() => ({ plans: [] as Plan[] }));
    const planned = (plans.plans ?? []).find((p) => p.trip_id === trip && p.replacing === seat.crew_id && p.status === "submitted") ?? null;
    try {
      const cover = await tool<CoverResult>("find_cover", { trip_id: trip, rank: seat.seat, vacated_by: seat.crew_id, limit: 10 });
      return { details, seat, cover, coverError: null, running, planned };
    } catch (err) {
      return { details, seat, cover: null, coverError: message(err), running, planned };
    }
  }, `${trip}|${crewParam}`);

  const t = data?.details;
  const seat = data?.seat;
  const r = data?.cover;
  const crewed = (r?.choices ?? []).filter((c) => c.crew_id);
  const others = (r?.choices ?? []).filter((c) => !c.crew_id);
  const pickOf = (id?: string) => (id ? Object.entries(r?.picks ?? {}).filter(([, c]) => c.crew_id === id).map(([k]) => PICK[k] ?? k) : []);

  return (
    <div className="animate-rise">
      <PageHeader
        title="Find cover"
        lede="Every legal way to fill a seat, cheapest first, with how many people each check ruled out. Crew who reported they cannot fly stay on the list, struck through. Risk is shown beside each person; it never changes the order."
      />
      {report && (
        <Notice className="mb-4">
          Working cover for report{" "}
          <Link className="font-medium underline" href={`/disruptions/view/?id=${encodeURIComponent(report)}`}>
            <Mono>{report}</Mono>
          </Link>
          : plans and callouts started here are linked to it.
        </Notice>
      )}
      <form
        className="mb-5 grid gap-3 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          go(tripInput.trim().toUpperCase());
        }}
      >
        <Input label="Trip" value={tripInput} onChange={(e) => setTripInput(e.target.value)} pattern="[Tt]-\d{4}" required />
        <Label label="Who leaves the seat">
          <select className={control} value={seat?.crew_id ?? ""} onChange={(e) => go(trip, e.target.value)} disabled={!t}>
            {(t?.crew ?? []).map((c) => (
              <option key={c.crew_id} value={c.crew_id}>
                {c.seat}: {c.name} ({c.crew_id})
              </option>
            ))}
          </select>
        </Label>
        <Button type="submit">
          <Search size={15} /> Find cover
        </Button>
      </form>

      {error ? (
        <Notice tone="error">{error}</Notice>
      ) : !data ? (
        <Loading />
      ) : (
        <div className={cx("grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]", loading && "opacity-70")}>
          <Panel
            flush
            title={r ? `Choices for the ${r.seat} seat on ${r.trip_id}` : "Choices"}
            icon={<Filter size={15} />}
            action={r ? plural(r.facts?.legal_choices ?? r.choices.length, "legal crew choice") : undefined}
          >
            {data.coverError || !r ? (
              <div className="p-4">
                <Notice tone="error">{data.coverError}</Notice>
              </div>
            ) : (
              <>
                <Funnel r={r} />
                {(data.planned || data.running) && (
                  <div className="px-4 pt-4 sm:px-5">
                    <Notice tone="pending">
                      {data.planned ? (
                        <>
                          <b>{data.planned.name}</b> is already proposed for this seat (<Mono>{data.planned.plan_id}</Mono>) and waits for an approver. No other plan can be submitted.
                        </>
                      ) : (
                        <>
                          A callout is running for this seat (<Mono>{data.running!.callout_id}</Mono>): plans wait until it ends.
                        </>
                      )}
                    </Notice>
                  </div>
                )}
                <div className="flex flex-col gap-3 p-4 sm:p-5">
                  {crewed.map((c, i) => (
                    <ChoiceCard
                      key={`${c.position}-${c.crew_id}`}
                      c={c}
                      top={i === 0}
                      tags={pickOf(c.crew_id)}
                      saving={i === 0 ? r.facts?.saving_vs_cancel : undefined}
                      action={
                        c.crew_id && can("assign_crew") && seat && !data.planned && !data.running ? (
                          <Button
                            size={i === 0 ? "md" : "sm"}
                            tone={i === 0 ? "accent" : "secondary"}
                            disabled={busy !== null}
                            onClick={() =>
                              setAsk({
                                title: "Submit this plan?",
                                body: (
                                  <>
                                    <b>{c.name}</b> ({c.crew_id}) for {seat.name}&apos;s seat on <Mono>{trip}</Mono>, {c.way.toLowerCase()}, {inr(c.cost_inr)}. It goes to an approver; nothing
                                    on the roster changes until they approve it.
                                  </>
                                ),
                                action: "Submit to an approver",
                                run: () =>
                              run(
                                c.crew_id!,
                                () =>
                                  tool<Plan>("assign_crew", {
                                    trip_id: trip,
                                    replace_crew_id: seat.crew_id,
                                    with_crew_id: c.crew_id,
                                    expected_version: t!.version,
                                    reason: `cover for ${seat.crew_id}`,
                                    ...(report ? { report_id: report } : {}),
                                  }),
                                (p) => `${p.plan_id} submitted. An approver decides it.`,
                              ),
                              })
                            }
                          >
                            {busy === c.crew_id ? <Spinner /> : <Send size={14} />} Submit plan
                          </Button>
                        ) : null
                      }
                    />
                  ))}
                  {!crewed.length && <Notice tone="pending">No crew member can legally take this seat.</Notice>}
                  {(r.withdrawn ?? []).map((w) => (
                    <div key={`w-${w.crew_id}`} className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-[#f3c4c0] px-4 py-3 text-sm text-faint">
                      <span className="line-through">{w.summary}</span>
                      <Pill tone="critical">cannot fly: {w.reason}</Pill>
                      <span className="ml-auto font-mono">{inr(w.cost_inr)}</span>
                    </div>
                  ))}
                  {r.more_choices > 0 && <p className="text-xs text-muted">{plural(r.more_choices, "more legal choice")} not shown.</p>}
                  {others.map((c) => (
                    <div key={`o-${c.position}`} className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-control bg-sunken px-4 py-3">
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface text-muted ring-1 ring-line">
                        <Ban size={15} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium">{c.way}</span>
                        <span className="block text-xs text-muted">{c.note ?? c.summary} · last resort: needs a controller to propose it and an approver to agree</span>
                      </span>
                      <span className="font-mono text-sm font-semibold text-critical">{inr(c.cost_inr)}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </Panel>
          <Confirm open={ask !== null} title={ask?.title ?? ""} action={ask?.action ?? "Confirm"} onClose={() => setAsk(null)} onConfirm={() => { ask?.run(); setAsk(null); }}>
            {ask?.body}
          </Confirm>
          <div className="flex min-w-0 flex-col gap-5">
            {t && (
              <Panel title={<>Trip <Mono>{t.trip_id}</Mono></>} icon={<Plane size={15} />} action={`version ${t.version}`}>
                <p className="text-xs text-muted">
                  {t.route} · <Mono>{t.tail}</Mono> · reports {short(t.report)}
                </p>
                <ul className="mt-3 divide-y divide-rule text-sm">
                  {t.legs.map((l) => (
                    <li key={l.flight + l.departs} className="flex items-center gap-3 py-1.5">
                      <Mono className="w-16 font-medium">{l.flight}</Mono>
                      <span className="flex-1">
                        {l.from}–{l.to}
                      </span>
                      <span className="font-mono text-xs text-muted">
                        {short(l.departs).slice(0, 6)} {hhmm(l.departs)}
                      </span>
                      <span className="w-16 whitespace-nowrap text-right font-mono text-xs text-muted">{l.booked} pax</span>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
            {can("start_callout") && seat && (
              <Panel title="Call crew out" icon={<Radio size={15} />}>
                <p className="mb-3 text-sm text-muted">Offers the seat to the cheapest legal crew member, then the next, until someone accepts. It never cancels on its own.</p>
                {data.running && (
                  <Notice tone={data.running.status === "accepted" ? "ok" : "pending"} className="mb-3">
                    <b>{CALLOUT_NOW[data.running.status] ?? data.running.status}</b>
                    {data.running.waiting_for ? <> ({data.running.asked.find((o) => o.crew_id === data.running!.waiting_for)?.name ?? data.running.waiting_for})</> : null}. <Mono>{data.running.callout_id}</Mono>
                  </Notice>
                )}
                <Button
                  disabled={busy !== null || data.running !== null}
                  onClick={() =>
                    setAsk({
                      title: "Start a callout?",
                      body: <>The seat is offered to the cheapest legal crew member, then the next, until someone accepts. Each has 20 minutes to answer.</>,
                      action: "Start the callout",
                      run: () => run("callout", () => tool("start_callout", { trip_id: trip, replace_crew_id: seat.crew_id, ...(report ? { report_id: report } : {}) }), () => "Callout started."),
                    })
                  }
                >
                  Start callout for <Mono>{seat.crew_id}</Mono>
                </Button>
              </Panel>
            )}
            {seat && <WhyNot trip={trip} rank={seat.seat} crew={seat.crew_id} />}
          </div>
        </div>
      )}
    </div>
  );
}
