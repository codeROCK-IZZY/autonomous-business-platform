import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RunDetail } from "../api/types";
import { Json, OutcomeBadge, RiskBadge, StatusBadge, Timeline, WORKFLOW_LABEL, money } from "../components/agent";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, SourceTag, Stat, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";
import { ApprovalCard } from "./ApprovalsPage";

export function RunPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["run", id], queryFn: () => api<RunDetail>(`/runs/${id}`),
    refetchInterval: (query) => (["queued", "running"].includes(query.state.data?.run.status ?? "queued") ? 1500 : false),
  });
  const retry = useMutation({ mutationFn: () => api(`/runs/${id}/retry`, { method: "POST" }), onSuccess: () => void qc.invalidateQueries({ queryKey: ["run", id] }) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const d = q.data;
  const r = d.run;
  const pending = d.approvals.filter((a) => a.status === "pending");
  return (
    <>
      <PageHeader title={r.title} subtitle={<>{WORKFLOW_LABEL[r.workflow]} · started by {r.created_by ?? "system"} · {dateTime(r.created_at)}</>}
        actions={<><StatusBadge status={r.status} /><OutcomeBadge outcome={r.outcome} /></>} />
      {r.status === "failed" && (
        <div className="mb-4 flex items-center justify-between gap-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <span>{d.error}</span>
          {can("operator") && <Button variant="secondary" busy={retry.isPending} onClick={() => retry.mutate()}>Retry from last step</Button>}
        </div>
      )}
      {pending.map((a) => <ApprovalCard key={a.id} approval={a} onDone={() => void qc.invalidateQueries({ queryKey: ["run", id] })} />)}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Steps" value={d.steps.length} />
        <Stat label="Tool calls" value={d.tool_calls.length} sub={`${d.tool_calls.filter((t) => t.attempts > 1).length} retried · ${d.tool_calls.filter((t) => t.status === "blocked").length} blocked`} />
        <Stat label="Reasoner" value={<SourceTag source={r.reasoner?.startsWith("claude") ? r.reasoner : "rule:offline"} />}
          sub={r.llm_calls ? `${r.llm_calls} calls · ${d.tokens.input + d.tokens.output} tokens` : "no language-model calls"} />
        <Stat label="Cost" value={`$${r.cost_usd.toFixed(5)}`} />
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_420px]">
        <div className="space-y-6">
          <Card title="What the agent did">
            {!d.steps.length ? <Empty>Not started yet.</Empty> : (
              <Timeline items={d.steps.map((s) => ({
                key: s.seq, tone: s.status === "completed" ? "done" : s.status === "waiting" ? "wait" : "fail",
                title: <><span className="font-mono text-xs text-slate-500">{s.node}</span> {s.summary}</>,
              }))} />
            )}
          </Card>
          <Card title="Tool calls">
            {!d.tool_calls.length ? <Empty>No tools used.</Empty> : (
              <Table>
                <thead><tr><th>Tool</th><th>Risk</th><th>Status</th><th>Attempts</th><th>Details</th></tr></thead>
                <tbody>{d.tool_calls.map((t) => (
                  <tr key={t.id}>
                    <td className="font-mono text-xs">{t.tool}</td><td><RiskBadge risk={t.risk} /></td>
                    <td><Badge tone={t.status === "succeeded" ? "green" : t.status === "blocked" ? "red" : "amber"}>{t.status}</Badge></td>
                    <td className="tabular-nums">{t.attempts}</td>
                    <td><details className="text-xs"><summary className="cursor-pointer text-sky-700">args / result</summary>
                      <Json value={{ args: t.args, result: t.result, error: t.error }} className="mt-1" /></details></td>
                  </tr>))}
                </tbody>
              </Table>
            )}
            <p className="mt-2 text-xs text-slate-500">Tools this agent may use: {d.allowed_tools.join(", ")}.</p>
          </Card>
        </div>
        <div className="space-y-6">
          <Card title="Request"><Json value={d.input} /></Card>
          <Card title="Result">
            {"amount_cents" in d.output && <p className="mb-2 text-sm">Amount: <strong>{money(d.output.amount_cents as number)}</strong></p>}
            <Json value={d.output} />
          </Card>
          {d.approvals.length > 0 && (
            <Card title="Approvals">
              <ul className="space-y-2 text-sm">{d.approvals.map((a) => (
                <li key={a.id}><Badge tone={a.status === "approved" ? "green" : a.status === "rejected" ? "red" : "amber"}>{a.status}</Badge>{" "}
                  {a.action.replaceAll("_", " ")} {a.amount_cents !== null && money(a.amount_cents)}
                  {a.decided_by && <p className="text-xs text-slate-500">by {a.decided_by} · {dateTime(a.decided_at)}{a.comment && ` — “${a.comment}”`}</p>}</li>))}
              </ul>
            </Card>
          )}
          <Card title="Audit trail (this run)">
            <ul className="space-y-1 text-xs">{d.audit.map((e) => (
              <li key={e.id} className="flex justify-between gap-2"><span><span className="font-medium">{e.action}</span> · {e.actor}</span>
                <code className="text-slate-400" title="SHA-256 chain hash">{e.hash}</code></li>))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
