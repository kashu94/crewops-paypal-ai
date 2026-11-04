"use client";

import { useState, type ReactNode } from "react";

import { Input } from "./ui";

/** A "from" and an "until" field, dates or date-times: the until field cannot go before the
 * from field, and moves with it if from is set past it. Rendered as two cells, so the parent's
 * grid lays them out. */
export function FromUntil({
  type = "date",
  from,
  until,
  fromLabel,
  untilLabel,
  fromName = type === "date" ? "first_day" : "start",
  untilName = type === "date" ? "last_day" : "end",
  untilRequired = true,
  hint,
}: {
  type?: "date" | "datetime-local";
  from: string;
  until?: string;
  fromLabel: ReactNode;
  untilLabel: ReactNode;
  fromName?: string;
  untilName?: string;
  untilRequired?: boolean;
  hint?: ReactNode;
}) {
  const [start, setStart] = useState(from);
  const [end, setEnd] = useState(until ?? "");
  return (
    <>
      <Input
        label={fromLabel}
        type={type}
        name={fromName}
        value={start}
        required
        onChange={(e) => {
          const next = e.target.value;
          setStart(next);
          if (end && next && end < next) setEnd(next);
        }}
      />
      <Input label={untilLabel} type={type} name={untilName} value={end} min={start || undefined} required={untilRequired} hint={hint} onChange={(e) => setEnd(e.target.value)} />
    </>
  );
}
