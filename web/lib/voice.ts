/* Speech in and out, both in the browser: speech recognition hears the question (Chrome and
   Edge), and the browser's own speech synthesis speaks the answer. No audio leaves the page;
   only the words heard are sent on, like a typed question. */

import { speakable } from "./format";

/** What was heard: the words, and how long the person spoke. */
export type Speech = { text: string; seconds: number };

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
};

function recognition(): Recognition {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition;
  if (!Ctor) throw new Error("This browser cannot listen. Use Chrome or Edge, or type the question.");
  return new Ctor();
}

const LANG: Record<string, string> = { English: "en-IN", Hindi: "hi-IN", Telugu: "te-IN" };

export class Recorder {
  private rec: Recognition | null = null;
  private heard: string[] = [];
  private failed: string | null = null;
  private started = 0;
  private ended: Promise<void> = Promise.resolve();

  get active() {
    return this.rec !== null;
  }

  async start(language = "English") {
    const rec = recognition();
    rec.lang = LANG[language.split(",")[0].trim()] ?? "en-IN";
    rec.continuous = true;
    rec.interimResults = false;
    this.heard = [];
    this.failed = null;
    this.started = Date.now();
    rec.onresult = (e) => {
      this.heard = Array.from(e.results, (r) => r[0]?.transcript ?? "");
    };
    rec.onerror = (e) => {
      this.failed = e.error === "not-allowed" ? "The microphone is blocked for this site." : `Could not hear that (${e.error}).`;
    };
    this.ended = new Promise((done) => {
      rec.onend = () => done();
    });
    rec.start();
    this.rec = rec;
  }

  async stop(): Promise<Speech> {
    const r = this.rec;
    this.rec = null;
    if (!r) throw new Error("Not recording.");
    r.stop();
    await Promise.race([this.ended, new Promise((done) => setTimeout(done, 2500))]);
    if (this.failed) throw new Error(this.failed);
    return { text: this.heard.join(" ").trim(), seconds: Math.round((Date.now() - this.started) / 100) / 10 };
  }
}

/** The words heard, checked: nothing heard is an error the page shows in place. */
export async function transcribe(speech: Speech, languages?: string): Promise<Speech> {
  void languages; // the language is chosen when listening starts
  if (!speech.text) throw new Error("Nothing was heard. Try again, a little closer to the microphone.");
  return speech;
}

/** Every language the assistant understands, as the transcription hint when nobody chose one. */
export const ANY_LANGUAGE = "English,Hindi,Telugu";

/** The language a text is written in, from its script: Telugu, Devanagari (Hindi), or English. */
export function langOf(text: string): "te-IN" | "hi-IN" | "en-IN" {
  const telugu = (text.match(/[\u0C00-\u0C7F]/g) ?? []).length;
  const hindi = (text.match(/[\u0900-\u097F]/g) ?? []).length;
  if (telugu > 8 && telugu >= hindi) return "te-IN";
  if (hindi > 8) return "hi-IN";
  return "en-IN";
}

function voices(): Promise<SpeechSynthesisVoice[]> {
  const now = speechSynthesis.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((done) => {
    const t = setTimeout(() => done(speechSynthesis.getVoices()), 1200);
    speechSynthesis.addEventListener(
      "voiceschanged",
      () => {
        clearTimeout(t);
        done(speechSynthesis.getVoices());
      },
      { once: true },
    );
  });
}

/** A voice that speaks the language natively: never an English voice reading Hindi or Telugu.
 * Prefers the higher quality network voices browsers offer (Google, Microsoft "Online"). */
async function voiceFor(lang: string): Promise<SpeechSynthesisVoice | undefined> {
  const all = await voices();
  const norm = (v: SpeechSynthesisVoice) => v.lang.replace("_", "-").toLowerCase();
  const exact = all.filter((v) => norm(v) === lang.toLowerCase());
  const family = all.filter((v) => norm(v).startsWith(lang.slice(0, 2).toLowerCase()));
  const pool = exact.length ? exact : family;
  const good = (v: SpeechSynthesisVoice) => /google|online|natural|neural/i.test(v.name);
  return pool.find(good) ?? pool[0];
}

/** Long answers are spoken a sentence at a time: browsers cut a single long utterance short. */
function pieces(text: string): string[] {
  return (text.match(/[^.!?।\n]+[.!?।]?/g) ?? [text]).map((p) => p.trim()).filter(Boolean);
}

/** Speak ``text`` in its own language, found from its script: an English notice always sounds
 * English, a Telugu answer is read by a Telugu voice. */
export async function say(text: string, { rate = 1 }: { rate?: number } = {}): Promise<void> {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  const clean = speakable(text);
  const lang = langOf(clean);
  const voice = await voiceFor(lang);
  await new Promise<void>((done) => {
    const parts = pieces(clean);
    parts.forEach((part, i) => {
      const u = new SpeechSynthesisUtterance(part);
      u.lang = voice?.lang ?? lang;
      if (voice) u.voice = voice;
      u.rate = rate;
      if (i === parts.length - 1) {
        u.onend = () => done();
        u.onerror = () => done();
      }
      speechSynthesis.speak(u);
    });
    if (!parts.length) done();
  });
}

/** Whether this browser has a voice for ``lang`` at all (Telugu often needs one installed). */
export async function hasVoice(lang: string): Promise<boolean> {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return false;
  return (await voiceFor(lang)) !== undefined;
}
