"""Business policies: the thresholds that decide what agents may do on their own.

Values live in the ``policies`` table (editable by admins, every change audited); these defaults
apply until a value is set. Approval requirements are always computed by code from these values —
never by the language model — so text in an e-mail or invoice cannot talk an agent out of them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Policy

DEFAULTS: dict[str, tuple[Any, str]] = {
    "refund.auto_approve_max_cents": (10_000, "Refunds up to this amount are issued without human approval."),
    "refund.window_days": (30, "Days after delivery during which a refund can be requested."),
    "invoice.auto_pay_max_cents": (500_000, "Matched supplier invoices up to this total are paid without approval."),
    "invoice.price_tolerance_pct": (2.0, "Allowed difference between invoiced and ordered unit price, in percent."),
    "purchase.auto_approve_max_cents": (100_000, "Purchase requests up to this total are ordered without approval."),
    "run.budget_usd": (0.50, "Maximum language-model spend per run; above it the agent continues rule-based."),
    "email.approve_generated": (True, "Customer e-mails written by the language model need approval before sending."),
}


def get(db: Session, key: str) -> Any:
    value = db.scalar(select(Policy.value).where(Policy.key == key))
    return DEFAULTS[key][0] if value is None else value


def all_policies(db: Session) -> dict[str, Any]:
    stored = {p.key: p for p in db.scalars(select(Policy))}
    return {k: stored[k].value if k in stored else default for k, (default, _) in DEFAULTS.items()}
