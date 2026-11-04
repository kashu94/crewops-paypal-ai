"use client";

import { Gauge } from "lucide-react";

import { LimitsTable } from "@/components/limits";
import { useSession } from "@/components/session";
import { PageHeader, Panel } from "@/components/ui";

export default function LimitsPage() {
  const { can } = useSession();
  return (
    <div className="animate-rise">
      <PageHeader
        title="Duty limits"
        lede="The engine judges every seat against these. An airline may run stricter than the regulator, never looser. Each change needs a reason and is logged."
      />
      <Panel flush title="Limits" icon={<Gauge size={15} />} action={can("set_rule_limit") ? "you can change these" : "read only"}>
        <LimitsTable />
      </Panel>
    </div>
  );
}
