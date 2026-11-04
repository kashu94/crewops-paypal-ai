"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSession } from "@/components/session";

import { message } from "./api";

export type Loaded<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void };

/** Loads data for a page and loads it again whenever the operation moves (the session's
 * tick) or `key` changes. A reload keeps the old data on screen until the new arrives. */
export function useLoad<T>(fn: () => Promise<T>, key: string = ""): Loaded<T> {
  const { tick } = useSession();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [again, setAgain] = useState(0);
  const latest = useRef(fn);
  useEffect(() => {
    latest.current = fn;
  });
  const shownKey = useRef(key);

  useEffect(() => {
    let alive = true;
    const keyChanged = shownKey.current !== key;
    shownKey.current = key;
    Promise.resolve()
      .then(() => {
        if (!alive) return;
        setLoading(true);
        if (keyChanged) setData(null);
        return latest.current();
      })
      .then(
        (d) => {
          if (!alive || d === undefined) return;
          setData(d as T);
          setError(null);
          setLoading(false);
        },
        (err) => {
          if (!alive) return;
          setError(message(err));
          setLoading(false);
        },
      );
    return () => {
      alive = false;
    };
  }, [key, tick, again]);

  const reload = useCallback(() => setAgain((n) => n + 1), []);
  return { data, error, loading, reload };
}

/** What an action says when it is done, with its tone when it is not plain good news. */
export type Said = { text: string; tone: "ok" | "pending" | "critical" };

/** Runs one action from a button: a busy flag meanwhile, the error shown as a toast, and a
 * refresh of everything after. */
export function useAction() {
  const { flash, bump } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(
    async <T,>(id: string, fn: () => Promise<T>, done?: (r: T) => string | Said | void): Promise<T | null> => {
      setBusy(id);
      try {
        const r = await fn();
        const said = done?.(r);
        if (typeof said === "string") flash(said);
        else if (said) flash(said.text, said.tone);
        bump();
        return r;
      } catch (err) {
        flash(message(err), "critical");
        return null;
      } finally {
        setBusy(null);
      }
    },
    [flash, bump],
  );
  return { busy, run };
}
