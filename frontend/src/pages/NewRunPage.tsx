import { useMutation, useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Run, Sample, Workflow } from "../api/types";
import { WORKFLOW_LABEL } from "../components/agent";
import { Button, Card, ErrorBox, Field, Input, PageHeader } from "../components/ui";

const FIELDS: Record<Workflow, { key: string; label: string; area?: boolean }[]> = {
  refund: [{ key: "from_email", label: "Customer e-mail" }, { key: "subject", label: "Subject" }, { key: "body", label: "Message", area: true }],
  invoice: [{ key: "vendor_name", label: "Supplier" }, { key: "text", label: "Invoice text", area: true }],
  purchase: [{ key: "requester_email", label: "Requester e-mail" }, { key: "department", label: "Department" }, { key: "text", label: "What do you need?", area: true }],
};
const HELP: Record<Workflow, string> = {
  refund: "A customer e-mail from the support inbox. The agent finds the order, checks the refund policy, refunds or declines, and replies.",
  invoice: "A supplier invoice. The agent matches it against the purchase order and goods receipt, then pays, rejects, or asks for review.",
  purchase: "An employee request. The agent matches the catalogue, checks the department budget, and creates purchase orders.",
};

export function NewRunPage() {
  const nav = useNavigate();
  const [workflow, setWorkflow] = useState<Workflow>("refund");
  const [values, setValues] = useState<Record<string, string>>({});
  const samples = useQuery({ queryKey: ["samples"], queryFn: () => api<Record<Workflow, Sample[]>>("/runs/samples") });
  const start = useMutation({
    mutationFn: () => api<Run>(`/runs/${workflow}`, { body: values }),
    onSuccess: (r) => nav(`/runs/${r.id}`),
  });
  const switchTo = (wf: Workflow) => { setWorkflow(wf); setValues({}); };
  return (
    <>
      <PageHeader title="New request" subtitle="Hand a task to an agent. Risky actions pause for a human; every step is recorded." />
      <div className="mb-4 flex gap-2">
        {(Object.keys(WORKFLOW_LABEL) as Workflow[]).map((wf) => (
          <button key={wf} onClick={() => switchTo(wf)}
            className={clsx("rounded-full border px-3 py-1.5 text-sm", wf === workflow ? "border-sky-600 bg-sky-50 font-medium text-sky-800" : "border-slate-200 hover:bg-slate-50")}>
            {WORKFLOW_LABEL[wf]}
          </button>
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
        <Card title={WORKFLOW_LABEL[workflow]}>
          <p className="mb-4 text-sm text-slate-600">{HELP[workflow]}</p>
          <form className="space-y-3" onSubmit={(e: FormEvent) => { e.preventDefault(); start.mutate(); }}>
            {FIELDS[workflow].map((f) => (
              <Field key={f.key} label={f.label}>
                {f.area ? (
                  <textarea aria-label={f.label} rows={workflow === "invoice" ? 12 : 6} value={values[f.key] ?? ""}
                    onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                    className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm focus:border-sky-500 focus:outline-none" />
                ) : (
                  <Input aria-label={f.label} value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                )}
              </Field>
            ))}
            <Button type="submit" busy={start.isPending}>Start agent</Button>
          </form>
          <ErrorBox error={start.error} />
        </Card>
        <Card title="Try a scenario">
          <p className="mb-2 text-xs text-slate-500">Examples from the synthetic demo data, including attempts to trick the agent.</p>
          <ul className="space-y-1">{samples.data?.[workflow].map((s) => (
            <li key={s.label}>
              <button onClick={() => setValues(s.input)} className="w-full rounded px-2 py-1.5 text-left text-sm capitalize hover:bg-slate-50">{s.label}</button>
            </li>))}
          </ul>
        </Card>
      </div>
    </>
  );
}
