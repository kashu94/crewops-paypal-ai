"use client";

import { BookUser, CalendarDays, ChevronLeft, ChevronRight, Clock, HeartPulse, ListChecks, Phone, Plane, Search, TriangleAlert, UserX } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useSession } from "@/components/session";
import { Button, control, cx, Empty, Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill } from "@/components/ui";
import { tool } from "@/lib/api";
import { addDays } from "@/lib/clock";
import { hhmm, plural } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

type Directory = { teams: { team: string; title: string; job: string; per_station: boolean; contacts: { station: string; name: string; phone: string }[] }[] };
type Person = { crew_id: string; name: string; out: boolean };
type Flight = {
  leg_id: string;
  cancelled?: boolean;
  flight_no: string;
  trip_id: string;
  airline: string;
  from: string;
  to: string;
  departs: string;
  arrives: string;
  tail: string;
  aircraft_type: string;
  gate: string | null;
  booked: number;
  seats: number;
  captain: Person[];
  first_officer: Person[];
  cabin: number;
  cabin_out: Person[];
  disruptions: { report_id: string; severity: string; title: string }[];
  departed: boolean;
};
type Week = {
  today: string;
  start: string;
  first_day: string;
  last_day: string;
  day: string;
  airlines: { code: string; name: string; color: string }[];
  week: { day: string; flights: number; passengers: number; by_airline: Record<string, number>; crew_out: number; disruptions: number }[];
  flights: Flight[];
  crew_out: { crew_id: string; name: string; rank: string; airline: string; days: string[] }[];
};

const STATIONS = ["HYD", "DEL", "BOM", "BLR", "MAA", "GOI", "VGA", "RJA", "TIR", "VTZ"];
const SEVERITY_TONE = { high: "critical", medium: "pending", low: "neutral" } as const;

const dayName = (iso: string, opts: Intl.DateTimeFormatOptions) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { ...opts, timeZone: "UTC" });

function report(params: Record<string, string>) {
  return `/report/?${new URLSearchParams(params)}`;
}

/** A crew member on a flight: click to report them unavailable that day. */
function CrewChip({ p, rank, day, open }: { p: Person; rank: string; day: string; open: boolean }) {
  if (p.out)
    return (
      <span className="inline-flex items-center gap-1 rounded bg-critical-soft px-1.5 py-0.5 text-xs text-critical-ink line-through" title={`${p.name} is reported unavailable`}>
        <HeartPulse size={11} /> {rank} {p.name}
      </span>
    );
  if (!open)
    return (
      <span className="inline-flex items-center gap-1 rounded bg-sunken px-1.5 py-0.5 text-xs">
        <b className="font-mono text-[10px] text-faint">{rank}</b> {p.name}
      </span>
    );
  return (
    <Link
      href={report({ kind: "crew_unavailable", crew: p.crew_id, name: p.name, from: day })}
      title={`Report ${p.name} (${p.crew_id}) unavailable on ${dayName(day, { day: "numeric", month: "short" })}`}
      className="inline-flex items-center gap-1 rounded bg-sunken px-1.5 py-0.5 text-xs hover:bg-accent-soft hover:text-accent-ink"
    >
      <b className="font-mono text-[10px] text-faint">{rank}</b> {p.name}
    </Link>
  );
}

function AirlineDot({ color }: { color?: string }) {
  return <span className="inline-block h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color ?? "#8b929c" }} />;
}

