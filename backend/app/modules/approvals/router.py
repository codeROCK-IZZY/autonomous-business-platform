"""Human approval inbox: review what an agent wants to do and why, then approve or reject."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents import runner
from app.api.deps import DB, Approver, Viewer
from app.core.errors import NotFound
from app.db.models import Approval, Run, User
from app.modules.runs.router import approval_out

router = APIRouter(tags=["approvals"])


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = Field(default="", max_length=2000)


@router.get("/approvals")
def list_approvals(
    db: DB,
    _: Viewer,
    status: Literal["pending", "approved", "rejected", "all"] = "pending",
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(Approval, Run).join(Run, Run.id == Approval.run_id).order_by(Approval.requested_at.desc())
    if status != "all":
        stmt = stmt.where(Approval.status == status)
    rows = db.execute(stmt.limit(limit)).all()
    users = {r.created_by for _, r in rows if r.created_by} | {a.decided_by for a, _ in rows if a.decided_by}
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(users))).all()) if users else {}
    return [
        {
            **approval_out(a, emails.get(a.decided_by) if a.decided_by else None),
            "workflow": r.workflow,
            "run_title": r.title,
            "requested_by": emails.get(r.created_by) if r.created_by else None,
        }
        for a, r in rows
    ]


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: uuid.UUID, body: DecisionIn, db: DB, user: Approver) -> dict[str, Any]:
    appr = db.get(Approval, approval_id, with_for_update=True)
    if appr is None:
        raise NotFound("Approval request not found")
    appr = runner.decide(db, appr, user, body.decision == "approve", body.comment)
    return approval_out(appr, user.email)
