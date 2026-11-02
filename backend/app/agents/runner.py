"""Starting, executing, resuming and approving agent runs.

* A run is created by the API and executed by the worker (job ``run_agent``).
* Graph state is checkpointed in PostgreSQL by LangGraph (``PostgresSaver``), keyed by the run id,
  so a run paused for approval survives restarts and is resumed exactly where it stopped.
* Approval decisions are recorded, audited, and trigger a ``resume_agent`` job.
* **Separation of duties:** the person who started a run can never approve its actions.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy.orm import Session

from app.agents.workflows import invoice, purchase, refund
from app.api.deps import ROLE_RANK
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden
from app.db.models import Approval, Run, User
from app.db.session import new_session
from app.jobs.queue import enqueue
from app.modules import audit

logger = logging.getLogger(__name__)

BUILDERS = {"refund": refund.build, "invoice": invoice.build, "purchase": purchase.build}
OUTPUT_KEYS = (
    "outcome",
    "intent",
    "order_number",
    "amount_cents",
    "refund",
    "email",
    "extracted",
    "issues",
    "invoice",
    "payment",
    "matched",
    "unmatched",
    "total_cents",
    "pos",
    "notes",
)

_lock = threading.Lock()
_pool: ConnectionPool | None = None
_graphs: dict[str, Any] = {}


def _conninfo() -> str:
    return get_settings().database_url.replace("postgresql+psycopg://", "postgresql://")


def checkpointer() -> PostgresSaver:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ConnectionPool(
                _conninfo(),
                min_size=1,
                max_size=4,
                open=True,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
            )
        return PostgresSaver(_pool)  # type: ignore[arg-type]


def setup_checkpointer() -> None:
    """Create LangGraph's checkpoint tables (idempotent; run with the migrations)."""
    checkpointer().setup()


def close() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
        _graphs.clear()


def graph(workflow: str) -> Any:
    """The compiled graph for a workflow (compiled once per process)."""
    compiled = _graphs.get(workflow)
    if compiled is None:
        compiled = BUILDERS[workflow]().compile(checkpointer=checkpointer())
        _graphs[workflow] = compiled
    return compiled


def create_run(db: Session, workflow: str, data: dict[str, Any], title: str, user: User | None) -> Run:
    run = Run(workflow=workflow, title=title[:300], input=data, created_by=user.id if user else None)
    db.add(run)
    db.flush()
    audit.record(
        db,
        user.id if user else "system",
        "run.created",
        "run",
        str(run.id),
        run_id=run.id,
        workflow=workflow,
        title=title,
    )
    enqueue(db, "run_agent", {"run_id": str(run.id)})
    db.commit()
    return run


def execute(run_id: uuid.UUID, resume: dict[str, Any] | None = None, *, retry: bool = False) -> dict[str, Any]:
    """Run the graph until it finishes or pauses for approval. ``resume`` continues after an approval
    decision; ``retry`` continues a failed run from its last checkpoint (completed steps are not
    repeated, and the failed step's writes are idempotent)."""
    db = new_session()
    try:
        run = db.get(Run, run_id)
        if run is None or run.status == "completed" or (run.status == "failed" and not retry):
            return {"status": "skipped"}
        run.status, run.error = "running", None
        db.commit()
        config = {"configurable": {"thread_id": str(run_id), "run_id": str(run_id)}, "recursion_limit": 50}
        try:
            if resume is not None:
                payload: Any = Command(resume=resume)
            elif retry:
                payload = None  # continue from the last checkpoint
            else:
                payload = run.input
            result = graph(run.workflow).invoke(payload, config)
        except Exception as exc:  # tool failure, policy violation, bug: the run fails visibly, never silently
            logger.exception("run failed", extra={"run_id": str(run_id)})
            db.rollback()
            run = db.get(Run, run_id, populate_existing=True)
            assert run is not None
            run.status, run.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
            audit.record(db, f"agent:{run.workflow}", "run.failed", "run", str(run.id), run_id=run.id, error=run.error)
            db.commit()
            return {"status": "failed"}
        run = db.get(Run, run_id, populate_existing=True)
        assert run is not None
        if result.get("__interrupt__"):
            run.status = "waiting_approval"
            audit.record(db, f"agent:{run.workflow}", "run.paused", "run", str(run.id), run_id=run.id)
        else:
            run.status, run.outcome = "completed", result.get("outcome")
            run.output = {k: result[k] for k in OUTPUT_KEYS if k in result}
            run.completed_at = datetime.now(UTC)
            audit.record(
                db, f"agent:{run.workflow}", "run.completed", "run", str(run.id), run_id=run.id, outcome=run.outcome
            )
        db.commit()
        return {"status": run.status, "outcome": run.outcome}
    finally:
        db.close()


def decide(db: Session, appr: Approval, user: User, approve: bool, comment: str) -> Approval:
    if appr.status != "pending":
        raise Conflict(f"This request was already {appr.status}")
    if ROLE_RANK[user.role] < ROLE_RANK[appr.required_role]:
        raise Forbidden(f"Requires the '{appr.required_role}' role")
    run = db.get(Run, appr.run_id)
    assert run is not None
    if run.created_by == user.id:
        raise Forbidden("You started this run, so someone else must approve it (separation of duties)")
    appr.status = "approved" if approve else "rejected"
    appr.decided_by, appr.decided_at, appr.comment = user.id, datetime.now(UTC), comment[:2000]
    audit.record(
        db,
        user.id,
        f"approval.{appr.status}",
        "approval",
        str(appr.id),
        run_id=run.id,
        approval_action=appr.action,
        amount_cents=appr.amount_cents,
        comment=comment,
    )
    enqueue(db, "resume_agent", {"run_id": str(run.id), "approval_id": str(appr.id), "approved": approve})
    db.commit()
    return appr


def retry(db: Session, run: Run, user: User) -> Run:
    """Re-run a failed run from its last checkpoint (e.g. after a supplier system outage)."""
    if run.status != "failed":
        raise Conflict("Only failed runs can be retried")
    run.status = "queued"
    audit.record(db, user.id, "run.retried", "run", str(run.id), run_id=run.id)
    enqueue(db, "run_agent", {"run_id": str(run.id), "retry": True})
    db.commit()
    return run
