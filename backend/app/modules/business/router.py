"""Read-only views of the business systems the agents act on (to see their effects)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.db.models import (
    Budget,
    Customer,
    Order,
    OutboxEmail,
    Payment,
    PurchaseOrder,
    Refund,
    Vendor,
    VendorInvoice,
)

router = APIRouter(prefix="/business", tags=["business systems"])
Limit = Query(default=100, ge=1, le=500)


@router.get("/refunds")
def refunds(db: DB, _: Viewer, limit: int = Limit) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Refund, Order, Customer)
        .join(Order, Order.id == Refund.order_id)
        .join(Customer, Customer.id == Order.customer_id)
        .order_by(Refund.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "order": o.number,
            "customer": c.email,
            "amount_cents": r.amount_cents,
            "reason": r.reason,
            "run_id": r.run_id,
            "created_at": r.created_at,
        }
        for r, o, c in rows
    ]


@router.get("/payments")
def payments(db: DB, _: Viewer, limit: int = Limit) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Payment, VendorInvoice, Vendor)
        .join(VendorInvoice, VendorInvoice.id == Payment.invoice_id)
        .join(Vendor, Vendor.id == VendorInvoice.vendor_id)
        .order_by(Payment.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": p.id,
            "vendor": v.name,
            "invoice": i.invoice_number,
            "amount_cents": p.amount_cents,
            "scheduled_for": p.scheduled_for,
            "run_id": p.run_id,
            "created_at": p.created_at,
        }
        for p, i, v in rows
    ]


@router.get("/invoices")
def invoices(db: DB, _: Viewer, limit: int = Limit) -> list[dict[str, Any]]:
    rows = db.execute(
        select(VendorInvoice, Vendor)
        .join(Vendor, Vendor.id == VendorInvoice.vendor_id)
        .order_by(VendorInvoice.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": i.id,
            "vendor": v.name,
            "invoice": i.invoice_number,
            "total_cents": i.total_cents,
            "status": i.status,
            "run_id": i.run_id,
            "created_at": i.created_at,
        }
        for i, v in rows
    ]


@router.get("/purchase-orders")
def purchase_orders(db: DB, _: Viewer, limit: int = Limit) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PurchaseOrder, Vendor)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "number": po.number,
            "vendor": v.name,
            "department": po.department,
            "status": po.status,
            "total_cents": po.total_cents,
            "run_id": po.run_id,
            "created_at": po.created_at,
        }
        for po, v in rows
    ]


@router.get("/budgets")
def budgets(db: DB, _: Viewer) -> list[dict[str, Any]]:
    return [
        {
            "department": b.department,
            "limit_cents": b.limit_cents,
            "committed_cents": b.committed_cents,
            "remaining_cents": b.limit_cents - b.committed_cents,
        }
        for b in db.scalars(select(Budget).order_by(Budget.department))
    ]


@router.get("/emails")
def emails(db: DB, _: Viewer, limit: int = Limit) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "to": e.to_address,
            "subject": e.subject,
            "body": e.body,
            "run_id": e.run_id,
            "created_at": e.created_at,
        }
        for e in db.scalars(select(OutboxEmail).order_by(OutboxEmail.created_at.desc()).limit(limit))
    ]
