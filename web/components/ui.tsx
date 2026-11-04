import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  Ref,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

import { cap, words } from "@/lib/format";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "primary" | "secondary" | "danger" | "danger-quiet" | "ghost" | "accent";

export function Button({
  tone = "primary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone; size?: "sm" | "md" }) {
  const tones: Record<Tone, string> = {
    primary: "bg-brand text-brand-ink hover:bg-[#2b2f35]",
    accent: "bg-accent text-white hover:bg-[#184d7d]",
    secondary: "border border-control bg-surface text-foreground hover:bg-sunken",
    danger: "bg-critical text-white hover:bg-[#a51f25]",
    "danger-quiet": "border border-[#f1c0bc] bg-surface text-critical hover:bg-critical-soft",
    ghost: "text-muted hover:bg-sunken hover:text-foreground",
  };
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-55",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        size === "sm" ? "min-h-7 px-2.5 py-1 text-[13px]" : "min-h-9 px-3.5 py-1.5 text-sm",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}

export function Panel({
  title,
  icon,
  action,
  children,
  className,
  flush,
  id,
  backdrop,
}: {
  title?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
  id?: string;
  /** A picture behind the panel's content, very faint, for a little atmosphere. */
  backdrop?: string;
}) {
  return (
    <section id={id} className={cx("relative flex min-w-0 flex-col rounded-lg border border-line bg-surface", backdrop && "overflow-hidden", className)}>
      {backdrop && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-cover bg-center opacity-[0.12] [mask-image:linear-gradient(160deg,transparent_5%,black_60%)]"
          style={{ backgroundImage: `url(${backdrop})` }}
        />
      )}
      {title && (
        <header className="relative flex min-h-12 flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-rule px-4 py-2.5 sm:px-5">
          <h2 className="flex items-center gap-2 text-[15px] font-semibold text-foreground [&_svg]:text-accent">
            {icon}
            {title}
          </h2>
          {action && <div className="text-[13px] text-muted">{action}</div>}
        </header>
      )}
      <div className={cx("relative min-w-0 flex-1", flush ? "" : "p-4 sm:p-5")}>{children}</div>
    </section>
  );
}

export type PillTone = "critical" | "pending" | "ok" | "accent" | "neutral";

const PILL: Record<PillTone, string> = {
  critical: "bg-critical-soft text-critical-ink ring-[#f3c4c0]",
  pending: "bg-pending-soft text-pending-ink ring-[#f6dc9c]",
  ok: "bg-ok-soft text-ok-ink ring-[#b9e6c9]",
  accent: "bg-accent-soft text-accent-ink ring-[#cbdcec]",
  neutral: "bg-sunken text-[#3a3f46] ring-line",
};

export function Pill({ tone = "neutral", children, title, className }: { tone?: PillTone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        PILL[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const LEVEL: Record<string, { tone: PillTone; dot: string }> = {
  high: { tone: "critical", dot: "bg-critical" },
  medium: { tone: "pending", dot: "bg-pending" },
  low: { tone: "neutral", dot: "bg-faint" },
};

export function SeverityBadge({ severity }: { severity?: { level: string; reasons?: string[] } | null }) {
  if (!severity) return null;
  const s = LEVEL[severity.level] ?? LEVEL.low;
  const why = (severity.reasons ?? []).join("; ");
  return (
    <Pill tone={s.tone} title={why ? `${cap(severity.level)} severity: ${why}` : `${cap(severity.level)} severity`}>
      <span className={cx("h-1.5 w-1.5 rounded-full", s.dot)} />
      {cap(severity.level)}
    </Pill>
  );
}

const STATUS_TONE: Record<string, PillTone> = {
  open: "critical",
  in_progress: "accent",
  resolved: "pending",
  closed: "ok",
  revoked: "neutral",
  submitted: "pending",
  approved: "ok",
  rejected: "critical",
  sent_back: "pending",
  withdrawn: "neutral",
  stale: "critical",
  offered: "accent",
  accepted: "pending",
  filled: "ok",
  needs_decision: "critical",
  awaiting_approval: "pending",
};
const STATUS_LABEL: Record<string, string> = {
  in_progress: "In progress",
  resolved: "Awaiting sign-off",
  needs_decision: "Decision needed",
  awaiting_approval: "Awaiting approval",
};

export function StatusPill({ value }: { value: string }) {
  return <Pill tone={STATUS_TONE[value] ?? "neutral"}>{STATUS_LABEL[value] ?? cap(words(value))}</Pill>;
}

/** Ids, times and stations in monospace, so C-1042 and T-1018 read at a glance. */
export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cx("whitespace-nowrap font-mono text-[0.92em] tracking-tight", className)}>{children}</span>;
}

export function Notice({
  tone = "info",
  children,
  className,
  icon,
}: {
  tone?: "info" | "error" | "ok" | "pending";
  children: ReactNode;
  className?: string;
  icon?: ReactNode;
}) {
  const tones = {
    info: "border-[#cbdcec] bg-accent-soft text-accent-ink",
    error: "border-[#f3c4c0] bg-critical-soft text-critical-ink",
    ok: "border-[#b9e6c9] bg-ok-soft text-ok-ink",
    pending: "border-[#f6dc9c] bg-pending-soft text-pending-ink",
  };
  return (
    <div className={cx("flex items-start gap-2 rounded-md border px-3 py-2 text-sm", tones[tone], className)}>
      {icon && <span className="mt-0.5 shrink-0">{icon}</span>}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-label="Loading"
      role="status"
      className={cx("inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent", className)}
    />
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex min-h-[45vh] w-full flex-1 items-center justify-center gap-2 py-10 text-sm text-muted">
      <Spinner /> {label}
    </div>
  );
}

export function Empty({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-9 text-center text-sm text-muted [&_svg]:text-faint">
      {icon}
      {children}
    </div>
  );
}

export function PageHeader({ title, lede, actions, eyebrow }: { title: ReactNode; lede?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-3 border-b border-rule pb-5">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {eyebrow && <div className="mb-1 text-[13px] text-muted">{eyebrow}</div>}
          <h1 className="text-[24px] font-semibold leading-tight tracking-[-0.015em]">{title}</h1>
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {lede && <p className="w-full text-[14.5px] leading-relaxed text-muted">{lede}</p>}
    </header>
  );
}

export function Kpis({ children }: { children: ReactNode }) {
  return (
    <div className="mb-5 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-[repeat(auto-fit,minmax(170px,1fr))]">
      {children}
    </div>
  );
}

export function Kpi({
  value,
  label,
  sub,
  tone,
  icon,
}: {
  value: ReactNode;
  label: ReactNode;
  sub?: ReactNode;
  tone?: "critical" | "pending" | "ok";
  icon?: ReactNode;
}) {
  return (
    <div className="bg-surface px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted [&_svg]:text-faint">
        {icon}
        {label}
      </div>
      <div
        className={cx(
          "mt-1 text-[26px] font-semibold leading-none tracking-[-0.02em] tabular-nums",
          tone === "critical" && "text-critical",
          tone === "pending" && "text-pending",
          tone === "ok" && "text-ok",
        )}
      >
        {value}
        {sub && <span className="ml-1.5 text-sm font-normal tracking-normal text-muted">{sub}</span>}
      </div>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: ReactNode; count?: number }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex max-w-full flex-wrap rounded-md border border-line bg-sunken p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded px-2.5 py-1 text-[13px] transition-colors",
            value === o.value ? "bg-surface font-semibold text-foreground shadow-[0_0_0_1px_var(--line)]" : "text-muted hover:text-foreground",
          )}
        >
          {o.label}
          {o.count !== undefined && <span className="ml-1.5 font-mono text-[11px] text-faint">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

export const control =
  "h-9 w-full min-w-0 rounded-md border border-control bg-surface px-2.5 text-sm text-foreground outline-none transition-colors placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/15 disabled:bg-sunken disabled:text-muted";

export function Label({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx("flex min-w-0 flex-col gap-1 text-[13px]", className)}>
      <span className="font-medium text-foreground">{label}</span>
      {children}
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </label>
  );
}

export function Input({
  label,
  hint,
  className,
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label: ReactNode; hint?: ReactNode; ref?: Ref<HTMLInputElement> }) {
  return (
    <Label label={label} hint={hint} className={className}>
      <input ref={ref} className={control} {...props} />
    </Label>
  );
}

export function Select({
  label,
  hint,
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { label: ReactNode; hint?: ReactNode }) {
  return (
    <Label label={label} hint={hint} className={className}>
      <select className={control} {...props}>
        {children}
      </select>
    </Label>
  );
}

export function TextArea({ label, hint, className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: ReactNode; hint?: ReactNode }) {
  return (
    <Label label={label} hint={hint} className={className}>
      <textarea className={cx(control, "h-auto min-h-20 py-2")} {...props} />
    </Label>
  );
}

export function Chips<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div className="flex flex-col gap-1 text-[13px]">
      <span className="font-medium">{label}</span>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label={label}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={value === o.value}
            onClick={() => onChange(o.value)}
            className={cx(
              "rounded-full border px-3 py-1 text-[13px] transition-colors",
              value === o.value ? "border-accent bg-accent-soft font-semibold text-accent-ink" : "border-control bg-surface hover:bg-sunken",
            )}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A filled bar against a limit: amber past 80%, red past 95%. */
export function Meter({ value, max, label }: { value: number; max: number; label: ReactNode }) {
  const ratio = max > 0 ? value / max : 0;
  const tone = ratio > 0.95 ? "bg-critical" : ratio > 0.8 ? "bg-pending" : "bg-accent";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-[13px]">
        <span>{label}</span>
        <span className="font-mono text-xs text-muted">
          {value}h / {max}h
        </span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-sunken">
        <i className={cx("block h-full rounded-full", tone)} style={{ width: `${Math.min(100, ratio * 100)}%` }} />
      </div>
      <p className="mt-1 text-xs text-faint">{Math.max(0, max - value).toFixed(1)}h left before the limit</p>
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto">
      <table
        className={cx(
          "w-full border-collapse text-left text-sm",
          "[&_th]:whitespace-nowrap [&_th]:border-b [&_th]:border-line [&_th]:px-3 [&_th]:py-2 [&_th]:text-xs [&_th]:font-medium [&_th]:text-muted",
          "[&_td]:border-b [&_td]:border-rule [&_td]:px-3 [&_td]:py-2.5 [&_td]:align-top [&_tr:last-child_td]:border-b-0",
          "[&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4",
          className,
        )}
      >
        {children}
      </table>
    </div>
  );
}

export function Dl({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
