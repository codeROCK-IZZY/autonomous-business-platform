"""Governance: policies, the tool catalogue, the audit trail and the operations dashboard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.agents import policy
from app.agents.runtime import ALLOWED_TOOLS, TOOLS
from app.api.deps import DB, Admin, Viewer
from app.core.config import get_settings
from app.core.errors import NotFound, UnprocessableData
from app.db.models import Approval, AuditEvent, Policy, Run, ToolCall, User
from app.modules import audit

router = APIRouter(tags=["governance"])


class PolicyIn(BaseModel):
    value: Any


@router.get("/policies")
def list_policies(db: DB, _: Viewer) -> list[dict[str, Any]]:
    stored = {p.key: p for p in db.scalars(select(Policy))}
    return [
        {
            "key": k,
            "value": stored[k].value if k in stored else default,
            "default": default,
            "description": desc,
            "updated_at": stored[k].updated_at if k in stored else None,
        }
        for k, (default, desc) in policy.DEFAULTS.items()
    ]


@router.put("/policies/{key}")
def set_policy(key: str, body: PolicyIn, db: DB, admin: Admin) -> dict[str, Any]:
    if key not in policy.DEFAULTS:
        raise NotFound("Unknown policy")
    default = policy.DEFAULTS[key][0]
    value = body.value
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise UnprocessableData("Expected true or false")
    elif isinstance(default, int | float):
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise UnprocessableData("Expected a non-negative number")
        value = int(value) if isinstance(default, int) else float(value)
    old = policy.get(db, key)
    row = db.get(Policy, key)
    if row is None:
        db.add(Policy(key=key, value=value, description=policy.DEFAULTS[key][1], updated_by=admin.id))
    else:
        row.value, row.updated_by = value, admin.id
    audit.record(db, admin.id, "policy.updated", "policy", key, old=old, new=value)
    db.commit()
    return {"key": key, "value": value}


@router.get("/tools")
def list_tools(_: Viewer) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "risk": t.risk,
            "description": t.description,
            "guarded": t.guard is not None,
            "workflows": sorted(w for w, allowed in ALLOWED_TOOLS.items() if t.name in allowed),
        }
        for t in TOOLS.values()
    ]


@router.get("/audit")
def list_audit(
    db: DB,
    _: Viewer,
    action: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action, autoescape=True))
    events = list(db.scalars(stmt))
    ids = {e.actor.removeprefix("user:") for e in events if e.actor.startswith("user:")}
    emails = (
        {str(k): v for k, v in db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()} if ids else {}
    )
    return [
        {
            "id": e.id,
            "ts": e.ts,
            "actor": emails.get(e.actor.removeprefix("user:"), e.actor),
            "action": e.action,
            "entity": e.entity,
            "entity_id": e.entity_id,
            "run_id": e.run_id,
            "data": e.data,
            "hash": e.hash,
            "prev_hash": e.prev_hash,
        }
        for e in events
    ]


@router.get("/audit/verify")
def verify_audit(db: DB, _: Viewer) -> dict[str, Any]:
    return audit.verify_chain(db)


@router.get("/dashboard")
def dashboard(db: DB, _: Viewer, days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    by_status = dict(
        db.execute(select(Run.status, func.count()).where(Run.created_at >= since).group_by(Run.status)).all()
    )
    by_workflow = {}
    for wf, total, auto, cost in db.execute(
        select(
            Run.workflow,
            func.count(),
            func.count().filter(~Run.id.in_(select(Approval.run_id)), Run.status == "completed"),
            func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .where(Run.created_at >= since)
        .group_by(Run.workflow)
    ):
        by_workflow[wf] = {"runs": total, "completed_without_approval": auto, "cost_usd": float(cost)}
    outcomes = [
        {"workflow": wf, "outcome": oc, "runs": n}
        for wf, oc, n in db.execute(
            select(Run.workflow, Run.outcome, func.count())
            .where(Run.created_at >= since, Run.outcome.is_not(None))
            .group_by(Run.workflow, Run.outcome)
            .order_by(func.count().desc())
        )
    ]
    completed = by_status.get("completed", 0)
    automated = sum(w["completed_without_approval"] for w in by_workflow.values())
    tool_stats = dict(
        db.execute(
            select(ToolCall.status, func.count()).where(ToolCall.created_at >= since).group_by(ToolCall.status)
        ).all()
    )
    retried = db.scalar(select(func.count()).where(ToolCall.created_at >= since, ToolCall.attempts > 1)) or 0
    s = get_settings()
    return {
        "days": days,
        "runs_by_status": by_status,
        "by_workflow": by_workflow,
        "outcomes": outcomes,
        "pending_approvals": db.scalar(select(func.count()).where(Approval.status == "pending")) or 0,
        "automation_rate": automated / completed if completed else None,
        "tool_calls": tool_stats,
        "tool_calls_retried": retried,
        "total_cost_usd": round(sum(w["cost_usd"] for w in by_workflow.values()), 4),
        "reasoner": s.claude_model if s.llm_mode == "claude" else "offline-rules",
    }
