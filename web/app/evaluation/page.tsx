"use client";

import { ChevronDown, LineChart } from "lucide-react";
import { useEffect, useState } from "react";

import { Kpi, Kpis, Loading, Mono, Notice, PageHeader, Panel, Pill, Segmented } from "@/components/ui";
import { Logo } from "@/components/shell";
import { api, message } from "@/lib/api";
import { words } from "@/lib/format";

type Scenario = {
  id: string;
  question: string;
  role: string;
  expected_intent: string;
  intent: string;
  routed_by?: string;
  passed: boolean;
  why: string;
  verified: boolean;
  blocked: boolean | string;
  tools?: string[];
  tokens: number;
  seconds: number;
  answer?: string;
};
type Scorecard = { run_at: string; summary: string; passed: number; total: number; scenarios: Scenario[] };

/** The published scorecard: a page of its own, open to anyone, like the TV board. */
export default function EvaluationPage() {
  return (
    <div className="min-h-screen bg-background">
      <header className="flex h-14 items-center gap-2.5 border-b border-line bg-surface px-4 sm:px-8">
        <Logo />
        <b className="text-[15px] tracking-tight">CrewPay</b>
        <span className="text-sm text-muted">Evaluation</span>
        <a href="/login/" className="ml-auto text-sm text-accent hover:underline">
          Sign in
        </a>
      </header>
      <main className="mx-auto flex max-w-6xl flex-col gap-8 px-4 py-7 sm:px-8">
        <Scorecard />
        <RouterEval />
      </main>
    </div>
  );
}

type RouterRun = {
  name: string;
  questions: number;
  intent_right: number;
  all_right: number;
  attacks_caught: number;
  attacks: number;
  false_alarms: number;
  median_seconds: number | null;
  tokens_per_question: number | null;
  misses: { question: string; intent: string; got_intent: string | null }[];
};

