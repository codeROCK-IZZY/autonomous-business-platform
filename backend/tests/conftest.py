"""Test fixtures. Integration tests run against a real PostgreSQL database (``abp_test``); the
schema is rebuilt from the Alembic migration at session start, plus LangGraph's checkpoint tables."""

import os
from collections.abc import Callable, Iterator

os.environ.setdefault("ABP_DATABASE_URL", "postgresql+psycopg://abp:abp@127.0.0.1:5437/abp_test")
os.environ["ABP_JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["ABP_ENV"] = "test"
os.environ["ABP_COOKIE_SECURE"] = "false"
os.environ["ABP_LOG_JSON"] = "false"
os.environ["ABP_LOG_LEVEL"] = "WARNING"
os.environ["ABP_LLM_PROVIDER"] = "offline"  # tests never call a paid API
os.environ["ABP_TOOL_RETRY_BASE_SECONDS"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.agents import runner
from app.core.config import get_settings
from app.core.security import hash_password
from app.db.models import User
from app.db.session import get_engine, new_session
from app.main import app
from app.modules.auth.router import login_limiter
from app.synthetic import business

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-Battery-9"
TABLES = (
    "llm_usage, tool_calls, run_steps, approvals, runs, outbox_emails, payments, vendor_invoices, goods_receipts, "
    "po_lines, purchase_orders, budgets, catalog_items, vendors, refunds, order_lines, orders, products, customers, "
    "jobs, audit_events, policies, refresh_tokens, users"
)


def _alembic(direction: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    (command.downgrade if direction == "down" else command.upgrade)(cfg, "base" if direction == "down" else "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    _alembic("down")
    _alembic("up")
    runner.setup_checkpointer()
    yield
    runner.close()


def reset_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
        for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.execute(text(f"DELETE FROM {t}"))
    login_limiter.reset()


@pytest.fixture
def clean_db() -> None:
    reset_database()


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def data(clean_db: None) -> business.Dataset:
    """Fresh synthetic business data, plus its labelled cases."""
    ds = business.build(seed=7)
    with new_session() as db:
        business.load(db, ds)
        db.commit()
    return ds


def create_user(email: str, role: str = "operator", password: str = PASSWORD, active: bool = True) -> User:
    with new_session() as s:
        u = User(
            email=email,
            full_name=email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            is_active=active,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> Callable[..., dict[str, str]]:
    """``auth("olivia", "operator")`` → headers for olivia@example.com (created on first use)."""
    made: dict[str, dict[str, str]] = {}

    def _auth(name: str, role: str = "operator") -> dict[str, str]:
        if name not in made:
            create_user(f"{name}@example.com", role)
            made[name] = login(client, f"{name}@example.com")
        return made[name]

    return _auth


def drain_jobs() -> int:
    from app.jobs.worker import drain

    return drain()


def case(ds: business.Dataset, workflow: str, kind: str, n: int = 0) -> dict[str, Any]:
    return [c for c in ds.cases if c["workflow"] == workflow and c["kind"] == kind][n]


def start(client: TestClient, headers: dict[str, str], c: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/api/v1/runs/{c['workflow']}", headers=headers, json=c["input"])
    assert r.status_code == 202, r.text
    drain_jobs()
    return client.get(f"/api/v1/runs/{r.json()['id']}", headers=headers).json()
