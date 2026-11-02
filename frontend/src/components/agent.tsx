import clsx from "clsx";
import type { ReactNode } from "react";
import type { Risk, RunStatus, Workflow } from "../api/types";
import { Badge } from "./ui";

export const money = (cents: number | null | undefined) =>
  cents === null || cents === undefined ? "—" : `$${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const WORKFLOW_LABEL: Record<Workflow, string> = {
  refund: "Customer refund", invoice: "Supplier invoice", purchase: "Purchase request",
};

const STATUS_TONE = { queued: "slate", running: "sky", waiting_approval: "amber", completed: "green", failed: "red" } as const;
const STATUS_LABEL: Record<RunStatus, string> = {
  queued: "queued", running: "running", waiting_approval: "waiting for approval", completed: "completed", failed: "failed",
};

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Badge>;
}

/** Outcomes that moved money or created an order are green; refusals amber; hand-offs grey. */
export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="text-xs text-slate-400">—</span>;
  const tone = /^(refunded|paid|ordered|status_answered)/.test(outcome) ? "green"
    : outcome === "handed_to_human" ? "slate" : outcome.startsWith("rejected_duplicate") || outcome.includes("unknown") ? "red" : "amber";
  return <Badge tone={tone}>{outcome.replaceAll("_", " ")}</Badge>;
}

const RISK_TONE = { read: "slate", write: "sky", financial: "red", communication: "violet" } as const;

export function RiskBadge({ risk }: { risk: Risk }) {
  return <span title={`Risk level: ${risk}`}><Badge tone={RISK_TONE[risk]}>{risk}</Badge></span>;
}

export function Json({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={clsx("max-h-72 overflow-auto rounded bg-slate-950 p-3 text-xs leading-5 text-slate-100", className)}>
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

/** Vertical timeline of agent steps. */
export function Timeline({ items }: { items: { key: string | number; title: ReactNode; body?: ReactNode; tone: "done" | "wait" | "fail" }[] }) {
  const dot = { done: "bg-emerald-500", wait: "bg-amber-400", fail: "bg-red-500" };
  return (
    <ol className="relative ml-2 border-l border-slate-200">
      {items.map((it) => (
        <li key={it.key} className="mb-4 ml-4">
          <span className={clsx("absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white", dot[it.tone])} />
          <div className="text-sm">{it.title}</div>
          {it.body && <div className="mt-1 text-xs text-slate-600">{it.body}</div>}
        </li>
      ))}
    </ol>
  );
}
