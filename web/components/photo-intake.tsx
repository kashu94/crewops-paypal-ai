"use client";

import { CheckCircle2, ImageUp, RefreshCw, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api, apiUrl, headers, message } from "@/lib/api";

import { Button, cx, Pill, Spinner } from "./ui";

type Box = { x1: number; y1: number; x2: number; y2: number; width: number; height: number; confidence: number };
export type Intake = {
  status: "rejected" | "reshoot" | "confirm" | "draft";
  message: string;
  confidence?: string | null;
  box?: Box | null;
  evidence_id?: string;
  suggestion?: { severity: "low" | "medium" | "high"; description: string } | null;
  crew?: { crew_id: string; name: string }[];
  signals?: { label?: string; tail_seen?: string | null; gate_seen?: string | null };
};

/** Where the photo stands, for the form around it: nothing chosen, being checked, kept, or
 * not accepted (the form must not be filed with it). */
export type PhotoState = { status: "none" | "checking" | "kept" | "refused"; evidenceId: string | null; result?: Intake };

/** What each kind of report is backed up by, and whether it must be. */
export const PHOTO: Record<string, { kind: string; label: string; hint: string; required?: boolean; max?: number }> = {
  crew_unavailable: { kind: "sick_note", label: "Sick note or medical report", hint: "Optional. Read for the name and the dates." },
  certification_lapse: { kind: "certificate", label: "Photo of the certificate", hint: "Optional. Read for the holder and the expiry date." },
  flight_delay: { kind: "disruption", label: "Departures board or delay notice", hint: "Optional. The flights and times on it are checked against the schedule." },
  gate_change: { kind: "disruption", label: "Gate screen or departures board", hint: "Optional. The new gate is checked against who holds it." },
  aircraft_technical: { kind: "defect", label: "Photos of the fault", hint: "Required, up to 5. Each is checked for the damage; one must show the registration.", required: true, max: 5 },
  station_disruption: { kind: "scene", label: "Photo of the conditions", hint: "Optional. Fog, flooding, snow or a storm on the airfield." },
  gate_closure: { kind: "gate", label: "Photos of the closed gate", hint: "Optional, up to 3. A barrier, tape, a spillage or the jet bridge; one must show the gate's sign.", max: 3 },
};

const TONE = { rejected: "critical", reshoot: "pending", confirm: "pending", draft: "ok" } as const;
const WORD = { rejected: "Not accepted", reshoot: "Take it again", confirm: "Check this", draft: "Checked" } as const;

function Marked({ file, box, label = "damage", small = false }: { file: File; box?: Box | null; label?: string; small?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const g = c.getContext("2d");
      if (!g) return;
      g.drawImage(img, 0, 0);
      if (box) {
        const sx = c.width / box.width;
        const sy = c.height / box.height;
        g.lineWidth = Math.max(4, c.width / 250);
        g.strokeStyle = "#ff3b30";
        g.strokeRect(box.x1 * sx, box.y1 * sy, (box.x2 - box.x1) * sx, (box.y2 - box.y1) * sy);
        g.font = `${Math.max(18, c.width / 45)}px system-ui`;
        g.fillStyle = "#ff3b30";
        g.fillText(`${label} ${Math.round(box.confidence * 100)}%`, box.x1 * sx, Math.max(24, box.y1 * sy - 10));
      }
    };
    img.src = url;
    return () => URL.revokeObjectURL(url);
  }, [file, box, label]);
  return <canvas ref={canvas} className={cx("w-auto max-w-full rounded-md border border-line object-contain", small ? "max-h-28 shrink-0 sm:max-w-[11rem]" : "max-h-56")} />;
}

/** A photo for a report, checked the moment it is chosen: picture quality first, then a model
 * reads it into a fixed form, then the rules in code decide. One that passes is kept with the
 * report; one that does not says why, and can be replaced. */
