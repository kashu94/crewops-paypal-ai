"use client";

import { ArrowRight, LogOut } from "lucide-react";
import { useEffect, useState } from "react";

import { RolePicker } from "@/components/role-picker";
import { NightApproach } from "@/components/login-art";
import { Logo } from "@/components/shell";
import { Button, cx, Notice, Spinner } from "@/components/ui";
import { api, hasSession, loadConfig, message, signIn, signOut, type Config, type DemoAccount } from "@/lib/api";
import { demoAccounts } from "@/lib/demo";
import { homeFor, ROLE_LABEL, type Me } from "@/lib/roles";

/** Where to go after signing in: `?next=` on this site only, else the role's home. */
function nextPage(me: Me): string {
  const next = new URLSearchParams(window.location.search).get("next");
  if (next && next.startsWith("/")) {
    try {
      const url = new URL(next, window.location.origin);
      if (url.origin === window.location.origin && !url.pathname.startsWith("/login")) return url.pathname + url.search;
    } catch {
      /* fall through */
    }
  }
  return homeFor(me.role);
}

const AIRLINES = [
  ["Sahyadri Air", "#1d5c96"],
  ["Kaveri Flyers", "#e08a1e"],
  ["Godavari Link", "#2a9d6f"],
] as const;

// What CrewPay runs on, and what each part does here.
const MODELS = [
  ["Amazon Nova on Bedrock", "Plans every answer with tool calls, and reads photos into fixed forms"],
  ["Nova Micro router", "Routes each question and screens out attacks on the assistant"],
  ["PayPal Payouts", "Pays callout, service and travel allowance to crew the moment cover is approved"],
  ["PayPal Checkout & webhooks", "Every approval is confirmed by paying for the cover, and tracked to settled"],
] as const;

// The duty rules every cover is checked against (data/seed/rules.json), in short.
const RULES = [
  ["Duty period", "13 h, less 30 min per sector after the second"],
  ["Night duty", "10 h at most through 00:00–06:00, two landings"],
  ["Duty", "60 h in any 7 days"],
  ["Flying", "100 block hours in any 28 days"],
  ["Rest", "12 h, and at least the duty before it"],
  ["Weekly rest", "Two clear days in a row every 7 days"],
  ["Rating", "Rated on the type, flying for their own airline"],
  ["Certificates", "Every certificate valid on each duty date"],
  ["Base", "From home base, or positioned in that day"],
] as const;

export default function Login() {
  const [config, setConfig] = useState<Config | null>(null);
  const [current, setCurrent] = useState<Me | null>(null);
  const [accounts, setAccounts] = useState<DemoAccount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      const c = await loadConfig();
      setConfig(c);
      if (c.auth === "demo") demoAccounts().then(setAccounts);
      if (hasSession()) setCurrent(await api<Me>("/api/me").catch(() => null));
    })();
  }, []);

  async function finish() {
    const me = await api<Me>("/api/me");
    window.location.replace(nextPage(me));
  }

  async function actAs(a: DemoAccount, password: string) {
    setBusy(a.user);
    setError(null);
    try {
      await signIn(a, password);
      await finish();
    } catch (err) {
      signOut();
      setError(message(err));
      setBusy(null);
    }
  }

  return (
    <main className="grid min-h-screen flex-1 bg-surface lg:grid-cols-[1.08fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-[#040a18] px-12 py-9 text-white lg:sticky lg:top-0 lg:h-screen lg:flex">
        <NightApproach />
        <div className="relative">
          <div className="flex items-center gap-2.5">
            <Logo size={30} />
            <span className="text-[15px] font-semibold">CrewPay</span>
            <span className="text-sm text-[#b9c6d6]">crew control that pays its crew</span>
          </div>
          <ul className="mt-5 grid max-w-xl grid-cols-2 gap-x-5 gap-y-2.5 text-[13px]">
            {MODELS.map(([model, job]) => (
              <li key={model} className="leading-snug">
                <b className="block font-semibold text-white">{model}</b>
                <span className="text-[#b9c6d6]">{job}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="relative max-w-xl">
          <h2 className="text-[34px] font-semibold leading-tight tracking-[-0.01em]">Crew control for three airlines</h2>
          <p className="mt-3 max-w-md text-base leading-relaxed text-[#d4dde8]">
            A sick call, a late departure or a closed gate becomes ranked, legal cover within seconds. A second person
            approves every roster change and confirms it by paying with PayPal, and the crew who step in are paid out the
            moment it goes through.
          </p>
          <p className="mt-7 text-sm font-medium text-white">Every cover is checked against {RULES.length} rules</p>
          <dl className="mt-3 grid grid-cols-[7.5rem_1fr] gap-x-4 gap-y-1.5 rounded-lg bg-[#040a18]/60 p-4 text-sm ring-1 ring-white/10 backdrop-blur-[1px]">
            {RULES.map(([name, text]) => (
              <div key={name} className="contents">
                <dt className="text-[#b9c6d6]">{name}</dt>
                <dd className="text-[#eef2f7]">{text}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="relative flex flex-wrap items-center justify-between gap-4 text-sm text-[#d4dde8]">
          <ul className="flex flex-wrap gap-x-6 gap-y-2">
            {AIRLINES.map(([name, color]) => (
              <li key={name} className="flex items-center gap-2">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                {name}
              </li>
            ))}
          </ul>
          <span className="text-xs text-[#8ea3ba]">A demo operation: every name in it is made up.</span>
        </div>
      </section>

      <section className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className={cx("w-full", "max-w-xl")}>
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Logo size={30} />
            <span className="text-[15px] font-semibold">CrewPay</span>
            <span className="text-sm text-muted">Crew control</span>
          </div>

          {current && (
            <div className="mb-6 rounded-lg border border-line bg-sunken p-4">
              <p className="text-sm">
                This browser is signed in as <b>{current.name || current.user}</b> ({ROLE_LABEL[current.role] ?? current.role}).
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button tone="accent" onClick={() => window.location.replace(nextPage(current))}>
                  Continue <ArrowRight size={14} />
                </Button>
                <Button
                  tone="secondary"
                  onClick={() => {
                    signOut();
                    setCurrent(null);
                  }}
                >
                  <LogOut size={14} /> Sign in as someone else
                </Button>
              </div>
            </div>
          )}

          {!config ? (
            <div className="flex items-center gap-2 text-sm text-muted">
              <Spinner /> Loading…
            </div>
          ) : (
            <>
              <h1 className="text-2xl font-semibold tracking-tight">Choose who to be</h1>
              <p className="mt-1 text-sm text-muted">
                {config.password ? "Pick a role and a person, then enter the demo password (the same for everyone)." : "Demo mode: no passwords. Pick a role, then a person."} Each sees exactly what that role may see and do, and money moves only in the PayPal sandbox.
              </p>
              {error && <Notice tone="error" className="mt-4">{error}</Notice>}
              <RolePicker accounts={accounts} busy={busy} onPick={actAs} needsPassword={Boolean(config.password)} />
            </>
          )}
        </div>
      </section>
    </main>
  );
}
