from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, Admin, Viewer
from app.core.config import get_settings
from app.db.models import Approval, AuditEvent, Job, Run, ToolCall

router = APIRouter(tags=["admin"])


@router.get("/system/info")
def system_info(_: Viewer) -> dict[str, Any]:
    """What the UI needs to label AI output honestly (which provider/model is active)."""
    s = get_settings()
    return {
        "llm_mode": s.llm_mode,
        "llm_model": s.claude_model if s.llm_mode == "claude" else None,
        "data_leaves_server": s.llm_mode == "claude",
        "chaos_failure_rate": s.chaos_failure_rate,
    }


@router.get("/admin/stats")
def stats(db: DB, _: Admin) -> dict[str, Any]:
    return {
        "runs_by_status": dict(db.execute(select(Run.status, func.count()).group_by(Run.status)).all()),
        "approvals_by_status": dict(db.execute(select(Approval.status, func.count()).group_by(Approval.status)).all()),
        "tool_calls_by_status": dict(db.execute(select(ToolCall.status, func.count()).group_by(ToolCall.status)).all()),
        "audit_events": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
    }


@router.get("/admin/jobs")
def jobs(db: DB, _: Admin, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500)))
    return [
        {
            "id": j.id,
            "type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "payload": j.payload,
            "last_error": (j.last_error or "")[:500] or None,
            "created_at": j.created_at.isoformat(),
        }
        for j in rows
    ]
