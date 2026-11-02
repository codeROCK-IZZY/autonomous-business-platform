"""Agent workflows end to end through the API, the worker and LangGraph (real database)."""

import pytest
from sqlalchemy import func, select, text

from app.agents.reasoning import ORDER_RE
from app.agents.runtime import PolicyViolation, RunContext
from app.core.config import get_settings
from app.db.models import Approval, OutboxEmail, Payment, PurchaseOrder, Refund, Run, ToolCall
from app.db.session import new_session
from app.erp import service as erp
from app.evaluation.run import safety_checks
from app.modules import audit
from tests.conftest import case, drain_jobs, start

API = "/api/v1"


def auto_refund_case(ds, n: int = 0) -> dict:
    """An eligible refund small enough to be issued without approval."""
    return [c for c in ds.cases if c["kind"] == "eligible_small" and not c["expect"]["approval"]][n]


def _count(model, **where) -> int:
    with new_session() as db:
        stmt = select(func.count()).select_from(model)
        for k, v in where.items():
            stmt = stmt.where(getattr(model, k) == v)
        return db.scalar(stmt) or 0


def test_small_refund_is_automatic_and_fully_recorded(client, auth, data):
    op = auth("olivia", "operator")
    c = auto_refund_case(data)
    detail = start(client, op, c)
    assert detail["run"]["status"] == "completed" and detail["run"]["outcome"] == "refunded"
    assert detail["output"]["amount_cents"] == c["expect"]["amount_cents"]
    assert [s["node"] for s in detail["steps"]][:3] == ["understand", "lookup_order", "assess"]
    assert [t["tool"] for t in detail["tool_calls"]] == ["erp.get_order", "erp.issue_refund", "comms.send_email"]
    assert {e["action"] for e in detail["audit"]} >= {"run.created", "tool.erp.issue_refund", "run.completed"}
    assert _count(OutboxEmail) == 1


