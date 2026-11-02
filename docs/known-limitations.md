# Known limitations

| Area | Limitation | Why / path forward |
|---|---|---|
| Business systems | A mock ERP in the same database, not integrations with real ERPs, payment providers or mail servers | The tools are the integration boundary: replace `app/erp/service.py` functions with API clients; the guards, idempotency keys and audit stay the same |
| Refunds | Whole-order refunds only (minus final-sale lines and earlier refunds); no partial or per-item returns, no return shipping | Add item selection to triage and a returns-authorisation step |
| Offline reasoner | Keyword triage misses reworded requests (hands them to a person); invoice extraction knows three layouts | Enable Claude, or add rules; misses fail safe (no money moves) |
| E-mail | Plain-text body only; no attachments (PDF invoices must be pasted as text) | Reuse the document pipeline from the Intelligent Document AI project for PDFs and scans |
| Approvals | One approval step per action, one approver role; no amount-based approval chains (e.g. CFO above $50k) or delegation | Add `required_role` per threshold band to the policies |
| Evaluation | 129 synthetic cases from one generator and seed; labels follow the generator's own rules | A human-labelled sample of real (anonymised) tickets and invoices |
| Fault injection | Simulated transient failures only; no partial writes, timeouts after commit, or slow responses | Contract tests against real sandbox APIs |
| Concurrency | One worker processes runs sequentially by default; several workers are safe (SKIP LOCKED) but budget commits rely on row locks only | Fine for back-office volumes; partition budgets if needed |
| Rate limiting | Per-process (login) | Shared store (Redis) or WAF rules with several API replicas |
| Notifications | Approvers see the inbox; no e-mail/Slack notification of new approval requests | Send from the outbox when an approval is requested |
