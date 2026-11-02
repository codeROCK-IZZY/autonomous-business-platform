import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { money } from "../components/agent";
import { Card, Empty, Loading, PageHeader, Table } from "../components/ui";
import { dateTime } from "../lib/format";

type Row = Record<string, string | number | null>;
const VIEWS = {
  refunds: { label: "Refunds", cols: ["order", "customer", "amount_cents", "reason", "created_at"] },
  payments: { label: "Payments", cols: ["vendor", "invoice", "amount_cents", "scheduled_for", "created_at"] },
  invoices: { label: "Invoices", cols: ["vendor", "invoice", "total_cents", "status", "created_at"] },
  "purchase-orders": { label: "Purchase orders", cols: ["number", "vendor", "department", "total_cents", "created_at"] },
  budgets: { label: "Budgets", cols: ["department", "limit_cents", "committed_cents", "remaining_cents"] },
  emails: { label: "Outbox", cols: ["to", "subject", "body", "created_at"] },
} as const;
type View = keyof typeof VIEWS;

function cell(col: string, v: Row[string] | undefined) {
  if (v === null || v === undefined) return "—";
  if (col.endsWith("_cents")) return money(Number(v));
  if (col === "created_at") return dateTime(String(v));
  if (col === "body") return <span className="line-clamp-2 whitespace-pre-wrap text-xs">{String(v)}</span>;
  return String(v);
}

export function BusinessPage() {
  const [view, setView] = useState<View>("refunds");
  const q = useQuery({ queryKey: ["business", view], queryFn: () => api<Row[]>(`/business/${view}`), refetchInterval: 5000 });
  const cols = VIEWS[view].cols;
  return (
    <>
      <PageHeader title="Business systems" subtitle="What the agents changed in the (simulated) ERP: refunds, payments, invoices, orders, budgets and e-mails." />
      <nav className="mb-4 flex flex-wrap gap-2">
        {(Object.keys(VIEWS) as View[]).map((v) => (
          <button key={v} onClick={() => setView(v)} className={clsx("rounded-full border px-3 py-1.5 text-sm",
            v === view ? "border-sky-600 bg-sky-50 font-medium text-sky-800" : "border-slate-200 hover:bg-slate-50")}>{VIEWS[v].label}</button>
        ))}
      </nav>
      <Card>
        {q.isLoading ? <Loading /> : !q.data?.length ? <Empty>Nothing yet.</Empty> : (
          <Table>
            <thead><tr>{cols.map((c) => <th key={c}>{c.replace("_cents", "").replaceAll("_", " ")}</th>)}<th>Run</th></tr></thead>
            <tbody>{q.data.map((r, i) => (
              <tr key={String(r.id ?? r.number ?? r.department ?? i)}>
                {cols.map((c) => <td key={c} className={c.endsWith("_cents") ? "tabular-nums" : "text-sm"}>{cell(c, r[c])}</td>)}
                <td>{r.run_id ? <Link className="text-xs text-sky-700 hover:underline" to={`/runs/${r.run_id}`}>agent</Link> : <span className="text-xs text-slate-400">seed</span>}</td>
              </tr>))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
