import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Approval } from "../api/types";
import { Json, RiskBadge, WORKFLOW_LABEL, money } from "../components/agent";
import { Badge, Button, Card, Empty, ErrorBox, Input, Loading, PageHeader, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

export function ApprovalsPage() {
  const qc = useQueryClient();
  const pending = useQuery({ queryKey: ["approvals", "pending"], queryFn: () => api<Approval[]>("/approvals?status=pending"), refetchInterval: 4000 });
  const decided = useQuery({ queryKey: ["approvals", "decided"], queryFn: () => api<Approval[]>("/approvals?status=all&limit=30") });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["approvals"] }); };
  return (
    <>
      <PageHeader title="Approvals" subtitle="Actions the agents may not take on their own. Review the evidence, then approve or reject." />
      {pending.isLoading ? <Loading /> : !pending.data?.length ? <Empty>Nothing waiting. 🎉</Empty> : (
        pending.data.map((a) => <ApprovalCard key={a.id} approval={a} onDone={refresh} showRun />)
      )}
      <Card title="Recently decided" className="mt-6">
        <Table>
          <thead><tr><th>Action</th><th>Amount</th><th>Decision</th><th>By</th><th>When</th></tr></thead>
          <tbody>{decided.data?.filter((a) => a.status !== "pending").map((a) => (
            <tr key={a.id}><td><Link className="text-sky-700 hover:underline" to={`/runs/${a.run_id}`}>{a.action.replaceAll("_", " ")}</Link>
              <p className="text-xs text-slate-500">{a.run_title}</p></td>
              <td className="tabular-nums">{money(a.amount_cents)}</td>
              <td><Badge tone={a.status === "approved" ? "green" : "red"}>{a.status}</Badge></td>
              <td className="text-xs">{a.decided_by}</td><td className="text-xs">{dateTime(a.decided_at)}</td></tr>))}
          </tbody>
        </Table>
      </Card>
    </>
  );
}

export function ApprovalCard({ approval: a, onDone, showRun }: { approval: Approval; onDone: () => void; showRun?: boolean }) {
  const { can, user } = useAuth();
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (decision: "approve" | "reject") => api(`/approvals/${a.id}/decision`, { body: { decision, comment } }),
    onSuccess: onDone,
  });
  const own = a.requested_by && user?.email === a.requested_by;
  const { customer_message, draft, invoice_text, request, ...rest } = a.context as Record<string, unknown>;
  const quoted = (customer_message ?? draft ?? invoice_text ?? request) as string | undefined;
  return (
    <Card className="mb-4 border-amber-300" title={<span className="flex flex-wrap items-center gap-2">
      <Badge tone="amber">needs approval</Badge>{a.action.replaceAll("_", " ")}{a.amount_cents !== null && <strong>{money(a.amount_cents)}</strong>}<RiskBadge risk={a.risk} /></span>}
      actions={showRun && <Link className="text-sm text-sky-700 hover:underline" to={`/runs/${a.run_id}`}>{a.workflow && WORKFLOW_LABEL[a.workflow]} run →</Link>}>
      <p className="text-sm"><span className="font-medium">Why:</span> {a.reason}</p>
      {a.run_title && <p className="mt-1 text-xs text-slate-500">{a.run_title} · requested {dateTime(a.requested_at)} by {a.requested_by ?? "system"}</p>}
      {quoted && <blockquote className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded border-l-4 border-slate-300 bg-slate-50 p-3 text-xs text-slate-700">{quoted}</blockquote>}
      <details className="mt-2 text-xs"><summary className="cursor-pointer text-sky-700">Evidence the agent collected</summary><Json value={{ action: a.args, ...rest }} className="mt-1" /></details>
      {can("approver") && !own ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Input aria-label="Comment" placeholder="Comment (recorded in the audit trail)" value={comment} onChange={(e) => setComment(e.target.value)} className="max-w-md" />
          <Button busy={decide.isPending} onClick={() => decide.mutate("approve")}>Approve</Button>
          <Button variant="secondary" busy={decide.isPending} onClick={() => decide.mutate("reject")}>Reject</Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-slate-500">{own ? "You started this run, so another approver must decide (separation of duties)." : "Only approvers can decide."}</p>
      )}
      <ErrorBox error={decide.error} />
    </Card>
  );
}
