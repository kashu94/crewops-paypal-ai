export const inr = (n: unknown) => (typeof n === "number" ? `₹${n.toLocaleString("en-IN")}` : "–");

export const plural = (n: number, word: string, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

export const initials = (name: string | undefined) =>
  String(name || "?")
    .split(/[.\s_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join("");

/** "2026-11-05 06:00 IST" -> "06:00". */
export const hhmm = (s: string | null | undefined) => (s ? s.slice(11, 16) : "–");

/** "2026-11-05 06:00 IST" -> "05 Nov 06:00". */
export function short(s: string | null | undefined): string {
  if (!s) return "–";
  if (!/^\d{4}-\d{2}-\d{2}/.test(s)) return s;
  const d = new Date(`${s.slice(0, 10)}T00:00:00Z`);
  const day = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
  return s.length > 10 ? `${day} ${s.slice(11, 16)}` : day;
}

export const words = (s: string | null | undefined) => String(s ?? "").replace(/_/g, " ");

export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** Times as the speaker should say them. */
export const speakable = (text: string) =>
  text
    .replace(/\bC-(\d{4})\b/g, "C $1")
    .replace(/\bT-(\d{4})\b/g, "trip $1")
    .replace(/\bR-\w+/g, "the report")
    .replace(/\bPL-\w+/g, "the plan")
    .replace(/\bCO-\w+/g, "the callout")
    .replace(/₹|INR\s?/g, "rupees ")
    .replace(/\bIST\b/g, "");

export const KIND_LABEL: Record<string, string> = {
  crew_unavailable: "Crew can't fly",
  planned_leave: "Planned leave",
  certification_lapse: "Certificate lapse",
  flight_delay: "Delay",
  aircraft_technical: "Aircraft fault",
  station_disruption: "Station closure",
  gate_closure: "Gate closure",
  gate_change: "Gate change",
};

export const kindLabel = (k: string) => KIND_LABEL[k] ?? cap(words(k));

/** 4.58 -> "4h35m": how a controller reads a delay. */
export function hm(hours: number): string {
  const minutes = Math.round(hours * 60);
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}m`;
}
