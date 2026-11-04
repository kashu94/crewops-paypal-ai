"use client";

import { Bell, BellOff, BellRing } from "lucide-react";
import { useEffect, useState } from "react";

import { api, message } from "@/lib/api";

import { cx } from "./ui";

type State = "loading" | "hidden" | "off" | "on" | "blocked" | "busy";

const supported = () =>
  typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const b64 = base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(b64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function encode(buffer: ArrayBuffer | null): string {
  if (!buffer) return "";
  let s = "";
  for (const b of new Uint8Array(buffer)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** A subscription made with a key the server has since replaced is refused by push services. */
const madeWith = (sub: PushSubscription, key: string) => {
  const used = sub.options.applicationServerKey;
  return !used || encode(used) === key;
};

const send = (sub: PushSubscription) => api("/api/push/subscribe", { method: "POST", json: sub.toJSON() });

/** Unsubscribes this browser, so a shared desk computer does not show the next person
 * someone else's alerts. Best effort. */
export async function forgetDesktopAlerts(): Promise<void> {
  if (!supported()) return;
  const reg = await navigator.serviceWorker.getRegistration("/").catch(() => undefined);
  const sub = await reg?.pushManager.getSubscription().catch(() => null);
  if (!sub) return;
  await api("/api/push/unsubscribe", { method: "POST", json: { endpoint: sub.endpoint } }).catch(() => undefined);
  await sub.unsubscribe().catch(() => undefined);
}

/** The bell in the top bar: a desktop notification when something needs this person, even
 * with CrewPay closed. Hidden where the browser or the server cannot do it. */
export function DesktopAlerts() {
  const [state, setState] = useState<State>("loading");
  const [key, setKey] = useState("");
  const [problem, setProblem] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      if (!supported()) return setState("hidden");
      const k = await api<{ public_key?: string }>("/api/push/key")
        .then((r) => r.public_key ?? "")
        .catch(() => "");
      if (!alive) return;
      setKey(k);
      if (!k) return setState("hidden");
      if (Notification.permission === "denied") return setState("blocked");
      const reg = await navigator.serviceWorker.getRegistration("/");
      const sub = await reg?.pushManager.getSubscription();
      if (!sub) return alive && setState("off");
      if (!madeWith(sub, k)) {
        await sub.unsubscribe().catch(() => undefined);
        return alive && setState("off");
      }
      await send(sub); // on in this browser: make it this person's
      if (alive) setState("on");
    })().catch(() => alive && setState("off"));
    return () => {
      alive = false;
    };
  }, []);

  async function turnOn() {
    setState("busy");
    setProblem("");
    try {
      const permission = await Notification.requestPermission();
      if (permission === "denied") return setState("blocked");
      if (permission !== "granted") return setState("off");
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription();
      if (sub && !madeWith(sub, key)) {
        await sub.unsubscribe().catch(() => undefined);
        sub = null;
      }
      sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
      await send(sub);
      setState("on");
    } catch (err) {
      setProblem(message(err));
      setState("off");
    }
  }

  async function turnOff() {
    setState("busy");
    try {
      await forgetDesktopAlerts();
    } finally {
      setState("off");
    }
  }

  if (state === "loading" || state === "hidden") return null;
  const on = state === "on";
  const title =
    state === "blocked"
      ? "Desktop alerts are blocked for this site in the browser's settings"
      : on
        ? "Desktop alerts are on in this browser (click to turn off)"
        : problem
          ? `Desktop alerts could not be turned on (${problem}). Click to try again`
          : "Turn on desktop alerts: a notification when something needs you, even with CrewPay closed";
  return (
    <button
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors hover:bg-active",
        on ? "text-accent" : problem ? "text-critical" : "text-muted hover:text-foreground",
        state === "busy" && "animate-pulse",
      )}
      title={title}
      aria-label={title}
      aria-pressed={on}
      disabled={state === "busy" || state === "blocked"}
      onClick={on ? turnOff : turnOn}
    >
      {state === "blocked" ? <BellOff size={16} /> : on ? <BellRing size={16} /> : <Bell size={16} />}
      <span className="hidden xl:inline">{on ? "Alerts on" : "Alerts"}</span>
    </button>
  );
}
