"use client";

/* Signing in to the demo in two steps: pick a role from colour-coded cards, then a person from
   that role's dropdown. Ordered along the demo path: controller, approver, crew. */

import { ArrowRight, Check, ChevronDown, Eye, EyeOff, LockKeyhole, ClipboardList, Headset, KeyRound, Plane, ScrollText, Search, ShieldCheck, UserCog, Volume2, type LucideIcon } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

import type { DemoAccount } from "@/lib/api";
import { initials } from "@/lib/format";
import { ROLE_LABEL, type Role } from "@/lib/roles";

import { cx, Spinner } from "./ui";

type RoleCard = { role: Role; color: string; icon: LucideIcon; does: string; one: string; step?: number };

const CARDS: RoleCard[] = [
  { role: "controller", one: "a controller", color: "#1d5c96", icon: Headset, does: "Finds legal cover and submits plans", step: 1 },
  { role: "approver", one: "an approver", color: "#6a4bc4", icon: ShieldCheck, does: "Approves and confirms with PayPal", step: 2 },
  { role: "crew", one: "a crew member", color: "#0b7f63", icon: Plane, does: "Answers callouts, sees roster and pay", step: 3 },
  { role: "reporter", one: "a station reporter", color: "#b26a0c", icon: ClipboardList, does: "Reports sick calls, delays and gates" },
  { role: "auditor", one: "the auditor", color: "#4a5a6e", icon: ScrollText, does: "Reads every decision and payment" },
  { role: "admin", one: "the admin", color: "#b23a64", icon: UserCog, does: "Accounts, alert addresses and rules" },
  { role: "super_admin", one: "the super admin", color: "#8f2d2d", icon: KeyRound, does: "All of admin, plus resetting the demo" },
  { role: "speaker", one: "the ops-room speaker", color: "#0d6f86", icon: Volume2, does: "The ops-room speaker's feed" },
];

/** The role's colour, and a wash of it, as CSS variables for one card. */
const tint = (color: string): CSSProperties =>
  ({
    "--role": color,
    "--role-soft": `color-mix(in srgb, ${color} 9%, var(--surface))`,
    "--role-line": `color-mix(in srgb, ${color} 30%, var(--line))`,
  }) as CSSProperties;

