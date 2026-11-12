"use client";

import {
  ArrowDownLeft,
  ArrowUpRight,
  Download,
  Receipt,
  RotateCcw,
  ShieldCheck,
  Undo2,
  Wallet,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
  Button,
  Empty,
  Kpi,
  Kpis,
  Loading,
  Mono,
  Notice,
  PageHeader,
  Panel,
  Pill,
  Segmented,
  StatusPill,
  Table,
} from "@/components/ui";
import { tool } from "@/lib/api";
import { inr, plural } from "@/lib/format";
import { useLoad } from "@/lib/use-load";

type Kind = "checkout" | "refund" | "payout";
type Txn = {
  at: string;
  kind: Kind;
  what: string;
  direction: "in" | "out";
  amount: string;
  currency: string;
  inr: number;
  fee?: string | null;
  by: string | null;
  for: string;
  plan_id: string;
  trip_id: string;
  crew_id: string;
  status: string;
  mode: "paypal" | "simulated";
  paypal: Record<string, string | null>;
};
type Ledger = {
  currency: string;
  count: number;
  totals: {
    taken_with_checkout: string;
    refunded: string;
    paid_out: string;
    on_its_way: string;
    paypal_fees: string;
  };
  transactions: Txn[];
};

const money = (amount: string | number, currency: string) =>
  `${currency === "USD" ? "$" : ""}${Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${currency === "USD" ? "" : ` ${currency}`}`;

const when = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });

const KIND: Record<Kind, { label: string; icon: typeof Receipt }> = {
  checkout: { label: "Checkout", icon: ArrowDownLeft },
  refund: { label: "Refund", icon: Undo2 },
  payout: { label: "Payout", icon: ArrowUpRight },
};

const ID_LABEL: Record<string, string> = {
  order_id: "order",
  capture_id: "capture",
  refund_id: "refund",
  batch_id: "batch",
  item_id: "item",
  transaction_id: "transaction",
};

