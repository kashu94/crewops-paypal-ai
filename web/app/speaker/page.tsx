"use client";

import { Bell, BellRing, Check, Mic, Power, Send, Square, Volume2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Logo } from "@/components/shell";
import { cx } from "@/components/ui";
import { apiUrl, demoIdentity, headers, loadConfig, message, saved } from "@/lib/api";
import type { Answer, SpeakerFeed } from "@/lib/types";
import { ANY_LANGUAGE, Recorder, say, transcribe } from "@/lib/voice";

type Item = SpeakerFeed["items"][number];
type Waiting = SpeakerFeed["waiting"] & { resolutions?: number; disruptions?: number; proposals?: number };

const HEARD = "crewpay.speaker.heard";
const REPEAT_EVERY = 120_000;

const TONE = {
  critical: { rail: "bg-[#ff6b6b]", tag: "bg-[#ff6b6b] text-[#1a0606]", card: "bg-[#ff6b6b]/[0.07]", label: "action" },
  pending: { rail: "bg-[#f5b94a]", tag: "bg-[#f5b94a] text-[#1f1400]", card: "", label: "waiting" },
  ok: { rail: "bg-[#4fd18b]", tag: "bg-[#4fd18b] text-[#04170d]", card: "", label: "done" },
  info: { rail: "bg-[#5f6d7c]", tag: "bg-white/10 text-[#c9d4df]", card: "", label: "info" },
} as const;

function readHeard(): Set<string> | null {
  const raw = saved.get(HEARD);
  if (raw === null) return null;
  try {
    return new Set(JSON.parse(raw) as string[]);
  } catch {
    return new Set();
  }
}

/** The ops-room speaker: reads the desk's notices aloud as they arrive, with a chime, and
 * repeats critical ones every two minutes until someone acknowledges them. Public: the
 * speaker belongs to the room, not to a person. */
