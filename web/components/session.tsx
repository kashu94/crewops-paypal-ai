"use client";

import { CheckCircle2, CircleAlert, Clock3, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { api, apiUrl, hasSession, loadConfig, message, tool, toSignIn, type Config } from "@/lib/api";
import { isDeskRole, type Me } from "@/lib/roles";
import type { QueueItem, SpeakerFeed } from "@/lib/types";

import { Button, cx, Spinner } from "./ui";

type Toast = { id: number; text: string; tone: "ok" | "critical" | "pending" };

type Session = {
  me: Me;
  config: Config;
  /** Moves whenever /api/version moves: pages reload their data on it. */
  tick: number;
  live: boolean;
  counts: { approvals: number; queue: number; critical: number };
  queue: QueueItem[];
  bump: () => void;
  flash: (text: string, tone?: Toast["tone"]) => void;
  can: (toolName: string) => boolean;
};

const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

/** Loads who is signed in, then polls /api/version every 3 s and moves `tick` when any of its
 * numbers change, so the page on screen refreshes itself. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [config, setConfig] = useState<Config | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [live, setLive] = useState(true);
  const [counts, setCounts] = useState({ approvals: 0, queue: 0, critical: 0 });
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const last = useRef("");

  useEffect(() => {
    let alive = true;
    (async () => {
      const c = await loadConfig();
      if (!alive) return;
      setConfig(c);
      if (!hasSession()) return toSignIn();
      try {
        const m = await api<Me>("/api/me");
        if (!alive) return;
        if (m.role === "speaker") return router.replace("/speaker/");
        setMe({ ...m, tools: m.tools ?? [] });
      } catch (err) {
        if (alive) setError(message(err));
      }
    })();
    return () => {
      alive = false;
    };
  }, [router]);

  const refreshCounts = useCallback(async (m: Me) => {
    const next = { approvals: 0, queue: 0, critical: 0 };
    if (isDeskRole(m.role)) {
      const feed = await fetch(apiUrl("/api/speaker"), { cache: "no-store" })
        .then((r) => (r.ok ? (r.json() as Promise<SpeakerFeed>) : null))
        .catch(() => null);
      // What controllers sent for a second person: plans, cancellations proposed, resolutions.
      if (feed) next.approvals = feed.waiting.plans + (feed.waiting.proposals ?? 0) + (feed.waiting.resolutions ?? 0);
    }
    const q = await tool<{ items: QueueItem[] }>("my_queue").catch(() => null);
    if (q) {
      setQueue(q.items ?? []);
      next.queue = q.items?.length ?? 0;
      next.critical = (q.items ?? []).filter((i) => i.tone === "critical").length;
    }
    setCounts(next);
  }, []);

  useEffect(() => {
    if (!me) return;
    let alive = true;
    const poll = async () => {
      try {
        const v = await api<Record<string, unknown>>("/api/version");
        if (!alive) return;
        setLive(true);
        const key = JSON.stringify(v);
        if (key !== last.current) {
          const first = last.current === "";
          last.current = key;
          if (!first) setTick((t) => t + 1);
          refreshCounts(me);
        }
      } catch {
        if (alive) setLive(false);
      }
    };
    poll();
    const timer = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [me, refreshCounts]);

  const flash = useCallback((text: string, tone: Toast["tone"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "critical" ? 8000 : 4000);
  }, []);
  const bump = useCallback(() => {
    setTick((t) => t + 1);
    if (me) refreshCounts(me);
  }, [me, refreshCounts]);
  const can = useCallback((name: string) => Boolean(me?.tools.includes(name)), [me]);

  if (error) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="max-w-md rounded-lg border border-[#f3c4c0] bg-critical-soft px-4 py-3 text-sm text-critical-ink">
          <p className="font-medium">CrewPay could not load your account.</p>
          <p className="mt-1">{error}</p>
          <div className="mt-3 flex gap-2">
            <Button size="sm" tone="secondary" onClick={() => window.location.reload()}>
              Try again
            </Button>
            <Button size="sm" tone="ghost" onClick={() => toSignIn()}>
              Sign in again
            </Button>
          </div>
        </div>
      </div>
    );
  }
  if (!me || !config) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted">
        <Spinner /> Opening CrewPay…
      </div>
    );
  }
  return (
    <SessionContext value={{ me, config, tick, live, counts, queue, bump, flash, can }}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(440px,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              "animate-rise pointer-events-auto flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-[0_8px_28px_rgb(0_0_0/0.14)]",
              t.tone === "critical" && "border-[#f3c4c0] bg-critical-soft text-critical-ink",
              t.tone === "pending" && "border-[#f6dc9c] bg-pending-soft text-pending-ink",
              t.tone === "ok" && "border-[#b9e6c9] bg-ok-soft text-ok-ink",
            )}
          >
            {t.tone === "critical" ? <CircleAlert size={16} className="mt-0.5 shrink-0" /> : t.tone === "pending" ? <Clock3 size={16} className="mt-0.5 shrink-0" /> : <CheckCircle2 size={16} className="mt-0.5 shrink-0" />}
            <span className="flex-1">{t.text}</span>
            <button aria-label="Dismiss" className="opacity-60 hover:opacity-100" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </SessionContext>
  );
}