/** The router measured alone, on held-out questions in four ways of speaking. */
function RouterEval() {
  const [card, setCard] = useState<{ run_at: string; questions: number; examples_added: number; runs: RouterRun[] } | null>(null);
  useEffect(() => {
    api<typeof card>("/api/router-eval", { auth: false }).then(setCard, () => setCard(null));
  }, []);
  if (!card) return null;
  return (
    <Panel title="Router: which kind of question is this?" icon={<LineChart size={15} />} action={`${card.questions} held-out questions`} flush>
      <p className="px-4 pt-4 text-sm text-muted sm:px-5">
        English, Hinglish, Hindi and Telugu questions whose phrasings never appear in the training set. Run {card.run_at}.
      </p>
      <div className="overflow-x-auto p-4 sm:p-5">
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th className="py-2 pr-4 font-medium">Setup</th>
              <th className="py-2 pr-4 font-medium">Right</th>
              <th className="py-2 pr-4 font-medium">Attacks caught</th>
              <th className="py-2 pr-4 font-medium">False alarms</th>
              <th className="py-2 pr-4 font-medium">Median</th>
              <th className="py-2 font-medium">Tokens / question</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {card.runs.map((r) => (
              <tr key={r.name}>
                <td className="py-2.5 pr-4 font-medium">{r.name}</td>
                <td className="py-2.5 pr-4 font-mono">
                  {r.all_right}/{r.questions} <span className="text-muted">({Math.round((100 * r.all_right) / r.questions)}%)</span>
                </td>
                <td className="py-2.5 pr-4 font-mono">
                  {r.attacks_caught}/{r.attacks}
                </td>
                <td className="py-2.5 pr-4 font-mono">{r.false_alarms}</td>
                <td className="py-2.5 pr-4 font-mono">{r.median_seconds}s</td>
                <td className="py-2.5 font-mono">{r.tokens_per_question?.toLocaleString("en-IN")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 text-xs text-muted">
          Tuned by examples adds {card.examples_added} worked examples from the training set. Every miss is listed in docs/router_eval.md.
        </p>
      </div>
    </Panel>
  );
}

function Scorecard() {
  const [s, setS] = useState<Scorecard | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Scorecard>("/api/scorecard", { auth: false }).then(setS, (err) => setError(message(err)));
  }, []);
  const [show, setShow] = useState<"all" | "failed">("all");
  if (error)
    return (
      <div>
        <PageHeader title="Evaluation" />
        <Notice tone="error">{error}</Notice>
      </div>
    );
  if (!s) return <Loading />;
  const list = s.scenarios;
  const secs = list.filter((x) => !x.blocked).map((x) => x.seconds).sort((a, b) => a - b);
  const pct = (p: number) => (secs.length ? secs[Math.min(secs.length - 1, Math.floor(p * secs.length))] : 0);
  const routed = list.filter((x) => x.expected_intent !== "refused");
  const routeOk = routed.filter((x) => x.expected_intent.split(" or ").includes(x.intent)).length;
  const answered = list.filter((x) => !x.blocked);
  const verified = answered.filter((x) => x.verified).length;
  const shouldRefuse = list.filter((x) => x.expected_intent === "refused");
  const refused = shouldRefuse.filter((x) => x.blocked).length;
  const crew = list.filter((x) => x.role === "crew");
  const privacy = crew.filter((x) => !/crew member was shown/.test(x.why)).length;
  const shown = show === "failed" ? list.filter((x) => !x.passed) : list;

  return (
    <div className="animate-rise">
      <PageHeader title="Evaluation" lede={<>{s.summary} Run <Mono>{s.run_at.replace("T", " ").slice(0, 16)}</Mono> UTC against the live models; published as it came out, failures included.</>} />
      <Kpis>
        <Kpi label="Scenarios passed" value={s.passed} sub={`/ ${s.total}`} tone={s.passed === s.total ? "ok" : "pending"} />
        <Kpi label="Routed right" value={routeOk} sub={`/ ${routed.length}`} />
        <Kpi label="Every figure checked" value={verified} sub={`/ ${answered.length}`} />
        <Kpi label="Attacks refused" value={refused} sub={`/ ${shouldRefuse.length}`} />
        <Kpi label="Seconds per answer" value={pct(0.5)} sub={`median · p95 ${pct(0.95)}`} />
      </Kpis>
      <p className="mb-4 text-sm text-muted">
        Crew kept to their own record in {privacy} of {crew.length} crew scenarios.
      </p>
      <Panel
        flush
        title="Scenarios"
        icon={<LineChart size={15} />}
        action={
          <Segmented
            label="Show"
            value={show}
            onChange={setShow}
            options={[
              { value: "all", label: "All", count: list.length },
              { value: "failed", label: "Failed", count: list.filter((x) => !x.passed).length },
            ]}
          />
        }
      >
        <ul className="divide-y divide-rule">
          {shown.map((x) => (
            <li key={x.id}>
              <details>
                <summary className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-sunken sm:px-5">
                  <ChevronDown size={14} className="text-faint" />
                  <Mono className="w-8 text-muted">{x.id}</Mono>
                  <span className="min-w-0 flex-1 basis-72 text-sm">{x.question}</span>
                  <Pill>{words(x.role)}</Pill>
                  <span className="text-xs text-muted">
                    {x.expected_intent} → {x.intent}
                  </span>
                  {x.passed ? <Pill tone="ok">pass</Pill> : <Pill tone="critical">fail</Pill>}
                  <span className="w-28 text-right font-mono text-xs text-muted">
                    {x.tokens.toLocaleString("en-IN")} · {x.seconds}s
                  </span>
                </summary>
                <div className="flex flex-col gap-2 border-t border-rule bg-sunken/60 px-4 py-3 text-sm sm:px-5 sm:pl-16">
                  {!x.passed && x.why && <Notice tone="error">{x.why}</Notice>}
                  {x.tools && x.tools.length > 0 && <p className="text-xs text-muted">Tools: {x.tools.join(", ")}</p>}
                  {x.answer && <p className="whitespace-pre-wrap leading-relaxed">{x.answer}</p>}
                </div>
              </details>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
