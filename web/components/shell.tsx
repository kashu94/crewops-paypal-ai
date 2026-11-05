"use client";

import {
  Activity,
  CalendarClock,
  CalendarDays,
  ChevronDown,
  DoorOpen,
  Gauge,
  Inbox,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  MessageSquareText,
  Settings,
  ShieldCheck,
  TriangleAlert,
  Tv,
  UserPlus,
  Volume2,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { authMode, demoIdentity, setDemoIdentity, signOut, type DemoAccount } from "@/lib/api";
import { demoAccounts } from "@/lib/demo";
import { initials, words } from "@/lib/format";
import { homeFor, navFor, ROLE_LABEL, type NavItem } from "@/lib/roles";

import { DesktopAlerts, forgetDesktopAlerts } from "./desktop-alerts";
import { useSession } from "./session";
import { cx } from "./ui";

const ICONS: Record<string, LucideIcon> = {
  desk: LayoutDashboard,
  disruptions: ListChecks,
  approvals: ShieldCheck,
  cover: UserPlus,
  report: TriangleAlert,
  operations: CalendarClock,
  gates: DoorOpen,
  assistant: MessageSquareText,
  activity: Activity,
  limits: Gauge,
  crew: CalendarDays,
  messages: Inbox,
  admin: Settings,
  board: Tv,
  speaker: Volume2,
  pay: Wallet,
};

export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="shrink-0">
      <rect width="32" height="32" rx="8" fill="#1d5c96" />
      <path d="M7 18.5 25 11l-5.5 12.5-3.2-4.8z" fill="#fff" />
    </svg>
  );
}

function Brand() {
  const { me } = useSession();
  return (
    <Link href={homeFor(me.role)} className="flex h-12 items-center gap-2.5 px-2">
      <Logo />
      <span className="leading-tight">
        <span className="block text-[15px] font-semibold tracking-tight">CrewPay</span>
        <span className="block text-[11px] text-muted">Crew control</span>
      </span>
    </Link>
  );
}

function matches(path: string, item: NavItem) {
  return path.startsWith(item.href);
}

