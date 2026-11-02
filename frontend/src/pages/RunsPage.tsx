import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Run, Workflow } from "../api/types";
import { WORKFLOW_LABEL } from "../components/agent";
import { Card, Loading, PageHeader, Select } from "../components/ui";
import { RunTable } from "./DashboardPage";

export function RunsPage() {
  const [workflow, setWorkflow] = useState<Workflow | "">("");
  const [status, setStatus] = useState("");
  const q = new URLSearchParams({ limit: "200" });
  if (workflow) q.set("workflow", workflow);
  if (status) q.set("status", status);
  const runs = useQuery({ queryKey: ["runs", workflow, status], queryFn: () => api<Run[]>(`/runs?${q}`), refetchInterval: 4000 });
  return (
    <>
      <PageHeader title="Runs" subtitle="Every task handed to an agent, with its outcome." />
      <div className="mb-4 grid max-w-md gap-2 sm:grid-cols-2">
        <Select aria-label="Workflow" value={workflow} onChange={(e) => setWorkflow(e.target.value as Workflow | "")}>
          <option value="">All workflows</option>
          {(Object.keys(WORKFLOW_LABEL) as Workflow[]).map((w) => <option key={w} value={w}>{WORKFLOW_LABEL[w]}</option>)}
        </Select>
        <Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Any status</option>
          {["queued", "running", "waiting_approval", "completed", "failed"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </Select>
      </div>
      <Card>{runs.isLoading ? <Loading /> : <RunTable runs={runs.data ?? []} />}</Card>
    </>
  );
}
