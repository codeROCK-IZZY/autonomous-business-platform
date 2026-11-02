"""Customer refund requests (support inbox).

    understand → lookup_order → assess ─┬─ auto ───────────────→ refund ─→ draft → (email approval) → send
                                        ├─ needs approval → approve ─┬→ refund
                                        │                            └→ draft (declined after review)
                                        ├─ decline / status reply ────────────→ draft
                                        └─ unclear → hand to a person (end)

The refund amount and whether approval is needed are computed from ERP data and policies — the
e-mail text only contributes the order number, which is verified against the sender's account.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.runtime import RunContext, money
from app.agents.workflows.common import node


class RefundState(TypedDict, total=False):
    from_email: str
    subject: str
    body: str
    intent: str
    order_number: str | None
    reason: str
    order: dict[str, Any] | None
    notes: list[str]
    decision: str
    reply: str
    amount_cents: int
    age_days: int
    refund: dict[str, Any]
    email: dict[str, Any]
    send: bool
    outcome: str


@node
def understand(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    t = ctx.reasoner.triage_email(state.get("subject", ""), state["body"])
    ctx.step("understand", f"Intent '{t['intent']}', order {t['order_number'] or 'not given'}", t)
    return {"intent": t["intent"], "order_number": t["order_number"], "reason": t.get("reason", "")}


@node
def lookup_order(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    if state["intent"] == "other":
        ctx.step("lookup_order", "Not an order request; nothing to look up")
        return {"order": None}
    order = (
        ctx.call("lookup_order", "erp.get_order", {"order_number": state["order_number"]})
        if state["order_number"]
        else None
    )
    if order is not None and order["customer_email"].lower() != state["from_email"].strip().lower():
        # Never reveal or act on another customer's order.
        ctx.step("lookup_order", "Order belongs to a different customer — treated as not found", {"order": None})
        return {"order": None, "notes": ["order number did not belong to the sender"]}
    ctx.step("lookup_order", f"Order {order['number']} found ({order['status']})" if order else "Order not found")
    return {"order": order}


@node
def assess(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    order = state.get("order")
    p = ctx.policies
    if state["intent"] == "other":
        decision: dict[str, Any] = {"decision": "handoff"}
    elif order is None:
        decision = {"decision": "decline", "reply": "order_not_found"}
    elif state["intent"] == "order_status":
        decision = {"decision": "reply", "reply": "order_status"}
    elif order["status"] != "delivered" or not order["delivered_at"]:
        decision = {"decision": "decline", "reply": "declined_not_delivered"}
    else:
        delivered = datetime.fromisoformat(order["delivered_at"])
        age = (datetime.now(UTC) - delivered).days
        refundable_lines = [ln for ln in order["lines"] if not ln["final_sale"]]
        eligible = sum(ln["qty"] * ln["unit_price_cents"] for ln in refundable_lines)
        amount = min(eligible, order["total_cents"] - order["refunded_cents"])
        if age > p["refund.window_days"]:
            decision = {"decision": "decline", "reply": "declined_window", "age_days": age}
        elif not refundable_lines:
            decision = {"decision": "decline", "reply": "declined_final_sale"}
        elif amount <= 0:
            decision = {"decision": "decline", "reply": "already_refunded"}
        elif amount > p["refund.auto_approve_max_cents"]:
            decision = {"decision": "approve", "amount_cents": amount, "age_days": age}
        else:
            decision = {"decision": "refund", "amount_cents": amount, "age_days": age}
    labels = {
        "handoff": "Not a refund or status request — hand to a person",
        "decline": "Decline",
        "reply": "Answer with order status",
        "approve": "Refund needs approval",
        "refund": "Refund automatically",
    }
    summary = labels[decision["decision"]]
    if "amount_cents" in decision:
        summary += f" ({money(decision['amount_cents'])})"
    if decision.get("reply"):
        summary += f" — {decision['reply'].replace('_', ' ')}"
    ctx.step("assess", summary, {**decision, "policy": {k: v for k, v in p.items() if k.startswith("refund.")}})
    return decision


@node
def approve(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    order = state["order"]
    appr = ctx.require_approval(
        "approve",
        "issue_refund",
        "erp.issue_refund",
        {"order_number": order["number"], "amount_cents": state["amount_cents"]},
        amount_cents=state["amount_cents"],
        reason=f"Refund of {money(state['amount_cents'])} for {order['number']} is above the "
        f"{money(ctx.policies['refund.auto_approve_max_cents'])} automatic limit",
        context={
            "customer": order["customer_email"],
            "customer_message": state["body"][:2000],
            "delivered_days_ago": state.get("age_days"),
            "lines": order["lines"],
        },
    )
    if appr.status == "approved":
        ctx.step("approve", f"Approved{f': {appr.comment}' if appr.comment else ''}")
        return {"decision": "refund"}
    ctx.step("approve", f"Rejected{f': {appr.comment}' if appr.comment else ''}")
    return {"decision": "decline", "reply": "declined_reviewed"}


@node
def refund(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    order = state["order"]
    result = ctx.call(
        "refund",
        "erp.issue_refund",
        {
            "order_number": order["number"],
            "amount_cents": state["amount_cents"],
            "reason": state.get("reason") or "customer request",
            "idempotency_key": f"refund:{ctx.run_id}",
        },
    )
    ctx.step("refund", f"Refunded {money(state['amount_cents'])}", result)
    return {"refund": result, "reply": "refund_issued"}


@node
def draft(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    order = state.get("order") or {}
    facts = {
        "customer_name": order.get("customer_name"),
        "order_number": order.get("number") or state.get("order_number"),
        "status": order.get("status"),
        "window": ctx.policies["refund.window_days"],
        "amount": money(state["amount_cents"]) if state.get("amount_cents") else None,
    }
    email = ctx.reasoner.draft_email(state["reply"], facts)
    ctx.step("draft", f"Drafted reply: {email['subject']}", {**email, "kind": state["reply"]})
    return {"email": email}


@node
def approve_email(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    if not (state["email"].get("generated") and ctx.policies["email.approve_generated"]):
        return {"send": True}
    appr = ctx.require_approval(
        "approve_email",
        "send_email",
        "comms.send_email",
        {"to": state["from_email"], "subject": state["email"]["subject"]},
        amount_cents=None,
        reason="The reply was written by the language model",
        context={"draft": state["email"]["body"], "customer_message": state["body"][:2000]},
    )
    ctx.step("approve_email", "Reply approved" if appr.status == "approved" else "Reply rejected — not sent")
    return {"send": appr.status == "approved"}


@node
def send(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    outcome = {"refund_issued": "refunded", "order_status": "status_answered"}.get(state["reply"], state["reply"])
    if not state.get("send"):
        ctx.step("send", "Reply not sent (rejected by reviewer)")
        return {"outcome": outcome}
    email = state["email"]
    ctx.call(
        "send",
        "comms.send_email",
        {
            "to": state["from_email"],
            "subject": email["subject"],
            "body": email["body"],
            "generated": email.get("generated", False),
            "idempotency_key": f"email:{ctx.run_id}",
        },
    )
    ctx.step("send", f"Reply sent to {state['from_email']}")
    return {"outcome": outcome}


@node
def handoff(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    ctx.step("handoff", "Handed to the support team (no automated action)")
    return {"outcome": "handed_to_human"}


def build() -> StateGraph:
    g = StateGraph(RefundState)
    for fn in (understand, lookup_order, assess, approve, refund, draft, approve_email, send, handoff):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "understand")
    g.add_edge("understand", "lookup_order")
    g.add_edge("lookup_order", "assess")
    g.add_conditional_edges(
        "assess",
        lambda s: {"handoff": "handoff", "approve": "approve", "refund": "refund"}.get(s["decision"], "draft"),
        ["handoff", "approve", "refund", "draft"],
    )
    g.add_conditional_edges(
        "approve", lambda s: "refund" if s["decision"] == "refund" else "draft", ["refund", "draft"]
    )
    g.add_edge("refund", "draft")
    g.add_edge("draft", "approve_email")
    g.add_edge("approve_email", "send")
    g.add_edge("send", END)
    g.add_edge("handoff", END)
    return g
