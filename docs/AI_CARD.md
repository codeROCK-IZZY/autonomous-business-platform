# AI card — what the model does, and what it never does

| Decision or output | Made by | Deterministic? |
|---|---|---|
| Intent of a customer e-mail, the order number it mentions | Offline rules, or Claude | Rules yes; Claude no |
| Invoice fields (supplier, number, PO, lines, total) | Offline rules, or Claude | Rules yes; Claude no |
| Items and quantities in a purchase request | Offline rules, or Claude | Rules yes; Claude no |
| Wording of customer e-mails | Templates, or Claude (then approval required before sending) | Templates yes; Claude no |
| Whether the sender owns the order | Code, from ERP data | Yes |
| Refund eligibility and amount | Code: delivery date, window, final-sale lines, earlier refunds | Yes |
| Three-way invoice match | Code: PO lines, goods receipts, price tolerance, totals | Yes |
| Catalogue match, budget check | Code | Yes |
| **Whether an action needs human approval** | **Code, from policies — re-checked by the executor on every call** | **Yes** |
| Moving money, creating orders, sending e-mail | Tools, only after the checks above | Yes |

## Why this split
The language model is good at reading messy text and bad at being a control. So it only turns text into
fields and fields into text. Every number that matters is looked up or computed, and the approval rule is
enforced twice (workflow and executor). An e-mail saying "this refund is pre-approved by management"
changes nothing: the evaluation includes such cases, and in every one the approval was still requested.

## Data sent to third parties
None in offline mode (the default without `ANTHROPIC_API_KEY`). With Claude enabled, the request text
(customer e-mail, invoice, purchase request) and the facts for a reply draft are sent to Anthropic.
`/api/v1/system/info` reports `data_leaves_server`, and the UI shows the active mode.

## When not to trust it
* **Offline triage relies on keywords.** Reworded requests without words like "refund", "return" or
  "broken" are handed to a person (by design, the safe failure) — 4 of 4 such evaluation cases.
* **Offline invoice extraction knows a few layouts.** Unusual layouts give missing fields, which become
  exceptions for a human — safe, but not automated. Claude handles free-form layouts.
* **The case set is synthetic** (129 cases, one company). Real inboxes contain multi-order e-mails,
  attachments, partial returns and edge cases it does not cover.
