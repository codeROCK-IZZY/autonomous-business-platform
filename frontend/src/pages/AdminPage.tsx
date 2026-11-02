import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Role, User } from "../api/types";
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, PageHeader, Select, Stat, Table } from "../components/ui";
import { useAuth } from "../lib/auth";
import { dateTime } from "../lib/format";

interface Stats { runs_by_status: Record<string, number>; approvals_by_status: Record<string, number>;
  tool_calls_by_status: Record<string, number>; audit_events: number }
interface JobRow { id: number; type: string; status: string; attempts: number; last_error: string | null; created_at: string }

export function AdminPage() {
  const stats = useQuery({ queryKey: ["admin-stats"], queryFn: () => api<Stats>("/admin/stats") });
  const jobs = useQuery({ queryKey: ["admin-jobs"], queryFn: () => api<JobRow[]>("/admin/jobs?limit=30"), refetchInterval: 5000 });
  const s = stats.data;
  return (
    <>
      <PageHeader title="Administration" />
      {s && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Stat label="Runs" value={Object.values(s.runs_by_status).reduce((a, b) => a + b, 0)}
            sub={Object.entries(s.runs_by_status).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · ")} />
          <Stat label="Approvals" value={Object.values(s.approvals_by_status).reduce((a, b) => a + b, 0)}
            sub={Object.entries(s.approvals_by_status).map(([k, v]) => `${v} ${k}`).join(" · ")} />
          <Stat label="Tool calls" value={Object.values(s.tool_calls_by_status).reduce((a, b) => a + b, 0)}
            sub={Object.entries(s.tool_calls_by_status).map(([k, v]) => `${v} ${k}`).join(" · ")} />
          <Stat label="Audit events" value={s.audit_events} />
        </div>
      )}
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <UsersCard />
        <Card title="Recent processing jobs">
          {jobs.isLoading ? <Loading /> : (
            <Table>
              <thead><tr><th>#</th><th>Type</th><th>Status</th><th>Tries</th><th>Created</th></tr></thead>
              <tbody>{jobs.data?.map((j) => (
                <tr key={j.id} title={j.last_error ?? undefined}><td>{j.id}</td><td>{j.type}</td>
                  <td><Badge tone={j.status === "succeeded" ? "green" : j.status === "failed" ? "red" : "sky"}>{j.status}</Badge></td>
                  <td>{j.attempts}</td><td className="whitespace-nowrap">{dateTime(j.created_at)}</td></tr>
              ))}</tbody>
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}

function UsersCard() {
  const { user: me } = useAuth();
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/users") });
  const [form, setForm] = useState({ email: "", password: "", role: "operator" as Role });
  const create = useMutation({
    mutationFn: () => api("/users", { body: form }),
    onSuccess: () => { setForm({ email: "", password: "", role: "operator" }); void qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<User> }) => api(`/users/${id}`, { method: "PATCH", body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["users"] }),
  });
  return (
    <Card title="Users">
      <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }} className="mb-4 grid gap-2 sm:grid-cols-4">
        <Field label="Email"><Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
        <Field label="Password"><Input type="password" required minLength={12} autoComplete="new-password" value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
        <Field label="Role"><Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
          <option value="viewer">viewer</option><option value="operator">operator</option><option value="approver">approver</option><option value="admin">admin</option></Select></Field>
        <div className="flex items-end"><Button type="submit" busy={create.isPending} className="w-full">Add user</Button></div>
      </form>
      <ErrorBox error={create.error ?? update.error} />
      {users.isLoading ? <Loading /> : (
        <Table>
          <thead><tr><th>Email</th><th>Role</th><th>Status</th><th /></tr></thead>
          <tbody>{users.data?.map((u) => (
            <tr key={u.id}><td>{u.email}</td>
              <td><Select aria-label={`Role for ${u.email}`} value={u.role} disabled={u.id === me?.id} className="w-28"
                onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                <option value="viewer">viewer</option><option value="operator">operator</option><option value="approver">approver</option><option value="admin">admin</option></Select></td>
              <td>{u.is_active ? "active" : "deactivated"}</td>
              <td>{u.id !== me?.id && <Button variant="secondary" onClick={() => update.mutate({ id: u.id, body: { is_active: !u.is_active } })}>
                {u.is_active ? "Deactivate" : "Reactivate"}</Button>}</td></tr>
          ))}</tbody>
        </Table>
      )}
    </Card>
  );
}