def test_large_refund_waits_for_a_different_approver(client, auth, data):
    op, approver = auth("olivia", "operator"), auth("aaron", "approver")
    c = case(data, "refund", "eligible_large")
    detail = start(client, op, c)
    assert detail["run"]["status"] == "waiting_approval" and detail["run"]["pending_approval"]
    assert _count(Refund, run_id=detail["run"]["id"]) == 0  # no money before the decision
    inbox = client.get(f"{API}/approvals", headers=approver).json()
    assert len(inbox) == 1 and inbox[0]["action"] == "issue_refund" and "above the" in inbox[0]["reason"]
    appr = inbox[0]["id"]
    # operators cannot approve at all
    assert client.post(f"{API}/approvals/{appr}/decision", headers=op, json={"decision": "approve"}).status_code == 403
    r = client.post(f"{API}/approvals/{appr}/decision", headers=approver, json={"decision": "approve", "comment": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert (
        client.post(f"{API}/approvals/{appr}/decision", headers=approver, json={"decision": "approve"}).status_code
        == 409
    )
    drain_jobs()
    done = client.get(f"{API}/runs/{detail['run']['id']}", headers=op).json()
    assert done["run"]["status"] == "completed" and done["run"]["outcome"] == "refunded"
    assert done["approvals"][0]["decided_by"] == "aaron@example.com"


def test_rejected_refund_moves_no_money(client, auth, data):
    op, approver = auth("olivia", "operator"), auth("aaron", "approver")
    detail = start(client, op, case(data, "refund", "eligible_large", 1))
    appr = client.get(f"{API}/approvals", headers=approver).json()[0]["id"]
    client.post(f"{API}/approvals/{appr}/decision", headers=approver, json={"decision": "reject", "comment": "fraud?"})
    drain_jobs()
    done = client.get(f"{API}/runs/{detail['run']['id']}", headers=op).json()
    assert done["run"]["outcome"] == "declined_reviewed" and _count(Refund, run_id=detail["run"]["id"]) == 0


def test_nobody_approves_their_own_run(client, auth, data):
    approver = auth("aaron", "approver")
    start(client, approver, case(data, "refund", "eligible_large"))
    appr = client.get(f"{API}/approvals", headers=approver).json()[0]["id"]
    r = client.post(f"{API}/approvals/{appr}/decision", headers=approver, json={"decision": "approve"})
    assert r.status_code == 403 and "separation of duties" in r.json()["detail"]


@pytest.mark.parametrize("kind", ["other_customers_order", "prompt_injection"])
def test_security_cases(client, auth, data, kind):
    op = auth("olivia", "operator")
    for c in [x for x in data.cases if x["kind"] == kind and x["workflow"] == "refund"]:
        detail = start(client, op, c)
        asked = bool(detail["approvals"])
        assert asked == c["expect"]["approval"], c["id"]
        if c["expect"]["approval"]:
            assert detail["run"]["status"] == "waiting_approval"
        else:
            assert detail["run"]["outcome"] == c["expect"]["outcome"], c["id"]
    with new_session() as db:  # verified in the ERP tables, independently of the agent's records
        assert safety_checks(db)["refunds_on_another_customers_order"] == 0


def test_invoice_workflows(client, auth, data):
    op, approver = auth("olivia", "operator"), auth("aaron", "approver")
    clean = start(client, op, next(c for c in data.cases if c["kind"] == "clean" and not c["expect"]["approval"]))
    assert clean["run"]["outcome"] == "paid" and _count(Payment) == 1
    dup = start(client, op, case(data, "invoice", "duplicate"))
    assert dup["run"]["outcome"] == "rejected_duplicate" and _count(Payment) == 1
    fake = start(client, op, case(data, "invoice", "unknown_supplier"))
    assert fake["run"]["outcome"] == "rejected_unknown_supplier"
    mismatch = start(client, op, case(data, "invoice", "price_mismatch"))
    assert mismatch["run"]["status"] == "waiting_approval"
    inbox = client.get(f"{API}/approvals", headers=approver).json()
    assert "price" in inbox[0]["reason"] and inbox[0]["context"]["issues"]
    client.post(f"{API}/approvals/{inbox[0]['id']}/decision", headers=approver, json={"decision": "approve"})
    drain_jobs()
    done = client.get(f"{API}/runs/{mismatch['run']['id']}", headers=op).json()
    assert done["run"]["outcome"] == "paid_after_review" and _count(Payment) == 2


def test_purchase_workflows_commit_budget(client, auth, data):
    op = auth("olivia", "operator")
    before = {b["department"]: b for b in client.get(f"{API}/business/budgets", headers=op).json()}
    c = case(data, "purchase", "catalog_small")
    detail = start(client, op, c)
    assert detail["run"]["outcome"] == "ordered" and _count(PurchaseOrder) >= 1
    after = {b["department"]: b for b in client.get(f"{API}/business/budgets", headers=op).json()}
    dept = c["input"]["department"]
    assert after[dept]["committed_cents"] - before[dept]["committed_cents"] == detail["output"]["total_cents"]
    over = start(client, op, case(data, "purchase", "over_budget"))
    assert over["run"]["outcome"] == "rejected_over_budget"
    unclear = start(client, op, case(data, "purchase", "unclear"))
    assert unclear["run"]["outcome"] == "handed_to_human"


def test_executor_blocks_unapproved_and_disallowed_actions(client, auth, data):
    op = auth("olivia", "operator")
    detail = start(client, op, case(data, "refund", "eligible_large"))  # paused, approval pending
    with new_session() as db:
        run = db.get(Run, detail["run"]["id"])
        ctx = RunContext(db, run)
        number = ORDER_RE.search(run.input["body"]).group(1)
        with pytest.raises(PolicyViolation):  # above the automatic limit, approval still pending
            ctx.call(
                "test",
                "erp.issue_refund",
                {"order_number": number, "amount_cents": 50_000, "reason": "x", "idempotency_key": "test-direct"},
            )
        with pytest.raises(PolicyViolation):  # not on the refund agent's allow-list
            ctx.call("test", "erp.schedule_payment", {"invoice_id": 1, "amount_cents": 100, "idempotency_key": "k"})
        blocked = db.scalar(select(func.count()).where(ToolCall.run_id == run.id, ToolCall.status == "blocked"))
    assert blocked == 2 and _count(Refund, idempotency_key="test-direct") == 0
    assert any(e["action"] == "policy.blocked" for e in client.get(f"{API}/audit", headers=op).json())


def test_erp_writes_are_idempotent_and_guard_invariants(data):
    with new_session() as db:
        order = data.orders[0]
        first = erp.issue_refund(db, order["number"], 100, "test", "same-key", None)
        again = erp.issue_refund(db, order["number"], 100, "test", "same-key", None)
        assert again == {**first, "replayed": True}
        with pytest.raises(erp.ToolError):
            erp.issue_refund(db, order["number"], order["total"] * 10, "too much", "other-key", None)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Refund).where(Refund.idempotency_key == "same-key")) == 1


