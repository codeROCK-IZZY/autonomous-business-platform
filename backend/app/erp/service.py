"""The company's business systems (a mock ERP in the same database), exposed as plain functions.

These are what the agents' tools call. Every write is **idempotent**: it takes an
``idempotency_key`` and, if that key was already used, returns the original result instead of
acting twice — so a retried tool call, a resumed run or a duplicate request can never refund,
pay or order twice. Business invariants (refund ≤ refundable amount, budget limits) are enforced
here as well, independently of the agent's own checks.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Budget,
    CatalogItem,
    Customer,
    GoodsReceipt,
    Order,
    OrderLine,
    OutboxEmail,
    Payment,
    POLine,
    Product,
    PurchaseOrder,
    Refund,
    Vendor,
    VendorInvoice,
)


class ToolError(Exception):
    """A permanent failure (bad input, business rule). Retrying will not help."""


class TransientToolError(Exception):
    """A temporary failure (timeout, unavailable). The executor retries with backoff."""


# ---------------------------------------------------------------------- customers & orders
def refunded_cents(db: Session, order_id: int) -> int:
    total = db.scalar(select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(Refund.order_id == order_id))
    return int(total or 0)


def get_order(db: Session, number: str) -> dict[str, Any] | None:
    order = db.scalar(select(Order).where(Order.number == number.strip().upper()))
    if order is None:
        return None
    customer = db.get(Customer, order.customer_id)
    assert customer is not None
    lines = db.execute(
        select(OrderLine, Product).join(Product, Product.sku == OrderLine.sku).where(OrderLine.order_id == order.id)
    ).all()
    return {
        "number": order.number,
        "customer_email": customer.email,
        "customer_name": customer.name,
        "customer_tier": customer.tier,
        "status": order.status,
        "placed_at": order.placed_at.isoformat(),
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "total_cents": order.total_cents,
        "refunded_cents": refunded_cents(db, order.id),
        "lines": [
            {
                "sku": ln.sku,
                "name": p.name,
                "qty": ln.qty,
                "unit_price_cents": ln.unit_price_cents,
                "final_sale": p.final_sale,
            }
            for ln, p in lines
        ],
    }


def issue_refund(
    db: Session, order_number: str, amount_cents: int, reason: str, idempotency_key: str, run_id: uuid.UUID | None
) -> dict[str, Any]:
    existing = db.scalar(select(Refund).where(Refund.idempotency_key == idempotency_key))
    if existing is not None:
        return {"refund_id": existing.id, "amount_cents": existing.amount_cents, "replayed": True}
    order = db.scalar(select(Order).where(Order.number == order_number))
    if order is None:
        raise ToolError(f"order {order_number} not found")
    if amount_cents <= 0:
        raise ToolError("refund amount must be positive")
    remaining = order.total_cents - refunded_cents(db, order.id)
    if amount_cents > remaining:
        raise ToolError(f"refund {amount_cents} exceeds refundable amount {remaining}")
    refund = Refund(
        order_id=order.id,
        amount_cents=amount_cents,
        reason=reason[:500],
        idempotency_key=idempotency_key,
        run_id=run_id,
    )
    db.add(refund)
    db.flush()
    return {"refund_id": refund.id, "amount_cents": amount_cents, "replayed": False}


# ---------------------------------------------------------------------- vendors, POs, invoices
def find_vendor(db: Session, name: str) -> dict[str, Any] | None:
    v = db.scalar(select(Vendor).where(func.lower(Vendor.name) == name.strip().lower()))
    if v is None:
        return None
    return {"id": v.id, "name": v.name, "email": v.email, "preferred": v.preferred, "terms_days": v.payment_terms_days}


def get_purchase_order(db: Session, number: str) -> dict[str, Any] | None:
    po = db.scalar(select(PurchaseOrder).where(PurchaseOrder.number == number.strip().upper()))
    if po is None:
        return None
    lines = db.scalars(select(POLine).where(POLine.po_id == po.id)).all()
    received: dict[str, int] = {}
    for gr in db.scalars(select(GoodsReceipt).where(GoodsReceipt.po_id == po.id)):
        received[gr.sku] = received.get(gr.sku, 0) + gr.qty
    return {
        "id": po.id,
        "number": po.number,
        "vendor_id": po.vendor_id,
        "department": po.department,
        "status": po.status,
        "total_cents": po.total_cents,
        "lines": [
            {
                "sku": ln.sku,
                "description": ln.description,
                "qty": ln.qty,
                "unit_price_cents": ln.unit_price_cents,
                "received_qty": received.get(ln.sku, 0),
            }
            for ln in lines
        ],
    }


def find_invoice(db: Session, vendor_id: int, invoice_number: str) -> dict[str, Any] | None:
    inv = db.scalar(
        select(VendorInvoice).where(
            VendorInvoice.vendor_id == vendor_id, VendorInvoice.invoice_number == invoice_number.strip()
        )
    )
    return None if inv is None else {"id": inv.id, "status": inv.status, "total_cents": inv.total_cents}


def record_invoice(
    db: Session,
    vendor_id: int,
    invoice_number: str,
    po_id: int | None,
    total_cents: int,
    status: str,
    run_id: uuid.UUID | None,
) -> dict[str, Any]:
    existing = find_invoice(db, vendor_id, invoice_number)
    if existing is not None:
        return {**existing, "replayed": True}
    inv = VendorInvoice(
        vendor_id=vendor_id,
        invoice_number=invoice_number.strip(),
        po_id=po_id,
        total_cents=total_cents,
        status=status,
        run_id=run_id,
    )
    db.add(inv)
    db.flush()
    return {"id": inv.id, "status": inv.status, "total_cents": total_cents, "replayed": False}


def schedule_payment(
    db: Session, invoice_id: int, amount_cents: int, idempotency_key: str, run_id: uuid.UUID | None
) -> dict[str, Any]:
    existing = db.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
    if existing is not None:
        return {"payment_id": existing.id, "scheduled_for": existing.scheduled_for.isoformat(), "replayed": True}
    inv = db.get(VendorInvoice, invoice_id)
    if inv is None:
        raise ToolError("invoice not found")
    if inv.status == "rejected":
        raise ToolError("a rejected invoice cannot be paid")
    if amount_cents != inv.total_cents:
        raise ToolError("payment amount must equal the invoice total")
    vendor = db.get(Vendor, inv.vendor_id)
    terms = vendor.payment_terms_days if vendor else 30
    pay = Payment(
        invoice_id=invoice_id,
        amount_cents=amount_cents,
        scheduled_for=date.today() + timedelta(days=terms),
        idempotency_key=idempotency_key,
        run_id=run_id,
    )
    db.add(pay)
    inv.status = "paid"
    db.flush()
    return {"payment_id": pay.id, "scheduled_for": pay.scheduled_for.isoformat(), "replayed": False}


# ---------------------------------------------------------------------- procurement
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "with",
        "to",
        "in",
        "on",
        "per",
        "x",
        "pcs",
        "pack",
        "box",
        "boxes",
        "of",
        "new",
    ]
)


def _words(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall(text.lower()):
        if w in _STOP or w.isdigit():
            continue
        out.add(w[:-1] if len(w) > 3 and w.endswith("s") else w)  # crude singular
    return out


def search_catalog(db: Session, query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Rank catalog items by word overlap with the request (name + keywords)."""
    q = _words(query)
    if not q:
        return []
    scored: list[dict[str, Any]] = []
    for item, vendor in db.execute(select(CatalogItem, Vendor).join(Vendor, Vendor.id == CatalogItem.vendor_id)):
        words = _words(f"{item.name} {item.keywords} {item.category}")
        overlap = len(q & words)
        if overlap:
            score = overlap / len(q)
            scored.append(
                {
                    "sku": item.sku,
                    "name": item.name,
                    "unit_price_cents": item.unit_price_cents,
                    "vendor_id": vendor.id,
                    "vendor": vendor.name,
                    "preferred_vendor": vendor.preferred,
                    "score": round(score, 3),
                }
            )
    scored.sort(key=lambda x: (-x["score"], -x["preferred_vendor"], x["unit_price_cents"]))
    return scored[:limit]


