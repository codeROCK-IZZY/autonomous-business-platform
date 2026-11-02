# Operations runbook

## Health
`GET /api/health/live` (process) · `GET /api/health/ready` (database + reasoner mode) ·
`GET /api/v1/system/info` (reasoner, whether data leaves the server, fault-injection rate).

## Runs
- **A run stays `queued`.** The worker isn't running: `docker compose ps worker`, then its logs.
- **A run is `failed`.** The run page shows the error. Tool errors after all retries (e.g. a supplier
  system outage) → fix the cause, then **Retry from last step**: the run resumes from its checkpoint;
  completed steps are not repeated and writes are idempotent. Permanent errors (bad input, business rule)
  are shown as such.
- **A run waits for approval.** It appears in **Approvals** for users with the approver role (not the
  person who started it). Paused runs survive restarts: their state is checkpointed in PostgreSQL.
- **`policy.blocked` in the audit trail.** The executor refused an action without a matching approval.
  This should not happen in normal operation — investigate the run (it indicates a workflow bug or an
  attempted bypass).

## Policies
Change thresholds in **Governance → Policies** (admin). Changes apply to runs from their next step
and are audited (`policy.updated`, with old and new value).

## Audit
**Governance → Audit trail → Verify hash chain** recomputes every hash. "Broken at #N" means a row was
altered or inserted outside the application (the database rejects UPDATE/DELETE, so this requires
direct table access — treat it as a security incident). Export with
`docker compose exec db pg_dump -U abp -t audit_events abp`.

## Cost
With Claude enabled, each run's tokens and cost are on the run page and the dashboard. `run.budget_usd`
caps spend per run; above it the run continues with the rule-based reasoner.

## Backups
- **Local:** `docker compose exec db pg_dump -U abp -Fc abp > abp.dump` (everything — business data,
  runs, checkpoints, audit — is in PostgreSQL).
- **AWS:** RDS automated backups (7 days) and a final snapshot on destroy.

## Changing the reasoner
- **Turn Claude on:** set `ANTHROPIC_API_KEY`, restart the worker. Customer e-mails it writes then need
  approval before sending (`email.approve_generated`).
- **Turn Claude off:** remove the key or set `ABP_LLM_PROVIDER=offline`.
