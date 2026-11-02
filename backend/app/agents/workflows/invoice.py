"""Supplier invoices (accounts payable): extract, three-way match, pay or escalate.

    extract → lookup ─┬─ duplicate / unknown supplier → reject (end)
                      └→ match → decide ─┬─ clean and within limit ──────→ pay
                                         └─ exceptions or above limit → approve ─┬→ pay
                                                                                 └→ reject

Three-way match: every invoice line must be on the purchase order, the billed quantity must not
exceed what the goods receipt says arrived, and the unit price must be within the tolerance of
the ordered price. Line totals must add up to the invoice total.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.runtime import RunContext, money
from app.agents.workflows.common import node


class InvoiceState(TypedDict, total=False):
    vendor_name: str
    text: str
    extracted: dict[str, Any]
    vendor: dict[str, Any] | None
    po: dict[str, Any] | None
    issues: list[str]
    decision: str
    invoice: dict[str, Any]
    payment: dict[str, Any]
    outcome: str


@node
def extract(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    x = ctx.reasoner.extract_invoice(state["text"])
    if not x.get("vendor"):
        x["vendor"] = state.get("vendor_name")
    issues: list[str] = []
    if not x.get("invoice_number"):
        issues.append("no invoice number found")
    if not x.get("lines"):
        issues.append("no invoice lines found")
    line_sum = sum(ln["qty"] * ln["unit_price_cents"] for ln in x.get("lines") or [])
    if x.get("total_cents") is None:
        issues.append("no invoice total found")
    elif x.get("lines") and abs(line_sum - x["total_cents"]) > 1:
        issues.append(f"line totals {money(line_sum)} do not add up to the invoice total {money(x['total_cents'])}")
    ctx.step(
        "extract",
        f"Invoice {x.get('invoice_number') or '?'} from {x.get('vendor') or '?'}: "
        f"{len(x.get('lines') or [])} lines, total {money(x['total_cents']) if x.get('total_cents') else '?'}",
        x,
    )
    return {"extracted": x, "issues": issues}


@node
def lookup(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    x = state["extracted"]
    vendor = ctx.call("lookup", "erp.find_vendor", {"name": x["vendor"] or ""})
    if vendor is None:
        ctx.step("lookup", f"Unknown supplier '{x['vendor']}' — rejected (possible fraud)")
        return {"vendor": None, "decision": "reject", "outcome": "rejected_unknown_supplier"}
    if x.get("invoice_number"):
        dup = ctx.call("lookup", "erp.find_invoice", {"vendor_id": vendor["id"], "invoice_number": x["invoice_number"]})
        if dup is not None:
            ctx.step("lookup", f"Invoice {x['invoice_number']} was already processed ({dup['status']}) — duplicate")
            return {"vendor": vendor, "decision": "reject", "outcome": "rejected_duplicate"}
    po = ctx.call("lookup", "erp.get_purchase_order", {"po_number": x["po_number"]}) if x.get("po_number") else None
    issues = list(state.get("issues") or [])
    if po is None:
        issues.append("no matching purchase order" if x.get("po_number") else "no purchase order referenced")
    elif po["vendor_id"] != vendor["id"]:
        issues.append(f"purchase order {po['number']} belongs to a different supplier")
    ctx.step("lookup", f"Supplier {vendor['name']}; PO {po['number'] if po else 'none'}", {"vendor": vendor, "po": po})
    return {"vendor": vendor, "po": po, "issues": issues, "decision": "match"}


@node
def match(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    x, po = state["extracted"], state.get("po")
    issues = list(state.get("issues") or [])
    tolerance = float(ctx.policies["invoice.price_tolerance_pct"])
    checked = []
    if po is not None:
        po_lines = {ln["sku"]: ln for ln in po["lines"]}
        for ln in x.get("lines") or []:
            pl = po_lines.get(ln["sku"])
            if pl is None:
                issues.append(f"{ln['sku']} is not on {po['number']}")
                continue
            if ln["qty"] > pl["received_qty"]:
                issues.append(f"{ln['sku']}: billed {ln['qty']}, received {pl['received_qty']}")
            diff = abs(ln["unit_price_cents"] - pl["unit_price_cents"]) / pl["unit_price_cents"] * 100
            if diff > tolerance:
                issues.append(
                    f"{ln['sku']}: price {money(ln['unit_price_cents'])} vs ordered "
                    f"{money(pl['unit_price_cents'])} ({diff:.1f}% > {tolerance:g}%)"
                )
            checked.append(
                {
                    "sku": ln["sku"],
                    "billed_qty": ln["qty"],
                    "received_qty": pl["received_qty"],
                    "price_diff_pct": round(diff, 2),
                }
            )
    total = x.get("total_cents") or 0
    over_limit = total > ctx.policies["invoice.auto_pay_max_cents"]
    decision = "approve" if issues or over_limit else "pay"
    summary = "Three-way match passed" if not issues else f"{len(issues)} exception(s): " + "; ".join(issues[:3])
    if over_limit and not issues:
        summary += f" — total {money(total)} is above the automatic limit"
    ctx.step("match", summary, {"lines": checked, "issues": issues, "over_limit": over_limit})
    return {"issues": issues, "decision": decision}


@node
def approve(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    x = state["extracted"]
    issues = state.get("issues") or []
    total = x.get("total_cents") or 0
    reason = (
        ("Exceptions: " + "; ".join(issues))
        if issues
        else (f"Total {money(total)} is above the {money(ctx.policies['invoice.auto_pay_max_cents'])} automatic limit")
    )
    appr = ctx.require_approval(
        "approve",
        "schedule_payment",
        "erp.schedule_payment",
        {"vendor": state["vendor"]["name"], "invoice_number": x.get("invoice_number"), "amount_cents": total},
        amount_cents=total,
        reason=reason,
        context={"invoice_text": state["text"][:4000], "purchase_order": state.get("po"), "issues": issues},
    )
    ctx.step("approve", f"{appr.status.capitalize()}{f': {appr.comment}' if appr.comment else ''}")
    return {"decision": "pay" if appr.status == "approved" else "reject_reviewed"}


@node
def pay(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    x, vendor, po = state["extracted"], state["vendor"], state.get("po")
    total = x.get("total_cents") or 0
    invoice = ctx.call(
        "pay",
        "erp.record_invoice",
        {
            "vendor_id": vendor["id"],
            "invoice_number": x.get("invoice_number") or f"UNNUMBERED-{ctx.run_id}",
            "po_id": po["id"] if po else None,
            "total_cents": total,
            "status": "approved",
        },
    )
    payment = ctx.call(
        "pay",
        "erp.schedule_payment",
        {
            "invoice_id": invoice["id"],
            "amount_cents": total,
            "exception": bool(state.get("issues")),
            "idempotency_key": f"payment:{vendor['id']}:{x.get('invoice_number') or ctx.run_id}",
        },
    )
    ctx.step("pay", f"Payment of {money(total)} scheduled for {payment['scheduled_for']}", payment)
    reviewed = bool(state.get("issues")) or total > ctx.policies["invoice.auto_pay_max_cents"]
    return {"invoice": invoice, "payment": payment, "outcome": "paid_after_review" if reviewed else "paid"}


@node
def reject(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    if state.get("outcome") in ("rejected_unknown_supplier", "rejected_duplicate"):
        return {}
    x, vendor = state["extracted"], state["vendor"]
    ctx.call(
        "reject",
        "erp.record_invoice",
        {
            "vendor_id": vendor["id"],
            "invoice_number": x.get("invoice_number") or f"UNNUMBERED-{ctx.run_id}",
            "po_id": (state.get("po") or {}).get("id"),
            "total_cents": x.get("total_cents") or 0,
            "status": "rejected",
        },
    )
    ctx.step("reject", "Invoice recorded as rejected")
    return {"outcome": "rejected_by_reviewer"}


def build() -> StateGraph:
    g = StateGraph(InvoiceState)
    for fn in (extract, lookup, match, approve, pay, reject):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "extract")
    g.add_edge("extract", "lookup")
    g.add_conditional_edges("lookup", lambda s: "match" if s["decision"] == "match" else "reject", ["match", "reject"])
    g.add_conditional_edges("match", lambda s: s["decision"], ["approve", "pay"])
    g.add_conditional_edges("approve", lambda s: "pay" if s["decision"] == "pay" else "reject", ["pay", "reject"])
    g.add_edge("pay", END)
    g.add_edge("reject", END)
    return g
