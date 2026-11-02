"""Employee purchase requests (procurement).

    parse → match_catalog → check_budget ─┬─ over budget / unknown department / nothing understood → notify (end)
                                          └→ decide ─┬─ catalogue items within limit ───────→ order
                                                     └─ off-catalogue or above limit → approve ─┬→ order → notify
                                                                                                └→ notify (rejected)

Items are matched to the procurement catalogue (preferred suppliers first); one purchase order is
created per supplier and the department budget is committed atomically.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.runtime import RunContext, money
from app.agents.workflows.common import node

MIN_MATCH_SCORE = 0.6  # share of the request's words found in the catalogue item


class PurchaseState(TypedDict, total=False):
    requester_email: str
    department: str
    text: str
    items: list[dict[str, Any]]
    justification: str
    matched: list[dict[str, Any]]
    unmatched: list[dict[str, Any]]
    total_cents: int
    budget: dict[str, Any] | None
    decision: str
    reply: str
    pos: list[dict[str, Any]]
    outcome: str


@node
def parse(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    p = ctx.reasoner.parse_purchase(state["text"])
    items = [i for i in p["items"] if i.get("qty", 0) > 0]
    ctx.step("parse", f"{len(items)} item(s) requested", p)
    return {"items": items, "justification": p.get("justification", "")}


@node
def match_catalog(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    matched, unmatched = [], []
    for item in state["items"]:
        found = ctx.call("match_catalog", "erp.search_catalog", {"query": item["query"]})["matches"]
        best = found[0] if found and found[0]["score"] >= MIN_MATCH_SCORE else None
        if best is None:
            unmatched.append(item)
        else:
            matched.append(
                {
                    **best,
                    "qty": item["qty"],
                    "query": item["query"],
                    "line_total_cents": best["unit_price_cents"] * item["qty"],
                }
            )
    total = sum(m["line_total_cents"] for m in matched)
    ctx.step(
        "match_catalog",
        f"{len(matched)} matched ({money(total)}), {len(unmatched)} not in the catalogue",
        {"matched": matched, "unmatched": unmatched},
    )
    return {"matched": matched, "unmatched": unmatched, "total_cents": total}


@node
def check_budget(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    if not state["items"]:
        ctx.step("check_budget", "Nothing understood in the request — hand to procurement")
        return {"decision": "handoff"}
    budget = ctx.call("check_budget", "erp.get_budget", {"department": state["department"]})
    if budget is None:
        ctx.step("check_budget", f"Unknown department '{state['department']}'")
        return {"decision": "reject", "reply": "po_rejected_review", "outcome": "rejected_unknown_department"}
    total = state["total_cents"]
    if total > budget["remaining_cents"]:
        ctx.step("check_budget", f"{money(total)} exceeds the remaining {money(budget['remaining_cents'])}", budget)
        return {
            "budget": budget,
            "decision": "reject",
            "reply": "po_rejected_budget",
            "outcome": "rejected_over_budget",
        }
    needs_review = bool(state["unmatched"]) or total > ctx.policies["purchase.auto_approve_max_cents"]
    ctx.step(
        "check_budget",
        f"Within budget ({money(budget['remaining_cents'])} remaining)" + (" — needs approval" if needs_review else ""),
        budget,
    )
    return {"budget": budget, "decision": "approve" if needs_review else "order"}


@node
def approve(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    reasons = []
    if state["unmatched"]:
        reasons.append("not in the catalogue: " + ", ".join(i["query"] for i in state["unmatched"]))
    if state["total_cents"] > ctx.policies["purchase.auto_approve_max_cents"]:
        reasons.append(
            f"total {money(state['total_cents'])} is above the "
            f"{money(ctx.policies['purchase.auto_approve_max_cents'])} automatic limit"
        )
    appr = ctx.require_approval(
        "approve",
        "create_purchase_order",
        "erp.create_purchase_order",
        {"department": state["department"], "request_total_cents": state["total_cents"]},
        amount_cents=state["total_cents"],
        reason="; ".join(reasons).capitalize(),
        context={
            "requester": state["requester_email"],
            "request": state["text"][:2000],
            "justification": state.get("justification"),
            "matched": state["matched"],
            "unmatched": state["unmatched"],
            "budget": state.get("budget"),
        },
    )
    ctx.step("approve", f"{appr.status.capitalize()}{f': {appr.comment}' if appr.comment else ''}")
    if appr.status == "approved":
        return {"decision": "order"}
    return {"decision": "reject", "reply": "po_rejected_review", "outcome": "rejected_by_reviewer"}


@node
def order(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    by_vendor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in state["matched"]:
        by_vendor[m["vendor_id"]].append(m)
    pos = []
    for vendor_id, lines in sorted(by_vendor.items()):
        pos.append(
            ctx.call(
                "order",
                "erp.create_purchase_order",
                {
                    "vendor_id": vendor_id,
                    "department": state["department"],
                    "lines": [
                        {"sku": m["sku"], "name": m["name"], "qty": m["qty"], "unit_price_cents": m["unit_price_cents"]}
                        for m in lines
                    ],
                    "request_total_cents": state["total_cents"],
                    "needs_review": bool(state["unmatched"]),
                    "idempotency_key": f"po:{ctx.run_id}:{vendor_id}",
                },
            )
        )
    ctx.step(
        "order",
        f"Created {', '.join(p['po_number'] for p in pos) or 'no purchase order'}"
        + (" (off-catalogue items go to procurement)" if state["unmatched"] else ""),
        {"pos": pos},
    )
    reviewed = bool(state["unmatched"]) or state["total_cents"] > ctx.policies["purchase.auto_approve_max_cents"]
    return {"pos": pos, "reply": "po_created", "outcome": "ordered_after_review" if reviewed else "ordered"}


@node
def notify(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    budget = state.get("budget") or {}
    facts = {
        "amount": money(state.get("total_cents") or 0),
        "po_numbers": ", ".join(p["po_number"] for p in state.get("pos") or []) or "none",
        "department": state["department"],
        "remaining": money(budget.get("remaining_cents") or 0),
    }
    # Internal notification: templated (no language model), so it needs no approval.
    email = _template(state["reply"], facts)
    ctx.call(
        "notify",
        "comms.send_email",
        {
            "to": state["requester_email"],
            "subject": email["subject"],
            "body": email["body"],
            "generated": False,
            "idempotency_key": f"email:{ctx.run_id}",
        },
    )
    ctx.step("notify", f"Requester notified: {email['subject']}")
    return {}


def _template(kind: str, facts: dict[str, Any]) -> dict[str, Any]:
    from app.agents.reasoning import OfflineReasoner

    return OfflineReasoner().draft_email(kind, facts)


@node
def handoff(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    ctx.step("handoff", "Handed to the procurement team")
    return {"outcome": "handed_to_human"}


def build() -> StateGraph:
    g = StateGraph(PurchaseState)
    for fn in (parse, match_catalog, check_budget, approve, order, notify, handoff):
        g.add_node(fn.__name__, fn)
    g.add_edge(START, "parse")
    g.add_edge("parse", "match_catalog")
    g.add_edge("match_catalog", "check_budget")
    g.add_conditional_edges(
        "check_budget",
        lambda s: {"handoff": "handoff", "reject": "notify", "approve": "approve"}.get(s["decision"], "order"),
        ["handoff", "notify", "approve", "order"],
    )
    g.add_conditional_edges("approve", lambda s: "order" if s["decision"] == "order" else "notify", ["order", "notify"])
    g.add_edge("order", "notify")
    g.add_edge("notify", END)
    g.add_edge("handoff", END)
    return g
