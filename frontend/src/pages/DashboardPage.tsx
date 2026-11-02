import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Dashboard, Run, Workflow } from "../api/types";
import { OutcomeBadge, StatusBadge, WORKFLOW_LABEL } from "../components/agent";
import { Card, Empty, Loading, PageHeader, Stat, Table } from "../components/ui";
import { dateTime, pct } from "../lib/format";

export function DashboardPage() {
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api<Dashboard>("/dashboard"), refetchInterval: 5000 });
  const runs = useQuery({ queryKey: ["runs", "recent"], queryFn: () => api<Run[]>("/runs?limit=8"), refetchInterval: 5000 });
  const d = dash.data;
  if (!d) return <Loading />;
  const total = Object.values(d.runs_by_status).reduce((a, b) => a + (b ?? 0), 0);
  return (
    <>
      <PageHeader title="Operations" subtitle={`Agent activity over the last ${d.days} days · reasoner: ${d.reasoner}`} />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Runs" value={total} sub={`${d.runs_by_status.completed ?? 0} completed · ${d.runs_by_status.failed ?? 0} failed`} />
        <Stat label="Automation rate" value={pct(d.automation_rate)} sub="of completed runs, finished without a human decision" />
        <Stat label="Waiting for approval" value={<Link className="text-amber-700 hover:underline" to="/approvals">{d.pending_approvals}</Link>} sub="open requests in the inbox" />
        <Stat label="Tool calls" value={(d.tool_calls.succeeded ?? 0) + (d.tool_calls.failed ?? 0) + (d.tool_calls.blocked ?? 0)}
          sub={`${d.tool_calls_retried} retried · ${d.tool_calls.blocked ?? 0} blocked by policy`} />
        <Stat label="LLM cost" value={`$${d.total_cost_usd.toFixed(4)}`} sub={d.reasoner === "offline-rules" ? "offline rules: free" : d.reasoner} />
      </div>
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="By workflow">
          <Table>
            <thead><tr><th>Workflow</th><th>Runs</th><th>Without approval</th><th>Cost</th></tr></thead>
            <tbody>{(Object.entries(d.by_workflow) as [Workflow, NonNullable<Dashboard["by_workflow"][Workflow]>][]).map(([wf, v]) => (
              <tr key={wf}><td>{WORKFLOW_LABEL[wf]}</td><td className="tabular-nums">{v.runs}</td>
                <td className="tabular-nums">{v.completed_without_approval}</td><td className="tabular-nums">${v.cost_usd.toFixed(4)}</td></tr>))}
            </tbody>
          </Table>
        </Card>
        <Card title="Outcomes">
          {!d.outcomes.length ? <Empty>No completed runs yet.</Empty> : (
            <ul className="space-y-1.5 text-sm">{d.outcomes.slice(0, 12).map((o) => (
              <li key={`${o.workflow}-${o.outcome}`} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2"><span className="w-28 text-xs text-slate-500">{WORKFLOW_LABEL[o.workflow]}</span><OutcomeBadge outcome={o.outcome} /></span>
                <span className="tabular-nums text-slate-700">{o.runs}</span>
              </li>))}
            </ul>
          )}
        </Card>
      </div>
      <Card title="Recent runs" className="mt-6" actions={<Link className="text-sm text-sky-700 hover:underline" to="/runs">All runs →</Link>}>
        <RunTable runs={runs.data ?? []} />
      </Card>
    </>
  );
}

export function RunTable({ runs }: { runs: Run[] }) {
  if (!runs.length) return <Empty>No runs yet. Start one from “New request”.</Empty>;
  return (
    <Table>
      <thead><tr><th>Request</th><th>Workflow</th><th>Status</th><th>Outcome</th><th>Started</th></tr></thead>
      <tbody>{runs.map((r) => (
        <tr key={r.id}>
          <td><Link className="text-sky-700 hover:underline" to={`/runs/${r.id}`}>{r.title}</Link>
            <p className="text-xs text-slate-500">{r.created_by ?? "system"}</p></td>
          <td className="text-xs">{WORKFLOW_LABEL[r.workflow]}</td>
          <td><StatusBadge status={r.status} /></td>
          <td><OutcomeBadge outcome={r.outcome} /></td>
          <td className="text-xs">{dateTime(r.created_at)}</td>
        </tr>))}
      </tbody>
    </Table>
  );
}
