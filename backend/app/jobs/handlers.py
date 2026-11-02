"""Background job handlers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.agents import runner


def handle_run_agent(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return runner.execute(uuid.UUID(payload["run_id"]), retry=bool(payload.get("retry")))


def handle_resume_agent(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return runner.execute(
        uuid.UUID(payload["run_id"]), resume={"approval_id": payload["approval_id"], "approved": payload["approved"]}
    )


HANDLERS = {"run_agent": handle_run_agent, "resume_agent": handle_resume_agent}
