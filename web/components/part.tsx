import type { ReactNode } from "react";

import type { Settled } from "@/lib/settle";

import { Notice } from "./ui";

/** Renders one settled part of a page: its content, or its error in place. */
export function Part<T>({ of, children }: { of: Settled<T> | undefined | null; children: (value: T) => ReactNode }) {
  if (!of) return null;
  if (!of.ok)
    return (
      <div className="p-4">
        <Notice tone="error">{of.error}</Notice>
      </div>
    );
  return <>{children(of.value)}</>;
}
