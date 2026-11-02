import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AuditRow, PolicyRow, ToolRow } from "../api/types";
import { RiskBadge, WORKFLOW_LABEL, money } from "../components/agent";
import { Badge, Button, Card, ErrorBox, Input, Loading, PageHeader, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

const TABS = [["policies", "Policies"], ["tools", "Tools & permissions"], ["audit", "Audit trail"]] as const;

export function GovernancePage() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("policies");
  return (
    <>
      <PageHeader title="Governance" subtitle="What agents may do on their own, which tools they can use, and everything they did." />
      <nav className="mb-5 flex gap-1 border-b border-slate-200">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} className={clsx("-mb-px border-b-2 px-3 py-2 text-sm",
            tab === k ? "border-sky-600 font-medium text-sky-800" : "border-transparent text-slate-600 hover:text-slate-900")}>{label}</button>
        ))}
      </nav>
      {tab === "policies" && <Policies />}
      {tab === "tools" && <Tools />}
      {tab === "audit" && <Audit />}
    </>
  );
}

const isMoney = (key: string) => key.endsWith("_cents");

function Policies() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["policies"], queryFn: () => api<PolicyRow[]>("/policies") });
  const [edits, setEdits] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number | boolean }) => api(`/policies/${key}`, { method: "PUT", body: { value } }),
    onSuccess: () => { setEdits({}); void qc.invalidateQueries({ queryKey: ["policies"] }); },
  });
  if (!q.data) return <Loading />;
  return (
    <Card>
      <Table>
        <thead><tr><th>Policy</th><th>Value</th><th>Default</th>{can("admin") && <th />}</tr></thead>
        <tbody>{q.data.map((p) => {
          const show = (v: number | boolean) => (typeof v === "boolean" ? (v ? "yes" : "no") : isMoney(p.key) ? money(v) : String(v));
          return (
            <tr key={p.key}>
              <td><p className="font-mono text-xs">{p.key}</p><p className="text-xs text-slate-600">{p.description}</p></td>
              <td className="font-medium">{show(p.value)}</td><td className="text-xs text-slate-500">{show(p.default)}</td>
              {can("admin") && (
                <td>{typeof p.value === "boolean" ? (
                  <Button variant="secondary" onClick={() => save.mutate({ key: p.key, value: !p.value })}>Turn {p.value ? "off" : "on"}</Button>
                ) : (
                  <div className="flex gap-1">
                    <Input aria-label={`New value for ${p.key}`} className="w-28" placeholder={isMoney(p.key) ? "dollars" : "value"}
                      value={edits[p.key] ?? ""} onChange={(e) => setEdits({ ...edits, [p.key]: e.target.value })} />
                    <Button variant="secondary" disabled={!edits[p.key]} onClick={() => {
                      const n = Number(edits[p.key]);
                      save.mutate({ key: p.key, value: isMoney(p.key) ? Math.round(n * 100) : n });
                    }}>Save</Button>
                  </div>
                )}</td>
              )}
            </tr>
          );
        })}</tbody>
      </Table>
      <ErrorBox error={save.error} />
      <p className="mt-2 text-xs text-slate-500">Approval requirements are computed by code from these values — never by the language model. Every change is audited.</p>
    </Card>
  );
}

function Tools() {
  const q = useQuery({ queryKey: ["tools"], queryFn: () => api<ToolRow[]>("/tools") });
  if (!q.data) return <Loading />;
  return (
    <Card>
      <Table>
        <thead><tr><th>Tool</th><th>Risk</th><th>Guarded</th><th>Used by</th></tr></thead>
        <tbody>{q.data.map((t) => (
          <tr key={t.name}><td><p className="font-mono text-xs">{t.name}</p><p className="text-xs text-slate-600">{t.description}</p></td>
            <td><RiskBadge risk={t.risk} /></td>
            <td>{t.guarded ? <Badge tone="amber">needs approval above limit</Badge> : <span className="text-xs text-slate-400">—</span>}</td>
            <td className="text-xs">{t.workflows.map((w) => WORKFLOW_LABEL[w]).join(", ")}</td></tr>))}
        </tbody>
      </Table>
      <p className="mt-2 text-xs text-slate-500">Least privilege: each agent can only call the tools of its own workflow. Calls outside the list, or guarded calls without an approved request, are blocked and audited.</p>
    </Card>
  );
}

function Audit() {
  const [action, setAction] = useState("");
  const q = useQuery({ queryKey: ["audit", action], queryFn: () => api<AuditRow[]>(`/audit?limit=300${action ? `&action=${encodeURIComponent(action)}` : ""}`) });
  const verify = useMutation({ mutationFn: () => api<{ valid: boolean; events: number; broken_at: number | null }>("/audit/verify") });
  return (
    <Card actions={<div className="flex items-center gap-2">
      {verify.data && <Badge tone={verify.data.valid ? "green" : "red"}>{verify.data.valid ? `chain valid · ${verify.data.events} events` : `broken at #${verify.data.broken_at}`}</Badge>}
      <Button variant="secondary" busy={verify.isPending} onClick={() => verify.mutate()}>Verify hash chain</Button></div>}>
      <Input aria-label="Filter by action" placeholder="Filter by action, e.g. approval. or tool.erp" value={action} onChange={(e) => setAction(e.target.value)} className="mb-3 max-w-sm" />
      {!q.data ? <Loading /> : (
        <Table>
          <thead><tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Run</th><th>Hash</th></tr></thead>
          <tbody>{q.data.map((e) => (
            <tr key={e.id} title={JSON.stringify(e.data)}>
              <td className="tabular-nums text-xs">{e.id}</td><td className="text-xs">{dateTime(e.ts)}</td>
              <td className="text-xs">{e.actor}</td><td className="font-mono text-xs">{e.action}</td>
              <td>{e.run_id && <Link className="text-xs text-sky-700 hover:underline" to={`/runs/${e.run_id}`}>open</Link>}</td>
              <td><code className="text-[11px] text-slate-400">{e.hash.slice(0, 12)}</code></td>
            </tr>))}
          </tbody>
        </Table>
      )}
      <p className="mt-2 text-xs text-slate-500">Append-only: the database rejects edits and deletions, and each event's hash covers the previous one.</p>
    </Card>
  );
}
