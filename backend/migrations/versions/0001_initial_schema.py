"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
-- ============================================================ platform
CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email              varchar(320) NOT NULL,
    full_name          varchar(200) NOT NULL DEFAULT '',
    password_hash      varchar(255) NOT NULL,
    role               varchar(20)  NOT NULL CHECK (role IN ('admin', 'approver', 'operator', 'viewer')),
    is_active          boolean      NOT NULL DEFAULT true,
    failed_login_count integer      NOT NULL DEFAULT 0,
    locked_until       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email))
);
ALTER TABLE users RENAME CONSTRAINT users_pkey TO pk_users;

CREATE TABLE refresh_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,
    token_hash  varchar(64) NOT NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE refresh_tokens RENAME CONSTRAINT refresh_tokens_pkey TO pk_refresh_tokens;
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    job_type     varchar(50) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    finished_at  timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE jobs RENAME CONSTRAINT jobs_pkey TO pk_jobs;
CREATE INDEX ix_jobs_status_run_after ON jobs (status, run_after);

-- Tamper-evident audit trail: every row stores the SHA-256 of (previous hash + its own content).
-- UPDATE and DELETE are rejected by a trigger, so history can only grow; a broken chain is detectable.
CREATE TABLE audit_events (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    actor     varchar(100) NOT NULL,          -- user:<email> | agent:<workflow> | system
    action    varchar(100) NOT NULL,
    entity    varchar(50) NOT NULL,
    entity_id varchar(100),
    run_id    uuid,
    data      jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash varchar(64) NOT NULL,
    hash      varchar(64) NOT NULL
);
ALTER TABLE audit_events RENAME CONSTRAINT audit_events_pkey TO pk_audit_events;
CREATE INDEX ix_audit_events_run_id ON audit_events (run_id);

CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END $$;
CREATE TRIGGER trg_audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

