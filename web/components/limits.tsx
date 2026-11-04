"use client";

import { useState } from "react";

import { tool } from "@/lib/api";
import { words } from "@/lib/format";
import { useAction, useLoad } from "@/lib/use-load";

import { useSession } from "./session";
import { Button, control, cx, Loading, Mono, Notice, Pill, Table } from "./ui";

type Limit = {
  rule: string;
  title: string;
  param: string;
  value: number;
  regulator: number;
  stricter_is: "lower" | "higher";
  tightened: boolean;
  may_relax?: boolean;
};

function Row({ x, editable }: { x: Limit; editable: boolean }) {
  const { flash } = useSession();
  const { busy, run } = useAction();
  const [value, setValue] = useState(String(x.value));
  const [reason, setReason] = useState("");
  const lower = x.stricter_is === "lower";
  // Without may_relax a change can only be stricter than now; with it, back to the regulator.
  const loosest = x.may_relax ? x.regulator : x.value;
  const v = Number(value);
  const looser = lower ? v > loosest : v < loosest;
  const pastRegulator = lower ? v > x.regulator : v < x.regulator;
  const problem = !value || Number.isNaN(v) || v <= 0 ? "Enter a number." : pastRegulator ? "Never looser than the regulator." : looser ? "You may only make it stricter." : null;
  return (
    <tr>
      <td>
        <Mono className="font-medium">{x.rule}</Mono>
        <span className="block text-xs text-muted">{x.title}</span>
      </td>
      <td>{words(x.param)}</td>
      <td className="text-right font-mono">{x.regulator}</td>
      <td className="text-right font-mono">
        {x.value} {x.tightened && <Pill tone="pending">stricter</Pill>}
      </td>
      <td className="text-xs text-muted">{lower ? "lower" : "higher"}</td>
      {editable && (
        <td>
          <form
            className="flex flex-wrap items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (problem) return flash(problem, "pending");
              if (reason.trim().length < 5) return flash("Give a reason of at least five characters.", "pending");
              run(x.rule + x.param, () => tool<{ value: number }>("set_rule_limit", { rule_id: x.rule, param: x.param, value: v, reason: reason.trim() }), (r) => `${x.rule} ${words(x.param)} is now ${r.value}.`).then(
                (r) => r && setReason(""),
              );
            }}
          >
            <input
              type="number"
              step="0.5"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              {...(lower ? { max: loosest } : { min: loosest })}
              aria-label={`New value for ${x.rule}`}
              className={cx(control, "h-8 w-20", problem && value !== String(x.value) && "border-critical")}
            />
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason" aria-label="Reason" className={cx(control, "h-8 w-44")} />
            <Button size="sm" tone="secondary" disabled={busy !== null}>
              Set
            </Button>
            <span className="basis-full text-[11px] text-faint">
              {x.may_relax ? `From the regulator's ${x.regulator} or stricter.` : `Only stricter than ${x.value}.`}
            </span>
          </form>
        </td>
      )}
    </tr>
  );
}

/** The duty limits, editable by an admin (stricter only) or a super admin (who may also
 * relax a limit back towards the regulator's value, never past it). */
export function LimitsTable() {
  const { can, me } = useSession();
  const editable = can("set_rule_limit");
  const { data, error } = useLoad(() => tool<{ limits: Limit[] }>("rule_limits"));
  if (error) return <div className="p-4"><Notice tone="error">{error}</Notice></div>;
  if (!data) return <Loading />;
  return (
    <>
      {editable && (
        <p className="border-b border-rule px-4 py-2.5 text-xs text-muted sm:px-5">
          {me.role === "super_admin"
            ? "As super admin you may tighten a limit, or relax it back towards the regulator's figure; never past it."
            : "As admin you may only make a limit stricter. Relaxing one back needs a super admin."}{" "}
          Every change needs a reason and is logged.
        </p>
      )}
      <Table>
        <thead>
          <tr>
            <th>Rule</th>
            <th>Limit</th>
            <th className="text-right">Regulator</th>
            <th className="text-right">Now</th>
            <th>Stricter is</th>
            {editable && <th>Change</th>}
          </tr>
        </thead>
        <tbody>
          {data.limits.map((x) => (
            <Row key={x.rule + x.param + x.value} x={x} editable={editable} />
          ))}
        </tbody>
      </Table>
    </>
  );
}
