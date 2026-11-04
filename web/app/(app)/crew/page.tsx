"use client";

import { BadgeCheck, CalendarDays, Check, Gauge, Inbox, ListChecks, Mic, Plane, Radio, Square, Timer, UserX, X } from "lucide-react";
import { useRef, useState, type FormEvent } from "react";

import { Modal } from "@/components/modal";
import { NoticeList, ReportRows, RiskDetail } from "@/components/ops";
import { Part } from "@/components/part";
import { FromUntil } from "@/components/range";
import { useSession } from "@/components/session";
import { Button, Chips, cx, Empty, Input, Loading, Meter, Mono, Notice, PageHeader, Panel, Pill, Spinner, StatusPill, Table } from "@/components/ui";
import { api, message, missing, tool } from "@/lib/api";
import { addDays, opDay, workDay } from "@/lib/clock";
import { plural, short, words } from "@/lib/format";
import { settle } from "@/lib/settle";
import { ANY_LANGUAGE, Recorder, transcribe } from "@/lib/voice";
import type { Callout, Notice as NoticeT, Profile, Report, ReportSummary, Trip } from "@/lib/types";
import { useAction, useLoad } from "@/lib/use-load";

const DAYS = 14;

type Earlier = { callout_id: string; trip_id: string; seat: string; offered_at: string; answer_by: string; outcome: string; answered_at?: string; your_remark?: string; seat_now: string };

const OUTCOME: Record<string, string> = {
  accepted: "You accepted",
  declined: "You declined",
  "no answer": "No answer in time: offered to the next crew member",
  unavailable: "Withdrawn: you reported you cannot fly",
  "no longer legal": "Withdrawn: you were no longer legal for it",
  "not approved": "You accepted, but the approver did not approve it",
  "no longer needed": "No longer needed",
};

