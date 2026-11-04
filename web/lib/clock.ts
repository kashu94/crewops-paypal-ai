const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The operation's date from the server clock ("Thu 05 Nov 2026, 06:00 IST" -> "2026-11-05").
 * The demo runs on a fixed clock, so forms default to its day, not the browser's. */
export function opDay(clock: string | undefined, fallback = "2026-11-05"): string {
  const m = /(\d{1,2}) (\w{3}) (\d{4})/.exec(clock ?? "");
  if (!m) return fallback;
  const month = MONTHS.indexOf(m[2]);
  if (month < 0) return fallback;
  return `${m[3]}-${String(month + 1).padStart(2, "0")}-${m[1].padStart(2, "0")}`;
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** The day forms should start on: the operation's day, or the next one late in the evening
 * when nothing is left to fly today. */
export function workDay(clock: string | undefined): string {
  const day = opDay(clock);
  const hour = Number(/, (\d{2}):/.exec(clock ?? "")?.[1] ?? 0);
  return hour >= 20 ? addDays(day, 1) : day;
}