/** The ledger as a CSV file the auditor can keep: one row per movement, every PayPal id. */
function download(rows: Txn[]) {
  const head = [
    "when_utc",
    "kind",
    "direction",
    "amount",
    "currency",
    "inr",
    "fee",
    "by",
    "for",
    "plan",
    "trip",
    "status",
    "mode",
    "paypal_ids",
  ];
  const cell = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = rows.map((r) =>
    [
      r.at,
      r.kind,
      r.direction,
      r.amount,
      r.currency,
      r.inr,
      r.fee ?? "",
      r.by ?? "",
      r.for,
      r.plan_id,
      r.trip_id,
      r.status,
      r.mode,
      Object.entries(r.paypal)
        .filter(([, v]) => v)
        .map(([k, v]) => `${ID_LABEL[k] ?? k}=${v}`)
        .join(" "),
    ]
      .map(cell)
      .join(","),
  );
  const blob = new Blob([[head.join(","), ...lines].join("\n")], {
    type: "text/csv",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "crewpay-paypal-transactions.csv";
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function TransactionsPage() {
  const [kind, setKind] = useState<"all" | Kind>("all");
  const { data, error } = useLoad(() =>
    tool<Ledger>("transactions", { limit: 500 }),
  );
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;

  const cur = data.currency;
  const count = (k: Kind) =>
    data.transactions.filter((t) => t.kind === k).length;
  const rows =
    kind === "all"
      ? data.transactions
      : data.transactions.filter((t) => t.kind === kind);

  return (
    <div className="animate-rise">
      <PageHeader
        title="Transactions"
        lede="Every movement of money through PayPal, newest first: what each approver paid with PayPal Checkout to confirm an approval, anything refunded, and every payout to crew, with the PayPal ids to trace each one. Read only."
        actions={
          <Button
            tone="secondary"
            disabled={!data.transactions.length}
            onClick={() => download(data.transactions)}
          >
            <Download size={14} /> Download CSV
          </Button>
        }
      />

      <Kpis>
        <Kpi
          icon={<ArrowDownLeft size={13} />}
          label="Taken with Checkout"
          value={money(data.totals.taken_with_checkout, cur)}
          sub={plural(count("checkout"), "approval")}
        />
        <Kpi
          icon={<RotateCcw size={13} />}
          label="Refunded"
          value={money(data.totals.refunded, cur)}
          sub={plural(count("refund"), "refund")}
          tone={Number(data.totals.refunded) ? "pending" : undefined}
        />
        <Kpi
          icon={<ArrowUpRight size={13} />}
          label="Paid out to crew"
          value={money(data.totals.paid_out, cur)}
          sub={plural(count("payout"), "payout")}
          tone="ok"
        />
        <Kpi
          icon={<Wallet size={13} />}
          label="PayPal fees"
          value={money(data.totals.paypal_fees, cur)}
        />
      </Kpis>

      <Panel
        flush
        title="Ledger"
        icon={<Receipt size={15} />}
        action={
          <Segmented
            label="Which transactions"
            value={kind}
            onChange={setKind}
            options={[
              { value: "all", label: "All", count: data.transactions.length },
              {
                value: "checkout",
                label: "Checkout",
                count: count("checkout"),
              },
              { value: "refund", label: "Refunds", count: count("refund") },
              { value: "payout", label: "Payouts", count: count("payout") },
            ]}
          />
        }
      >
        {rows.length ? (
          <Table>
            <thead>
              <tr>
                <th>When (IST)</th>
                <th>What</th>
                <th>By</th>
                <th>For</th>
                <th className="text-right">Amount</th>
                <th>Status</th>
                <th className="hidden lg:table-cell">PayPal ids</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t, i) => {
                const K = KIND[t.kind];
                return (
                  <tr key={`${t.kind}-${t.plan_id}-${i}`}>
                    <td className="whitespace-nowrap font-mono text-xs">
                      {when(t.at)}
                    </td>
                    <td>
                      <span className="inline-flex items-center gap-1.5 font-medium">
                        <K.icon
                          size={13}
                          className={
                            t.direction === "in" ? "text-ok" : "text-accent"
                          }
                        />
                        {K.label}
                      </span>
                      <div className="text-xs text-faint">
                        <Link
                          href="/approvals/"
                          className="font-mono hover:text-accent"
                        >
                          {t.plan_id}
                        </Link>{" "}
                        · <Mono>{t.trip_id}</Mono>
                      </div>
                    </td>
                    <td className="text-sm">{t.by ?? "–"}</td>
                    <td className="max-w-72 text-xs text-muted">{t.for}</td>
                    <td className="text-right tabular-nums">
                      <span className="font-semibold">
                        {t.direction === "in" ? "+" : "−"}
                        {money(t.amount, t.currency)}
                      </span>
                      <div className="text-xs text-muted">{inr(t.inr)}</div>
                      {t.fee && (
                        <div className="text-xs text-faint">
                          fee {money(t.fee, t.currency)}
                        </div>
                      )}
                    </td>
                    <td>
                      <StatusPill value={t.status} />
                      {t.mode === "simulated" && (
                        <Pill
                          className="ml-1"
                          title="No PayPal credentials on this server: the same steps, simulated"
                        >
                          simulated
                        </Pill>
                      )}
                    </td>
                    <td className="hidden lg:table-cell">
                      {Object.entries(t.paypal)
                        .filter(([, v]) => v)
                        .map(([k, v]) => (
                          <div key={k} className="text-xs text-faint">
                            {ID_LABEL[k] ?? k}{" "}
                            <Mono className="text-muted">{v}</Mono>
                          </div>
                        ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        ) : (
          <Empty icon={<ShieldCheck size={18} />}>
            {data.count
              ? "No transactions of this kind."
              : "No money has moved yet. When an approver pays for an approval with PayPal, it appears here, and so does the payout to the crew member."}
          </Empty>
        )}
      </Panel>
    </div>
  );
}
