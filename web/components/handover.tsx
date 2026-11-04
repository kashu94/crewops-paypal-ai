"use client";

import { ArrowRightLeft, Sparkles } from "lucide-react";
import { useState } from "react";

import { api, message } from "@/lib/api";

import { Modal } from "./modal";
import { Button, Notice, Pill, Spinner } from "./ui";

type Note = { headline: string; desks: string[]; next_shift: string[]; written_by: string };

/** What the next shift inherits, written by the writer model from the reports, plans and callouts,
 * with every id and count checked against them. */
export function Handover() {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setOpen(true);
    setNote(null);
    setError(null);
    try {
      setNote(await api<Note>("/api/handover"));
    } catch (err) {
      setError(message(err));
    }
  }

  return (
    <>
      <Button tone="secondary" onClick={load}>
        <ArrowRightLeft size={15} /> Shift handover
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Shift handover">
        {error ? (
          <Notice tone="error">{error}</Notice>
        ) : !note ? (
          <p className="flex items-center gap-2 py-6 text-sm text-muted">
            <Spinner /> Writing the handover from every desk&apos;s reports…
          </p>
        ) : (
          <div className="flex flex-col gap-4 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base font-semibold">{note.headline}</h3>
              <Pill tone={note.written_by === "model" ? "accent" : "neutral"}>
                <Sparkles size={11} /> {note.written_by === "model" ? "Nova on Bedrock · every figure checked" : "from the facts"}
              </Pill>
            </div>
            <section>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-[0.06em] text-faint">The desks</p>
              <ul className="flex flex-col gap-1.5">
                {note.desks.map((d) => (
                  <li key={d} className="rounded-md bg-sunken px-3 py-2 leading-relaxed">
                    {d}
                  </li>
                ))}
              </ul>
            </section>
            <section>
              <p className="mb-1.5 text-xs font-medium uppercase tracking-[0.06em] text-faint">First things for the next shift</p>
              <ol className="flex list-decimal flex-col gap-1 pl-5 leading-relaxed">
                {note.next_shift.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ol>
            </section>
          </div>
        )}
      </Modal>
    </>
  );
}
