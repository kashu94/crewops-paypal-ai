"use client";

import { BellRing, CalendarOff, RotateCcw, Copy, FileUp, Gauge, KeyRound, ListChecks, Mail, Plus, Trash2, UserCog, Users, XCircle } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";

import { LimitsTable } from "@/components/limits";
import { Confirm } from "@/components/modal";
import { useSession } from "@/components/session";
import { Button, control, cx, Empty, Input, Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill, Segmented, Select, SeverityBadge, Spinner, StatusPill, Table } from "@/components/ui";
import { api, message, tool } from "@/lib/api";
import { plural, short, words } from "@/lib/format";
import { ROLE_LABEL, ROLES, type Role } from "@/lib/roles";
import type { ReportSummary } from "@/lib/types";
import { useAction, useLoad } from "@/lib/use-load";

type Account = { username: string; role: Role; crew_id: string | null; enabled: boolean; status: string; created: string };
type Address = { address: string; team: string; team_title: string; added_by: string; at: string; status: string };
type Upload = {
  status: "accepted" | "rejected";
  applied: boolean;
  counts: Record<string, Record<string, number>>;
  errors: (string | { where?: string; detail?: string })[];
  new_problems: { crew_id: string; trip_id: string; rule: string; detail: string }[];
};

type Tab = "accounts" | "alerts" | "roster" | "reports" | "limits";

const ADMIN_ROLES: Role[] = ["admin", "super_admin"];

/** A temporary password, shown once with a copy button. */
function Secret({ user, password, onDone }: { user: string; password: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Notice tone="pending" icon={<KeyRound size={15} />} className="mb-4">
      <p>
        Temporary password for <b>{user}</b>. It is shown once: pass it on now. They choose a new one at their first sign-in.
      </p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <code className="rounded bg-surface px-2.5 py-1 font-mono text-sm text-foreground ring-1 ring-line">{password}</code>
        <Button
          size="sm"
          tone="secondary"
          onClick={() =>
            navigator.clipboard?.writeText(password).then(
              () => setCopied(true),
              () => setCopied(false),
            )
          }
        >
          <Copy size={13} /> {copied ? "Copied" : "Copy"}
        </Button>
        <Button size="sm" tone="ghost" onClick={onDone}>
          I have passed it on
        </Button>
      </div>
    </Notice>
  );
}

