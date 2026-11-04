"use client";

import { ArrowRight, ChevronDown, Mic, Pause, Play, RotateCcw, Send, Square, Workflow } from "lucide-react";
import { useRef, useState } from "react";

import { useSession } from "@/components/session";
import { Button, control, cx, Mono, Notice, PageHeader, Panel, Pill, Spinner, Table } from "@/components/ui";
import { api, message, saved } from "@/lib/api";
import { opDay } from "@/lib/clock";
import { plural } from "@/lib/format";
import type { Answer } from "@/lib/types";
import { ANY_LANGUAGE, hasVoice, langOf, Recorder, say, transcribe } from "@/lib/voice";

const ALOUD = "crewpay.assistant.aloud";

/** "8 November", a number of days into the demo roster. */
function onDay(first: string, n: number) {
  const d = new Date(`${first}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return `${d.getUTCDate()} ${d.toLocaleString("en-GB", { month: "long", timeZone: "UTC" })}`;
}

// The demo's trips sit at the same days into its roster, whichever day it starts.
const deskSuggestions = (first: string) => [
  `A. Nair is unwell from ${onDay(first, 6)} to ${onDay(first, 9)}. How should we cover it?`,
  "Anything I should worry about in the next few days?",
  `Gates G1 and G2 at Hyderabad are closed 6 to 9 am on ${onDay(first, 3)}. What happens?`,
  `VT-SYB is out of service until 11 am on ${onDay(first, 6)}.`,
];

const SUGGEST = {
  crew: ["Who am I, and what should I do today?", "Am I legal for my next trip?", "How many duty hours have I done this week?", "Do I have any callout offers?"],
  reporter: ["What is my role here?", "Who do I call about a gate problem at Hyderabad?", "What departs Hyderabad in the next few hours?", "Where does my last report stand?"],
};

type Turn = { q: string; a?: Answer; error?: string };

function Trace({ a }: { a: Answer }) {
  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Pill tone={a.blocked ? "critical" : "ok"}>screen</Pill>
        <ArrowRight size={12} className="text-faint" />
        <Pill>route{a.route ? `: ${a.route.intent}` : ""}</Pill>
        <ArrowRight size={12} className="text-faint" />
        <Pill>{plural(a.steps.length, "tool call")}</Pill>
        <ArrowRight size={12} className="text-faint" />
        <Pill tone={a.verified ? "ok" : "pending"}>check every figure</Pill>
        <ArrowRight size={12} className="text-faint" />
        <Pill>answer</Pill>
      </div>
      {a.blocked && <Notice tone="error">Refused before planning: {a.blocked}. Logged for the admin.</Notice>}
      {a.route && (
        <section>
          <h3 className="mb-1 text-[13px] font-semibold">Route</h3>
          <p className="text-muted">
            {a.route.intent}, decided by {a.route.by === "model" ? "the router model (strict JSON schema)" : "keyword rules"}; reasoning effort {a.route.effort}. Tools offered:{" "}
            {a.route.tools.join(", ") || "none"}.
          </p>
        </section>
      )}
      {a.steps.length > 0 && (
        <section>
          <h3 className="mb-1 text-[13px] font-semibold">Tool calls, answered by the rules engine</h3>
          <div className="flex flex-col gap-1.5">
            {a.steps.map((s, i) => {
              const failed = Boolean(s.result && typeof s.result === "object" && "error" in (s.result as object));
              return (
                <details key={i} className="rounded-md border border-line">
                  <summary className="flex items-center gap-2 px-2.5 py-1.5 text-[13px]">
                    <ChevronDown size={13} className="shrink-0 text-faint" />
                    <span className="font-mono text-xs text-faint">{i + 1}.</span>
                    <b className="font-medium">{s.tool}</b>
                    <span className="truncate font-mono text-xs text-faint">{JSON.stringify(s.arguments)}</span>
                    {failed && <Pill tone="critical">error</Pill>}
                  </summary>
                  <pre className="max-h-72 overflow-auto border-t border-rule bg-sunken p-2.5 font-mono text-[11px]">{JSON.stringify(s.result, null, 2)}</pre>
                </details>
              );
            })}
          </div>
        </section>
      )}
      <section>
        <h3 className="mb-1 text-[13px] font-semibold">Evidence check</h3>
        {a.draft && (
          <div className="mb-2">
            <p className="text-pending-ink">The first draft said things no tool returned:</p>
            <ul className="list-disc pl-5 text-muted">
              {a.draft_problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
            <p className="text-muted">It was rewritten from the evidence.</p>
          </div>
        )}
        {a.verified ? (
          <p className="text-ok">Every id, time, hour figure, count and amount in the answer matches a tool result.</p>
        ) : (
          <p className="text-pending-ink">{a.problems.length ? a.problems.join("; ") : "Not checked."}</p>
        )}
      </section>
      {a.calls.length > 0 && (
        <section>
          <h3 className="mb-1 text-[13px] font-semibold">
            Model calls · {(a.tokens.prompt + a.tokens.completion).toLocaleString("en-IN")} tokens
            {a.tokens.cached > 0 && <span className="font-normal text-muted"> ({a.tokens.cached.toLocaleString("en-IN")} from the cache)</span>}
          </h3>
          <div className="rounded-md border border-line">
            <Table className="text-xs">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>For</th>
                  <th className="text-right">In</th>
                  <th className="text-right">Out</th>
                  <th className="text-right">s</th>
                </tr>
              </thead>
              <tbody>
                {a.calls.map((c, i) => (
                  <tr key={i}>
                    <td className="font-mono">{c.model}</td>
                    <td>{c.purpose}</td>
                    <td className="text-right font-mono">{c.prompt}</td>
                    <td className="text-right font-mono">{c.completion}</td>
                    <td className="text-right font-mono">{c.seconds}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
        </section>
      )}
    </div>
  );
}

export default function AssistantPage() {
  const { me, bump } = useSession();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [aloud, setAloudState] = useState(() => saved.get(ALOUD) !== "off");
  /** Which answer is being read aloud, and whether it is paused. */
  const [voice, setVoice] = useState<{ turn: number; paused: boolean } | null>(null);
  const setAloud = (on: boolean) => {
    setAloudState(on);
    saved.set(ALOUD, on ? "on" : "off");
    if (!on) stopVoice();
  };
  function stopVoice() {
    if (typeof window !== "undefined" && "speechSynthesis" in window) speechSynthesis.cancel();
    setVoice(null);
  }
  function readAloud(turn: number, text: string) {
    stopVoice();
    const lang = langOf(text);
    if (lang !== "en-IN")
      hasVoice(lang).then((ok) => !ok && setMicNote(`This browser has no ${lang === "te-IN" ? "Telugu" : "Hindi"} voice installed, so the answer is shown but not read.`));
    setVoice({ turn, paused: false });
    say(text).then(() => setVoice((v) => (v && v.turn === turn ? null : v)));
  }
  const [mic, setMic] = useState<"idle" | "listening" | "working">("idle");
  const [micNote, setMicNote] = useState("");
  const [shown, setShown] = useState<number | null>(null);
  const recorder = useRef(new Recorder());
  const suggestions =
    me.role === "crew" ? SUGGEST.crew : me.role === "reporter" ? SUGGEST.reporter : deskSuggestions(me.days?.first ?? opDay(me.clock));

  async function ask(q: string) {
    if (!q.trim() || busy) return;
    stopVoice();
    setBusy(true);
    setQuestion("");
    const index = turns.length;
    setTurns((t) => [...t, { q }]);
    setShown(index);
    try {
      const a = await api<Answer>("/api/ask", { method: "POST", json: { question: q, conversation_id: conversation } });
      setConversation(a.conversation_id);
      setTurns((t) => t.map((x, i) => (i === index ? { ...x, a } : x)));
      if (aloud && !a.blocked) readAloud(index, a.text);
      bump();
    } catch (err) {
      setTurns((t) => t.map((x, i) => (i === index ? { ...x, error: message(err) } : x)));
    } finally {
      setBusy(false);
    }
  }

  async function toggleMic() {
    if (mic === "idle") {
      try {
        await recorder.current.start();
        setMic("listening");
        setMicNote("Listening… press Stop when you finish.");
      } catch (err) {
        setMicNote(`Microphone not available: ${message(err)}`);
      }
      return;
    }
    if (mic !== "listening") return;
    setMic("working");
    setMicNote("Transcribing…");
    try {
      const t = await transcribe(await recorder.current.stop(), ANY_LANGUAGE);
      setMicNote(t.text ? `Heard (${t.seconds}s)` : "Nothing was heard. Try again a little closer.");
      if (t.text) ask(t.text);
    } catch (err) {
      setMicNote(message(err));
    } finally {
      setMic("idle");
    }
  }

  const current = shown !== null ? turns[shown] : null;

  return (
    <div className="animate-rise">
      <PageHeader
        title="Assistant"
        lede="Ask in English, Hindi or Telugu, by text or by voice. Every figure in an answer comes from the rules engine and is checked before you see it. The panel beside shows how each answer was made."
        actions={
          <Button
            tone="secondary"
            onClick={() => {
              stopVoice();
              setConversation(null);
              setTurns([]);
              setShown(null);
            }}
          >
            <RotateCcw size={14} /> New conversation
          </Button>
        }
      />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {suggestions.map((q) => (
              <button key={q} onClick={() => ask(q)} disabled={busy} className="rounded-full border border-line bg-surface px-3 py-1.5 text-left text-[13px] hover:border-control hover:bg-sunken disabled:opacity-50">
                {q}
              </button>
            ))}
          </div>
          <div className="flex min-h-48 flex-col gap-3 rounded-lg border border-line bg-sunken/60 p-4">
            {turns.length === 0 && <p className="m-auto text-sm text-muted">Ask about crew, trips, cover, delays or gates.</p>}
            {turns.map((t, i) => (
              <div key={i} className="flex flex-col gap-2">
                <div className="max-w-[85%] self-end rounded-xl rounded-br-sm bg-accent px-3.5 py-2 text-sm text-white">{t.q}</div>
                {t.a ? (
                  <button onClick={() => setShown(i)} className={cx("max-w-[92%] self-start rounded-xl rounded-bl-sm border bg-surface px-3.5 py-2.5 text-left text-sm", shown === i ? "border-accent" : "border-line")}>
                    <p className="whitespace-pre-wrap leading-relaxed">{t.a.text}</p>
                    {voice?.turn === i && (
                      <span className="mt-2 flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                        <span className="voice-bar-sm flex h-4 items-end gap-0.5" aria-hidden>
                          {[0, 1, 2].map((b) => (
                            <i key={b} className={cx("w-0.5 rounded-full bg-accent", voice.paused ? "h-1.5" : "voice-bar h-4")} style={{ animationDelay: `${b * 0.12}s` }} />
                          ))}
                        </span>
                        <Button
                          type="button"
                          size="sm"
                          tone="secondary"
                          onClick={() => {
                            if (voice.paused) speechSynthesis.resume();
                            else speechSynthesis.pause();
                            setVoice({ turn: i, paused: !voice.paused });
                          }}
                        >
                          {voice.paused ? <Play size={13} /> : <Pause size={13} />} {voice.paused ? "Resume" : "Pause"}
                        </Button>
                        <Button type="button" size="sm" tone="ghost" onClick={stopVoice}>
                          <Square size={12} /> Stop
                        </Button>
                      </span>
                    )}
                    <span className="mt-2 flex flex-wrap gap-1.5">
                      {t.a.blocked && <Pill tone="critical">refused: {t.a.blocked}</Pill>}
                      {t.a.route && <Pill>{t.a.route.intent}</Pill>}
                      {!t.a.blocked && <Pill tone={t.a.verified ? "ok" : "pending"}>{t.a.verified ? "every figure checked" : "unverified"}</Pill>}
                      {t.a.steps.length > 0 && <Pill>{plural(t.a.steps.length, "tool call")}</Pill>}
                    </span>
                  </button>
                ) : t.error ? (
                  <Notice tone="error" className="max-w-[92%] self-start">{t.error}</Notice>
                ) : (
                  <p className="flex items-center gap-2 self-start text-sm text-muted">
                    <Spinner /> Working through the tools…
                  </p>
                )}
              </div>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(question);
            }}
            className="flex flex-col gap-2"
          >
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  ask(question);
                }
              }}
              rows={3}
              placeholder="Ask about crew, trips, cover, delays, gates…"
              className={cx(control, "h-auto py-2")}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" disabled={busy || !question.trim()}>
                {busy ? <Spinner /> : <Send size={14} />} Ask
              </Button>
              <Button type="button" tone={mic === "listening" ? "danger" : "secondary"} onClick={toggleMic} disabled={mic === "working"}>
                {mic === "listening" ? <Square size={14} /> : <Mic size={14} />} {mic === "listening" ? "Stop" : "Speak"}
              </Button>
              <label className="flex items-center gap-1.5 text-[13px] text-muted">
                <input type="checkbox" checked={aloud} onChange={(e) => setAloud(e.target.checked)} /> Read answers aloud, in the language of the answer
              </label>
              {micNote && <span className="text-xs text-muted">{micNote}</span>}
            </div>
          </form>
        </div>
        <Panel title="How the answer was made" icon={<Workflow size={15} />} className="self-start">
          {current?.a ? (
            <Trace a={current.a} />
          ) : (
            <p className="text-sm text-muted">
              Ask something to see the route, every tool call with its <Mono>arguments</Mono>, the evidence check and the tokens.
            </p>
          )}
        </Panel>
      </div>
    </div>
  );
}
