"use client";

import { AtSign, Ban, CircleCheck, Clock3, Hourglass, Plane, RefreshCw, Send, ShieldCheck, Wallet } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { useSession } from "@/components/session";
import { Button, control, cx, Empty, Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill, StatusPill, Table } from "@/components/ui";
import { api, tool } from "@/lib/api";
import { cap, inr, plural, words } from "@/lib/format";
import { useAction, useLoad } from "@/lib/use-load";

type Line = { label: string; inr: number };
type Step = { at: string; status: string; by: string; note: string };
export type Payment = {
  payment_id: string;
  plan_id: string;
  trip_id: string;
  crew_id: string;
  name: string;
  rank: string;
  replacing: string;
  submitted_by: string;
  approved_by: string | null;
  released_by?: string | null;
  lines: Line[];
  airline_costs: Line[];
  total_inr: number;
  fx_inr_per_usd: number;
  amount: string;
  currency: string;
  receiver: string;
  receiver_is?: string;
  mode: "paypal" | "simulated";
  status: string;
  batch_id: string | null;
  item_id: string | null;
  transaction_id: string | null;
  paypal_status: string | null;
  fee?: string;
  error: string | null;
  created_at: string;
  history: Step[];
};
type Ledger = {
  mode: "paypal" | "simulated";
  currency: string;
  count: number;
  cap: number;
  fx_inr_per_usd: number;
  by_status: Record<string, { count: number; amount: string; inr: number }>;
  payments: Payment[];
  approvals: Funded[];
  payout_account: { email: string; is: string; set_at: string | null } | null;
};

const money = (amount: string | number, currency: string) =>
  `${currency === "USD" ? "$" : ""}${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${currency === "USD" ? "" : ` ${currency}`}`;

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });

function Actions({ p }: { p: Payment }) {
  const { me, can, flash } = useSession();
  const { busy, run } = useAction();
  const [reason, setReason] = useState("");
  const canRelease = p.status === "held" && can("release_payment") && me.user !== p.approved_by;
  const canCancel = ["held", "queued", "unclaimed"].includes(p.status) && can("cancel_payment");
  const canCheck = ["sent", "unclaimed", "blocked"].includes(p.status) && can("payment_status");
  if (!canRelease && !canCancel && !canCheck) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {canRelease && (
        <Button size="sm" disabled={busy !== null} onClick={() => run("release", () => tool("release_payment", { payment_id: p.payment_id }), () => `${p.payment_id} released to PayPal.`)}>
          <Send size={13} /> Release to PayPal
        </Button>
      )}
      {canCheck && (
        <Button size="sm" tone="secondary" disabled={busy !== null} onClick={() => run("check", () => tool("payment_status", { payment_id: p.payment_id }), () => `${p.payment_id} checked with PayPal.`)}>
          <RefreshCw size={13} /> Check with PayPal
        </Button>
      )}
      {canCancel && (
        <>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason to cancel" className={cx(control, "h-7 min-w-36 flex-1 text-[13px]")} />
          <Button
            size="sm"
            tone="secondary"
            disabled={busy !== null}
            onClick={() => {
              if (reason.trim().length < 3) return flash("Say why it is cancelled.", "pending");
              run("cancel", () => tool("cancel_payment", { payment_id: p.payment_id, reason: reason.trim() }), () => `${p.payment_id} cancelled.`);
            }}
          >
            <Ban size={13} /> Cancel
          </Button>
        </>
      )}
    </div>
  );
}

