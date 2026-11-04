"use client";

import { Check, Copy, MessageSquareText, Sparkles } from "lucide-react";
import { useState } from "react";

import { api, message } from "@/lib/api";

import { Modal } from "./modal";
import { Button, cx, Notice, Pill, Segmented, Spinner } from "./ui";

type Drafted = { sms: string; app_title: string; app_body: string; written_by: string; verified: boolean };

const LANGS = ["English", "Hindi", "Telugu"] as const;

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={() => navigator.clipboard?.writeText(text).then(() => {
        setDone(true);
        setTimeout(() => setDone(false), 1500);
      })}
      className="inline-flex items-center gap-1 rounded border border-control px-2 py-0.5 text-xs hover:bg-sunken"
    >
      {done ? <Check size={12} /> : <Copy size={12} />} {done ? "Copied" : "Copy"}
    </button>
  );
}

/** The message customer care sends passengers about a disruption, drafted by the writer model from
 * the engine's facts and checked before it is shown. Nothing is sent from here. */
export function PassengerMessage({ reportId }: { reportId: string }) {
  const [open, setOpen] = useState(false);
  const [lang, setLang] = useState<(typeof LANGS)[number]>("English");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Drafted | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function write(language: string) {
    setBusy(true);
    setError(null);
    try {
      setDraft(await api<Drafted>(`/api/reports/${encodeURIComponent(reportId)}/passenger-message`, { method: "POST", json: { language } }));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button
        tone="secondary"
        size="sm"
        onClick={() => {
          setOpen(true);
          if (!draft) write(lang);
        }}
      >
        <MessageSquareText size={13} /> Draft passenger message
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Message to passengers">
        <div className="flex flex-col gap-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Segmented
              label="Language"
              value={lang}
              onChange={(v) => {
                setLang(v);
                write(v);
              }}
              options={LANGS.map((l) => ({ value: l, label: l }))}
            />
            {draft && (
              <Pill tone={draft.written_by === "model" ? "accent" : "neutral"}>
                <Sparkles size={11} /> {draft.written_by === "model" ? "Nova on Bedrock · every figure checked" : "from the facts"}
              </Pill>
            )}
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          {busy ? (
            <p className="flex items-center gap-2 py-6 text-muted">
              <Spinner /> Writing it from the engine&apos;s facts…
            </p>
          ) : draft ? (
            <>
              <section className="rounded-lg border border-line">
                <header className="flex items-center justify-between border-b border-rule px-3 py-2 text-xs font-medium text-muted">
                  <span>
                    SMS <span className={cx("font-mono", draft.sms.length > 300 && "text-critical")}>{draft.sms.length}/300</span>
                  </span>
                  <CopyButton text={draft.sms} />
                </header>
                <p className="whitespace-pre-wrap px-3 py-2.5 leading-relaxed">{draft.sms}</p>
              </section>
              <section className="rounded-lg border border-line">
                <header className="flex items-center justify-between border-b border-rule px-3 py-2 text-xs font-medium text-muted">
                  <span>App notice</span>
                  <CopyButton text={`${draft.app_title}\n${draft.app_body}`} />
                </header>
                <div className="px-3 py-2.5">
                  <b className="block">{draft.app_title}</b>
                  <p className="mt-1 leading-relaxed">{draft.app_body}</p>
                </div>
              </section>
              <p className="text-xs text-muted">Nothing is sent from here: customer care checks it and sends it from their own system.</p>
            </>
          ) : null}
        </div>
      </Modal>
    </>
  );
}