def get_budget(db: Session, department: str) -> dict[str, Any] | None:
    b = db.get(Budget, department.strip().lower())
    if b is None:
        return None
    return {
        "department": b.department,
        "limit_cents": b.limit_cents,
        "committed_cents": b.committed_cents,
        "remaining_cents": b.limit_cents - b.committed_cents,
    }


def create_purchase_order(
    db: Session,
    vendor_id: int,
    department: str,
    lines: list[dict[str, Any]],
    idempotency_key: str,
    run_id: uuid.UUID | None,
) -> dict[str, Any]:
    existing = db.scalar(select(PurchaseOrder).where(PurchaseOrder.idempotency_key == idempotency_key))
    if existing is not None:
        return {"po_number": existing.number, "total_cents": existing.total_cents, "replayed": True}
    budget = db.get(Budget, department, with_for_update=True)
    if budget is None:
        raise ToolError(f"unknown department {department}")
    total = sum(int(ln["qty"]) * int(ln["unit_price_cents"]) for ln in lines)
    if budget.committed_cents + total > budget.limit_cents:
        raise ToolError("the purchase order would exceed the department budget")
    seq = (db.scalar(select(func.count()).select_from(PurchaseOrder)) or 0) + 1
    po = PurchaseOrder(
        number=f"PO-{datetime.now(UTC):%y}{seq:05d}",
        vendor_id=vendor_id,
        department=department,
        total_cents=total,
        idempotency_key=idempotency_key,
        run_id=run_id,
    )
    db.add(po)
    db.flush()
    for ln in lines:
        db.add(
            POLine(
                po_id=po.id,
                sku=ln["sku"],
                description=ln["name"][:200],
                qty=int(ln["qty"]),
                unit_price_cents=int(ln["unit_price_cents"]),
            )
        )
    budget.committed_cents += total
    db.flush()
    return {"po_number": po.number, "total_cents": total, "replayed": False}


# ---------------------------------------------------------------------- communication
def send_email(
    db: Session, to: str, subject: str, body: str, idempotency_key: str, run_id: uuid.UUID | None
) -> dict[str, Any]:
    """Queue an e-mail in the outbox (a real deployment relays the outbox through SES/SMTP)."""
    existing = db.scalar(select(OutboxEmail).where(OutboxEmail.idempotency_key == idempotency_key))
    if existing is not None:
        return {"email_id": existing.id, "replayed": True}
    if "@" not in to:
        raise ToolError("invalid recipient")
    mail = OutboxEmail(
        to_address=to, subject=subject[:300], body=body[:10_000], run_id=run_id, idempotency_key=idempotency_key
    )
    db.add(mail)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent duplicate
        raise TransientToolError("duplicate send race") from exc
    return {"email_id": mail.id, "replayed": False}