export function RolePicker({
  accounts,
  busy,
  onPick,
  needsPassword,
}: {
  accounts: DemoAccount[];
  busy: string | null;
  onPick: (a: DemoAccount, password: string) => void;
  needsPassword: boolean;
}) {
  const byRole = useMemo(() => {
    const m = new Map<Role, DemoAccount[]>();
    for (const a of accounts) m.set(a.role, [...(m.get(a.role) ?? []), a]);
    return m;
  }, [accounts]);
  const cards = CARDS.filter((c) => byRole.has(c.role));
  const [role, setRole] = useState<Role | null>(null);
  const [person, setPerson] = useState<DemoAccount | null>(null);
  const [password, setPassword] = useState("");
  const [reveal, setReveal] = useState(false);
  const step2 = useRef<HTMLDivElement>(null);
  const ready = Boolean(person) && (!needsPassword || password.length > 0);
  const go = () => {
    if (person && ready && busy === null) onPick(person, password);
  };

  const choose = (r: Role) => {
    setRole(r);
    setPerson(byRole.get(r)?.[0] ?? null);
    requestAnimationFrame(() => step2.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
  };
  const card = cards.find((c) => c.role === role);
  const people = role ? byRole.get(role) ?? [] : [];

  return (
    <div className="mt-6">
      <StepLabel n={1} text="Pick a role" hint="Demo path: 1 → 2 → 3" />
      <div role="radiogroup" aria-label="Role" className="grid grid-cols-2 gap-2 min-[440px]:gap-2.5">
        {cards.map((c) => {
          const on = c.role === role;
          const Icon = c.icon;
          const count = byRole.get(c.role)?.length ?? 0;
          return (
            <button
              key={c.role}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => choose(c.role)}
              style={tint(c.color)}
              className={cx(
                "group relative flex items-start gap-2.5 rounded-xl border p-2.5 text-left min-[440px]:gap-3 min-[440px]:p-3 transition-[background-color,border-color,box-shadow,transform] duration-150",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--role)]/40",
                on
                  ? "border-[var(--role)] bg-[var(--role-soft)] shadow-[0_0_0_1px_var(--role),0_6px_18px_-8px_var(--role)]"
                  : "border-[var(--role-line)] bg-[var(--role-soft)]/60 hover:-translate-y-px hover:border-[var(--role)] hover:bg-[var(--role-soft)]",
              )}
            >
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--role)] text-white min-[440px]:h-9 min-[440px]:w-9 shadow-[inset_0_-1px_0_rgb(0_0_0/0.15)]">
                <Icon size={17} strokeWidth={2.1} />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="text-[13px] font-semibold leading-tight text-[var(--role)] min-[440px]:text-sm">{ROLE_LABEL[c.role]}</span>
                  {c.step && (
                    <span className="grid h-4 w-4 place-items-center rounded-full border border-[var(--role)]/40 text-[10px] font-semibold leading-none text-[var(--role)]" title={`Step ${c.step} of the demo`}>
                      {c.step}
                    </span>
                  )}
                </span>
                <span className="mt-0.5 hidden text-xs leading-snug text-muted min-[440px]:block">{c.does}</span>
                <span className="mt-1 block text-[11px] text-faint">{count === 1 ? "1 person" : `${count} people`}</span>
              </span>
              <span
                aria-hidden
                className={cx(
                  "absolute right-2.5 top-2.5 grid h-5 w-5 place-items-center rounded-full transition-all",
                  on ? "scale-100 bg-[var(--role)] text-white" : "scale-75 opacity-0",
                )}
              >
                <Check size={12} strokeWidth={3} />
              </span>
            </button>
          );
        })}
      </div>

      <div ref={step2} className={cx("mt-6 transition-opacity duration-200", card ? "opacity-100" : "pointer-events-none opacity-40")} aria-disabled={!card}>
        <StepLabel n={2} text={card ? `Pick ${card.one}` : "Pick a person"} />
        <form
          style={tint(card?.color ?? "#6b7785")}
          className={cx("flex flex-col gap-2.5", !needsPassword && "sm:flex-row")}
          onSubmit={(e) => {
            e.preventDefault();
            go();
          }}
        >
          <PersonSelect key={role ?? "none"} people={people} value={person} onChange={setPerson} disabled={!card} />
          {needsPassword && (
            <label className={cx("flex h-11 items-center gap-2.5 rounded-lg border border-control bg-surface px-3 transition-colors focus-within:border-[var(--role)] focus-within:ring-2 focus-within:ring-[var(--role)]/15", !card && "opacity-60")}>
              <LockKeyhole size={16} className="shrink-0 text-muted" />
              <input
                type={reveal ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={!card}
                autoComplete="current-password"
                placeholder="Demo password"
                aria-label="Demo password"
                className="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-faint"
              />
              <button type="button" onClick={() => setReveal((v) => !v)} disabled={!card} className="rounded p-1 text-muted hover:text-foreground" aria-label={reveal ? "Hide password" : "Show password"}>
                {reveal ? <EyeOff size={15} /> : <Eye size={15} />}
              </button>
            </label>
          )}
          <button
            type="submit"
            disabled={!ready || busy !== null}
            className="inline-flex h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-[var(--role)] px-4 text-sm font-semibold text-white shadow-[0_6px_16px_-8px_var(--role)] transition-[filter,transform] hover:brightness-110 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            {busy ? <Spinner className="h-4 w-4" /> : null}
            {person ? `Continue as ${first(person.label)}` : "Continue"}
            {!busy && <ArrowRight size={15} />}
          </button>
        </form>
        {!card && <p className="mt-2 text-xs text-faint">Choose a role above first.</p>}
      </div>
    </div>
  );
}

function StepLabel({ n, text, hint }: { n: number; text: string; hint?: string }) {
  return (
    <div className="mb-2.5 flex items-baseline gap-2">
      <span className="grid h-5 w-5 place-items-center self-center rounded-full bg-foreground text-[11px] font-semibold text-surface">{n}</span>
      <span className="text-sm font-semibold">{text}</span>
      {hint && <span className="ml-auto text-xs text-faint">{hint}</span>}
    </div>
  );
}

/** "D. Reddy, captain" -> "D. Reddy" */
const first = (label: string) => label.split(",")[0];
const detail = (a: DemoAccount) => {
  const rest = a.label.split(",").slice(1).join(",").trim();
  return [rest && rest.charAt(0).toUpperCase() + rest.slice(1), a.crew_id].filter(Boolean).join(" · ");
};