function WeekChart({ w, onPick }: { w: Week; onPick: (day: string) => void }) {
  const max = Math.max(1, ...w.week.map((d) => d.passengers));
  const colour = Object.fromEntries(w.airlines.map((a) => [a.code, a.color]));
  return (
    <div role="tablist" aria-label="Day" className="grid grid-cols-7 gap-1.5 sm:gap-2">
      {w.week.map((d) => {
        const on = d.day === w.day;
        return (
          <button
            key={d.day}
            role="tab"
            aria-selected={on}
            onClick={() => onPick(d.day)}
            className={cx("flex min-w-0 flex-col items-stretch rounded-lg border p-1.5 text-left transition-colors sm:p-2", on ? "border-accent bg-accent-soft" : "border-line bg-surface hover:bg-sunken")}
          >
            <span className="flex h-20 flex-col-reverse overflow-hidden rounded bg-sunken sm:h-24">
              {w.airlines.map((a) => (
                <i key={a.code} style={{ height: `${((d.by_airline[a.code] ?? 0) / max) * 100}%`, backgroundColor: colour[a.code] }} title={`${a.name}: ${d.by_airline[a.code] ?? 0} passengers`} />
              ))}
            </span>
            <span className="mt-1.5 text-[11px] font-medium text-muted">{dayName(d.day, { weekday: "short" })}{d.day === w.today ? " · today" : ""}</span>
            <span className="font-mono text-base font-semibold leading-tight">{dayName(d.day, { day: "numeric" })}</span>
            <span className="hidden text-[11px] text-muted sm:block">{d.passengers.toLocaleString("en-IN")} pax · {d.flights} flights</span>
            <span className="mt-1 flex flex-wrap gap-1">
              {d.crew_out > 0 && <span className="rounded bg-critical-soft px-1 text-[10px] font-medium text-critical-ink">{d.crew_out} out</span>}
              {d.disruptions > 0 && <span className="rounded bg-pending-soft px-1 text-[10px] font-medium text-pending-ink">{d.disruptions} hit</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function FlightRow({ f, day, colour, future }: { f: Flight; day: string; colour?: string; future: boolean }) {
  const worst = f.disruptions.some((d) => d.severity === "high") ? "high" : f.disruptions.length || f.cabin_out.length || [...f.captain, ...f.first_officer].some((p) => p.out) ? "some" : "";
  const load = f.seats ? f.booked / f.seats : 0;
  const open = future && !f.departed && !f.cancelled;
  return (
    <li className={cx("grid gap-x-4 gap-y-2 border-l-[3px] px-4 py-3 sm:grid-cols-[4.5rem_minmax(0,1.1fr)_minmax(0,1.4fr)_auto] sm:px-5", worst === "high" ? "border-critical" : worst ? "border-pending" : "border-transparent")}>
      <div className="font-mono text-sm">
        <b>{hhmm(f.departs)}</b>
        <span className="block text-xs text-faint">arr {hhmm(f.arrives)}</span>
      </div>
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-sm font-semibold">
          <AirlineDot color={colour} /> <span className={cx(f.cancelled && "line-through")}>{f.flight_no}</span> <span className="font-normal text-muted">{f.from} → {f.to}</span>
          {f.cancelled && <Pill tone="critical">Cancelled</Pill>}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-muted">
          {open ? (
            <Link href={report({ kind: "aircraft_technical", tail: f.tail, day })} title={`Report a fault on ${f.tail}`} className="font-mono text-accent hover:underline">
              {f.tail}
            </Link>
          ) : (
            <Mono>{f.tail}</Mono>
          )}
          <span>{f.aircraft_type}</span>
          <span>· <Mono>{f.trip_id}</Mono></span>
          {f.gate &&
            (open ? (
              <Link href={report({ kind: "gate_change", leg: f.leg_id })} title="Report a gate change" className="text-accent hover:underline">
                · gate {f.gate}
              </Link>
            ) : (
              <span>· gate {f.gate}</span>
            ))}
        </p>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        {f.captain.map((p) => <CrewChip key={p.crew_id} p={p} rank="CPT" day={day} open={open} />)}
        {f.first_officer.map((p) => <CrewChip key={p.crew_id} p={p} rank="FO" day={day} open={open} />)}
        <span className="text-xs text-muted">+ {f.cabin} cabin</span>
        {f.cabin_out.map((p) => <CrewChip key={p.crew_id} p={p} rank="CC" day={day} open={false} />)}
      </div>
      <div className="flex flex-wrap items-center gap-2 sm:flex-col sm:items-end">
        <span className="flex items-center gap-2 text-xs text-muted" title={`${f.booked} of ${f.seats} seats booked`}>
          <span className="h-1.5 w-16 overflow-hidden rounded-full bg-sunken">
            <i className={cx("block h-full rounded-full", load >= 0.9 ? "bg-pending" : "bg-accent")} style={{ width: `${Math.min(100, load * 100)}%` }} />
          </span>
          {f.booked}
        </span>
        <span className="flex flex-wrap items-center gap-1.5">
          {f.disruptions.map((d) => (
            <Link key={d.report_id} href={`/disruptions/view/?id=${encodeURIComponent(d.report_id)}`} title={d.title}>
              <Pill tone={SEVERITY_TONE[d.severity as keyof typeof SEVERITY_TONE] ?? "neutral"}>
                <TriangleAlert size={11} /> {d.report_id}
              </Pill>
            </Link>
          ))}
          {open && (
            <Link href={report({ kind: "flight_delay", trip: f.trip_id, day })} className="inline-flex items-center gap-1 rounded-md border border-control px-2 py-0.5 text-xs font-medium hover:bg-sunken">
              <Clock size={11} /> Delay
            </Link>
          )}
        </span>
      </div>
    </li>
  );
}

function Contacts() {
  const [station, setStation] = useState("HYD");
  const { data, error } = useLoad(() => tool<Directory>("contacts", { station }), station);
  return (
    <Panel
      flush
      title="Who to call"
      icon={<BookUser size={15} />}
      action={
        <select aria-label="Station" value={station} onChange={(e) => setStation(e.target.value)} className={`${control} h-7 w-24 text-[13px]`}>
          {STATIONS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      }
    >
      {error ? (
        <div className="p-4">
          <Notice tone="error">{error}</Notice>
        </div>
      ) : !data ? (
        <Loading />
      ) : !data.teams?.length ? (
        <Empty>No contacts for {station}.</Empty>
      ) : (
        <ul className="divide-y divide-rule">
          {data.teams.map((t) => (
            <li key={t.team} className="px-4 py-2.5 sm:px-5">
              <p className="text-sm font-medium">{t.title}</p>
              <p className="text-xs text-muted">{t.job}</p>
              {t.contacts.map((c) => (
                <p key={c.station + c.name} className="mt-1 flex flex-wrap items-center gap-2 text-[13px]">
                  {t.per_station && <Pill>{c.station}</Pill>}
                  {c.name}
                  <a href={`tel:${c.phone.replace(/\s/g, "")}`} className="inline-flex items-center gap-1 font-mono text-accent hover:underline">
                    <Phone size={12} /> {c.phone}
                  </a>
                </p>
              ))}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export default function OperationsPage() {
  const { me } = useSession();
  const [start, setStart] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [airline, setAirline] = useState("");
  const [affected, setAffected] = useState(false);
  const { data: w, error, loading } = useLoad(() => tool<Week>("operations", { ...(start ? { start } : {}), ...(day ? { day } : {}) }), `${start}|${day}`);

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!w) return <Loading />;

  const colour = Object.fromEntries(w.airlines.map((a) => [a.code, a.color]));
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const flights = w.flights.filter((f) => {
    if (airline && f.airline !== airline) return false;
    const hit = f.disruptions.length > 0 || f.cabin_out.length > 0 || [...f.captain, ...f.first_officer].some((p) => p.out);
    if (affected && !hit) return false;
    const text = `${f.flight_no} ${f.from} ${f.to} ${f.tail} ${f.trip_id} ${[...f.captain, ...f.first_officer].map((p) => `${p.name} ${p.crew_id}`).join(" ")}`.toLowerCase();
    return words.every((x) => text.includes(x));
  });
  const thisDay = w.week.find((d) => d.day === w.day);
  const totals = w.week.reduce((a, d) => ({ flights: a.flights + d.flights, pax: a.pax + d.passengers }), { flights: 0, pax: 0 });
  const go = (s: string) => {
    setStart(s);
    setDay(s);
  };

  return (
    <div className={cx("animate-rise", loading && "opacity-80")}>
      <PageHeader
        eyebrow={<>Operations · {me.name || me.user}</>}
        title={`Week of ${dayName(w.week[0].day, { day: "numeric", month: "short" })} – ${dayName(w.week[6].day, { day: "numeric", month: "short" })}`}
        lede="Every flight, who flies it and who is out, from five days back to the end of the published roster. Click a crew name to report them unavailable, an aircraft to report a fault, a gate to change it, or Delay."
        actions={
          <>
            <Button tone="secondary" size="sm" aria-label="Previous week" disabled={w.start <= w.first_day} onClick={() => go(addDays(w.start, -7) < w.first_day ? w.first_day : addDays(w.start, -7))}>
              <ChevronLeft size={15} />
            </Button>
            <Button tone="secondary" size="sm" onClick={() => go(w.today)}>
              Today
            </Button>
            <Button tone="secondary" size="sm" aria-label="Next week" disabled={addDays(w.start, 7) > w.last_day} onClick={() => go(addDays(w.start, 7))}>
              <ChevronRight size={15} />
            </Button>
            <Link href="/report/">
              <Button>
                <TriangleAlert size={15} /> Report a disruption
              </Button>
            </Link>
          </>
        }
      />
      <Kpis>
        <Kpi icon={<Plane size={13} />} label="Flights this week" value={totals.flights} />
        <Kpi icon={<CalendarDays size={13} />} label="Passengers booked" value={totals.pax.toLocaleString("en-IN")} />
        <Kpi icon={<UserX size={13} />} label="Crew out" value={w.crew_out.length} sub={thisDay?.crew_out ? `${thisDay.crew_out} on the day shown` : undefined} tone={w.crew_out.length ? "pending" : undefined} />
        <Kpi icon={<ListChecks size={13} />} label="Flights hit, day shown" value={thisDay?.disruptions ?? 0} tone={thisDay?.disruptions ? "critical" : undefined} />
      </Kpis>

      <Panel
        title="Passengers by day"
        icon={<CalendarDays size={15} />}
        className="mb-5"
        action={
          <span className="flex flex-wrap gap-3">
            {w.airlines.map((a) => (
              <span key={a.code} className="flex items-center gap-1.5">
                <AirlineDot color={a.color} /> {a.name}
              </span>
            ))}
          </span>
        }
      >
        <WeekChart w={w} onPick={setDay} />
      </Panel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel flush title={`${dayName(w.day, { weekday: "long", day: "numeric", month: "short" })} · ${plural(w.flights.length, "flight")}`} icon={<Plane size={15} />}>
          <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-3 sm:px-5">
            <label className="relative min-w-48 flex-1">
              <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-faint" />
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Flight, station, aircraft or crew" className={cx(control, "pl-8")} />
            </label>
            <div className="inline-flex rounded-md border border-line bg-sunken p-0.5" role="radiogroup" aria-label="Airline">
              {[{ code: "", name: "All" }, ...w.airlines].map((a) => (
                <button
                  key={a.code || "all"}
                  role="radio"
                  aria-checked={airline === a.code}
                  onClick={() => setAirline(a.code)}
                  className={cx("rounded px-2.5 py-1 text-[13px]", airline === a.code ? "bg-surface font-semibold shadow-[0_0_0_1px_var(--line)]" : "text-muted hover:text-foreground")}
                >
                  {a.code || "All"}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-1.5 text-[13px] text-muted">
              <input type="checkbox" checked={affected} onChange={(e) => setAffected(e.target.checked)} /> Only affected
            </label>
          </div>
          {flights.length === 0 ? (
            <Empty>No flights match.</Empty>
          ) : (
            <ul className="divide-y divide-rule">
              {flights.map((f) => (
                <FlightRow key={f.leg_id} f={f} day={w.day} colour={colour[f.airline]} future={w.day >= w.today} />
              ))}
            </ul>
          )}
        </Panel>
        <div className="flex min-w-0 flex-col gap-5">
          <Panel flush title="Crew out" icon={<UserX size={15} />} action={plural(w.crew_out.length, "person", "people")}>
            {w.crew_out.length === 0 ? (
              <Empty>Nobody is reported unavailable this week.</Empty>
            ) : (
              <ul className="divide-y divide-rule">
                {w.crew_out.map((c) => (
                  <li key={c.crew_id} className="flex items-start gap-3 px-4 py-3 sm:px-5">
                    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-critical-soft text-xs font-semibold text-critical-ink">{c.name.replace(/[^A-Z]/g, "").slice(0, 2)}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5 text-sm font-medium">
                        <AirlineDot color={colour[c.airline]} /> {c.name}
                      </span>
                      <span className="block text-xs text-muted">
                        <Mono>{c.crew_id}</Mono> · {c.rank}
                      </span>
                      <span className="mt-1.5 flex gap-1">
                        {w.week.map((d) => (
                          <i key={d.day} title={d.day} className={cx("h-2.5 w-4 rounded-sm", c.days.includes(d.day) ? "bg-critical" : "bg-sunken")} />
                        ))}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Contacts />
        </div>
      </div>
    </div>
  );
}
