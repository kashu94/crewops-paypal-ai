import { message } from "./api";

export type Settled<T> = { ok: true; value: T } | { ok: false; error: string };

/** One part of a page that may fail on its own without blanking the rest. */
export const settle = <T,>(p: Promise<T>): Promise<Settled<T>> =>
  p.then(
    (value) => ({ ok: true as const, value }),
    (err) => ({ ok: false as const, error: message(err) }),
  );
