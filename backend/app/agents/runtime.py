"""Agent runtime: tool registry, guarded tool execution, approvals, step recording, cost metering.

**Tools** are registered with a risk level — ``read``, ``write``, ``financial``, ``communication``
— and each workflow may only use the tools on its allow-list (least privilege).

**Guards** are the second line of defence. Workflow code decides when to ask a human; the
executor independently re-checks every call against the policies and refuses (``blocked``,
audited) a financial or communication action that needs approval but has no matching approved
request in this run — or whose amount is larger than what was approved. A bug or a manipulated
model output therefore cannot move money without a person saying yes.

**Retries**: transient failures are retried with exponential backoff (``ABP_TOOL_MAX_ATTEMPTS``);
writes are idempotent (keys passed to the ERP), so a retry never acts twice.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from langgraph.types import interrupt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import policy
from app.agents.reasoning import Reasoner, get_reasoner
from app.core.config import get_settings
from app.db.models import Approval, LLMUsage, Run, RunStep, ToolCall
from app.db.session import new_session
from app.erp import service as erp
from app.modules import audit


class PolicyViolation(Exception):
    """A guarded action was attempted without the approval it requires."""


@dataclass(frozen=True)
class Tool:
    name: str
    risk: str  # read | write | financial | communication
    description: str
    fn: Callable[..., Any]
    # Returns (approval action, approved-amount argument) when the call needs approval, else None.
    guard: Callable[[dict[str, Any], dict[str, Any]], tuple[str, str | None] | None] | None = None


def _refund_guard(args: dict[str, Any], p: dict[str, Any]) -> tuple[str, str | None] | None:
    return ("issue_refund", "amount_cents") if args["amount_cents"] > p["refund.auto_approve_max_cents"] else None


def _payment_guard(args: dict[str, Any], p: dict[str, Any]) -> tuple[str, str | None] | None:
    if args.get("exception") or args["amount_cents"] > p["invoice.auto_pay_max_cents"]:
        return "schedule_payment", "amount_cents"
    return None


def _po_guard(args: dict[str, Any], p: dict[str, Any]) -> tuple[str, str | None] | None:
    if args.get("needs_review") or args["request_total_cents"] > p["purchase.auto_approve_max_cents"]:
        return "create_purchase_order", "request_total_cents"
    return None


def _email_guard(args: dict[str, Any], p: dict[str, Any]) -> tuple[str, str | None] | None:
    return ("send_email", None) if args.get("generated") and p["email.approve_generated"] else None


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "erp.get_order",
            "read",
            "Look up an order with its lines, customer and refunds",
            lambda db, a, ctx: erp.get_order(db, a["order_number"]),
        ),
        Tool(
            "erp.issue_refund",
            "financial",
            "Refund an amount to the customer's original payment method",
            lambda db, a, ctx: erp.issue_refund(
                db, a["order_number"], a["amount_cents"], a["reason"], a["idempotency_key"], ctx.run_id
            ),
            _refund_guard,
        ),
        Tool("erp.find_vendor", "read", "Find a supplier by name", lambda db, a, ctx: erp.find_vendor(db, a["name"])),
        Tool(
            "erp.get_purchase_order",
            "read",
            "Look up a purchase order with lines and received quantities",
            lambda db, a, ctx: erp.get_purchase_order(db, a["po_number"]),
        ),
        Tool(
            "erp.find_invoice",
            "read",
            "Check whether an invoice number was already recorded for a supplier",
            lambda db, a, ctx: erp.find_invoice(db, a["vendor_id"], a["invoice_number"]),
        ),
        Tool(
            "erp.record_invoice",
            "write",
            "Record a supplier invoice as approved or rejected",
            lambda db, a, ctx: erp.record_invoice(
                db, a["vendor_id"], a["invoice_number"], a.get("po_id"), a["total_cents"], a["status"], ctx.run_id
            ),
        ),
        Tool(
            "erp.schedule_payment",
            "financial",
            "Schedule payment of an approved invoice on the supplier's terms",
            lambda db, a, ctx: erp.schedule_payment(
                db, a["invoice_id"], a["amount_cents"], a["idempotency_key"], ctx.run_id
            ),
            _payment_guard,
        ),
        Tool(
            "erp.search_catalog",
            "read",
            "Search the procurement catalogue",
            lambda db, a, ctx: {"matches": erp.search_catalog(db, a["query"])},
        ),
        Tool(
            "erp.get_budget",
            "read",
            "Get a department's budget and remaining amount",
            lambda db, a, ctx: erp.get_budget(db, a["department"]),
        ),
        Tool(
            "erp.create_purchase_order",
            "financial",
            "Create a purchase order and commit budget",
            lambda db, a, ctx: erp.create_purchase_order(
                db, a["vendor_id"], a["department"], a["lines"], a["idempotency_key"], ctx.run_id
            ),
            _po_guard,
        ),
        Tool(
            "comms.send_email",
            "communication",
            "Send an e-mail from the company outbox",
            lambda db, a, ctx: erp.send_email(db, a["to"], a["subject"], a["body"], a["idempotency_key"], ctx.run_id),
            _email_guard,
        ),
    ]
}

# Least privilege: the tools each workflow's agent may use.
ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "refund": frozenset({"erp.get_order", "erp.issue_refund", "comms.send_email"}),
    "invoice": frozenset(
        {"erp.find_vendor", "erp.get_purchase_order", "erp.find_invoice", "erp.record_invoice", "erp.schedule_payment"}
    ),
    "purchase": frozenset({"erp.search_catalog", "erp.get_budget", "erp.create_purchase_order", "comms.send_email"}),
}


def _chaos(run_id: uuid.UUID, tool: str, call_no: int, attempt: int, rate: float) -> bool:
    """Deterministic fault injection: the same run, tool call and attempt always behave the same
    (a retried run makes new calls, so it gets new draws — like a real outage that ends)."""
    if rate <= 0:
        return False
    h = hashlib.sha256(f"{run_id}|{tool}|{call_no}|{attempt}".encode()).digest()
    return int.from_bytes(h[:4], "big") / 2**32 < rate


class RunContext:
    """Everything a workflow node needs, bound to one run and one database session."""

    def __init__(self, db: Session, run: Run):
        self.db = db
        self.run = run
        self.run_id = run.id
        self.workflow = run.workflow
        self.policies = policy.all_policies(db)
        spent = float(run.cost_usd or 0)
        self.reasoner: Reasoner = get_reasoner(self._usage, budget_exhausted=spent >= self.policies["run.budget_usd"])
        if self.run.reasoner is None:
            self.run.reasoner = self.reasoner.name

    # ------------------------------------------------------------------ recording
    def step(
        self,
        node: str,
        summary: str,
        detail: dict[str, Any] | None = None,
        *,
        status: str = "completed",
        started: datetime | None = None,
    ) -> None:
        seq = (self.db.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == self.run_id)) or 0) + 1
        self.db.add(
            RunStep(
                run_id=self.run_id,
                seq=seq,
                node=node,
                status=status,
                summary=summary,
                detail=detail or {},
                started_at=started or datetime.now(UTC),
            )
        )
        self.db.commit()

    def _usage(self, purpose: str, model: str, input_tokens: int, output_tokens: int) -> None:
        s = get_settings()
        cost = (input_tokens * s.claude_input_usd_per_mtok + output_tokens * s.claude_output_usd_per_mtok) / 1e6
        self.db.add(
            LLMUsage(
                run_id=self.run_id,
                purpose=purpose,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=Decimal(str(round(cost, 5))),
            )
        )
        self.run.llm_calls += 1
        self.run.input_tokens += input_tokens
        self.run.output_tokens += output_tokens
        self.run.cost_usd = Decimal(str(round(float(self.run.cost_usd or 0) + cost, 5)))
        self.db.commit()

    # ------------------------------------------------------------------ tools
    def call(self, node: str, tool_name: str, args: dict[str, Any]) -> Any:
        """Run a tool with permission check, approval guard, retries, idempotency and recording."""
        s = get_settings()
        tool = TOOLS[tool_name]
        started = time.perf_counter()
        key = args.get("idempotency_key")
        if tool_name not in ALLOWED_TOOLS[self.workflow]:
            self._record(node, tool, args, None, "blocked", 0, "tool not allowed for this workflow", key, started)
            raise PolicyViolation(f"{tool_name} is not allowed in the {self.workflow} workflow")
        if tool.guard is not None:
            needed = tool.guard(args, self.policies)
            if needed is not None and not self._approved(needed[0], args.get(needed[1]) if needed[1] else None):
                self._record(node, tool, args, None, "blocked", 0, f"requires approval: {needed[0]}", key, started)
                audit.record(
                    self.db,
                    f"agent:{self.workflow}",
                    "policy.blocked",
                    "tool",
                    tool_name,
                    run_id=self.run_id,
                    approval_action=needed[0],
                )
                self.db.commit()
                raise PolicyViolation(f"{tool_name} requires an approved '{needed[0]}' request")
        attempt = 0
        call_no = (
            self.db.scalar(select(func.count()).where(ToolCall.run_id == self.run_id, ToolCall.tool == tool_name)) or 0
        )
        while True:
            attempt += 1
            try:
                if _chaos(self.run_id, tool_name, call_no, attempt, s.chaos_failure_rate):
                    raise erp.TransientToolError("simulated outage (fault injection)")
                result = tool.fn(self.db, args, self)
            except erp.TransientToolError as exc:
                self.db.rollback()
                if attempt >= s.tool_max_attempts:
                    self._record(node, tool, args, None, "failed", attempt, str(exc), key, started)
                    raise
                time.sleep(s.tool_retry_base_seconds * 2 ** (attempt - 1))
                continue
            except erp.ToolError as exc:
                self.db.rollback()
                self._record(node, tool, args, None, "failed", attempt, str(exc), key, started)
                raise
            self._record(node, tool, args, result, "succeeded", attempt, None, key, started)
            if tool.risk != "read":
                audit.record(
                    self.db,
                    f"agent:{self.workflow}",
                    f"tool.{tool_name}",
                    "tool",
                    key,
                    run_id=self.run_id,
                    args=args,
                    result=result,
                )
            self.db.commit()
            return result

    def _record(
        self,
        node: str,
        tool: Tool,
        args: dict[str, Any],
        result: Any,
        status: str,
        attempts: int,
        error: str | None,
        key: str | None,
        started: float,
    ) -> None:
        self.db.add(
            ToolCall(
                run_id=self.run_id,
                node=node,
                tool=tool.name,
                risk=tool.risk,
                args=args,
                result=result if isinstance(result, dict) or result is None else {"value": result},
                status=status,
                attempts=attempts,
                error=error,
                idempotency_key=key,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
        )
        self.db.commit()

    def _approved(self, action: str, amount: int | None) -> bool:
        appr = self.db.scalar(select(Approval).where(Approval.run_id == self.run_id, Approval.action == action))
        if appr is None or appr.status != "approved":
            return False
        return amount is None or appr.amount_cents is None or amount <= appr.amount_cents

    # ------------------------------------------------------------------ human in the loop
    def require_approval(
        self,
        node: str,
        action: str,
        tool: str,
        args: dict[str, Any],
        *,
        amount_cents: int | None,
        reason: str,
        context: dict[str, Any],
    ) -> Approval:
        """Ask a person. The first time, this creates the request and suspends the graph
        (LangGraph ``interrupt``; the run's state is checkpointed in PostgreSQL). When the run is
        resumed after a decision, the node runs again, finds the decided request and returns it."""
        appr = self.db.scalar(select(Approval).where(Approval.run_id == self.run_id, Approval.action == action))
        if appr is None:
            appr = Approval(
                run_id=self.run_id,
                action=action,
                tool=tool,
                args=args,
                amount_cents=amount_cents,
                reason=reason,
                context=context,
            )
            self.db.add(appr)
            self.db.flush()
            audit.record(
                self.db,
                f"agent:{self.workflow}",
                "approval.requested",
                "approval",
                str(appr.id),
                run_id=self.run_id,
                approval_action=action,
                amount_cents=amount_cents,
                reason=reason,
            )
            self.db.commit()
            self.step(
                node,
                f"Waiting for approval: {reason}",
                {"approval_id": str(appr.id), "action": action},
                status="waiting",
            )
        if appr.status == "pending":
            interrupt({"approval_id": str(appr.id), "action": action})
            self.db.refresh(appr)
        return appr


@contextmanager
def run_context(config: dict[str, Any]) -> Iterator[RunContext]:
    run_id = uuid.UUID(config["configurable"]["run_id"])
    db = new_session()
    try:
        run = db.get(Run, run_id)
        if run is None:
            raise RuntimeError(f"run {run_id} not found")
        yield RunContext(db, run)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"