export default function SpeakerPage() {
  const [feed, setFeed] = useState<SpeakerFeed | null>(null);
  const [live, setLive] = useState(true);
  const [on, setOn] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [repeat, setRepeat] = useState(true);
  const [unack, setUnack] = useState<Set<string>>(new Set());
  const [heardNote, setHeardNote] = useState("");
  const [answer, setAnswer] = useState<{ q: string; text: string } | null>(null);
  const [question, setQuestion] = useState("");
  const [mic, setMic] = useState<"idle" | "listening" | "working">("idle");

  const audio = useRef<AudioContext | null>(null);
  const queue = useRef<Item[]>([]);
  const busy = useRef(false);
  const onRef = useRef(false);
  const heard = useRef<Set<string> | null>(null);
  const recorder = useRef(new Recorder());


  const chime = useCallback(() => {
    const ctx = audio.current;
    if (!ctx) return;
    const t = ctx.currentTime;
    [880, 660].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = f;
      o.type = "sine";
      g.gain.setValueAtTime(0.0001, t + i * 0.18);
      g.gain.exponentialRampToValueAtTime(0.2, t + i * 0.18 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.18 + 0.3);
      o.connect(g).connect(ctx.destination);
      o.start(t + i * 0.18);
      o.stop(t + i * 0.18 + 0.32);
    });
  }, []);

  const drain = useCallback(async () => {
    if (busy.current || !onRef.current) return;
    busy.current = true;
    setSpeaking(true);
    while (queue.current.length && onRef.current) {
      const item = queue.current.shift()!;
      chime();
      await new Promise((r) => setTimeout(r, 450));
      await say(`${item.subject}. ${item.body}`, { rate: 1.02 });
    }
    busy.current = false;
    setSpeaking(false);
  }, [chime]);

  // the feed, every 4 seconds
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await fetch(apiUrl("/api/speaker"), { cache: "no-store" });
        if (!r.ok) throw new Error();
        const f = (await r.json()) as SpeakerFeed;
        if (!alive) return;
        setLive(true);
        setFeed(f);
        if (heard.current === null) heard.current = readHeard();
        if (heard.current === null) {
          // first visit on this device: start from now, do not read the backlog
          heard.current = new Set(f.items.map((i) => i.id));
        } else {
          const fresh = f.items.filter((i) => !heard.current!.has(i.id)).reverse();
          if (fresh.length) {
            fresh.forEach((i) => heard.current!.add(i.id));
            setUnack((u) => {
              const next = new Set(u);
              fresh.filter((i) => i.tone !== "info").forEach((i) => next.add(i.id));
              return next;
            });
            queue.current.push(...fresh);
            drain();
          }
        }
        saved.set(HEARD, JSON.stringify([...heard.current].slice(-300)));
      } catch {
        if (alive) setLive(false);
      }
    };
    poll();
    const t = setInterval(poll, 4000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [drain]);

  // critical alerts nobody acknowledged are read again every two minutes
  useEffect(() => {
    if (!on || !repeat) return;
    const t = setInterval(() => {
      const again = (feed?.items ?? []).filter((i) => i.tone === "critical" && unack.has(i.id)).slice(0, 3);
      if (again.length) {
        queue.current.push(...again);
        drain();
      }
    }, REPEAT_EVERY);
    return () => clearInterval(t);
  }, [on, repeat, feed, unack, drain]);

  function power() {
    const next = !on;
    setOn(next);
    onRef.current = next;
    setUnack(new Set());
    if (next) {
      audio.current ??= new AudioContext();
      say("CrewPay speaker on.");
    } else {
      speechSynthesis.cancel();
      queue.current = [];
    }
  }

  async function waiting() {
    if (!feed) return;
    const lines = feed.summary ?? [];
    const text = lines.length ? lines.join(" ") : "Nothing is waiting on the desk.";
    setAnswer({ q: "What's waiting?", text: lines.length ? lines.map((l) => `• ${l}`).join("\n") : text });
    audio.current ??= new AudioContext();
    await say(text);
  }

  async function ask(q: string) {
    if (!q.trim()) return;
    await loadConfig();
    setAnswer({ q, text: "…" });
    try {
      // The room device asks as whoever is signed in on this browser, or as the speaker.
      const signedIn = Boolean(demoIdentity());
      const h = signedIn ? headers() : { "X-Demo-User": "ops.speaker", "X-Demo-Role": "speaker" };
      const r = await fetch(apiUrl("/api/ask"), { method: "POST", headers: { ...h, "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const a = (await r.json().catch(() => ({}))) as Partial<Answer> & { detail?: string };
      const text = a.text || a.detail || "No answer.";
      setAnswer({ q, text });
      await say(text);
    } catch (err) {
      setAnswer({ q, text: message(err) });
    }
  }

  async function toggleMic() {
    if (mic === "idle") {
      try {
        await recorder.current.start();
        setMic("listening");
        setHeardNote("Listening…");
      } catch (err) {
        setHeardNote(`Microphone not available: ${message(err)}`);
      }
      return;
    }
    if (mic !== "listening") return;
    setMic("working");
    setHeardNote("Transcribing…");
    try {
      await loadConfig();
      const t = await transcribe(await recorder.current.stop(), ANY_LANGUAGE).catch(async (err) => {
        if (/sign in|401|403/i.test(message(err))) throw new Error("Asking by voice needs someone signed in on this browser.");
        throw err;
      });
      setHeardNote(t.text ? `“${t.text}”` : "Nothing was heard.");
      if (t.text) await ask(t.text);
    } catch (err) {
      setHeardNote(message(err));
    } finally {
      setMic("idle");
    }
  }

  const w = (feed?.waiting ?? { plans: 0, decisions: 0 }) as Waiting;
  const count = w.plans + w.decisions + (w.resolutions ?? 0) + (w.proposals ?? 0);
  const critical = (feed?.items ?? []).some((i) => i.tone === "critical" && unack.has(i.id));
  const ring = !on ? "off" : speaking ? "speaking" : critical || w.decisions ? "critical" : count ? "pending" : "ok";
  const ringColor = { off: "#2a3542", speaking: "#6fb6ff", critical: "#ff6b6b", pending: "#f5b94a", ok: "#4fd18b" }[ring];

  return (
    <main className="flex min-h-screen flex-col bg-[#0b1016] text-[#e7edf3]">
      <header className="flex flex-wrap items-center gap-3 border-b border-white/5 px-5 py-3.5 lg:px-6">
        <Logo size={26} />
        <div>
          <b className="text-[15px]">CrewPay speaker</b>
          <p className="text-xs text-[#93a1b0]">Whole ops room · crew desks and duty supervisor</p>
        </div>
        <span className="ml-auto flex items-center gap-2 font-mono text-[13px] text-[#93a1b0]">
          <span className={cx("h-2 w-2 rounded-full", live ? "bg-[#4fd18b]" : "bg-[#5f6d7c]")} />
          {feed?.clock ?? "Connecting…"}
        </span>
      </header>

      <div className="grid flex-1 gap-6 p-5 lg:grid-cols-[minmax(320px,2fr)_3fr] lg:p-6">
        <section className="flex flex-col items-center justify-center gap-5 py-4">
          <div className="relative grid h-60 w-60 place-items-center rounded-full p-2.5 transition-colors xl:h-64 xl:w-64" style={{ color: ringColor }}>
            <span
              className={cx("absolute inset-0 rounded-full", ring === "speaking" && "ring-spin")}
              style={{
                background:
                  ring === "speaking"
                    ? "conic-gradient(from 0deg, #6fb6ff, #1d3b6e 30%, #6fb6ff 55%, #1d3b6e 80%, #6fb6ff)"
                    : `conic-gradient(${ringColor}, ${ringColor}cc, ${ringColor})`,
                opacity: ring === "off" ? 1 : 0.9,
              }}
            />
            {(ring === "critical" || ring === "pending") && <span className="halo absolute inset-0 rounded-full" />}
            <div className="relative grid h-full w-full place-items-center rounded-full bg-gradient-to-b from-[#18212d] to-[#070b10] shadow-[inset_0_6px_24px_rgba(0,0,0,0.6)]">
              <div className="grid h-[86%] w-[86%] place-items-center rounded-full bg-[radial-gradient(circle,#1f2a36_1px,transparent_1.4px)] bg-[length:9px_9px] text-center">
                {!on ? (
                  <button onClick={power} className="flex flex-col items-center gap-2 rounded-full px-6 py-4 text-[#c9d4df] hover:text-white">
                    <Power size={34} />
                    <span className="text-xs">Switch on sound</span>
                  </button>
                ) : speaking ? (
                  <div className="flex items-end gap-1.5" aria-label="Speaking">
                    {[0, 1, 2, 3, 4].map((i) => (
                      <span key={i} className="voice-bar h-10 w-1.5 rounded-full bg-[#6fb6ff]" style={{ animationDelay: `${i * 0.12}s` }} />
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-1 text-[#e7edf3]">
                    {critical ? <BellRing size={26} className="animate-pulse text-[#ff6b6b]" /> : <Bell size={24} className="text-[#5f6d7c]" />}
                    <span className="font-mono text-6xl font-semibold leading-none">{count}</span>
                    <span className="text-xs text-[#93a1b0]">waiting on the desk</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="flex flex-wrap justify-center gap-2">
            <button onClick={power} className={cx("flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-semibold", on ? "bg-white/10 ring-1 ring-white/15 hover:bg-white/15" : "bg-[#6fb6ff] text-[#06121f] hover:bg-[#8cc5ff]")}>
              <Power size={15} /> {on ? "Sound on · switch off" : "Switch on sound"}
            </button>
            <button onClick={waiting} className="flex items-center gap-2 rounded-full bg-white/5 px-4 py-2.5 text-sm font-medium ring-1 ring-white/15 hover:bg-white/10">
              <Volume2 size={15} /> What&apos;s waiting?
            </button>
            <button onClick={toggleMic} disabled={mic === "working"} className={cx("flex items-center gap-2 rounded-full px-4 py-2.5 text-sm font-medium ring-1", mic === "listening" ? "bg-[#ff6b6b] text-[#1a0606] ring-[#ff6b6b]" : "bg-white/5 ring-white/15 hover:bg-white/10")}>
              {mic === "listening" ? <Square size={15} /> : <Mic size={15} />} {mic === "listening" ? "Stop" : "Ask"}
            </button>
            {unack.size > 0 && (
              <button onClick={() => setUnack(new Set())} className="flex items-center gap-2 rounded-full bg-[#f5b94a] px-4 py-2.5 text-sm font-semibold text-[#1f1400] hover:bg-[#ffc864]">
                <Check size={15} /> Acknowledge {unack.size}
              </button>
            )}
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(question);
              setQuestion("");
            }}
            className="flex w-full max-w-sm items-center gap-2"
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Type a question for the desk"
              aria-label="Type a question"
              className="min-w-0 flex-1 rounded-full border border-white/10 bg-[#070b10] px-4 py-2 text-sm outline-none placeholder:text-[#5f6d7c] focus:border-[#6fb6ff]"
            />
            <button type="submit" disabled={!question.trim()} aria-label="Ask" className="grid h-9 w-9 place-items-center rounded-full bg-white/10 hover:bg-white/15 disabled:opacity-40">
              <Send size={15} />
            </button>
          </form>

          <div className="flex flex-wrap items-center justify-center gap-3 text-[13px] text-[#93a1b0]">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={repeat} onChange={(e) => setRepeat(e.target.checked)} className="accent-[#6fb6ff]" /> Repeat critical alerts until acknowledged
            </label>
          </div>
          {heardNote && <p className="max-w-sm text-center text-sm text-[#93a1b0]">{heardNote}</p>}
          {answer && (
            <div className="w-full max-w-sm rounded-2xl bg-[#6fb6ff]/10 px-4 py-3 text-sm ring-1 ring-[#6fb6ff]/20" aria-live="polite">
              <p className="text-xs text-[#93a1b0]">“{answer.q}”</p>
              <p className="mt-1 whitespace-pre-wrap">{answer.text}</p>
            </div>
          )}
          <p className="max-w-sm text-center text-[11px] text-[#5f6d7c]">This speaker belongs to the desk, not a person. It reads the same notices the console shows. Asking a question uses whoever is signed in on this browser.</p>
        </section>

        <section className="flex min-h-0 flex-col">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[#93a1b0]">Alert stream</h2>
            <span className="ml-auto flex gap-3 text-[11px] text-[#93a1b0]">
              <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#ff6b6b]" />needs action</span>
              <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#f5b94a]" />waiting</span>
              <span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-[#4fd18b]" />done</span>
            </span>
          </div>
          {!feed?.items.length ? (
            <p className="rounded-2xl bg-white/[0.03] px-4 py-8 text-center text-sm text-[#93a1b0] ring-1 ring-white/10">{feed ? "Nothing has happened on the desk yet." : "Connecting…"}</p>
          ) : (
            <ol className="flex flex-col gap-2.5 overflow-y-auto lg:max-h-[calc(100vh-150px)] lg:pr-1">
              {feed.items.map((i) => {
                const t = TONE[i.tone] ?? TONE.info;
                const fresh = unack.has(i.id);
                return (
                  <li key={i.id} className={cx("relative overflow-hidden rounded-2xl bg-[#121922] py-3 pl-5 pr-4 ring-1 ring-white/5", t.card, fresh && "ring-[#6fb6ff]/60")}>
                    <span className={cx("absolute inset-y-0 left-0 w-1.5", t.rail)} />
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={cx("rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider", t.tag)}>{t.label}</span>
                      <b className="text-sm font-semibold">{i.subject}</b>
                      {fresh && <span className="rounded-full bg-[#6fb6ff]/15 px-2 py-0.5 text-[10px] font-medium text-[#9ccaff]">new</span>}
                      <span className="ml-auto font-mono text-xs text-[#5f6d7c]">
                        {i.at}
                        {i.to === "duty-supervisor" ? " · supervisor" : ""}
                      </span>
                    </div>
                    <p className="mt-1 text-sm leading-relaxed text-[#b4c0cc]">{i.body}</p>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>
    </main>
  );
}
