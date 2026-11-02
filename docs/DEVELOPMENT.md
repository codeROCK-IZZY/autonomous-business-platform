# Development guide

## Prerequisites
Python 3.12+, Node 22, PostgreSQL 16. No other system packages.

## Database

```bash
docker run -d --name abp-db -e POSTGRES_USER=abp -e POSTGRES_PASSWORD=abp -e POSTGRES_DB=abp -p 127.0.0.1:5432:5432 postgres:16-alpine
docker exec abp-db psql -U abp -c "CREATE DATABASE abp_test"
```

## Backend

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
export ABP_DATABASE_URL=postgresql+psycopg://abp:abp@localhost:5432/abp ABP_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") ABP_COOKIE_SECURE=false ABP_DEMO_PASSWORD='choose-a-long-password'
```

```bash
python -m app.cli demo                       # migrations, LangGraph checkpoint tables, demo data, sample runs
uvicorn app.main:app --reload --port 8000
python -m app.cli worker                     # in another terminal: executes runs and resumes approved ones
```

Demo users: `olivia@example.com` (operator: starts runs), `aaron@example.com` (approver),
`admin@example.com` (admin: policies, users); password = `ABP_DEMO_PASSWORD`.

## Frontend

```bash
cd frontend && npm install && npm run dev
```

The app is at <http://localhost:5173>; `/api` is proxied to port 8000.

## Tests and evaluation

| Command | What it runs |
|---|---|
| `cd backend && pytest` | Unit and integration tests on `abp_test` (schema rebuilt from the migration). Reasoner offline: no API key, no cost |
| `cd frontend && npm test` | Vitest |
| `python -m app.cli evaluate --reset --out ../eval/reports` | **Deletes all business data in the configured database**, then runs every labelled case twice (normal and with injected tool failures) and writes the report |
| `python -m app.cli evaluate --reset --gate --out /tmp/eval` | Same, failing (exit 1) on any safety violation, a broken audit chain, missed approval, or accuracy below 90 % — what CI runs |

Tests read `ABP_DATABASE_URL` (default `postgresql+psycopg://abp:abp@127.0.0.1:5437/abp_test`).
`ABP_CHAOS_FAILURE_RATE=0.3` makes 30 % of tool calls fail transiently, to see retries in the UI.

## Code map
| Path | Responsibility |
|---|---|
| `app/agents/workflows/` | The three LangGraph graphs (refund, invoice, purchase) |
| `app/agents/runtime.py` | Tool registry, allow-lists, guards, retries, fault injection, approvals, step recording, cost meter |
| `app/agents/reasoning.py` | Offline rules and Claude reasoner behind one interface |
| `app/agents/runner.py` | Create, execute, resume, retry runs; approval decisions; LangGraph Postgres checkpointer |
| `app/agents/policy.py` | Policy defaults and lookup |
| `app/erp/service.py` | The business systems the tools act on (idempotent writes, invariants) |
| `app/modules/audit.py` | Hash-chained audit trail and verification |
| `app/modules/` | API routers: runs, approvals, governance (policies, tools, audit, dashboard), business data, auth, users, admin |
| `app/synthetic/business.py` | Seeded business data and the labelled cases |
| `app/evaluation/run.py` | Every reported metric and the CI gate |
| `migrations/versions/0001_initial_schema.py` | Schema, including the append-only audit trigger |
