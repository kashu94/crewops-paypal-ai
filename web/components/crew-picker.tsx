"use client";

import { Search, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { message, tool } from "@/lib/api";

import { control, cx, Mono, Spinner } from "./ui";

export type CrewPick = { crew_id: string; name: string; rank?: string; airline?: string; base?: string };

type Match = { crew_id: string; name: string; rank: string; airline: string; base: string; status: string };

/** Pick one or more crew by name: type part of a name (or a crew id) and choose from the
 * matches. Up to `max` people. */
export function CrewPicker({
  value,
  onChange,
  max = 12,
  label = "Crew",
}: {
  value: CrewPick[];
  onChange: (v: CrewPick[]) => void;
  max?: number;
  label?: string;
}) {
  const [q, setQ] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) return;
    let alive = true;
    const t = setTimeout(async () => {
      setBusy(true);
      try {
        const r = await tool<{ matches: Match[] }>("search_crew", { query, limit: 8 });
        if (!alive) return;
        setMatches(r.matches ?? []);
        setProblem(null);
      } catch (err) {
        if (!alive) return;
        setMatches([]);
        setProblem(message(err));
      } finally {
        if (alive) setBusy(false);
      }
    }, 220);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const chosen = new Set(value.map((v) => v.crew_id));
  const query = q.trim();
  const shown = query.length < 2 ? [] : matches.filter((m) => !chosen.has(m.crew_id));
  const asId = /^C-\d{4}$/i.test(query) && !chosen.has(query.toUpperCase()) && !shown.some((m) => m.crew_id === query.toUpperCase());
  const full = value.length >= max;

  function add(p: CrewPick) {
    if (full || chosen.has(p.crew_id)) return;
    onChange([...value, p]);
    setQ("");
    setMatches([]);
    setHi(0);
  }

  return (
    <div className="flex flex-col gap-1 text-[13px]" ref={box}>
      <span className="font-medium">
        {label} <span className="font-normal text-faint">· up to {max}, covered together</span>
      </span>
      <div className={cx(control, "flex h-auto min-h-9 flex-wrap items-center gap-1.5 py-1.5")} onClick={() => box.current?.querySelector("input")?.focus()}>
        {value.map((p) => (
          <span key={p.crew_id} className="inline-flex items-center gap-1 rounded bg-accent-soft py-0.5 pl-2 pr-1 text-xs text-accent-ink">
            {p.name} <Mono className="opacity-70">{p.crew_id}</Mono>
            <button type="button" aria-label={`Remove ${p.name}`} className="rounded p-0.5 hover:bg-white/60" onClick={() => onChange(value.filter((x) => x.crew_id !== p.crew_id))}>
              <X size={12} />
            </button>
          </span>
        ))}
        <span className="flex min-w-36 flex-1 items-center gap-1.5">
          <Search size={14} className="text-faint" />
          <input
            value={q}
            disabled={full}
            onChange={(e) => {
              setQ(e.target.value);
              setOpen(true);
              setHi(0);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setHi((h) => Math.min(h + 1, Math.max(0, shown.length - 1)));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setHi((h) => Math.max(0, h - 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                if (shown[hi]) add(shown[hi]);
                else if (asId) add({ crew_id: query.toUpperCase(), name: query.toUpperCase() });
              } else if (e.key === "Backspace" && !q && value.length) {
                onChange(value.slice(0, -1));
              }
            }}
            placeholder={full ? `${max} chosen` : value.length ? "Add another…" : "Type a name or crew id"}
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
            aria-label="Search crew"
          />
          {busy && <Spinner className="h-3.5 w-3.5 text-faint" />}
        </span>
      </div>
      {open && query.length >= 2 && (shown.length > 0 || asId || problem || !busy) && (
        <div className="relative">
          <ul className="animate-rise absolute left-0 right-0 top-1 z-30 max-h-72 overflow-y-auto rounded-md border border-line bg-surface py-1 shadow-[0_10px_28px_rgb(0_0_0/0.12)]">
            {shown.map((m, i) => (
              <li key={m.crew_id}>
                <button
                  type="button"
                  onMouseEnter={() => setHi(i)}
                  onClick={() => add(m)}
                  className={cx("flex w-full items-center gap-3 px-3 py-2 text-left text-sm", i === hi && "bg-sunken")}
                >
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{m.name}</span> <Mono className="text-faint">{m.crew_id}</Mono>
                    <span className="block text-xs text-muted">
                      {m.rank} · {m.airline} · {m.base}
                    </span>
                  </span>
                  {m.status !== "active" && <span className="text-xs text-pending-ink">{m.status}</span>}
                </button>
              </li>
            ))}
            {asId && (
              <li>
                <button type="button" onClick={() => add({ crew_id: query.toUpperCase(), name: query.toUpperCase() })} className="w-full px-3 py-2 text-left text-sm hover:bg-sunken">
                  Use crew id <Mono>{query.toUpperCase()}</Mono>
                </button>
              </li>
            )}
            {!shown.length && !asId && !busy && <li className="px-3 py-2 text-sm text-muted">{problem ? `Search unavailable: ${problem}. Type a crew id such as C-1111.` : "Nobody matches."}</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