function Row({ p, crewView }: { p: Payment; crewView: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="cursor-pointer hover:bg-sunken/60" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <td>
          <Mono className="text-xs">{p.payment_id}</Mono>
          <div className="text-xs text-faint">{when(p.created_at)}</div>
        </td>
        {!crewView && (
          <td>
            <span className="font-medium">{p.name}</span>
            <div className="text-xs text-muted">
              {p.rank} · <Mono>{p.crew_id}</Mono>
            </div>
          </td>
        )}
        <td>
          <Mono>{p.trip_id}</Mono>
          <div className="text-xs text-muted">covering {p.replacing}</div>
        </td>
        <td className="text-right tabular-nums">
          <span className="font-semibold">{money(p.amount, p.currency)}</span>
          <div className="text-xs text-muted">{inr(p.total_inr)}</div>
        </td>
        <td>
          <StatusPill value={p.status} />
          {p.mode === "simulated" && (
            <Pill className="ml-1" title="No PayPal credentials on this server: the same steps, simulated">
              simulated
            </Pill>
          )}
        </td>
        <td className="hidden lg:table-cell">
          <Mono className="text-xs text-muted">{p.batch_id ?? "–"}</Mono>
          {p.paypal_status && <div className="text-xs text-faint">PayPal: {p.paypal_status}</div>}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={crewView ? 5 : 6} className="bg-sunken/50">
            <div className="grid gap-4 py-1 md:grid-cols-3">
              <div>
                <p className="mb-1 text-xs font-medium text-muted">Paid to the crew member, from the rate card</p>
                <ul className="text-sm">
                  {p.lines.map((l) => (
                    <li key={l.label} className="flex justify-between gap-4">
                      <span>{cap(l.label)}</span>
                      <span className="tabular-nums">{inr(l.inr)}</span>
                    </li>
                  ))}
                  <li className="mt-1 flex justify-between gap-4 border-t border-line pt-1 font-medium">
                    <span>Total</span>
                    <span className="tabular-nums">
                      {inr(p.total_inr)} = {money(p.amount, p.currency)}
                    </span>
                  </li>
                </ul>
                <p className="mt-1 text-xs text-faint">At ₹{p.fx_inr_per_usd} per US dollar.</p>
                {p.airline_costs.length > 0 && (
                  <p className="mt-2 text-xs text-muted">
                    Airline costs, not paid to crew: {p.airline_costs.map((l) => `${l.label} ${inr(l.inr)}`).join(", ")}.
                  </p>
                )}
              </div>
              <div className="text-sm">
                <p className="mb-1 text-xs font-medium text-muted">Who and where</p>
                <p>
                  Plan{" "}
                  <Link href="/approvals/" className="font-mono text-[0.92em] hover:text-accent">
                    {p.plan_id}
                  </Link>{" "}
                  by {p.submitted_by}, approved by {p.approved_by ?? "–"}
                  {p.released_by ? `, released by ${p.released_by}` : ""}.
                </p>
                <p className="mt-1">
                  To <Mono className="text-xs">{p.receiver}</Mono>
                  {p.receiver_is && <span className="text-xs text-muted">, {p.receiver_is}</span>}
                </p>
                {p.item_id && (
                  <p className="mt-1 text-xs text-muted">
                    Item <Mono>{p.item_id}</Mono>
                    {p.transaction_id && (
                      <>
                        {" "}
                        · transaction <Mono>{p.transaction_id}</Mono>
                      </>
                    )}
                    {p.fee && ` · fee $${p.fee}`}
                  </p>
                )}
                {p.error && (
                  <Notice tone="error" className="mt-2">
                    {p.error}
                  </Notice>
                )}
                <Actions p={p} />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-muted">History</p>
                <ol className="space-y-1 text-xs">
                  {p.history.map((h, i) => (
                    <li key={i} className="flex gap-2">
                      <span className="w-24 shrink-0 text-faint">{when(h.at)}</span>
                      <span>
                        <b className="font-medium">{cap(words(h.status))}</b> by {h.by}
                        {h.note ? `: ${h.note}` : ""}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function PayoutAccount({ account }: { account: NonNullable<Ledger["payout_account"]> }) {
  const { can } = useSession();
  const { busy, run } = useAction();
  const [email, setEmail] = useState("");
  const own = account.set_at !== null;
  return (
    <Panel title="Where you are paid" icon={<AtSign size={15} />} className="mb-5">
      <p className="text-sm">
        Your next payout goes to <Mono>{account.email}</Mono>
        <span className="text-muted">, {own ? "the account you chose" : account.is}</span>
        {own && <span className="text-xs text-faint"> · set {when(account.set_at!)}</span>}.
      </p>
      {!own && <p className="mt-1 text-xs text-muted">Give the email of your own PayPal account and you are paid there from now on.</p>}
      {can("set_payout_email") && (
        <form
          className="mt-3 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            run("payee", () => tool("set_payout_email", { email: email.trim() }), () => {
              setEmail("");
              return "Saved. You and crew control have been told about the change.";
            });
          }}
        >
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" aria-label="PayPal email" className={cx(control, "h-8 max-w-72 text-sm")} />
          <Button size="sm" type="submit" disabled={busy !== null || !email.trim()}>
            Pay me here
          </Button>
          <span className="text-xs text-faint">Payments already sent are not moved.</span>
        </form>
      )}
    </Panel>
  );
}

type Funded = {
  funding_id: string;
  plan_id: string;
  trip_id: string;
  crew_id: string;
  name: string;
  approver: string;
  lines: { label: string; inr: number; amount: string; for: "crew" | "seat" }[];
  total_inr: number;
  amount: string;
  currency: string;
  mode: "paypal" | "simulated";
  status: string;
  order_id: string | null;
  capture_id: string | null;
  refund_id: string | null;
  created_at: string;
};

/** What approvers paid with PayPal to confirm their approvals: the cover's full cost. */
function ApprovalPayments({ rows }: { rows: Funded[] }) {
  return (
    <Panel flush title="Approvals confirmed with PayPal" icon={<ShieldCheck size={15} />} className="mb-5" action={plural(rows.length, "payment")}>
      <p className="px-4 pt-3 text-sm text-muted sm:px-5">
        An approval of cover takes effect only once the approver pays for it with PayPal Checkout: the crew pay below, and the seat when cover flies in first. Then the crew member is paid out.
      </p>
      <Table>
        <thead>
          <tr>
            <th>Plan</th>
            <th>Cover</th>
            <th>Paid for</th>
            <th className="text-right">Amount</th>
            <th>Status</th>
            <th className="hidden lg:table-cell">PayPal</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((f) => (
            <tr key={f.funding_id}>
              <td>
                <Link href="/approvals/" className="font-mono text-xs hover:text-accent">
                  {f.plan_id}
                </Link>
                <div className="text-xs text-faint">by {f.approver}</div>
              </td>
              <td>
                <span className="font-medium">{f.name}</span>
                <div className="text-xs text-muted">
                  <Mono>{f.trip_id}</Mono>
                </div>
              </td>
              <td className="text-xs text-muted">
                {f.lines.map((l) => (
                  <div key={l.label} className="flex items-center gap-1">
                    {l.for === "seat" && <Plane size={11} />}
                    {cap(l.label)}
                  </div>
                ))}
              </td>
              <td className="text-right tabular-nums">
                <span className="font-semibold">{money(f.amount, f.currency)}</span>
                <div className="text-xs text-muted">{inr(f.total_inr)}</div>
              </td>
              <td>
                <StatusPill value={f.status} />
                {f.mode === "simulated" && (
                  <Pill className="ml-1" title="No PayPal credentials on this server: the same steps, simulated">
                    simulated
                  </Pill>
                )}
              </td>
              <td className="hidden lg:table-cell">
                <Mono className="text-xs text-muted">{f.order_id ?? "–"}</Mono>
                {f.capture_id && <div className="text-xs text-faint">capture {f.capture_id}</div>}
                {f.refund_id && <div className="text-xs text-faint">refund {f.refund_id}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </Panel>
  );
}

const FILTERS = ["all", "held", "sent", "paid", "unclaimed", "failed"] as const;

export default function PayPage() {
  const { me, can } = useSession();
  const { busy, run } = useAction();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>("all");
  const { data, error } = useLoad(() => api<Ledger>("/api/payments"));
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;

  const crewView = me.role === "crew";
  const s = (k: string) => data.by_status[k] ?? { count: 0, amount: "0", inr: 0 };
  const inFlight = Number(s("sent").amount) + Number(s("queued").amount);
  const attention = s("held").count + s("unclaimed").count + s("failed").count + s("returned").count + s("blocked").count;
  const rows = filter === "all" ? data.payments : data.payments.filter((p) => p.status === filter);

  return (
    <div className="animate-rise">
      <PageHeader
        title={crewView ? "My pay" : "Crew pay"}
        lede={
          crewView
            ? "What you were paid for covering a trip at short notice, line by line, and where each PayPal payment stands."
            : "An approver confirms each approval of cover by paying for it with PayPal; then the crew member is paid through PayPal Payouts: the callout, service and travel allowance the rules engine costed, never a figure a model made up. Anything over the cap waits for a second approver."
        }
        actions={
          !crewView && can("payment_status") ? (
            <Button tone="secondary" disabled={busy !== null} onClick={() => run("sync", () => api<{ changed: number }>("/api/payments/sync", { method: "POST" }), (r) => `${plural(r.changed, "payment")} updated from PayPal.`)}>
              <RefreshCw size={14} /> Check PayPal now
            </Button>
          ) : undefined
        }
      />
      <Notice className="mb-5" tone={data.mode === "paypal" ? "ok" : "info"} icon={<ShieldCheck size={15} />}>
        {data.mode === "paypal" ? (
          <>
            Paying through the <b>PayPal sandbox</b>. Each payout is named after its payment, so a retry can never pay twice; webhooks and a check every minute keep each status current.
          </>
        ) : (
          <>
            <b>Simulated payouts</b>: this server has no PayPal sandbox credentials, so the same steps run without PayPal. Add <Mono>PAYPAL_CLIENT_ID</Mono> and <Mono>PAYPAL_CLIENT_SECRET</Mono> to pay for real in the sandbox.
          </>
        )}{" "}
        Rupees are the cost of record, paid at ₹{data.fx_inr_per_usd} per dollar; over {money(data.cap, data.currency)} needs a second approver.
      </Notice>

      <Kpis>
        <Kpi icon={<CircleCheck size={13} />} label="Paid" value={money(s("paid").amount, data.currency)} sub={plural(s("paid").count, "payment")} tone="ok" />
        <Kpi icon={<Clock3 size={13} />} label="On its way" value={money(inFlight, data.currency)} sub={plural(s("sent").count + s("queued").count, "payment")} />
        <Kpi icon={<Hourglass size={13} />} label="Held for release" value={s("held").count} sub={money(s("held").amount, data.currency)} tone={s("held").count ? "pending" : undefined} />
        <Kpi icon={<Wallet size={13} />} label="Need attention" value={attention} sub="held, unclaimed or failed" tone={attention ? "critical" : undefined} />
      </Kpis>

      {crewView && data.payout_account && <PayoutAccount account={data.payout_account} />}

      {!crewView && data.approvals.length > 0 && <ApprovalPayments rows={data.approvals} />}

      <Panel
        flush
        title={crewView ? "Your payments" : "Payments"}
        icon={<Wallet size={15} />}
        action={
          <span className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                aria-pressed={filter === f}
                className={cx("rounded px-2 py-0.5 text-xs", filter === f ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-sunken")}
              >
                {cap(f)}
              </button>
            ))}
          </span>
        }
      >
        {rows.length ? (
          <Table>
            <thead>
              <tr>
                <th>Payment</th>
                {!crewView && <th>Crew</th>}
                <th>Trip</th>
                <th className="text-right">Amount</th>
                <th>Status</th>
                <th className="hidden lg:table-cell">PayPal batch</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => (
                <Row key={p.payment_id} p={p} crewView={crewView} />
              ))}
            </tbody>
          </Table>
        ) : (
          <Empty icon={<Wallet size={18} />}>
            {data.count ? "No payments in this state." : crewView ? "No payments yet. When you cover a trip and the plan is approved, your pay appears here." : "No payments yet. Approve a plan and its crew member is paid here."}
          </Empty>
        )}
      </Panel>
    </div>
  );
}
