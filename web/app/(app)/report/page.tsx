"use client";

import {
  BadgeAlert,
  CalendarOff,
  Clock,
  CloudLightning,
  DoorClosed,
  FileWarning,
  MoveRight,
  Send,
  UserX,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Combo } from "@/components/combo";
import { FromUntil } from "@/components/range";
import { CrewPicker, type CrewPick } from "@/components/crew-picker";
import { EvidenceField, EvidenceSet, PHOTO, type PhotoSetState, type PhotoState } from "@/components/photo-intake";
import { useSession } from "@/components/session";
import { Button, Chips, cx, Input, Notice, PageHeader, Panel, Select, Spinner, TextArea } from "@/components/ui";
import { message, tool } from "@/lib/api";
import { addDays, workDay } from "@/lib/clock";
import { short } from "@/lib/format";
import type { Report } from "@/lib/types";
import { BACKDROP } from "@/lib/art";
import { useLoad } from "@/lib/use-load";

type Catalog = {
  trips: { trip_id: string; airline: string; tail: string; route: string; reports: string; days: string[] }[];
  flights: { leg_id: string; flight_no: string; trip_id: string | null; from: string; to: string; departs: string; gate: string | null }[];
  tails: { tail: string; airline: string; type: string }[];
  stations: string[];
  gates: Record<string, string[]>;
};

type Kind =
  | "crew_unavailable"
  | "planned_leave"
  | "certification_lapse"
  | "flight_delay"
  | "aircraft_technical"
  | "station_disruption"
  | "gate_closure"
  | "gate_change";

const KINDS: { id: Kind; label: string; hint: string; icon: LucideIcon }[] = [
  { id: "crew_unavailable", label: "Crew can't fly", hint: "Sick, fatigue, personal: every seat they leave", icon: UserX },
  { id: "planned_leave", label: "Planned leave", hint: "Annual, medical, training: book it ahead", icon: CalendarOff },
  { id: "certification_lapse", label: "Certificate lapse", hint: "A licence, medical or training runs out", icon: BadgeAlert },
  { id: "flight_delay", label: "Delay", hint: "A trip leaves late: who breaks a limit", icon: Clock },
  { id: "aircraft_technical", label: "Aircraft fault", hint: "Out of service: the day's flights slip", icon: Wrench },
  { id: "station_disruption", label: "Station closure", hint: "Weather or runway: flights held", icon: CloudLightning },
  { id: "gate_closure", label: "Gate closure", hint: "Gates shut: where each flight boards", icon: DoorClosed },
  { id: "gate_change", label: "Gate change", hint: "Move a departure: is the gate free", icon: MoveRight },
];

const STATIONS = ["HYD", "DEL", "BOM", "BLR", "MAA", "GOI", "VGA", "RJA", "TIR", "VTZ"];
const CERTS = [
  ["licence", "Licence"],
  ["medical", "Medical"],
  ["recurrent_training", "Recurrent training"],
  ["safety_equipment", "Safety equipment"],
];

function allowed(role: string): Kind[] {
  if (role === "admin" || role === "super_admin") return ["planned_leave"];
  if (role === "crew") return ["crew_unavailable"];
  return KINDS.map((k) => k.id).filter((k) => k !== "planned_leave");
}

