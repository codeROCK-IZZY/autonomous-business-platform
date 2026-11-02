"""Start workflow runs and inspect them (steps, tool calls, approvals, cost, audit)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.agents import runner
from app.agents.runtime import ALLOWED_TOOLS, TOOLS
from app.api.deps import DB, Operator, Viewer
from app.core.errors import NotFound
from app.db.models import Approval, AuditEvent, LLMUsage, Run, RunStep, ToolCall, User

router = APIRouter(tags=["runs"])


class RefundIn(BaseModel):
    from_email: EmailStr
    subject: str = Field(default="", max_length=300)
    body: str = Field(min_length=1, max_length=10_000)


class InvoiceIn(BaseModel):
    vendor_name: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=50_000)


class PurchaseIn(BaseModel):
    requester_email: EmailStr
    department: str = Field(min_length=1, max_length=50)
    text: str = Field(min_length=1, max_length=5_000)


class RunOut(BaseModel):
    id: uuid.UUID
    workflow: str
    title: str
    status: str
    outcome: str | None
    reasoner: str | None
    llm_calls: int
    cost_usd: float
    created_by: str | None
    created_at: datetime
    completed_at: datetime | None
    pending_approval: bool


def _run_out(db: DB, run: Run, emails: dict[uuid.UUID, str] | None = None) -> RunOut:
    pending = db.scalar(select(func.count()).where(Approval.run_id == run.id, Approval.status == "pending")) or 0
    if emails is None:
        emails = {}
        if run.created_by:
            u = db.get(User, run.created_by)
            emails = {run.created_by: u.email} if u else {}
    return RunOut(
        id=run.id,
        workflow=run.workflow,
        title=run.title,
        status=run.status,
        outcome=run.outcome,
        reasoner=run.reasoner,
        llm_calls=run.llm_calls,
        cost_usd=float(run.cost_usd or Decimal(0)),
        created_by=emails.get(run.created_by) if run.created_by else None,
        created_at=run.created_at,
        completed_at=run.completed_at,
        pending_approval=pending > 0,
    )


SAMPLE_KINDS = {
    "refund": ["eligible_small", "eligible_large", "outside_window", "other_customers_order", "prompt_injection"],
    "invoice": ["clean", "price_mismatch", "quantity_mismatch", "duplicate", "unknown_supplier", "prompt_injection"],
    "purchase": ["catalog_small", "catalog_large", "over_budget", "off_catalog"],
}


@router.get("/runs/samples")
def samples(_: Viewer) -> dict[str, list[dict[str, Any]]]:
    """Example requests from the demo data set (one per scenario), to try the agents quickly."""
    from app.synthetic.business import build

    cases = build().cases
    out: dict[str, list[dict[str, Any]]] = {}
    for workflow, kinds in SAMPLE_KINDS.items():
        out[workflow] = [
            {
                "label": kind.replace("_", " "),
                "input": next(c["input"] for c in cases if c["kind"] == kind and c["workflow"] == workflow),
            }
            for kind in kinds
        ]
    return out


@router.post("/runs/refund", response_model=RunOut, status_code=202)
def start_refund(body: RefundIn, db: DB, user: Operator) -> RunOut:
    title = f"{body.from_email}: {body.subject or body.body[:60]}"
    return _run_out(db, runner.create_run(db, "refund", body.model_dump(), title, user))


@router.post("/runs/invoice", response_model=RunOut, status_code=202)
def start_invoice(body: InvoiceIn, db: DB, user: Operator) -> RunOut:
    title = f"Invoice from {body.vendor_name or 'unknown supplier'}"
    return _run_out(db, runner.create_run(db, "invoice", body.model_dump(), title, user))


@router.post("/runs/purchase", response_model=RunOut, status_code=202)
def start_purchase(body: PurchaseIn, db: DB, user: Operator) -> RunOut:
    title = f"{body.department}: {body.text.splitlines()[0][:80]}"
    data = {**body.model_dump(), "department": body.department.strip().lower()}
    return _run_out(db, runner.create_run(db, "purchase", data, title, user))


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    db: DB,
    _: Viewer,
    workflow: Literal["refund", "invoice", "purchase"] | None = None,
    status: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[RunOut]:
    stmt = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if workflow:
        stmt = stmt.where(Run.workflow == workflow)
    if status:
        stmt = stmt.where(Run.status == status)
    runs = list(db.scalars(stmt))
    ids = {r.created_by for r in runs if r.created_by}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}
    return [_run_out(db, r, emails) for r in runs]


@router.post("/runs/{run_id}/retry", response_model=RunOut, status_code=202)
def retry_run(run_id: uuid.UUID, db: DB, user: Operator) -> RunOut:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFound("Run not found")
    return _run_out(db, runner.retry(db, run, user))


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: DB, _: Viewer) -> dict[str, Any]:
    run = db.get(Run, run_id)
    if run is None:
        raise NotFound("Run not found")
    steps = db.scalars(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq))
    calls = db.scalars(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.id))
    approvals = db.scalars(select(Approval).where(Approval.run_id == run_id).order_by(Approval.requested_at))
    usage = db.scalars(select(LLMUsage).where(LLMUsage.run_id == run_id).order_by(LLMUsage.id))
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.id)))
    user_ids = {uuid.UUID(e.actor.removeprefix("user:")) for e in events if e.actor.startswith("user:")}
    actors = {
        f"user:{uid}": email
        for uid, email in db.execute(select(User.id, User.email).where(User.id.in_(user_ids))).all()
    }
    deciders = {
        u.id: u.email
        for u in db.scalars(
            select(User).where(User.id.in_(select(Approval.decided_by).where(Approval.run_id == run_id)))
        )
    }
    return {
        "run": _run_out(db, run),
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "tokens": {"input": run.input_tokens, "output": run.output_tokens},
        "steps": [
            {
                "seq": s.seq,
                "node": s.node,
                "status": s.status,
                "summary": s.summary,
                "detail": s.detail,
                "at": s.finished_at,
            }
            for s in steps
        ],
        "tool_calls": [
            {
                "id": c.id,
                "node": c.node,
                "tool": c.tool,
                "risk": c.risk,
                "args": c.args,
                "result": c.result,
                "status": c.status,
                "attempts": c.attempts,
                "error": c.error,
                "duration_ms": c.duration_ms,
                "at": c.created_at,
            }
            for c in calls
        ],
        "approvals": [approval_out(a, deciders.get(a.decided_by) if a.decided_by else None) for a in approvals],
        "llm_usage": [
            {
                "purpose": u.purpose,
                "model": u.model,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "cost_usd": float(u.cost_usd),
            }
            for u in usage
        ],
        "audit": [
            {"id": e.id, "ts": e.ts, "actor": actors.get(e.actor, e.actor), "action": e.action, "hash": e.hash[:12]}
            for e in events
        ],
        "allowed_tools": sorted(ALLOWED_TOOLS[run.workflow]),
    }


def approval_out(a: Approval, decided_by: str | None = None) -> dict[str, Any]:
    return {
        "id": a.id,
        "run_id": a.run_id,
        "action": a.action,
        "tool": a.tool,
        "risk": TOOLS[a.tool].risk,
        "args": a.args,
        "amount_cents": a.amount_cents,
        "reason": a.reason,
        "context": a.context,
        "required_role": a.required_role,
        "status": a.status,
        "requested_at": a.requested_at,
        "decided_by": decided_by,
        "decided_at": a.decided_at,
        "comment": a.comment,
    }