export function EvidenceField({
  reportKind,
  tail,
  onChange,
}: {
  reportKind: string;
  /** For a fault: the aircraft named in the form, to check the registration against. */
  tail?: () => string;
  onChange: (state: PhotoState) => void;
}) {
  const spec = PHOTO[reportKind];
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Intake | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  if (!spec) return null;

  async function check(f: File) {
    setFile(f);
    setResult(null);
    setError(null);
    onChange({ status: "checking", evidenceId: null });
    setBusy(true);
    const form = new FormData();
    form.set("image", f);
    form.set("kind", spec.kind);
    form.set("keep", "true");
    const t = tail?.().trim();
    if (t) form.set("tail", t.toUpperCase());
    try {
      const r = await api<Intake>("/api/intake", { method: "POST", form });
      setResult(r);
      onChange(r.evidence_id ? { status: "kept", evidenceId: r.evidence_id, result: r } : { status: "refused", evidenceId: null, result: r });
    } catch (err) {
      setError(message(err));
      onChange({ status: "refused", evidenceId: null });
    } finally {
      setBusy(false);
    }
  }

  function clear() {
    setFile(null);
    setResult(null);
    setError(null);
    onChange({ status: "none", evidenceId: null });
    if (input.current) input.current.value = "";
  }

  return (
    <div className="flex flex-col gap-1.5 text-[13px]">
      <span className="flex items-center gap-2 font-medium">
        {spec.label}
        {spec.required ? <Pill tone="critical">required</Pill> : <Pill>optional</Pill>}
      </span>
      <input
        ref={input}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
        className="sr-only"
        id={`photo-${reportKind}`}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) check(f);
        }}
      />
      {!file ? (
        <label
          htmlFor={`photo-${reportKind}`}
          className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-control bg-sunken px-4 py-4 transition-colors hover:border-accent hover:bg-accent-soft/40"
        >
          <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface text-accent ring-1 ring-line">
            <ImageUp size={18} />
          </span>
          <span className="min-w-0">
            <b className="block text-sm text-foreground">Add a photo</b>
            <span className="block text-xs text-muted">{spec.hint} JPEG, PNG or WebP under 8 MB.</span>
          </span>
        </label>
      ) : (
        <div className="grid gap-3 rounded-lg border border-line bg-surface p-3 sm:grid-cols-[auto_1fr]">
          <Marked file={file} box={result?.box} label={result?.signals?.label} />
          <div className="flex min-w-0 flex-col gap-2">
            {busy && (
              <p className="flex items-center gap-2 text-sm text-muted">
                <Spinner /> Checking the photo…
              </p>
            )}
            {error && <p className="text-sm text-critical">{error}</p>}
            {result && (
              <>
                <p className="flex flex-wrap items-center gap-2">
                  <Pill tone={TONE[result.status]}>
                    {result.status === "draft" ? <CheckCircle2 size={12} /> : <ShieldAlert size={12} />}
                    {WORD[result.status]}
                  </Pill>
                  {result.confidence && (
                    <Pill tone={result.confidence === "high" ? "ok" : result.confidence === "low" ? "critical" : "pending"}>{result.confidence} confidence</Pill>
                  )}
                </p>
                <p className={cx("text-sm leading-relaxed", result.status === "rejected" ? "text-critical-ink" : "text-foreground")}>{result.message}</p>
                {!result.evidence_id && (
                  <p className="text-xs font-medium text-critical-ink">
                    This photo is not accepted, so the report cannot be filed with it. Add the right photo{spec.required ? "" : ", or remove it"}.
                  </p>
                )}
                {result.suggestion && result.evidence_id && <p className="text-xs text-muted">Severity and description below are filled from the photo: check them and change anything before filing.</p>}
              </>
            )}
            <div className="mt-auto flex flex-wrap gap-2">
              <label htmlFor={`photo-${reportKind}`} className="inline-flex min-h-7 cursor-pointer items-center gap-1.5 rounded-md border border-control px-2.5 text-[13px] font-medium hover:bg-sunken">
                <RefreshCw size={13} /> Another photo
              </label>
              <Button size="sm" tone="ghost" type="button" onClick={clear}>
                <Trash2 size={13} /> Remove
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Where a set of photos stands, for the form around it: the kept ones, why it cannot be filed
 * yet (empty when it can), and whether any is still being checked. */
export type PhotoSetState = { evidenceIds: string[]; problems: string[]; checking: boolean };

type Shot = { key: number; file: File; result?: Intake; error?: string };

const norm = (s: string) => s.trim().toUpperCase();

/** Why one photo cannot go with the report, or null when it can. */
function fault(shot: Shot, expected: string[], gate: boolean): string | null {
  const r = shot.result;
  if (shot.error) return `could not be checked: ${shot.error}`;
  if (!r) return null;
  if (r.status === "reshoot") return `is unclear. ${r.message}`;
  if (r.status !== "draft" || !r.evidence_id) return `is not accepted. ${r.message}`;
  const seen = gate ? r.signals?.gate_seen : r.signals?.tail_seen;
  if (seen && expected.length && !expected.includes(norm(seen))) {
    return gate ? `shows gate ${seen}, not ${expected.join(" or ")}.` : `shows ${seen}, not ${expected[0]}.`;
  }
  return null;
}

/** Several photos for one report (an aircraft fault, a closed gate), each checked as it is
 * added. The report can be filed only when every photo is clear and about this disruption,
 * and at least one shows the registration or the gate's sign. When they all pass, a model
 * writes one description of what they show together. */
export function EvidenceSet({
  reportKind,
  expected,
  onChange,
  onSummary,
}: {
  reportKind: string;
  /** The aircraft, or the gates, chosen in the form, which a photo must show. */
  expected: string[];
  onChange: (state: PhotoSetState) => void;
  onSummary: (summary: { description: string; severity: string | null } | null) => void;
}) {
  const spec = PHOTO[reportKind];
  const gate = spec?.kind === "gate";
  const max = spec?.max ?? 1;
  const [shots, setShots] = useState<Shot[]>([]);
  const [summarising, setSummarising] = useState(false);
  const counter = useRef(0);
  const filled = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const want = expected.map(norm).filter(Boolean);

  const checking = shots.some((s) => !s.result && !s.error);
  const faults = shots.map((s) => fault(s, want, gate));
  const kept = shots.flatMap((s, i) => (!faults[i] && s.result?.evidence_id ? [s.result.evidence_id] : []));
  const problems: string[] = [];
  faults.forEach((f, i) => f && problems.push(`Photo ${i + 1} ${f}`));
  if (spec?.required && !shots.length) problems.push(`Add at least one photo of the ${gate ? "gate" : "fault"}.`);
  if (shots.length && !checking && !faults.some(Boolean)) {
    const named = shots.some((s) => {
      const seen = gate ? s.result?.signals?.gate_seen : s.result?.signals?.tail_seen;
      return seen && want.includes(norm(seen));
    });
    if (!want.length) problems.push(gate ? "Choose the gates, so the photos can be checked against them." : "Choose the aircraft, so the photos can be checked against it.");
    else if (!named)
      problems.push(
        gate
          ? `None of the photos shows the sign for gate ${want.join(" or ")}: add one where it can be read.`
          : `None of the photos shows the registration ${want[0]}: add one where it can be read.`,
      );
  }
  const keptKey = kept.join(",");
  const ready = !checking && !problems.length && kept.length > 0;
  const problemsKey = problems.join("|");

  useEffect(() => {
    onChange({ evidenceIds: kept, problems, checking: checking || summarising });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keptKey, problemsKey, checking, summarising]);

  useEffect(() => {
    if (!ready) {
      // Only take back what the photos wrote, never what the person typed.
      if (filled.current) onSummary(null);
      filled.current = false;
      return;
    }
    let live = true;
    setSummarising(true);
    api<{ description: string; severity: string | null }>("/api/intake/summary", {
      method: "POST",
      json: { evidence_ids: kept, subject: want.join(", ") },
    })
      .then((r) => {
        if (!live) return;
        filled.current = true;
        onSummary(r);
      })
      .catch(() => live && onSummary(null))
      .finally(() => live && setSummarising(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, keptKey]);

  if (!spec) return null;

  async function check(key: number, f: File) {
    const form = new FormData();
    form.set("image", f);
    form.set("kind", spec.kind);
    form.set("keep", "true");
    try {
      const r = await api<Intake>("/api/intake", { method: "POST", form });
      setShots((all) => all.map((s) => (s.key === key ? { ...s, result: r } : s)));
    } catch (err) {
      setShots((all) => all.map((s) => (s.key === key ? { ...s, error: message(err) } : s)));
    }
  }

  function add(files: FileList | null) {
    const room = max - shots.length;
    const picked = [...(files ?? [])].slice(0, Math.max(0, room));
    const added = picked.map((file) => ({ key: ++counter.current, file }));
    setShots((all) => [...all, ...added]);
    added.forEach((s) => check(s.key, s.file));
    if (input.current) input.current.value = "";
  }

  return (
    <div className="flex flex-col gap-1.5 text-[13px]">
      <span className="flex items-center gap-2 font-medium">
        {spec.label}
        {spec.required ? <Pill tone="critical">required</Pill> : <Pill>optional</Pill>}
        <span className="font-mono text-xs text-muted">
          {shots.length}/{max}
        </span>
      </span>
      <input
        ref={input}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp"
        className="sr-only"
        id={`photos-${reportKind}`}
        onChange={(e) => add(e.target.files)}
      />
      {shots.length > 0 && (
        <ol className="flex flex-col gap-2">
          {shots.map((s, i) => {
            const f = faults[i];
            const r = s.result;
            return (
              <li key={s.key} className={cx("flex gap-3 rounded-lg border bg-surface p-2.5", f ? "border-critical/60" : "border-line")}>
                <Marked file={s.file} box={r?.box} label={r?.signals?.label} small />
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <p className="flex flex-wrap items-center gap-1.5">
                    <b>Photo {i + 1}</b>
                    {!r && !s.error ? (
                      <Spinner />
                    ) : f ? (
                      <Pill tone={r?.status === "reshoot" ? "pending" : "critical"}>
                        <ShieldAlert size={12} /> {r?.status === "reshoot" ? "Unclear" : "Not accepted"}
                      </Pill>
                    ) : (
                      <Pill tone="ok">
                        <CheckCircle2 size={12} /> Clear
                      </Pill>
                    )}
                    {(r?.signals?.tail_seen || r?.signals?.gate_seen) && !f && <Pill tone="accent">{gate ? `gate ${r?.signals?.gate_seen}` : r?.signals?.tail_seen}</Pill>}
                  </p>
                  <p className={cx("text-xs leading-relaxed", f ? "text-critical-ink" : "text-muted")}>{s.error ?? r?.message ?? "Checking…"}</p>
                  <button
                    type="button"
                    onClick={() => setShots((all) => all.filter((x) => x.key !== s.key))}
                    className="mt-auto inline-flex w-fit items-center gap-1 text-xs text-muted hover:text-critical"
                  >
                    <Trash2 size={12} /> Remove
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      )}
      {shots.length < max && (
        <label
          htmlFor={`photos-${reportKind}`}
          className="flex cursor-pointer items-center gap-3 rounded-lg border border-dashed border-control bg-sunken px-4 py-3 transition-colors hover:border-accent hover:bg-accent-soft/40"
        >
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-surface text-accent ring-1 ring-line">
            <ImageUp size={17} />
          </span>
          <span className="min-w-0">
            <b className="block text-sm text-foreground">{shots.length ? "Add another photo" : "Add photos"}</b>
            <span className="block text-xs text-muted">
              {spec.hint} JPEG, PNG or WebP under 8 MB{shots.length ? `; ${max - shots.length} more allowed` : ""}.
            </span>
          </span>
        </label>
      )}
      {summarising && (
        <p className="flex items-center gap-2 text-xs text-muted">
          <Spinner /> Writing one description from the photos…
        </p>
      )}
    </div>
  );
}

/** A photo kept with a report, fetched with the viewer's sign-in. */
export function EvidencePhoto({ id, alt }: { id: string; alt: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let made: string | null = null;
    fetch(apiUrl(`/api/evidence/${encodeURIComponent(id)}`), { headers: headers() })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        made = URL.createObjectURL(b);
        setUrl(made);
      })
      .catch(() => setFailed(true));
    return () => {
      if (made) URL.revokeObjectURL(made);
    };
  }, [id]);
  if (failed) return <div className="grid h-32 place-items-center rounded-md bg-sunken text-xs text-muted">Photo not available to you</div>;
  if (!url) return <div className="grid h-32 place-items-center rounded-md bg-sunken"><Spinner /></div>;
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={url} alt={alt} className="max-h-64 w-auto max-w-full rounded-md border border-line object-contain" />;
}
