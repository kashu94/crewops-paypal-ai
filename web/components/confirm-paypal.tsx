"use client";

/* The second half of approving cover: the approver pays for it with PayPal, and only then does
   the approval take effect (roster changed, crew paid, seat booked). */

import { ExternalLink, ShieldCheck, Undo2, Wallet } from "lucide-react";
import { useEffect, useState } from "react";

import { loadConfig, tool, type Config } from "@/lib/api";
import { inr } from "@/lib/format";
import { useAction, useLoad } from "@/lib/use-load";

import { PayPalButton } from "./paypal-button";
import { useSession } from "./session";
import { Button, Loading, Mono, Notice } from "./ui";

export type ApprovalPayment = {
  funding_id: string;
  status: string;
  plan_id: string;
  trip_id: string;
  name: string;
  approver: string;
  lines: { what: string; inr: number; amount: string }[];
  total_inr: number;
  amount: string;
  currency: string;
  paid_with: string;
  paypal_order_id: string | null;
  paypal_approve_link: string | null;
  paypal_capture_id: string | null;
  paypal_refund_id: string | null;
  error: string | null;
};

export const usd = (amount: string | number, currency = "USD") =>
  `${currency === "USD" ? "$" : ""}${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${currency === "USD" ? "" : ` ${currency}`}`;

export function ConfirmWithPayPal({ planId }: { planId: string }) {
  const { flash, bump } = useSession();
  const { busy, run } = useAction();
  const [config, setConfig] = useState<Config | null>(null);
  useEffect(() => {
    loadConfig().then(setConfig);
  }, []);
  const { data, error } = useLoad(() => tool<{ mode: string; payments: ApprovalPayment[] }>("approval_payments", { plan_id: planId }), planId);
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;
  const f = data.payments.find((p) => p.status === "to pay");
  if (!f) return null;

  const confirm = (orderId?: string) =>
    run("confirm", () => tool<{ plan_status: string }>("confirm_approval", { funding_id: f.funding_id, ...(orderId ? { order_id: orderId } : {}) }), (r) =>
      r.plan_status === "approved" ? { text: `Approved and paid with PayPal: ${f.name} is rostered on ${f.trip_id} and paid.`, tone: "ok" as const } : { text: `Paid, but the plan is ${r.plan_status}: the payment is refunded.`, tone: "critical" as const },
    );
  const live = data.mode === "paypal" && config?.paypal_client_id && f.paypal_order_id;

  return (
    <div className="mt-2.5 rounded-lg border border-accent/30 bg-accent-soft/40 p-3">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <ShieldCheck size={15} className="text-accent" /> Confirm your approval with PayPal
      </p>
      <p className="mt-0.5 text-xs text-muted">Nothing has changed yet. Your approval takes effect, and {f.name} is rostered and paid, once you pay for the cover.</p>
      <ul className="mt-2 text-[13px]">
        {f.lines.map((l) => (
          <li key={l.what} className="flex justify-between gap-4">
            <span>{l.what.charAt(0).toUpperCase() + l.what.slice(1)}</span>
            <span className="tabular-nums text-muted">
              {inr(l.inr)} · {usd(l.amount, f.currency)}
            </span>
          </li>
        ))}
        <li className="mt-1 flex justify-between gap-4 border-t border-line pt-1 font-medium">
          <span>Total</span>
          <span className="tabular-nums">
            {inr(f.total_inr)} · {usd(f.amount, f.currency)}
          </span>
        </li>
      </ul>
      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        {live ? (
          <>
            <PayPalButton
              clientId={config!.paypal_client_id!}
              currency={f.currency}
              orderId={f.paypal_order_id!}
              onApproved={async (orderId) => {
                await confirm(orderId);
              }}
              onError={(m) => {
                flash(m, "critical");
                bump();
              }}
            />
            {f.paypal_approve_link && (
              <a href={f.paypal_approve_link} className="inline-flex items-center gap-1 text-xs text-muted hover:text-accent">
                <ExternalLink size={12} /> Open in PayPal instead
              </a>
            )}
          </>
        ) : (
          <Button size="sm" disabled={busy !== null} onClick={() => confirm()}>
            <Wallet size={13} /> Confirm with PayPal{data.mode === "simulated" ? " (simulated)" : ""}
          </Button>
        )}
        <Button size="sm" tone="secondary" disabled={busy !== null} onClick={() => run("cancel", () => tool("cancel_approval", { funding_id: f.funding_id }), () => "Not approved: the plan waits for a decision again.")}>
          <Undo2 size={13} /> Cancel approval
        </Button>
      </div>
      <p className="mt-1.5 text-[11px] text-faint">
        PayPal order <Mono>{f.paypal_order_id ?? "–"}</Mono> · {f.paid_with}
      </p>
    </div>
  );
}

/** Back from PayPal's approve link (?fund=…&token=<order id>): confirm once. */
export function useReturnFromPayPal() {
  const { can } = useSession();
  const { run } = useAction();
  useEffect(() => {
    if (!can("confirm_approval")) return;
    const q = new URLSearchParams(window.location.search);
    const fundingId = q.get("fund");
    const orderId = q.get("token");
    if (!fundingId) return;
    window.history.replaceState(null, "", window.location.pathname);
    if (!orderId) return;
    run("confirm", () => tool<{ plan_status: string }>("confirm_approval", { funding_id: fundingId, order_id: orderId }), (r) => `Approval ${r.plan_status}.`);
  }, [can, run]);
}
