"use client";

import { useEffect } from "react";

import { Spinner } from "@/components/ui";
import { api, hasSession, loadConfig } from "@/lib/api";
import { homeFor, type Me } from "@/lib/roles";

/** The front door: straight to the signed-in person's home, or to sign-in. */
export default function Home() {
  useEffect(() => {
    (async () => {
      await loadConfig();
      if (!hasSession()) return window.location.replace("/login/");
      const me = await api<Me>("/api/me").catch(() => null);
      window.location.replace(me ? homeFor(me.role) : "/login/");
    })();
  }, []);
  return (
    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted">
      <Spinner /> Opening CrewPay…
    </div>
  );
}
