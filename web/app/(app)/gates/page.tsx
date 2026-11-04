"use client";

import { Clock, DoorOpen, GanttChart, Search } from "lucide-react";
import { useState, type FormEvent } from "react";

import { GatePlanView } from "@/components/ops";
import { useSession } from "@/components/session";
import { Button, cx, Input, Loading, Mono, Notice, PageHeader, Panel, Select, Table } from "@/components/ui";
import { message, tool } from "@/lib/api";
import { workDay } from "@/lib/clock";
import { hhmm } from "@/lib/format";
import type { GatePlan, GateStatus } from "@/lib/types";
import { useLoad } from "@/lib/use-load";

const STATIONS = ["HYD", "DEL", "BOM", "BLR", "MAA", "GOI", "VGA", "RJA", "TIR", "VTZ"];
const toMin = (s: string) => {
  const [h, m] = s.slice(11, 16).split(":").map(Number);
  return h * 60 + m;
};

type Query = { station: string; day: string; close: string; from: string; to: string };

function Gantt({ status, plan, closed, from, to }: { status: GateStatus; plan: GatePlan | null; closed: string[]; from: number; to: number }) {
  const moved = Object.fromEntries((plan?.moves ?? []).map((m) => [m.leg_id, m]));
  const deps = status.departures.map((d) => ({
    ...d,
    lane: moved[d.leg_id] ? moved[d.leg_id].to_gate : d.gate,
    a: toMin(d.boarding_from),
    b: toMin(d.departs) + (moved[d.leg_id]?.delay_minutes || 0),
  }));
  const lo = Math.max(0, Math.min(...deps.map((d) => d.a), plan ? from : 24 * 60, 300) - 30);
  const hi = Math.min(24 * 60, Math.max(...deps.map((d) => d.b), plan ? to : 0, 1200) + 30);
  const pos = (m: number) => ((m - lo) / (hi - lo)) * 100;
  const lanes = [...status.gates];
  for (const m of plan?.moves ?? []) if (!lanes.some((g) => g.gate === m.to_gate)) lanes.push({ gate: m.to_gate, kind: m.to });
  const ticks: number[] = [];
  for (let h = Math.ceil(lo / 60); h * 60 <= hi; h += 2) ticks.push(h);
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[640px] text-xs">
        <div className="grid h-6 grid-cols-[76px_1fr] text-faint">
          <span />
          <div className="relative">
            {ticks.map((h) => (
              <span key={h} className="absolute -translate-x-1/2 font-mono" style={{ left: `${pos(h * 60)}%` }}>
                {String(h).padStart(2, "0")}:00
              </span>
            ))}
          </div>
        </div>
        {lanes.map((g) => (
          <div key={g.gate} className="grid h-9 grid-cols-[76px_1fr] items-center border-t border-rule">
            <div className={cx("font-medium", g.kind === "contact" ? "text-foreground" : "text-faint")}>
              {g.gate}
              {g.kind !== "contact" && <span className="block text-[10px] font-normal">{g.kind}</span>}
            </div>
            <div className="relative h-full">
              {ticks.map((h) => (
                <span key={h} className="absolute inset-y-0 border-l border-dashed border-rule" style={{ left: `${pos(h * 60)}%` }} />
              ))}
              {plan && closed.includes(g.gate) && (
                <div className="hatch-critical absolute inset-y-0 border-x border-[#f3c4c0]" style={{ left: `${pos(from)}%`, width: `${pos(to) - pos(from)}%` }} title="closed" />
              )}
              {deps
                .filter((d) => d.lane === g.gate)
                .map((d) => {
                  const m = moved[d.leg_id];
                  return (
                    <div
                      key={d.leg_id}
                      className={cx(
                        "absolute top-1.5 flex h-6 items-center overflow-hidden whitespace-nowrap rounded px-1.5 font-mono text-[11px] font-medium text-white",
                        m ? (m.delay_minutes ? "bg-critical" : "bg-pending") : "bg-accent",
                      )}
                      style={{ left: `${pos(d.a)}%`, width: `${Math.max(5, pos(d.b) - pos(d.a))}%` }}
                      title={`${d.flight} to ${d.to}, boards ${hhmm(d.boarding_from)}, departs ${hhmm(d.departs)} IST, ${d.passengers} pax${m ? `, moved from ${m.from_gate}` : ""}`}
                    >
                      {d.flight}
                    </div>
                  );
                })}
            </div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-4 text-xs text-muted">
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm bg-accent" />at its gate</span>
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm bg-pending" />moved</span>
        <span className="flex items-center gap-1.5"><i className="h-3 w-3 rounded-sm bg-critical" />bus from a remote stand</span>
        <span className="flex items-center gap-1.5"><i className="hatch-critical h-3 w-3 rounded-sm border border-[#f3c4c0]" />closed</span>
      </div>
    </div>
  );
}

function LateAtGate({ day }: { day: string }) {
  const [out, setOut] = useState<GatePlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function check(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setErr(null);
    try {
      setOut(await tool<GatePlan>("late_at_gate", { leg_id: String(f.get("leg_id")).trim().toUpperCase(), minutes: Number(f.get("minutes")) }));
    } catch (x) {
      setErr(message(x));
      setOut(null);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Panel title="What if a flight is late at its gate?" icon={<Clock size={15} />}>
      <form onSubmit={check} className="grid gap-3 sm:grid-cols-[1fr_8rem_auto] sm:items-end">
        <Input label="Flight and date" name="leg_id" defaultValue={`SY201-${day}`} required />
        <Input label="Minutes late" name="minutes" type="number" min={1} max={600} defaultValue={200} required />
        <Button tone="secondary" disabled={busy}>
          Check
        </Button>
      </form>
      <div className="mt-4">
        {err && <Notice tone="error">{err}</Notice>}
        {out && <GatePlanView r={out} />}
      </div>
    </Panel>
  );
}

export default function GatesPage() {
  const { me, can } = useSession();
  const closing = can("gate_closure");
  const today = workDay(me.clock);
  const [q, setQ] = useState<Query>({ station: "HYD", day: today, close: "", from: "05:00", to: "10:00" });
  const closed = q.close.split(",").map((g) => g.trim()).filter(Boolean);
  const { data, error, loading } = useLoad(async () => {
    const status = await tool<GateStatus>("gate_status", { station: q.station, day: q.day });
    let plan: GatePlan | null = null;
    let planError: string | null = null;
    if (closed.length && status.departures.length) {
      const first = status.departures[0].boarding_from.slice(0, 10);
      try {
        plan = await tool<GatePlan>("gate_closure", { station: q.station, gates: closed, start: `${first}T${q.from}`, end: `${first}T${q.to}` });
      } catch (err) {
        planError = message(err);
      }
    }
    return { status, plan, planError };
  }, JSON.stringify(q));

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setQ({ station: String(f.get("station")), day: String(f.get("day")), close: String(f.get("close") ?? ""), from: String(f.get("from") ?? q.from), to: String(f.get("to") ?? q.to) });
  }
  const [fh, fm] = q.from.split(":").map(Number);
  const [th, tm] = q.to.split(":").map(Number);

  return (
    <div className="animate-rise">
      <PageHeader
        title="Gates"
        lede="Who boards where. A departure holds its gate from 45 minutes before off-block. Close gates to see where each flight goes: a free gate, then the emergency gate, then a remote stand by bus, which adds 15 minutes."
      />
      <form onSubmit={submit} className={cx("mb-5 grid gap-3 sm:grid-cols-2 lg:items-end", closing ? "lg:grid-cols-[8rem_10rem_1fr_7rem_7rem_auto]" : "lg:grid-cols-[8rem_10rem_auto] lg:justify-start")}>
        <Select label="Station" name="station" defaultValue={q.station}>
          {STATIONS.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Input label="Day" type="date" name="day" defaultValue={q.day} />
        {closing && (
          <>
            <Input label="Close gates (try 21, 22)" name="close" placeholder="21, 22" defaultValue={q.close} />
            <Input label="From (IST)" type="time" name="from" defaultValue={q.from} />
            <Input label="To (IST)" type="time" name="to" defaultValue={q.to} />
          </>
        )}
        <Button type="submit">
          <Search size={15} /> Show
        </Button>
      </form>
      {error ? (
        <Notice tone="error">{error}</Notice>
      ) : !data ? (
        <Loading />
      ) : (
        <div className={cx("flex flex-col gap-5", loading && "opacity-70")}>
          <Panel title={<>{data.status.station} on {q.day}</>} icon={<GanttChart size={15} />} action={`${data.status.departures.length} departures · India time`}>
            {data.planError && <Notice tone="error" className="mb-3">{data.planError}</Notice>}
            <Gantt status={data.status} plan={data.plan} closed={closed} from={fh * 60 + fm} to={th * 60 + tm} />
          </Panel>
          <div className="grid gap-5 xl:grid-cols-2">
            {data.plan ? (
              <Panel title={data.plan.what} icon={<DoorOpen size={15} />}>
                <GatePlanView r={data.plan} />
              </Panel>
            ) : (
              <Panel flush title="Departures" icon={<DoorOpen size={15} />}>
                {data.status.departures.length === 0 ? (
                  <p className="px-5 py-8 text-center text-sm text-muted">No departures from {data.status.station} on {q.day}.</p>
                ) : (
                <Table>
                  <thead>
                    <tr>
                      <th>Flight</th>
                      <th>To</th>
                      <th>Gate</th>
                      <th className="text-right">Boards</th>
                      <th className="text-right">Departs</th>
                      <th className="text-right">Pax</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.status.departures.map((d) => (
                      <tr key={d.leg_id}>
                        <td><Mono className="font-medium">{d.flight}</Mono></td>
                        <td>{d.to}</td>
                        <td>{d.gate}</td>
                        <td className="text-right font-mono">{hhmm(d.boarding_from)}</td>
                        <td className="text-right font-mono">{hhmm(d.departs)}</td>
                        <td className="text-right font-mono">{d.passengers}</td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
                )}
              </Panel>
            )}
            {can("late_at_gate") && <LateAtGate day={q.day} />}
          </div>
        </div>
      )}
    </div>
  );
}