export default function ReportPage() {
  const { me, can, flash, bump } = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const kinds = allowed(me.role);
  const wanted = params.get("kind") as Kind | null;
  const [kind, setKind] = useState<Kind>(wanted && kinds.includes(wanted) ? wanted : kinds[0]);
  const prefillCrew = params.get("crew");
  const [crew, setCrew] = useState<CrewPick[]>(prefillCrew ? [{ crew_id: prefillCrew, name: params.get("name") ?? prefillCrew }] : []);
  const [trip, setTrip] = useState(params.get("trip") ?? "");
  const [tail, setTail] = useState(params.get("tail") ?? "");
  const [leg, setLeg] = useState(params.get("leg") ?? "");
  const [station, setStation] = useState(params.get("station") ?? "HYD");
  const [gates, setGates] = useState<string[]>([]);
  const [newGate, setNewGate] = useState("");
  const { data: cat } = useLoad(() => tool<Catalog>("catalog"));
  const from = params.get("from");
  const [reason, setReason] = useState("sick");
  const [busy, setBusy] = useState(false);
  const [photo, setPhoto] = useState<PhotoState>({ status: "none", evidenceId: null });
  const [severity, setSeverity] = useState("");
  const [description, setDescription] = useState("");
  const [written, setWritten] = useState(false);
  /** The days a sick note gives, once it is read: they replace the default dates. */
  const [noteDays, setNoteDays] = useState<{ from: string; until: string } | null>(null);
  /** What a photo of a board, a gate screen or a certificate fills in besides. */
  const [fromPhoto, setFromPhoto] = useState<{ hours?: string; day?: string; cert?: string; lapses?: string } | null>(null);
  const typing = useRef<number | null>(null);
  /** Type text into the description quickly, so it is plain that it was just written for
   * them: under a second whatever its length. Typing in the box stops it. */
  function writeOut(text: string) {
    if (typing.current) cancelAnimationFrame(typing.current);
    typing.current = null;
    setWritten(Boolean(text));
    if (!text) return setDescription("");
    const start = performance.now();
    const total = Math.min(900, 250 + text.length * 3);
    const step = (now: number) => {
      const shown = Math.ceil((text.length * Math.min(1, (now - start) / total)));
      setDescription(text.slice(0, shown));
      typing.current = shown < text.length ? requestAnimationFrame(step) : null;
    };
    typing.current = requestAnimationFrame(step);
  }
  useEffect(() => () => {
    if (typing.current) cancelAnimationFrame(typing.current);
  }, []);
  const evidence = photo.evidenceId;
  const named = photo.status === "kept" ? (photo.result?.crew ?? []) : [];
  const wrongPerson = named.length > 0 && crew.length > 0 && !named.some((n) => crew.some((c) => c.crew_id === n.crew_id));
  const many = (PHOTO[kind]?.max ?? 1) > 1;
  const [photos, setPhotos] = useState<PhotoSetState>({ evidenceIds: [], problems: [], checking: false });
  const photoBlocks = many
    ? photos.checking || photos.problems.length > 0
    : photo.status === "checking" || photo.status === "refused" || wrongPerson || (PHOTO[kind]?.required === true && !evidence);

  const [error, setError] = useState<string | null>(null);
  const today = workDay(me.clock);
  const def = KINDS.find((k) => k.id === kind)!;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries([...new FormData(e.currentTarget)].filter(([, v]) => v !== "")) as Record<string, string>;
    const args: Record<string, unknown> = { kind };
    if (["crew_unavailable", "planned_leave", "certification_lapse"].includes(kind)) {
      if (!crew.length) return setError("Choose at least one crew member.");
      args.crew_ids = crew.map((c) => c.crew_id);
    }
    if (kind === "crew_unavailable") args.reason_kind = reason;
    for (const k of ["first_day", "last_day", "since", "leave", "cert", "trip_id", "day", "tail", "start", "end", "station", "leg_id", "gate", "note"]) {
      if (f[k]) args[k] = k === "trip_id" || k === "tail" || k === "station" ? f[k].toUpperCase() : f[k];
    }
    if (f.hours) args.hours = Number(f.hours);
    if (kind === "flight_delay") {
      if (!trip) return setError("Choose the trip that leaves late.");
      args.trip_id = trip;
    }
    if (kind === "aircraft_technical") {
      if (!tail) return setError("Choose the aircraft.");
      args.tail = tail;
    }
    if (kind === "station_disruption" || kind === "gate_closure") args.station = station;
    if (kind === "gate_closure") {
      if (!gates.length) return setError("Choose at least one gate.");
      args.gates = gates;
    }
    if (kind === "gate_change") {
      if (!leg || !newGate) return setError("Choose the flight and its new gate.");
      args.leg_id = leg;
      args.gate = newGate;
    }
    if (many) {
      if (photos.problems.length) return setError(photos.problems[0]);
      if (photos.evidenceIds.length) args.evidence_ids = photos.evidenceIds;
    } else if (photo.status === "refused") return setError("The photo was not accepted. Add the right photo, or remove it.");
    else if (evidence) args.evidence_ids = [evidence];
    else if (PHOTO[kind]?.required) return setError("Add a photo of the fault: an aircraft fault is filed with one.");
    if (severity) args.severity = severity;
    if (description.trim()) args.note = description.trim();
    setBusy(true);
    setError(null);
    try {
      const r = await tool<Report>("report_disruption", args);
      const more = (r as Report & { also_filed?: { report_id: string }[] }).also_filed ?? [];
      flash(
        more.length
          ? `Crew on different flights are separate disruptions: filed ${[r.report_id, ...more.map((m) => m.report_id)].join(", ")}.`
          : `${r.report_id} filed${r.desk ? ` and sent to ${r.desk.name} (${r.desk.controller})` : ""}.`,
      );
      bump();
      router.push(`/disruptions/view/?id=${encodeURIComponent(r.report_id)}`);
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  }

  const crewKind = ["crew_unavailable", "planned_leave", "certification_lapse"].includes(kind);

  return (
    <div className="animate-rise">
      <PageHeader
        title="Report a disruption"
        lede="File it once: CrewPay works out what it breaks, who must be told and what it costs, and sends it to the least loaded desk. Nothing changes the roster without an approver."
      />
      {!can("report_disruption") && (
        <Notice tone="pending" className="mb-5">
          This server does not offer filing reports to your role yet.
        </Notice>
      )}
      {kinds.length > 1 && (
        <div className="mb-5 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-2" role="radiogroup" aria-label="Kind of disruption">
          {KINDS.filter((k) => kinds.includes(k.id)).map((k) => {
            const Icon = k.icon;
            const on = k.id === kind;
            return (
              <button
                key={k.id}
                role="radio"
                aria-checked={on}
                onClick={() => {
                  setKind(k.id);
                  setError(null);
                  setPhoto({ status: "none", evidenceId: null });
                  setPhotos({ evidenceIds: [], problems: [], checking: false });
                  setSeverity("");
                  writeOut("");
                }}
                className={cx(
                  "flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                  on ? "border-accent bg-accent-soft" : "border-line bg-surface hover:border-control hover:bg-sunken",
                )}
              >
                <span className={cx("mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md", on ? "bg-accent text-white" : "bg-sunken text-muted")}>
                  <Icon size={15} />
                </span>
                <span>
                  <b className="block text-sm font-semibold">{k.label}</b>
                  <span className="block text-xs text-muted">{k.hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}

      {
        <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <Panel title={def.label} icon={<def.icon size={15} />} backdrop={BACKDROP}>
            <form key={kind} onSubmit={submit} className="flex flex-col gap-4">
              {crewKind && <CrewPicker value={crew} onChange={setCrew} label={kind === "crew_unavailable" ? "Who cannot fly" : "Crew"} />}
              {kind === "crew_unavailable" && (
                <>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <FromUntil key={noteDays ? `${noteDays.from}|${noteDays.until}` : "default"} fromLabel="From" untilLabel="Until" from={noteDays?.from ?? from ?? today} until={noteDays?.until ?? addDays(from ?? today, 1)} />
                  </div>
                  <Chips
                    label="Reason"
                    value={reason}
                    onChange={setReason}
                    options={[
                      { value: "sick", label: "Sick" },
                      { value: "fatigue", label: "Fatigue" },
                      { value: "personal", label: "Personal" },
                      { value: "other", label: "Other" },
                    ]}
                  />
                  <Input label="Taken ill at (optional, India time)" type="time" name="since" hint="Duties that end before this time on the first day are still flown." />
                </>
              )}
              {kind === "planned_leave" && (
                <>
                  <div className="grid gap-4 sm:grid-cols-3">
                    <FromUntil fromLabel="From" untilLabel="Until" from={addDays(today, 2)} until={addDays(today, 4)} />
                    <Select label="Leave" name="leave" defaultValue="annual_leave">
                      <option value="annual_leave">Annual leave</option>
                      <option value="medical">Medical</option>
                      <option value="training">Training</option>
                      <option value="personal">Personal</option>
                    </Select>
                  </div>
                </>
              )}
              {kind === "certification_lapse" && (
                <div className="grid gap-4 sm:grid-cols-3">
                  <Select key={`cert-${fromPhoto?.cert ?? ""}`} label="Certificate" name="cert" defaultValue={fromPhoto?.cert ?? "medical"}>
                    {CERTS.map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </Select>
                  <FromUntil key={`lapse-${fromPhoto?.lapses ?? ""}`} fromLabel="Not valid from" untilLabel="Renewed by (optional)" from={fromPhoto?.lapses ?? today} untilRequired={false} />
                </div>
              )}
              {kind === "flight_delay" && (
                <div className="grid gap-4 sm:grid-cols-3">
                  <Combo
                    label="Trip"
                    value={trip}
                    onChange={setTrip}
                    required
                    placeholder="Trip, flight route or aircraft"
                    options={(cat?.trips ?? []).map((t) => ({ value: t.trip_id, label: `${t.trip_id} · ${t.route}`, hint: `${t.tail} · ${short(t.reports)}`, search: `${t.airline} ${t.tail}` }))}
                  />
                  <Input key={`hours-${fromPhoto?.hours ?? ""}`} label="Hours late" type="number" step="0.25" min="0.25" max="24" name="hours" defaultValue={fromPhoto?.hours ?? "3"} required />
                  <Input key={`day-${fromPhoto?.day ?? ""}`} label="Day (multi-day trips)" type="date" name="day" defaultValue={fromPhoto?.day ?? params.get("day") ?? undefined} />
                </div>
              )}
              {kind === "aircraft_technical" && (
                <div className="grid gap-4 sm:grid-cols-3">
                  <Combo
                    label="Aircraft"
                    value={tail}
                    onChange={setTail}
                    required
                    placeholder="Registration or airline"
                    options={(cat?.tails ?? []).map((t) => ({ value: t.tail, label: t.tail, hint: `${t.airline} · ${t.type}` }))}
                  />
                  <FromUntil
                    type="datetime-local"
                    fromLabel="Out of service from (IST)"
                    untilLabel="Back (IST)"
                    from={`${params.get("day") ?? today}T06:00`}
                    until={`${params.get("day") ?? today}T11:00`}
                  />
                </div>
              )}
              {kind === "station_disruption" && (
                <div className="grid gap-4 sm:grid-cols-3">
                  <Select label="Station" value={station} onChange={(e) => setStation(e.target.value)}>
                    {(cat?.stations ?? STATIONS).map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </Select>
                  <FromUntil type="datetime-local" fromLabel="Closes (IST)" untilLabel="Reopens (IST)" from={`${today}T06:30`} until={`${today}T09:30`} />
                </div>
              )}
              {kind === "gate_closure" && (
                <>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Select
                      label="Station"
                      value={station}
                      onChange={(e) => {
                        setStation(e.target.value);
                        setGates([]);
                      }}
                    >
                      {(cat?.stations ?? STATIONS).map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </Select>
                    <div className="flex flex-col gap-1 text-[13px]">
                      <span className="font-medium">Gates closed</span>
                      <div className="flex flex-wrap gap-1.5">
                        {(cat?.gates[station] ?? []).map((g) => (
                          <button
                            key={g}
                            type="button"
                            aria-pressed={gates.includes(g)}
                            onClick={() => setGates((x) => (x.includes(g) ? x.filter((y) => y !== g) : [...x, g]))}
                            className={cx(
                              "min-w-10 rounded-md border px-2.5 py-1.5 font-mono text-[13px] transition-colors",
                              gates.includes(g) ? "border-critical bg-critical-soft text-critical-ink" : "border-control bg-surface hover:bg-sunken",
                            )}
                          >
                            {g}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <FromUntil type="datetime-local" fromLabel="Closes (IST)" untilLabel="Reopens (IST)" from={`${today}T06:00`} until={`${today}T09:00`} />
                  </div>
                </>
              )}
              {kind === "gate_change" && (
                <div className="grid gap-4 sm:grid-cols-2">
                  <Combo
                    label="Flight"
                    value={leg}
                    onChange={(v) => {
                      setLeg(v);
                      setNewGate("");
                    }}
                    required
                    placeholder="Flight number, station or day"
                    options={(cat?.flights ?? []).map((l) => ({ value: l.leg_id, label: `${l.flight_no} ${l.from}–${l.to}`, hint: `${short(l.departs)}${l.gate ? ` · gate ${l.gate}` : ""}`, search: l.leg_id }))}
                  />
                  <Combo
                    label="New gate"
                    value={newGate}
                    onChange={setNewGate}
                    required
                    placeholder="Gate"
                    options={(cat?.gates[cat?.flights.find((l) => l.leg_id === leg)?.from ?? "HYD"] ?? [])
                      .filter((g) => g !== cat?.flights.find((l) => l.leg_id === leg)?.gate)
                      .map((g) => ({ value: g, label: `Gate ${g}` }))}
                  />
                </div>
              )}
              {PHOTO[kind] && many && (
                <EvidenceSet
                  key={`photos-${kind}`}
                  reportKind={kind}
                  expected={kind === "aircraft_technical" ? (tail ? [tail] : []) : gates}
                  onChange={setPhotos}
                  onSummary={(sum) => {
                    setSeverity(sum?.severity ?? "");
                    writeOut(sum?.description ?? "");
                  }}
                />
              )}
              {PHOTO[kind] && !many && (
                <EvidenceField
                  key={`photo-${kind}`}
                  reportKind={kind}
                  tail={() => tail}
                  onChange={(p) => {
                    setPhoto(p);
                    const s = p.result?.suggestion;
                    // A note that names one person and their days fills in who and when,
                    // unless someone is already chosen: the reporter checks it before filing.
                    const days = p.result?.proposal?.tool === "report_unavailable" ? p.result.proposal.arguments : null;
                    const one = p.status === "kept" && p.result?.crew?.length === 1 ? p.result.crew[0] : null;
                    if (one && crew.length === 0) setCrew([{ crew_id: one.crew_id, name: one.name }]);
                    if (p.status === "kept" && days?.first_day) setNoteDays({ from: String(days.first_day), until: String(days.last_day || days.first_day) });
                    // A departures board, a gate screen or a certificate fills in what it shows.
                    const proposal = p.status === "kept" ? p.result?.proposal : null;
                    if (proposal?.tool === "delay_impact") {
                      setTrip(String(proposal.arguments.trip_id));
                      setFromPhoto({ hours: String(proposal.arguments.hours), day: proposal.arguments.day ? String(proposal.arguments.day) : undefined });
                    }
                    if (proposal?.tool === "gate_change") {
                      setLeg(String(proposal.arguments.leg_id));
                      setNewGate(String(proposal.arguments.gate));
                    }
                    const form = p.result?.form;
                    if (kind === "certification_lapse" && p.status === "kept" && form) {
                      const expires = typeof form.expires === "string" ? form.expires : "";
                      setFromPhoto({
                        cert: CERTS.some(([v]) => v === form.kind) ? String(form.kind) : undefined,
                        lapses: /^\d{4}-\d{2}-\d{2}$/.test(expires) ? addDays(expires, 1) : undefined,
                      });
                    }
                    if (p.status === "kept" && s) {
                      setSeverity(s.severity);
                      writeOut(s.description ?? "");
                    } else {
                      // No photo, or one not accepted: back to what the form starts with.
                      setSeverity("");
                      writeOut("");
                    }
                  }}
                />
              )}
              {wrongPerson && (
                <Notice tone="error">
                  The photo is for {named.map((n) => `${n.name} (${n.crew_id})`).join(", ")}, not {crew.map((c) => c.name).join(", ")}. Add their own note or certificate, or remove the photo.
                </Notice>
              )}
              <div className="grid gap-4 sm:grid-cols-[12rem_1fr]">
                <Select label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)} hint="Raises the worked-out severity, never lowers it">
                  <option value="">Work it out</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </Select>
                <TextArea
                  label={
                    <span className="flex items-center gap-2">
                      Description
                      {written && <span className="text-xs font-normal text-accent">Written from the {many ? "photos" : "photo"}: check it</span>}
                    </span>
                  }
                  rows={many ? 4 : 3}
                  maxLength={500}
                  value={description}
                  onChange={(e) => {
                    if (typing.current) cancelAnimationFrame(typing.current);
                    typing.current = null;
                    setWritten(false);
                    setDescription(e.target.value);
                  }}
                  placeholder={many ? "What happened. Written from the photos, in three sentences at most, once they all pass." : "What happened, in a sentence or two. Filled from the photo when you add one."}
                />
              </div>
              {many && photos.problems.length > 0 && (
                <Notice tone="pending">
                  <b className="block">Not ready to file yet:</b>
                  <ul className="mt-1 list-disc pl-5">
                    {photos.problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </Notice>
              )}
              {error && <Notice tone="error">{error}</Notice>}
              <div>
                <Button type="submit" disabled={busy || !can("report_disruption") || photoBlocks}>
                  {busy ? <Spinner /> : <Send size={15} />} File the report
                </Button>
              </div>
            </form>
          </Panel>
          <Panel title="What happens next" icon={<FileWarning size={15} />}>
            <ol className="flex flex-col gap-3 text-sm">
              {[
                ["Worked out", "The rules engine finds every seat it empties or every flight it moves, and what breaks a duty limit."],
                ["Sent to a desk", "It goes to the controller desk with the fewest open reports; you can follow it on its own page."],
                ["Teams told", "The teams who must know (crew control, the station, maintenance) are listed with a contact."],
                ["Covered together", crewKind ? "The seats are covered together: nobody fills two, and nobody reported is used." : "Crew pushed over a limit are covered like any other seat."],
                ["Photo checked", "A photo is read the moment you add it and kept with the report, so the desk sees what you saw. It shows how far the report is confirmed; how severe it is comes from what it does to the flights."],
                ["Two people", "A controller plans it, an approver approves every change and confirms the resolution."],
              ].map(([t, d], i) => (
                <li key={t} className="flex gap-3">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent-soft font-mono text-xs font-semibold text-accent">{i + 1}</span>
                  <span>
                    <b className="font-medium">{t}.</b> <span className="text-muted">{d}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      }
    </div>
  );
}