def test_transient_failures_are_retried_and_failed_runs_can_resume(client, auth, data):
    op = auth("olivia", "operator")
    s = get_settings()
    original = (s.chaos_failure_rate, s.tool_max_attempts)
    try:
        s.chaos_failure_rate, s.tool_max_attempts = 0.5, 6
        detail = start(client, op, auto_refund_case(data, 1))
        assert detail["run"]["status"] == "completed"
        assert any(t["attempts"] > 1 for t in detail["tool_calls"])
        s.chaos_failure_rate, s.tool_max_attempts = 0.9, 1  # outage: the run fails visibly
        failed = start(client, op, auto_refund_case(data, 2))
        assert failed["run"]["status"] == "failed" and "outage" in failed["error"]
        s.chaos_failure_rate = 0.0  # outage over: retry resumes from the last checkpoint
        r = client.post(f"{API}/runs/{failed['run']['id']}/retry", headers=op)
        assert r.status_code == 202
        drain_jobs()
        done = client.get(f"{API}/runs/{failed['run']['id']}", headers=op).json()
        assert done["run"]["status"] == "completed" and done["run"]["outcome"] == "refunded"
        assert _count(Refund, run_id=failed["run"]["id"]) == 1
    finally:
        s.chaos_failure_rate, s.tool_max_attempts = original


def test_audit_trail_is_append_only_and_verifiable(client, auth, data):
    op = auth("olivia", "operator")
    start(client, op, auto_refund_case(data))
    assert client.get(f"{API}/audit/verify", headers=op).json()["valid"] is True
    with new_session() as db, pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE audit_events SET action = 'tampered' WHERE id = 1"))
    with new_session() as db:  # a forged row (bypassing record()) breaks the chain
        db.execute(
            text(
                "INSERT INTO audit_events (actor, action, entity, prev_hash, hash) "
                "VALUES ('x', 'forged', 'x', 'bad', 'bad')"
            )
        )
        db.commit()
        result = audit.verify_chain(db)
    assert result["valid"] is False and result["broken_at"] is not None


def test_policies_change_behaviour_and_are_admin_only(client, auth, data):
    op, admin = auth("olivia", "operator"), auth("root", "admin")
    assert client.put(f"{API}/policies/refund.auto_approve_max_cents", headers=op, json={"value": 1}).status_code == 403
    r = client.put(f"{API}/policies/refund.auto_approve_max_cents", headers=admin, json={"value": 1})
    assert r.status_code == 200
    assert client.put(f"{API}/policies/refund.window_days", headers=admin, json={"value": "x"}).status_code == 422
    detail = start(client, op, case(data, "refund", "eligible_small"))
    assert detail["run"]["status"] == "waiting_approval"  # every refund above 1 cent now needs a person
    with new_session() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 1
    assert any(e["action"] == "policy.updated" for e in client.get(f"{API}/audit", headers=op).json())


def test_read_endpoints(client, auth, data):
    viewer = auth("vera", "viewer")
    start(client, auth("olivia", "operator"), auto_refund_case(data))
    for path in [
        "/runs",
        "/approvals",
        "/policies",
        "/tools",
        "/audit",
        "/dashboard",
        "/business/refunds",
        "/business/payments",
        "/business/invoices",
        "/business/purchase-orders",
        "/business/budgets",
        "/business/emails",
        "/system/info",
    ]:
        assert client.get(f"{API}{path}", headers=viewer).status_code == 200, path
    dash = client.get(f"{API}/dashboard", headers=viewer).json()
    assert dash["runs_by_status"]["completed"] == 1 and dash["automation_rate"] == 1.0
    # viewers cannot start runs
    c = case(data, "refund", "eligible_small")
    assert client.post(f"{API}/runs/refund", headers=viewer, json=c["input"]).status_code == 403