function Nav({ onPick }: { onPick?: () => void }) {
  const { me, counts } = useSession();
  const raw = usePathname();
  const path = raw.endsWith("/") ? raw : `${raw}/`;
  return (
    <nav aria-label="Pages" className="mt-2 flex flex-col">
      {navFor(me.role).map((g) => (
        <div key={g.group} className="mb-1">
          <div className="px-2.5 pb-1 pt-2.5 text-[11px] font-medium uppercase tracking-[0.08em] text-faint">{g.group}</div>
          {g.items.map((item) => {
            const Icon = ICONS[item.icon] ?? LayoutDashboard;
            const active = !item.external && matches(path, item);
            const n = item.count === "approvals" ? counts.approvals : 0;
            const body = (
              <>
                <Icon size={16} strokeWidth={1.7} className={active ? "text-accent" : "text-muted"} />
                <span className="flex-1 truncate">{item.label}</span>
                {n > 0 && (
                  <span className="min-w-5 rounded-full bg-pending-soft px-1.5 text-center text-[11px] font-semibold text-pending-ink">{n}</span>
                )}
                {item.external && <span className="text-[10px] text-faint">↗</span>}
              </>
            );
            const cls = cx(
              "flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
              active ? "bg-surface font-medium text-foreground shadow-[0_0_0_1px_var(--line)]" : "text-[#444a52] hover:bg-active",
            );
            return item.external ? (
              <a key={item.href + item.label} href={item.href} target="_blank" rel="noopener" title={item.hint} className={cls}>
                {body}
              </a>
            ) : (
              <Link key={item.href + item.label} href={item.href} title={item.hint} aria-current={active ? "page" : undefined} onClick={onPick} className={cls}>
                {body}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

function Account() {
  const { me } = useSession();
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const demo = authMode() === "demo";
  useEffect(() => {
    if (demo) demoAccounts().then(setAccounts);
  }, [demo]);
  const current = demoIdentity();

  async function leave() {
    await forgetDesktopAlerts().catch(() => undefined);
    signOut();
    window.location.assign("/login/");
  }

  return (
    <div className="mt-auto border-t border-line pt-3">
      <div className="flex items-center gap-2.5 px-1.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{initials(me.name || me.user)}</span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-sm font-medium">{me.name || me.user}</span>
          <span className="block truncate text-xs text-muted">
            {ROLE_LABEL[me.role] ?? me.role}
            {me.desk && <> · {me.desk.name}</>}
            {me.crew_id && <> · {me.crew_id}</>}
          </span>
        </span>
        <button className="rounded-md p-1.5 text-muted hover:bg-active hover:text-foreground" title="Sign out" aria-label="Sign out" onClick={leave}>
          <LogOut size={16} />
        </button>
      </div>
      {demo && accounts.length > 0 && (
        <label className="mt-2.5 block px-1.5">
          <span className="text-[11px] font-medium uppercase tracking-[0.08em] text-faint">Demo: act as</span>
          <span className="relative mt-1 block">
            <select
              aria-label="Demo identity"
              value={current?.user ?? me.user}
              onChange={(e) => {
                const a = accounts.find((x) => x.user === e.target.value);
                if (!a) return;
                setDemoIdentity(a);
                window.location.assign(homeFor(a.role));
              }}
              className="h-8 w-full appearance-none rounded-md border border-control bg-surface pl-2 pr-7 text-[13px]"
            >
              {accounts.map((a) => (
                <option key={a.user} value={a.user}>
                  {a.label}
                </option>
              ))}
            </select>
            <ChevronDown size={14} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-muted" />
          </span>
        </label>
      )}
    </div>
  );
}

function QueueButton() {
  const { queue, counts } = useSession();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  if (counts.queue === 0) return null;
  return (
    <div ref={box} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={cx(
          "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset",
          counts.critical ? "bg-critical-soft text-critical-ink ring-[#f3c4c0]" : "bg-pending-soft text-pending-ink ring-[#f6dc9c]",
        )}
      >
        <span className={cx("h-1.5 w-1.5 rounded-full", counts.critical ? "bg-critical" : "bg-pending")} />
        {counts.queue} waiting on you
      </button>
      {open && (
        <div className="animate-rise absolute right-0 top-9 z-50 w-[min(380px,calc(100vw-2rem))] overflow-hidden rounded-lg border border-line bg-surface shadow-[0_12px_32px_rgb(0_0_0/0.14)]">
          <p className="border-b border-rule px-4 py-2.5 text-xs font-medium text-muted">Waiting on you</p>
          <ul className="max-h-[60vh] divide-y divide-rule overflow-y-auto">
            {queue.map((q) => (
              <li key={q.kind + q.ref}>
                <Link href={q.link || "#"} onClick={() => setOpen(false)} className="flex gap-2.5 px-4 py-2.5 hover:bg-sunken">
                  <span className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", q.tone === "critical" ? "bg-critical" : q.tone === "pending" ? "bg-pending" : "bg-accent")} />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{q.title}</span>
                    <span className="block text-xs text-muted">{words(q.detail)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { me, live } = useSession();
  const raw = usePathname();
  const path = raw.endsWith("/") ? raw : `${raw}/`;
  const current = navFor(me.role)
    .flatMap((g) => g.items)
    .find((i) => !i.external && matches(path, i));
  return (
    <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center gap-3 bg-background px-3 lg:static lg:pl-1 lg:pr-3">
      <button className="rounded-md p-1.5 text-muted hover:bg-active hover:text-foreground lg:hidden" onClick={onMenu} aria-label="Open the menu">
        <Menu size={18} />
      </button>
      <span className="lg:hidden">
        <Logo size={24} />
      </span>
      <p className="hidden min-w-0 truncate text-[13px] text-muted sm:block">
        {current ? (
          <>
            <b className="font-semibold text-foreground">{current.label}</b> <span className="text-faint">·</span> {current.hint}
          </>
        ) : (
          <b className="font-semibold text-foreground">CrewPay</b>
        )}
      </p>
      <div className="ml-auto flex items-center gap-2">
        <QueueButton />
        <span className="hidden items-center gap-1.5 text-xs text-muted md:flex" title={live ? "Up to date: refreshes when anything changes" : "Cannot reach the server"}>
          <span className={cx("h-2 w-2 rounded-full", live ? "bg-ok" : "bg-faint")} />
          <span className="font-mono">{me.clock}</span>
        </span>
        <DesktopAlerts />
      </div>
    </header>
  );
}

function MenuSheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
      <button className="absolute inset-0 bg-black/30" aria-label="Close the menu" onClick={onClose} />
      <div className="animate-rise quiet-scroll absolute inset-y-0 left-0 flex w-[280px] flex-col overflow-y-auto bg-background px-3 pb-3 shadow-xl">
        <div className="flex items-center justify-between">
          <Brand />
          <button className="rounded-md p-1.5 text-muted hover:bg-active" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <Nav onPick={onClose} />
        <Account />
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [menu, setMenu] = useState(false);
  return (
    <div className="flex min-h-screen overflow-x-clip bg-background">
      <aside className="quiet-scroll sticky top-0 hidden h-screen w-[236px] shrink-0 flex-col overflow-y-auto px-3 pb-3 lg:flex">
        <Brand />
        <Nav />
        <Account />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setMenu(true)} />
        <main className="flex-1 border-line bg-surface px-4 py-5 sm:px-6 lg:mb-2 lg:mr-2 lg:rounded-2xl lg:border lg:px-8 lg:py-7">{children}</main>
      </div>
      {menu && <MenuSheet onClose={() => setMenu(false)} />}
    </div>
  );
}