/** A listbox dropdown: type to filter when the list is long, arrows and Enter to choose. */
function PersonSelect({ people, value, onChange, disabled }: { people: DemoAccount[]; value: DemoAccount | null; onChange: (a: DemoAccount) => void; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [up, setUp] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const id = useId();
  const searchable = people.length > 6;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? people.filter((p) => `${p.label} ${p.user} ${p.crew_id ?? ""}`.toLowerCase().includes(q)) : people;
  }, [people, query]);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-i="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active, open]);

  const show = () => {
    if (disabled) return;
    setQuery("");
    setActive(Math.max(0, people.findIndex((p) => p.user === value?.user)));
    const box = root.current?.getBoundingClientRect();
    setUp(!!box && window.innerHeight - box.bottom < 340 && box.top > window.innerHeight - box.bottom);
    setOpen(true);
    if (searchable) requestAnimationFrame(() => search.current?.focus());
  };
  const pick = (a: DemoAccount | undefined) => {
    if (!a) return;
    onChange(a);
    setOpen(false);
  };
  const keys = (e: KeyboardEvent) => {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        show();
      }
      return;
    }
    const moves: Record<string, () => void> = {
      ArrowDown: () => setActive((i) => Math.min(shown.length - 1, i + 1)),
      ArrowUp: () => setActive((i) => Math.max(0, i - 1)),
      Enter: () => pick(shown[active]),
      Escape: () => setOpen(false),
    };
    const move = moves[e.key];
    if (move) {
      e.preventDefault();
      move();
    }
  };

  return (
    <div ref={root} className="relative min-w-0 flex-1" onKeyDown={keys}>
      <button
        type="button"
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => (open ? setOpen(false) : show())}
        className={cx(
          "flex h-11 w-full items-center gap-2.5 rounded-lg border bg-surface px-2.5 text-left transition-colors",
          open ? "border-[var(--role)] ring-2 ring-[var(--role)]/15" : "border-control hover:border-[var(--role)]",
          "disabled:cursor-not-allowed",
        )}
      >
        {value ? (
          <>
            <Avatar a={value} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium leading-tight">{first(value.label)}</span>
              <span className="block truncate text-[11px] leading-tight text-muted">{detail(value) || value.user}</span>
            </span>
          </>
        ) : (
          <span className="flex-1 text-sm text-faint">Choose a person…</span>
        )}
        <ChevronDown size={16} className={cx("shrink-0 text-muted transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div
          className={cx(
            "animate-rise absolute inset-x-0 z-30 overflow-hidden rounded-xl border border-line bg-surface shadow-[0_18px_40px_-12px_rgb(0_0_0/0.28)]",
            up ? "bottom-[calc(100%+6px)]" : "top-[calc(100%+6px)]",
          )}
        >
          {searchable && (
            <label className="flex items-center gap-2 border-b border-line px-3">
              <Search size={14} className="text-faint" />
              <input
                ref={search}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(0);
                }}
                placeholder={`Search ${people.length} people by name, rank or id`}
                aria-label="Search people"
                className="h-10 w-full bg-transparent text-sm outline-none placeholder:text-faint"
              />
            </label>
          )}
          <ul ref={list} id={id} role="listbox" aria-label="People" className="max-h-72 overflow-y-auto p-1.5">
            {shown.map((a, i) => {
              const chosen = a.user === value?.user;
              return (
                <li
                  key={a.user}
                  data-i={i}
                  role="option"
                  aria-selected={chosen}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => pick(a)}
                  className={cx("flex cursor-pointer items-center gap-2.5 rounded-lg px-2 py-1.5", i === active ? "bg-[var(--role-soft)]" : "")}
                >
                  <Avatar a={a} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium leading-tight">{first(a.label)}</span>
                    <span className="block truncate text-[11px] leading-tight text-muted">
                      {detail(a)}
                      <span className="font-mono text-faint">{detail(a) ? " · " : ""}{a.user}</span>
                    </span>
                  </span>
                  {chosen && <Check size={15} className="shrink-0 text-[var(--role)]" strokeWidth={2.6} />}
                </li>
              );
            })}
            {!shown.length && <li className="px-2 py-3 text-center text-sm text-muted">Nobody matches “{query}”.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}

function Avatar({ a }: { a: DemoAccount }) {
  return (
    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-[var(--role-soft)] text-[11px] font-semibold text-[var(--role)] ring-1 ring-[var(--role-line)]">
      {initials(first(a.label))}
    </span>
  );
}
