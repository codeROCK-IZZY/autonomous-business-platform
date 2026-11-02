"""Command-line tools: ``python -m app.cli <command>``.

migrate                          apply database migrations and create LangGraph's checkpoint tables
create-user --email E --role R   create a user (password from ABP_USER_PASSWORD or prompt)
load-demo                        demo users and business data, plus sample runs (some awaiting approval)
evaluate --reset --out DIR       RESETS business data, runs every labelled case, writes the report
worker                           run the background worker
drain                            process all queued jobs, then exit
demo                             migrate + load-demo + drain (one-command setup)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging

# olivia starts runs; aaron and admin can approve them (nobody approves their own run).
DEMO_USERS = {"olivia": "operator", "aaron": "approver", "admin": "admin"}
# One sample run per case type, so the dashboard, inbox and audit trail have content.
DEMO_KINDS = (
    "refund:eligible_small",
    "refund:eligible_large",
    "refund:outside_window",
    "refund:other_customers_order",
    "refund:prompt_injection",
    "invoice:clean",
    "invoice:price_mismatch",
    "invoice:duplicate",
    "invoice:unknown_supplier",
    "purchase:catalog_small",
    "purchase:catalog_large",
    "purchase:over_budget",
)


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    from app.agents import runner

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    runner.setup_checkpointer()
    runner.close()
    print("migrations applied")


def _create_user(email: str, role: str, password: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = password or os.environ.get("ABP_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} exists — skipped")
            return
        db.add(
            User(
                email=email.lower(),
                full_name=email.split("@")[0].title(),
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"user {email} ({role}) created")


def load_demo() -> None:
    from sqlalchemy import select

    from app.agents import runner
    from app.core.security import hash_password
    from app.db.models import Run, User
    from app.db.session import session_scope
    from app.synthetic import business

    password = os.environ.get("ABP_DEMO_PASSWORD")
    if not password or len(password) < 12:
        sys.exit("set ABP_DEMO_PASSWORD (12+ characters) for the demo users")
    with session_scope() as db:
        users = {}
        for name, role in DEMO_USERS.items():
            email = f"{name}@example.com"
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                u = User(email=email, full_name=name.title(), password_hash=hash_password(password), role=role)
                db.add(u)
                db.flush()
            users[name] = u
        ds = business.build()
        business.load(db, ds)
        db.commit()
        if db.scalar(select(Run.id).limit(1)) is None:
            seen = set()
            for case in ds.cases:
                key = f"{case['workflow']}:{case['kind']}"
                if key in DEMO_KINDS and key not in seen:
                    seen.add(key)
                    runner.create_run(db, case["workflow"], case["input"], case["title"], users["olivia"])
    print("demo loaded; sample runs queued")


def _evaluate(out: Path, reset: bool, gate: bool) -> None:
    from app.evaluation.run import evaluate, gate_failures, write_report

    if not reset:
        sys.exit("evaluate deletes all business data and runs; pass --reset to confirm (use a separate database)")
    report = evaluate()
    write_report(report, out)
    print(json.dumps({k: report[k] for k in ("normal", "chaos") if k in report}, indent=1, default=str)[:3000])
    if gate and (failures := gate_failures(report)):
        sys.exit("evaluation gate failed: " + "; ".join(failures))


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["admin", "approver", "operator", "viewer"], default="operator")
    sub.add_parser("load-demo")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../eval/reports"))
    ev.add_argument("--reset", action="store_true", help="confirm that all business data may be deleted")
    ev.add_argument("--gate", action="store_true", help="exit non-zero on any safety violation or accuracy drop")
    sub.add_parser("worker")
    sub.add_parser("drain")
    sub.add_parser("demo")
    args = p.parse_args(argv)
    try:
        _dispatch(args)
    finally:
        from app.agents import runner

        runner.close()  # release the checkpoint connection pool


def _dispatch(args: argparse.Namespace) -> None:
    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-user":
        _create_user(args.email, args.role)
    elif args.cmd == "load-demo":
        load_demo()
    elif args.cmd == "evaluate":
        _evaluate(args.out, args.reset, args.gate)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd in ("drain", "demo"):
        if args.cmd == "demo":
            _migrate()
            load_demo()
        from app.jobs.worker import drain

        print(f"processed {drain()} jobs")


if __name__ == "__main__":
    main()