-- ============================================================ business systems (mock ERP)
CREATE TABLE customers (
    id         serial PRIMARY KEY,
    name       varchar(200) NOT NULL,
    email      varchar(320) NOT NULL UNIQUE,
    tier       varchar(10) NOT NULL DEFAULT 'standard' CHECK (tier IN ('standard', 'gold')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE products (
    sku         varchar(30) PRIMARY KEY,
    name        varchar(200) NOT NULL,
    category    varchar(50) NOT NULL,
    price_cents integer NOT NULL CHECK (price_cents >= 0),
    final_sale  boolean NOT NULL DEFAULT false
);

CREATE TABLE orders (
    id           serial PRIMARY KEY,
    number       varchar(20) NOT NULL UNIQUE,
    customer_id  integer NOT NULL REFERENCES customers (id),
    placed_at    timestamptz NOT NULL,
    delivered_at timestamptz,
    status       varchar(12) NOT NULL CHECK (status IN ('processing', 'shipped', 'delivered', 'cancelled')),
    total_cents  integer NOT NULL
);

CREATE TABLE order_lines (
    order_id         integer NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    sku              varchar(30) NOT NULL REFERENCES products (sku),
    qty              integer NOT NULL CHECK (qty > 0),
    unit_price_cents integer NOT NULL,
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE refunds (
    id              serial PRIMARY KEY,
    order_id        integer NOT NULL REFERENCES orders (id),
    amount_cents    integer NOT NULL CHECK (amount_cents > 0),
    reason          text NOT NULL,
    idempotency_key varchar(120) NOT NULL UNIQUE,
    run_id          uuid,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE vendors (
    id                 serial PRIMARY KEY,
    name               varchar(200) NOT NULL UNIQUE,
    email              varchar(320) NOT NULL,
    preferred          boolean NOT NULL DEFAULT false,
    payment_terms_days integer NOT NULL DEFAULT 30
);

CREATE TABLE catalog_items (
    sku              varchar(30) PRIMARY KEY,
    name             varchar(200) NOT NULL,
    category         varchar(50) NOT NULL,
    vendor_id        integer NOT NULL REFERENCES vendors (id),
    unit_price_cents integer NOT NULL CHECK (unit_price_cents > 0),
    keywords         text NOT NULL DEFAULT ''
);

CREATE TABLE budgets (
    department      varchar(50) PRIMARY KEY,
    limit_cents     bigint NOT NULL,
    committed_cents bigint NOT NULL DEFAULT 0,
    CONSTRAINT ck_budgets_within_limit CHECK (committed_cents <= limit_cents)
);

CREATE TABLE purchase_orders (
    id          serial PRIMARY KEY,
    number      varchar(20) NOT NULL UNIQUE,
    vendor_id   integer NOT NULL REFERENCES vendors (id),
    department  varchar(50) NOT NULL,
    status      varchar(10) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'received', 'closed')),
    total_cents bigint NOT NULL,
    idempotency_key varchar(120) UNIQUE,
    run_id      uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE po_lines (
    po_id            integer NOT NULL REFERENCES purchase_orders (id) ON DELETE CASCADE,
    sku              varchar(30) NOT NULL,
    description      varchar(200) NOT NULL,
    qty              integer NOT NULL CHECK (qty > 0),
    unit_price_cents integer NOT NULL,
    PRIMARY KEY (po_id, sku)
);

CREATE TABLE goods_receipts (
    id          serial PRIMARY KEY,
    po_id       integer NOT NULL REFERENCES purchase_orders (id),
    sku         varchar(30) NOT NULL,
    qty         integer NOT NULL CHECK (qty > 0),
    received_at timestamptz NOT NULL
);

CREATE TABLE vendor_invoices (
    id             serial PRIMARY KEY,
    vendor_id      integer NOT NULL REFERENCES vendors (id),
    invoice_number varchar(60) NOT NULL,
    po_id          integer REFERENCES purchase_orders (id),
    total_cents    bigint NOT NULL,
    status         varchar(12) NOT NULL CHECK (status IN ('approved', 'rejected', 'paid')),
    run_id         uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_vendor_invoices_vendor_number UNIQUE (vendor_id, invoice_number)
);

CREATE TABLE payments (
    id              serial PRIMARY KEY,
    invoice_id      integer NOT NULL REFERENCES vendor_invoices (id),
    amount_cents    bigint NOT NULL CHECK (amount_cents > 0),
    scheduled_for   date NOT NULL,
    idempotency_key varchar(120) NOT NULL UNIQUE,
    run_id          uuid,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_emails (
    id         serial PRIMARY KEY,
    to_address varchar(320) NOT NULL,
    subject    varchar(300) NOT NULL,
    body       text NOT NULL,
    run_id     uuid,
    idempotency_key varchar(120) UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ============================================================ agents
CREATE TABLE runs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow      varchar(30) NOT NULL CHECK (workflow IN ('refund', 'invoice', 'purchase')),
    title         varchar(300) NOT NULL,
    status        varchar(20) NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed')),
    outcome       varchar(40),
    input         jsonb NOT NULL,
    output        jsonb NOT NULL DEFAULT '{}'::jsonb,
    error         text,
    reasoner      varchar(100),
    llm_calls     integer NOT NULL DEFAULT 0,
    input_tokens  integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    cost_usd      numeric(10, 5) NOT NULL DEFAULT 0,
    created_by    uuid REFERENCES users (id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz
);
CREATE INDEX ix_runs_status ON runs (status);
CREATE INDEX ix_runs_created_at ON runs (created_at DESC);

CREATE TABLE run_steps (
    id          bigserial PRIMARY KEY,
    run_id      uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    seq         integer NOT NULL,
    node        varchar(50) NOT NULL,
    status      varchar(10) NOT NULL CHECK (status IN ('completed', 'waiting', 'failed')),
    summary     text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_run_steps_run_seq UNIQUE (run_id, seq)
);

CREATE TABLE tool_calls (
    id              bigserial PRIMARY KEY,
    run_id          uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    node            varchar(50) NOT NULL,
    tool            varchar(60) NOT NULL,
    risk            varchar(15) NOT NULL CHECK (risk IN ('read', 'write', 'financial', 'communication')),
    args            jsonb NOT NULL,
    result          jsonb,
    status          varchar(10) NOT NULL CHECK (status IN ('succeeded', 'failed', 'blocked')),
    attempts        integer NOT NULL,
    error           text,
    idempotency_key varchar(120),
    duration_ms     integer NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_tool_calls_run_id ON tool_calls (run_id);

CREATE TABLE llm_usage (
    id            bigserial PRIMARY KEY,
    run_id        uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    purpose       varchar(50) NOT NULL,
    model         varchar(100) NOT NULL,
    input_tokens  integer NOT NULL,
    output_tokens integer NOT NULL,
    cost_usd      numeric(10, 5) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    action        varchar(60) NOT NULL,       -- e.g. issue_refund, schedule_payment
    tool          varchar(60) NOT NULL,
    args          jsonb NOT NULL,
    amount_cents  bigint,
    reason        text NOT NULL,
    context       jsonb NOT NULL DEFAULT '{}'::jsonb,
    required_role varchar(20) NOT NULL DEFAULT 'approver',
    status        varchar(10) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_at  timestamptz NOT NULL DEFAULT now(),
    decided_by    uuid REFERENCES users (id) ON DELETE SET NULL,
    decided_at    timestamptz,
    comment       text NOT NULL DEFAULT '',
    CONSTRAINT uq_approvals_run_action UNIQUE (run_id, action)
);
CREATE INDEX ix_approvals_status ON approvals (status);

CREATE TABLE policies (
    key         varchar(60) PRIMARY KEY,
    value       jsonb NOT NULL,
    description text NOT NULL,
    updated_by  uuid REFERENCES users (id) ON DELETE SET NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
"""

DOWNGRADE_SQL = """
DROP TABLE IF EXISTS policies, approvals, llm_usage, tool_calls, run_steps, runs, outbox_emails, payments,
    vendor_invoices, goods_receipts, po_lines, purchase_orders, budgets, catalog_items, vendors, refunds,
    order_lines, orders, products, customers, audit_events, jobs, refresh_tokens, users CASCADE;
DROP FUNCTION IF EXISTS audit_events_immutable();
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
