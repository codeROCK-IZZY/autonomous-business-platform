export type Role = "admin" | "approver" | "operator" | "viewer";
export type Workflow = "refund" | "invoice" | "purchase";
export type RunStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed";
export type Risk = "read" | "write" | "financial" | "communication";

export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; created_at: string }

export interface SystemInfo { llm_mode: "claude" | "offline"; llm_model: string | null; data_leaves_server: boolean; chaos_failure_rate: number }

export interface Run {
  id: string; workflow: Workflow; title: string; status: RunStatus; outcome: string | null; reasoner: string | null;
  llm_calls: number; cost_usd: number; created_by: string | null; created_at: string; completed_at: string | null;
  pending_approval: boolean;
}

export interface Step { seq: number; node: string; status: "completed" | "waiting" | "failed"; summary: string; detail: Record<string, unknown>; at: string }

export interface ToolCallRow {
  id: number; node: string; tool: string; risk: Risk; args: Record<string, unknown>; result: Record<string, unknown> | null;
  status: "succeeded" | "failed" | "blocked"; attempts: number; error: string | null; duration_ms: number; at: string;
}

export interface Approval {
  id: string; run_id: string; action: string; tool: string; risk: Risk; args: Record<string, unknown>;
  amount_cents: number | null; reason: string; context: Record<string, unknown>; required_role: Role;
  status: "pending" | "approved" | "rejected"; requested_at: string; decided_by: string | null; decided_at: string | null;
  comment: string; workflow?: Workflow; run_title?: string; requested_by?: string | null;
}

export interface RunDetail {
  run: Run; input: Record<string, unknown>; output: Record<string, unknown>; error: string | null;
  tokens: { input: number; output: number }; steps: Step[]; tool_calls: ToolCallRow[]; approvals: Approval[];
  llm_usage: { purpose: string; model: string; input_tokens: number; output_tokens: number; cost_usd: number }[];
  audit: { id: number; ts: string; actor: string; action: string; hash: string }[]; allowed_tools: string[];
}

export interface Sample { label: string; input: Record<string, string> }

export interface PolicyRow { key: string; value: number | boolean; default: number | boolean; description: string; updated_at: string | null }

export interface ToolRow { name: string; risk: Risk; description: string; guarded: boolean; workflows: Workflow[] }

export interface AuditRow {
  id: number; ts: string; actor: string; action: string; entity: string; entity_id: string | null; run_id: string | null;
  data: Record<string, unknown>; hash: string; prev_hash: string;
}

export interface Dashboard {
  days: number; runs_by_status: Partial<Record<RunStatus, number>>;
  by_workflow: Partial<Record<Workflow, { runs: number; completed_without_approval: number; cost_usd: number }>>;
  outcomes: { workflow: Workflow; outcome: string; runs: number }[]; pending_approvals: number;
  automation_rate: number | null; tool_calls: Partial<Record<"succeeded" | "failed" | "blocked", number>>;
  tool_calls_retried: number; total_cost_usd: number; reasoner: string;
}
