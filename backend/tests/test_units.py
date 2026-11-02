"""Unit tests without the database: offline reasoner, guards, fault injection, generated cases."""

import random
import uuid
from collections import Counter

from app.agents.policy import DEFAULTS
from app.agents.reasoning import ORDER_RE, OfflineReasoner, cents
from app.agents.runtime import ALLOWED_TOOLS, TOOLS, _chaos
from app.synthetic import business

R = OfflineReasoner()
P = {k: v for k, (v, _) in DEFAULTS.items()}


def test_triage_intents_and_order_numbers():
    t = R.triage_email("Return for HP-104233", "The kettle is broken, I'd like a refund.")
    assert t == {"intent": "refund", "order_number": "HP-104233", "reason": "refund"}
    assert R.triage_email("", "Where is order hp 104250? It hasn't arrived.")["intent"] == "order_status"
    assert R.triage_email("", "where is hp 104250")["order_number"] == "HP-104250"
    assert R.triage_email("Question", "Do you sell walnut desks?") == {
        "intent": "other",
        "order_number": None,
        "reason": "",
    }


def test_invoice_extraction_handles_all_layouts():
    rng = random.Random(1)
    lines = [
        ("CAT-1001", "Printer paper A4 (box of 5 reams)", 3, 2100),
        ("CAT-1004", "Toner cartridge, black", 2, 6900),
    ]
    total = sum(q * p for _, _, q, p in lines)
    seen_layouts = set()
    for _ in range(12):
        text = business._invoice_text(rng, "Paperwise Ltd", "INV-5101", "PO-2600007", lines, total)
        seen_layouts.add(text.splitlines()[0][:7])
        x = R.extract_invoice(text)
        assert x["vendor"] == "Paperwise Ltd" and x["invoice_number"] == "INV-5101" and x["po_number"] == "PO-2600007"
        assert [(ln["sku"], ln["qty"], ln["unit_price_cents"]) for ln in x["lines"]] == [
            ("CAT-1001", 3, 2100),
            ("CAT-1004", 2, 6900),
        ]
        assert x["total_cents"] == total
    assert len(seen_layouts) == 3
    assert cents("$1,234.50") == 123450


def test_purchase_parsing():
    p = R.parse_purchase("3 boxes of printer paper\n2 toner cartridges\nNeeded for the month-end reports.")
    assert p["items"] == [{"query": "printer paper", "qty": 3}, {"query": "toner cartridges", "qty": 2}]
    assert p["justification"].startswith("Needed")
    assert R.parse_purchase("Can someone help me with the thing we discussed?")["items"] == []


def test_guards_follow_policy_thresholds():
    refund_guard = TOOLS["erp.issue_refund"].guard
    assert refund_guard({"amount_cents": P["refund.auto_approve_max_cents"]}, P) is None
    assert refund_guard({"amount_cents": P["refund.auto_approve_max_cents"] + 1}, P) == ("issue_refund", "amount_cents")
    pay_guard = TOOLS["erp.schedule_payment"].guard
    assert pay_guard({"amount_cents": 100, "exception": True}, P) is not None
    email_guard = TOOLS["comms.send_email"].guard
    assert email_guard({"generated": False}, P) is None and email_guard({"generated": True}, P) is not None
    # least privilege: each workflow only sees its own tools
    assert "erp.schedule_payment" not in ALLOWED_TOOLS["refund"] and "erp.issue_refund" not in ALLOWED_TOOLS["invoice"]


def test_fault_injection_is_deterministic_and_calibrated():
    run = uuid.uuid4()
    draws = [_chaos(run, "erp.get_order", n, 1, 0.3) for n in range(4000)]
    assert draws == [_chaos(run, "erp.get_order", n, 1, 0.3) for n in range(4000)]
    assert 0.27 < sum(draws) / len(draws) < 0.33
    assert not any(_chaos(run, "x", n, 1, 0.0) for n in range(100))


def test_generated_cases_are_consistent():
    a, b = business.build(seed=7), business.build(seed=7)
    assert [c["input"] for c in a.cases] == [c["input"] for c in b.cases]  # deterministic
    kinds = Counter(c["workflow"] for c in a.cases)
    assert kinds["refund"] >= 50 and kinds["invoice"] >= 40 and kinds["purchase"] >= 20
    limit = P["refund.auto_approve_max_cents"]
    for c in a.cases:
        if c["workflow"] == "refund" and c["expect"]["outcome"] == "refunded":
            assert c["expect"]["approval"] == (c["expect"]["amount_cents"] > limit), c["id"]
    numbers = [m.group(1) for c in a.cases if c["workflow"] == "refund" and (m := ORDER_RE.search(c["input"]["body"]))]
    assert len(numbers) == len(set(numbers))  # every case uses its own order
    assert all("@example.com" in c["input"].get("from_email", "x@example.com") for c in a.cases)