function Calendar({ trips, p, today, start }: { trips: Trip[]; p: Profile; today: string; start: string }) {
  const duty = new Map<string, string>();
  for (const t of trips) for (const d of t.days) duty.set(d, t.trip_id);
  const standby = new Set(p.standby_days ?? []);
  const out = new Set<string>();
  for (const u of p.unavailable ?? []) for (let d = u.from; d <= u.to; d = addDays(d, 1)) out.add(d);
  const lead = (new Date(`${start}T00:00:00Z`).getUTCDay() + 6) % 7;
  const cells = Array.from({ length: DAYS }, (_, i) => addDays(start, i));
  return (
    <div>
      <div className="grid grid-cols-7 gap-1 sm:gap-1.5">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => (
          <div key={d} className="pb-1 text-center text-[11px] font-medium text-faint">
            {d}
          </div>
        ))}
        {Array.from({ length: lead }, (_, i) => (
          <div key={`l${i}`} />
        ))}
        {cells.map((iso) => {
          const trip = duty.get(iso);
          const away = out.has(iso);
          const sb = standby.has(iso);
          const d = new Date(`${iso}T00:00:00Z`);
          return (
            <div
              key={iso}
              className={cx(
                "relative flex min-h-16 flex-col gap-0.5 rounded-md p-1.5 text-[11px] sm:p-2",
                away ? "hatch-out border border-[#f3c4c0]" : trip ? "bg-accent text-white" : sb ? "bg-accent-soft text-accent-ink" : "bg-sunken",
                iso === today && "ring-2 ring-foreground ring-offset-2 ring-offset-surface",
                iso < today && "opacity-55",
              )}
              aria-current={iso === today ? "date" : undefined}
            >
              {iso === today && <span className="absolute -top-2 right-1.5 rounded bg-foreground px-1 text-[9px] font-semibold uppercase tracking-wide text-white">today</span>}
              <span className={cx("font-mono", trip && !away ? "text-white/80" : "text-muted")}>
                {d.getUTCDate()} {d.toLocaleString("en", { month: "short", timeZone: "UTC" })}
              </span>
              {trip && <span className={cx("font-mono font-semibold", away && "text-critical-ink line-through")}>{trip}</span>}
              {!trip && sb && <span>standby</span>}
              {!trip && !sb && !away && <span className="hidden text-faint sm:inline">day off</span>}
              {away && <span className="font-medium text-critical-ink">can&apos;t fly</span>}
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm bg-accent" />flying</span>
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm bg-accent-soft" />standby</span>
        <span className="flex items-center gap-1.5"><i className="hatch-out h-3 w-3 rounded-sm border border-[#f3c4c0]" />can&apos;t fly</span>
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm ring-2 ring-foreground ring-offset-1" />today</span>
      </div>
    </div>
  );
}

/** A seat crew control is asking this person to fly, answered here with an optional remark. */
function OfferCard({ c, busy, answer }: { c: Callout; busy: boolean; answer: (accept: boolean, note: string) => void }) {
  const [note, setNote] = useState("");
  return (
    <section className="mb-5 overflow-hidden rounded-xl border border-[#f6dc9c] bg-pending-soft">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-pending text-white">
          <Radio size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-[15px] font-semibold text-pending-ink">
            Callout: fly <Mono>{c.trip_id}</Mono> as {c.seat}
          </p>
          <p className="text-[13px] text-pending-ink/80">
            Crew control asks you to cover this seat{c.answer_by ? <>; please answer by <b>{short(c.answer_by)}</b>, after that it goes to the next crew member</> : ""}.
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-2 border-t border-[#f6dc9c] bg-surface/70 px-4 py-3 sm:px-5">
        <Input
          label="Remark for the desk (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={200}
          placeholder="Such as: can report by 06:15, or: at a family function"
          className="min-w-56 flex-1"
        />
        <Button tone="accent" disabled={busy} onClick={() => answer(true, note)}>
          <Check size={15} /> Accept
        </Button>
        <Button tone="secondary" disabled={busy} onClick={() => answer(false, note)}>
          <X size={15} /> Decline
        </Button>
      </div>
    </section>
  );
}

type Heard = { heard: string; understood: boolean; first_day?: string; last_day?: string; reason_kind?: string; since?: string; note?: string };

/** "I can't fly", said aloud: the browser hears it, the model reads it into the form, and the
 * crew member checks it before anything is sent. */
function SayIt({ onHeard }: { onHeard: (h: Heard) => void }) {
  const recorder = useRef(new Recorder());
  const [state, setState] = useState<"idle" | "listening" | "working">("idle");
  const [note, setNote] = useState("");
  async function toggle() {
    if (state === "idle") {
      try {
        await recorder.current.start();
        setState("listening");
        setNote("Listening… say when you can't fly and why, then press Stop.");
      } catch (err) {
        setNote(`Microphone not available: ${message(err)}`);
      }
      return;
    }
    if (state !== "listening") return;
    setState("working");
    setNote("Hearing it…");
    try {
      const t = await transcribe(await recorder.current.stop(), ANY_LANGUAGE);
      if (!t.text) {
        setNote("Nothing was heard. Try again a little closer.");
        return;
      }
      const h = await api<Heard>("/api/absence/understand", { method: "POST", json: { text: t.text } });
      setNote(h.understood ? `Heard: “${h.heard}”. Check the form below and send it.` : `Heard: “${h.heard}”, but it does not say you can't fly. Fill the form instead.`);
      if (h.understood) onHeard(h);
    } catch (err) {
      setNote(message(err));
    } finally {
      setState("idle");
    }
  }
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-dashed border-control bg-sunken px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" tone={state === "listening" ? "danger" : "secondary"} onClick={toggle} disabled={state === "working"}>
          {state === "working" ? <Spinner /> : state === "listening" ? <Square size={13} /> : <Mic size={13} />} {state === "listening" ? "Stop" : "Say it"}
        </Button>
        <span className="text-xs text-muted">In English, Hindi or Telugu: “kal se do din bukhar hai”.</span>
      </div>
      {note && <p className="text-xs text-foreground">{note}</p>}
    </div>
  );
}

function Unavailable({ crewId, today, onDone }: { crewId: string; today: string; onDone: (text: string) => void }) {
  const [kind, setKind] = useState("sick");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [heard, setHeard] = useState<Heard | null>(null);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    setBusy(true);
    setError(null);
    try {
      try {
        const r = await tool<Report>("report_disruption", {
          kind: "crew_unavailable",
          crew_ids: [crewId],
          first_day: f.first_day,
          last_day: f.last_day,
          reason_kind: kind,
          ...(f.since ? { since: f.since } : {}),
          ...(f.note ? { note: f.note } : {}),
        });
        onDone(`Crew control has been told (${r.report_id}, ${r.desk?.name ?? "a desk"}).`);
      } catch (err) {
        if (!missing(err)) throw err;
        const r = await tool<{ own_trips_now_short: string[] }>("report_unavailable", { first_day: f.first_day, last_day: f.last_day, reason: f.note || kind, kind, ...(f.since ? { since: f.since } : {}) });
        onDone(`Crew control has been told.${r.own_trips_now_short.length ? ` Your trips ${r.own_trips_now_short.join(", ")} need cover.` : ""}`);
      }
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  }
  return (
    <form key={heard?.heard ?? "typed"} onSubmit={submit} className="flex flex-col gap-4">
      <SayIt
        onHeard={(h) => {
          setHeard(h);
          if (h.reason_kind) setKind(h.reason_kind);
        }}
      />
      <div className="grid grid-cols-2 gap-3">
        <FromUntil fromLabel="From" untilLabel="Until" from={heard?.first_day ?? today} until={heard?.last_day ?? addDays(today, 1)} />
      </div>
      <Chips
        label="Reason"
        value={kind}
        onChange={setKind}
        options={[
          { value: "sick", label: "Sick" },
          { value: "fatigue", label: "Fatigue" },
          { value: "personal", label: "Personal" },
          { value: "other", label: "Other" },
        ]}
      />
      <Input label="Taken ill at (optional, India time)" type="time" name="since" defaultValue={heard?.since} />
      <Input label="Anything crew control should know" name="note" defaultValue={heard?.note ?? "unwell"} maxLength={200} />
      <p className="text-xs text-faint">Any callout offer to you for those dates moves to the next person at once.</p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex gap-2">
        <Button tone="danger" type="submit" disabled={busy}>
          {busy ? <Spinner /> : <UserX size={15} />} Tell crew control
        </Button>
      </div>
    </form>
  );
}

export default function CrewPage() {
  const { me, flash, bump } = useSession();
  const { busy, run } = useAction();
  const [dialog, setDialog] = useState(false);
  const id = me.crew_id ?? "";
  const today = opDay(me.clock);
  const { data, error } = useLoad(async () => {
    const [p, trips, offers, inbox, reports] = await Promise.all([
      tool<Profile>("crew_profile", { crew_id: id }),
      settle(tool<{ trips: Trip[] }>("find_trips", { crew_id: id, limit: 30 })),
      settle(tool<{ offers_to_me: Callout[]; earlier_offers?: Earlier[] }>("callout_status", {})),
      settle(api<{ notices: NoticeT[] }>("/api/notices?limit=10")),
      settle(tool<{ disruptions: ReportSummary[] }>("my_reports")),
    ]);
    return { p, trips, offers, inbox, reports };
  }, id);

  if (!id) return <Notice tone="error">This account has no crew id.</Notice>;
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;
  const { p } = data;
  const trips = data.trips.ok ? data.trips.value.trips ?? [] : [];
  const offers = data.offers.ok ? data.offers.value.offers_to_me ?? [] : [];
  const earlier = data.offers.ok ? data.offers.value.earlier_offers ?? [] : [];
  const start = me.days?.first ?? today;
  const next = trips.find((t) => t.days.some((d) => d >= today));

  return (
    <div className="animate-rise">
      <PageHeader
        eyebrow={
          <>
            {p.rank} · {p.airline} · based {p.base} · <Mono>{p.crew_id}</Mono>
          </>
        }
        title={`Good to see you, ${p.name.split(" ")[0]}`}
        lede={
          <>
            {p.rest_until ? <>Resting until {short(p.rest_until)}. </> : null}
            {next ? (
              <>
                Next duty: <Mono>{next.trip_id}</Mono> reporting {short(next.report)} ({next.route}).
              </>
            ) : (
              "Nothing rostered in the next two weeks."
            )}{" "}
            Your roster, your limits and anything crew control has sent you.
          </>
        }
        actions={
          <Button tone="danger-quiet" onClick={() => setDialog(true)}>
            <UserX size={15} /> I can&apos;t fly
          </Button>
        }
      />

      {offers.map((c) => (
        <OfferCard key={c.callout_id} c={c} busy={busy !== null} answer={(accept, note) =>
          run(c.callout_id, () => tool("answer_callout", { callout_id: c.callout_id, accept, ...(note ? { note } : {}) }), () =>
            accept ? "Accepted. The desk confirms once an approver approves." : "Declined. The next crew member is being asked.",
          )
        } />
      ))}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Panel title="My calendar" icon={<CalendarDays size={15} />} action={`${short(start)} – ${short(addDays(start, DAYS - 1))}`}>
            <Calendar trips={trips} p={p} today={today} start={start} />
          </Panel>
          <Panel flush title="My flights" icon={<Plane size={15} />} action={plural(trips.length, "trip")}>
            <Part of={data.trips}>
              {() =>
                trips.length ? (
                  <ul className="divide-y divide-rule">
                    {trips.map((t) => (
                      <li key={t.trip_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 sm:px-5">
                        <Mono className={cx("font-semibold", t.cancelled && "line-through")}>{t.trip_id}</Mono>
                        <span className="text-sm">{t.route}</span>
                        {t.cancelled ? (
                          <Pill tone="critical">Cancelled</Pill>
                        ) : (
                          <span className="text-xs text-muted">
                            <Mono>{t.tail}</Mono> · release {short(t.release)}
                          </span>
                        )}
                        <span className="ml-auto font-mono text-xs text-faint">reports {short(t.report)}</span>
                        {(t.flights ?? []).length > 0 && (
                          <span className="flex w-full flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
                            {(t.flights ?? []).map((f) => (
                              <span key={f.leg_id} className={cx(f.cancelled && "text-critical-ink line-through")}>
                                <Mono>{f.flight_no}</Mono> {f.from}→{f.to} {short(f.departs).slice(-5)}
                                {f.tail !== t.tail && !f.cancelled && <> · <Mono>{f.tail}</Mono></>}
                                {f.gate && !f.cancelled && <> · gate {f.gate}</>}
                                {f.cancelled && " cancelled"}
                              </span>
                            ))}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Empty>No trips rostered.</Empty>
                )
              }
            </Part>
          </Panel>
          <Panel flush title="My reports" icon={<ListChecks size={15} />}>
            {(p.unavailable ?? []).length > 0 && (
              <ul className="divide-y divide-rule border-b border-rule">
                {p.unavailable.map((u) => (
                  <li key={u.report_id} className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5">
                    <Pill tone="critical">{u.kind || "unavailable"}</Pill>
                    <span className="text-sm">
                      {short(u.from)} to {short(u.to)}
                    </span>
                    <span className="text-xs text-muted">
                      {u.reason}
                      {u.since ? ` · from ${u.since.slice(11, 16)}` : ""}
                    </span>
                    <Button
                      size="sm"
                      tone="secondary"
                      className="ml-auto"
                      disabled={busy !== null}
                      onClick={() => run(u.report_id, () => tool("clear_unavailable", { report_id: u.report_id }), () => "Withdrawn. You are back in the roster for those dates.")}
                    >
                      Withdraw, I can fly
                    </Button>
                  </li>
                ))}
              </ul>
            )}
            <Part of={data.reports}>
              {(v) =>
                (v.disruptions ?? []).length || !(p.unavailable ?? []).length ? <ReportRows reports={v.disruptions ?? []} empty="You have not reported anything." /> : null
              }
            </Part>
          </Panel>
        </div>
        <div className="flex min-w-0 flex-col gap-5">
          <Panel title="Duty limits" icon={<Timer size={15} />}>
            <div className="flex flex-col gap-4">
              <Meter label="Duty, last 7 days" value={p.duty_hours_last_7_days} max={p.duty_limit_7_days} />
              <Meter label="Flying, last 28 days" value={p.block_hours_last_28_days} max={p.block_limit_28_days} />
            </div>
          </Panel>
          <Panel title="My risk score" icon={<Gauge size={15} />}>
            <RiskDetail risk={p.risk} />
          </Panel>
          <Panel flush title="Certificates" icon={<BadgeCheck size={15} />}>
            <Table>
              <tbody>
                {p.certificates.map((c) => (
                  <tr key={c.kind}>
                    <td className="capitalize">{words(c.kind)}</td>
                    <td className="font-mono text-xs">{short(c.expires)} {c.expires.slice(0, 4)}</td>
                    <td className="text-right">
                      <Pill tone={c.days_left < 15 ? "critical" : c.days_left < 60 ? "pending" : "ok"}>{c.days_left} days</Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Panel>
          <Panel flush title="Callouts offered to me" icon={<Radio size={15} />} action={earlier.length ? plural(earlier.length, "earlier offer") : undefined}>
            {offers.length === 0 && earlier.length === 0 ? (
              <Empty>No callout has been offered to you.</Empty>
            ) : (
              <ul className="divide-y divide-rule">
                {offers.map((c) => (
                  <li key={c.callout_id} className="flex flex-wrap items-center gap-2 px-4 py-3 text-sm sm:px-5">
                    <StatusPill value="offered" />
                    <Mono>{c.trip_id}</Mono> as {c.seat}
                    <span className="ml-auto text-xs text-muted">answer by {c.answer_by ? short(c.answer_by) : "–"}</span>
                  </li>
                ))}
                {earlier.map((e) => (
                  <li key={e.callout_id + e.offered_at} className="px-4 py-3 text-sm sm:px-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <Mono className="font-semibold">{e.trip_id}</Mono> <span>as {e.seat}</span>
                      <span className="ml-auto font-mono text-xs text-faint">offered {short(e.offered_at)}</span>
                    </div>
                    <p className={cx("mt-1 text-xs", e.outcome === "accepted" ? "text-ok-ink" : e.outcome === "no answer" ? "text-pending-ink" : "text-muted")}>
                      {OUTCOME[e.outcome] ?? e.outcome}
                      {e.answered_at && e.outcome !== "no answer" ? ` at ${short(e.answered_at)}` : e.outcome === "no answer" ? ` (answer was due by ${short(e.answer_by)})` : ""}. Seat now {e.seat_now.replace("_", " ")}.
                    </p>
                    {e.your_remark && <p className="mt-1 text-xs italic text-muted">“{e.your_remark}”</p>}
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel flush title="Messages" icon={<Inbox size={15} />}>
            <Part of={data.inbox}>{(v) => <NoticeList notices={(v.notices ?? []).slice(0, 5)} />}</Part>
          </Panel>
        </div>
      </div>

      <Modal open={dialog} onClose={() => setDialog(false)} title="Report that you cannot fly">
        <Unavailable
          crewId={id}
          today={workDay(me.clock)}
          onDone={(text) => {
            setDialog(false);
            flash(text);
            bump();
          }}
        />
      </Modal>
    </div>
  );
}