function Accounts() {
  const { me, flash, bump } = useSession();
  const { busy, run } = useAction();
  const { data, error } = useLoad(() => tool<{ accounts: Account[] }>("team_accounts"));
  const [secret, setSecret] = useState<{ user: string; password: string } | null>(null);
  const [role, setRole] = useState<Role>("controller");
  const [creating, setCreating] = useState(false);
  const superAdmin = me.role === "super_admin";
  const assignable = ROLES.filter((r) => superAdmin || !ADMIN_ROLES.includes(r));
  const manageable = (a: Account) => a.username !== me.user && (superAdmin || !ADMIN_ROLES.includes(a.role));

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = Object.fromEntries(new FormData(form)) as Record<string, string>;
    setCreating(true);
    try {
      const r = await tool<{ username: string; temporary_password: string }>("create_account", {
        username: f.username.trim(),
        role,
        ...(role === "crew" && f.crew_id ? { crew_id: f.crew_id.trim().toUpperCase() } : {}),
        ...(f.email ? { email: f.email.trim() } : {}),
      });
      setSecret({ user: r.username, password: r.temporary_password });
      form.reset();
      bump();
    } catch (err) {
      flash(message(err), "critical");
    } finally {
      setCreating(false);
    }
  }

  const list = data?.accounts ?? [];
  return (
    <div className="flex flex-col gap-5">
      {secret && <Secret user={secret.user} password={secret.password} onDone={() => setSecret(null)} />}
      <Panel title="New account" icon={<Plus size={15} />}>
        <form onSubmit={create} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[1fr_12rem_9rem_1fr_auto] lg:items-end">
          <Input label="Username" name="username" required pattern="[a-z0-9._-]{3,40}" placeholder="j.doe" title="Lower case letters, digits, dots, dashes; 3 to 40 characters" />
          <Select label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {assignable.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
          <Input label="Crew id" name="crew_id" placeholder="C-1234" disabled={role !== "crew"} required={role === "crew"} pattern="[Cc]-\d{4}" />
          <Input label="Email (optional)" name="email" type="email" />
          <Button type="submit" disabled={creating}>
            {creating ? <Spinner /> : <Plus size={15} />} Create
          </Button>
        </form>
        <p className="mt-3 text-xs text-faint">
          Usernames are lower case, 3 to 40 characters. The new person gets a temporary password to change at first sign-in.
          {!superAdmin && " Admin accounts are managed by a super admin."}
        </p>
      </Panel>
      <Panel flush title="Team accounts" icon={<Users size={15} />} action={data ? plural(list.length, "account") : undefined}>
        {error ? (
          <div className="p-4">
            <Notice tone="error">{error}</Notice>
          </div>
        ) : !data ? (
          <Loading />
        ) : (
          <Table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Role</th>
                <th>State</th>
                <th>Created</th>
                <th className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((a) => {
                const ok = manageable(a);
                return (
                  <tr key={a.username} className={a.enabled ? "" : "text-muted"}>
                    <td>
                      <Mono className="font-medium">{a.username}</Mono>
                      {a.crew_id && <Mono className="ml-1.5 text-faint">{a.crew_id}</Mono>}
                      {a.username === me.user && <Pill className="ml-1.5">you</Pill>}
                    </td>
                    <td>
                      {ok ? (
                        <select
                          aria-label={`Role of ${a.username}`}
                          value={a.role}
                          disabled={busy !== null}
                          onChange={(e) => run(a.username, () => tool("set_account", { username: a.username, role: e.target.value }), () => `${a.username} is now ${words(e.target.value)}.`)}
                          className={cx(control, "h-8 w-40")}
                        >
                          {(assignable.includes(a.role) ? assignable : [a.role, ...assignable]).map((r) => (
                            <option key={r} value={r}>
                              {ROLE_LABEL[r]}
                            </option>
                          ))}
                        </select>
                      ) : (
                        ROLE_LABEL[a.role] ?? a.role
                      )}
                    </td>
                    <td>
                      <Pill tone={!a.enabled ? "neutral" : a.status === "active" ? "ok" : "pending"}>{a.enabled ? words(a.status) : "disabled"}</Pill>
                    </td>
                    <td className="font-mono text-xs text-muted">{a.created ? short(a.created.slice(0, 10)) : "–"}</td>
                    <td className="text-right">
                      {ok && (
                        <span className="inline-flex flex-wrap justify-end gap-1.5">
                          <Button
                            size="sm"
                            tone="secondary"
                            disabled={busy !== null}
                            onClick={() => {
                              if (!window.confirm(`Reset the password of ${a.username}? Their current one stops working.`)) return;
                              run(a.username, () => tool<{ username: string; temporary_password: string }>("reset_password", { username: a.username }), (r) => {
                                setSecret({ user: r.username, password: r.temporary_password });
                              });
                            }}
                          >
                            <KeyRound size={13} /> Reset password
                          </Button>
                          <Button
                            size="sm"
                            tone={a.enabled ? "danger-quiet" : "secondary"}
                            disabled={busy !== null}
                            onClick={() => run(a.username, () => tool("set_account", { username: a.username, enabled: !a.enabled }), () => `${a.username} ${a.enabled ? "disabled" : "enabled"}.`)}
                          >
                            {a.enabled ? "Disable" : "Enable"}
                          </Button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Panel>
    </div>
  );
}

function Alerts() {
  const { busy, run } = useAction();
  const { data, error } = useLoad(() => tool<{ addresses: Address[]; teams: { team: string; title: string }[] }>("alert_addresses"));
  const [address, setAddress] = useState("");
  const [team, setTeam] = useState("desk");
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;
  const byTeam = data.teams.map((t) => [t, data.addresses.filter((a) => a.team === t.team)] as const);
  return (
    <div className="flex flex-col gap-5">
      <Panel title="Add an alert address" icon={<Mail size={15} />}>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run("add", () => tool("add_alert_address", { address: address.trim(), team }), () => `${address.trim()} added.`).then((r) => r && setAddress(""));
          }}
          className="grid gap-3 sm:grid-cols-[1fr_16rem_auto] sm:items-end"
        >
          <Input label="Email address" type="email" required value={address} onChange={(e) => setAddress(e.target.value)} placeholder="desk@airline.example" />
          <Select label="Team" value={team} onChange={(e) => setTeam(e.target.value)}>
            {data.teams.map((t) => (
              <option key={t.team} value={t.team}>
                {t.title}
              </option>
            ))}
          </Select>
          <Button type="submit" disabled={busy !== null}>
            <Plus size={15} /> Add
          </Button>
        </form>
        <p className="mt-3 text-xs text-faint">A new address may need to confirm before it receives alerts.</p>
      </Panel>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {byTeam.map(([t, list]) => (
          <Panel key={t.team} flush title={t.title} icon={<BellRing size={15} />} action={plural(list.length, "address", "addresses")}>
            {list.length === 0 ? (
              <p className="px-4 py-4 text-sm text-muted sm:px-5">Nobody gets these alerts by email.</p>
            ) : (
              <ul className="divide-y divide-rule">
                {list.map((a) => (
                  <li key={a.address} className="flex items-center gap-2 px-4 py-2.5 sm:px-5">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm">{a.address}</span>
                      <span className="block text-xs text-faint">
                        added by {a.added_by} · {words(a.status)}
                      </span>
                    </span>
                    <button
                      aria-label={`Remove ${a.address}`}
                      title="Remove"
                      disabled={busy !== null}
                      onClick={() => run(a.address, () => tool("remove_alert_address", { address: a.address, team: a.team }), () => `${a.address} removed.`)}
                      className="rounded-md p-1.5 text-muted hover:bg-critical-soft hover:text-critical"
                    >
                      <Trash2 size={14} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        ))}
      </div>
    </div>
  );
}

function RosterUpload() {
  const { flash, bump } = useSession();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Upload | null>(null);
  const [checked, setChecked] = useState<File | null>(null);
  const [busy, setBusy] = useState<"dry" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(dry: boolean) {
    if (!file) return;
    setBusy(dry ? "dry" : "apply");
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", dry ? "true" : "false");
      let r: Upload & { error?: string };
      try {
        r = await api<Upload & { error?: string }>("/api/roster/upload", { method: "POST", form });
      } catch (err) {
        if (!/404|not available/i.test(message(err))) throw err;
        r = await tool<Upload>("upload_roster", { content: JSON.parse(await file.text()), dry_run: dry });
      }
      if (r.error) throw new Error(r.error);
      setResult(r);
      if (dry) setChecked(r.status === "accepted" ? file : null);
      else {
        setChecked(null);
        if (r.applied) {
          flash("Roster applied.");
          bump();
        }
      }
    } catch (err) {
      setError(message(err));
      setResult(null);
    } finally {
      setBusy(null);
    }
  }

  const counts = Object.entries(result?.counts ?? {});
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <Panel title="Upload a roster file" icon={<FileUp size={15} />}>
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            A JSON file with any of <Mono>trips</Mono>, <Mono>crew</Mono>, <Mono>standby</Mono> and <Mono>certificates</Mono>. Check it first: nothing changes until you apply a file that passed.
          </p>
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-control bg-sunken px-4 py-6 text-center text-sm text-muted hover:border-accent">
            <FileUp size={22} className="text-faint" />
            <span>
              <b className="text-foreground">{file ? file.name : "Choose a .json file"}</b>
              {file && <span className="block text-xs">{(file.size / 1024).toFixed(1)} KB · at most 2 MB</span>}
            </span>
            <input
              type="file"
              accept="application/json,.json"
              className="max-w-full text-xs"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setResult(null);
                setChecked(null);
              }}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button tone="secondary" disabled={!file || busy !== null} onClick={() => send(true)}>
              {busy === "dry" ? <Spinner /> : <ListChecks size={15} />} Check (dry run)
            </Button>
            <Button
              disabled={!file || checked !== file || busy !== null}
              title={checked !== file ? "Check the file first" : undefined}
              onClick={() => window.confirm("Apply this roster? The trips and crew in it replace what is there now.") && send(false)}
            >
              {busy === "apply" ? <Spinner /> : <FileUp size={15} />} Apply
            </Button>
          </div>
          {error && <Notice tone="error">{error}</Notice>}
        </div>
      </Panel>
      <Panel title="Result" icon={<ListChecks size={15} />}>
        {!result ? (
          <Empty>Check a file to see what it would change.</Empty>
        ) : (
          <div className="flex flex-col gap-4">
            <Notice tone={result.status === "rejected" ? "error" : result.applied ? "ok" : "info"}>
              {result.status === "rejected"
                ? "Rejected: nothing was changed. Fix the errors below and check again."
                : result.applied
                  ? "Applied. The roster now includes this file."
                  : "Passed the check. Nothing has changed yet: apply it to load it."}
            </Notice>
            {counts.length > 0 && (
              <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-4">
                {counts.map(([what, c]) => (
                  <div key={what} className="bg-surface px-3 py-2.5">
                    <p className="text-xs font-medium capitalize text-muted">{words(what)}</p>
                    <p className="mt-0.5 text-sm">
                      {Object.entries(c)
                        .map(([k, v]) => `${v} ${k}`)
                        .join(" · ")}
                    </p>
                  </div>
                ))}
              </div>
            )}
            {result.errors.length > 0 && (
              <div>
                <h3 className="mb-1 text-[13px] font-semibold text-critical">{plural(result.errors.length, "error")}</h3>
                <ul className="list-disc space-y-0.5 pl-5 text-sm">
                  {result.errors.map((e, i) => (
                    <li key={i}>{typeof e === "string" ? e : `${e.where ? `${e.where}: ` : ""}${e.detail ?? JSON.stringify(e)}`}</li>
                  ))}
                </ul>
              </div>
            )}
            <div>
              <h3 className="mb-1 text-[13px] font-semibold">New problems it would cause</h3>
              {result.new_problems.length === 0 ? (
                <p className="text-sm text-ok">None: every seat stays legal.</p>
              ) : (
                <div className="rounded-md border border-line">
                  <Table>
                    <thead>
                      <tr>
                        <th>Crew</th>
                        <th>Trip</th>
                        <th>Rule</th>
                        <th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.new_problems.map((p, i) => (
                        <tr key={i}>
                          <td><Mono>{p.crew_id}</Mono></td>
                          <td><Mono>{p.trip_id}</Mono></td>
                          <td><Pill tone="critical">{p.rule}</Pill></td>
                          <td className="text-xs">{p.detail}</td>
                        </tr>
                      ))}
                    </tbody>
                  </Table>
                </div>
              )}
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Revoke() {
  const { flash } = useSession();
  const { busy, run } = useAction();
  const { data, error } = useLoad(() => tool<{ disruptions: ReportSummary[] }>("disruptions", { status: "active", limit: 50 }));
  const [reason, setReason] = useState<Record<string, string>>({});
  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) return <Loading />;
  const list = data.disruptions ?? [];
  return (
    <Panel flush title="Revoke a report" icon={<XCircle size={15} />} action="for a report filed in error">
      <p className="border-b border-rule px-4 py-2.5 text-xs text-muted sm:px-5">Revoking stops all work on it and withdraws any absence it recorded. The reason is logged.</p>
      {list.length === 0 ? (
        <Empty>No active reports.</Empty>
      ) : (
        <ul className="divide-y divide-rule">
          {list.map((r) => (
            <li key={r.report_id} className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-5">
              <span className="min-w-0 flex-1 basis-64">
                <Link href={`/disruptions/view/?id=${encodeURIComponent(r.report_id)}`} className="block truncate text-sm font-medium hover:text-accent">
                  {r.title}
                </Link>
                <span className="text-xs text-muted">
                  <Mono>{r.report_id}</Mono> · {r.filed_by}, {r.filed_at}
                </span>
              </span>
              <SeverityBadge severity={r.severity} />
              <StatusPill value={r.status} />
              <input
                value={reason[r.report_id] ?? ""}
                onChange={(e) => setReason((x) => ({ ...x, [r.report_id]: e.target.value }))}
                placeholder="Reason"
                aria-label={`Reason to revoke ${r.report_id}`}
                className={cx(control, "h-8 w-44")}
              />
              <Button
                size="sm"
                tone="danger-quiet"
                disabled={busy !== null}
                onClick={() => {
                  const why = (reason[r.report_id] ?? "").trim();
                  if (why.length < 3) return flash("Say why it is revoked.", "pending");
                  if (!window.confirm(`Revoke ${r.report_id}?`)) return;
                  run(r.report_id, () => tool("revoke_report", { report_id: r.report_id, reason: why }), () => `${r.report_id} revoked.`);
                }}
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** A clean slate for the demo: every report, plan, callout and message goes, and the roster
 * starts again from today. Accounts and passwords are kept. */
function ResetDemo() {
  const { busy, run } = useAction();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button tone="danger-quiet" disabled={busy !== null} onClick={() => setOpen(true)}>
        <RotateCcw size={15} /> Reset the demo
      </Button>
      <Confirm
        open={open}
        title="Reset the demo to a clean slate?"
        action="Reset everything"
        onClose={() => setOpen(false)}
        onConfirm={() => {
          setOpen(false);
          run("reset", () => api("/api/admin/reset", { method: "POST" }), () => "The demo starts again from today: no reports, plans, callouts or messages.");
        }}
      >
        Every disruption report, plan, callout, message and photo record is removed, and the roster starts again from today with its one known problem. Accounts and passwords are not
        touched. This cannot be undone.
      </Confirm>
    </>
  );
}

export default function AdminPage() {
  const { me } = useSession();
  const [tab, setTab] = useState<Tab>("accounts");
  const { data } = useLoad(async () => {
    const [accounts, alerts] = await Promise.all([
      tool<{ accounts: Account[] }>("team_accounts").catch(() => null),
      tool<{ addresses: Address[] }>("alert_addresses").catch(() => null),
    ]);
    return { accounts: accounts?.accounts ?? null, addresses: alerts?.addresses ?? null };
  });
  return (
    <div className="animate-rise">
      <PageHeader
        eyebrow={ROLE_LABEL[me.role]}
        title="Administration"
        lede={
          me.role === "super_admin"
            ? "Team accounts including admins, alert addresses, the roster file, revoking reports, and duty limits, which you may also relax back towards the regulator."
            : "Team accounts, alert addresses, the roster file, revoking reports, and making duty limits stricter."
        }
        actions={
          <>
            <Link href="/report/?kind=planned_leave">
              <Button tone="secondary">
                <CalendarOff size={15} /> Book planned leave
              </Button>
            </Link>
            {me.role === "super_admin" && <ResetDemo />}
          </>
        }
      />
      <Kpis>
        <Kpi icon={<Users size={13} />} label="Accounts" value={data?.accounts?.length ?? "–"} />
        <Kpi icon={<UserCog size={13} />} label="Disabled" value={data?.accounts?.filter((a) => !a.enabled).length ?? "–"} />
        <Kpi icon={<Mail size={13} />} label="Alert addresses" value={data?.addresses?.length ?? "–"} />
        <Kpi icon={<KeyRound size={13} />} label="Must set a password" value={data?.accounts?.filter((a) => a.status !== "active").length ?? "–"} />
        <Kpi icon={<Gauge size={13} />} label="Your role" value={<span className="text-lg">{ROLE_LABEL[me.role]}</span>} />
      </Kpis>
      <div className="mb-5">
        <Segmented
          label="Section"
          value={tab}
          onChange={setTab}
          options={[
            { value: "accounts", label: "Team accounts" },
            { value: "alerts", label: "Alert addresses" },
            { value: "roster", label: "Roster upload" },
            { value: "reports", label: "Revoke a report" },
            { value: "limits", label: "Duty limits" },
          ]}
        />
      </div>
      {tab === "accounts" && <Accounts />}
      {tab === "alerts" && <Alerts />}
      {tab === "roster" && <RosterUpload />}
      {tab === "reports" && <Revoke />}
      {tab === "limits" && (
        <Panel flush title="Duty limits" icon={<Gauge size={15} />}>
          <LimitsTable />
        </Panel>
      )}
    </div>
  );
}
