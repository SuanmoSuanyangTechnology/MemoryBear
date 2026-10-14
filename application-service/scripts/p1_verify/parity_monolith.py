#!/usr/bin/env python
"""Parity battery, monolith side: run ``parity_common.run_scenario`` against
the core FastAPI app (``/api`` manager router) on the scratch database.

Harness notes:
  * Every request session is joined to one outer transaction through
    savepoints, so the scenario's commits roll back at the end and the
    scratch database keeps no rows (only the shared identity seed stays).
  * ``get_db`` and ``get_current_user`` are overridden with savepoint-bound
    versions; the quota lookup is stubbed because the community fallback
    free plan would cap the scenario at two apps.
  * All battery endpoints are definition-plane handlers that take the sync
    session via Depends(get_db); the async ``get_async_db_context`` call
    sites in the services sit on runtime chat/file paths outside this
    scenario.

Environment (point the monolith at the scratch stack):
  DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME (required)
  REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB (cache, default 0)

Run (from the enterprise repo root):
  DB_HOST=127.0.0.1 DB_PORT=5434 DB_USER=<user> DB_PASSWORD=<password> \
  DB_NAME=app_split_p1_verify REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
  REDIS_PASSWORD="" REDIS_DB=0 \
  uv run python core/application-service/scripts/p1_verify/parity_monolith.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parent))

if not os.getenv("DB_NAME"):
    print(
        "[parity] DB_NAME is not set; refusing to fall back to the default database",
        file=sys.stderr,
    )
    raise SystemExit(2)

sys.path.insert(0, str(REPO_ROOT / "core/api"))

import parity_common  # noqa: E402
from app.db import get_db  # noqa: E402
from app.dependencies import get_current_user  # noqa: E402
from app.main import app as monolith_app  # noqa: E402
from app.models.user_model import User  # noqa: E402
from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

DEFAULT_OUT = "/tmp/app_split_p1_parity_monolith.json"
PREFIX = "/api"


def db_url() -> str:
    from urllib.parse import quote

    user = quote(os.getenv("DB_USER", "postgres"))
    password = quote(os.getenv("DB_PASSWORD", ""))
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def _convert(value):
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_convert(item) for item in value]
    if isinstance(value, dict):
        return {k: _convert(v) for k, v in value.items()}
    return value


class MonolithRunner:
    def __init__(self, client: TestClient, connection):
        self.client = client
        self.connection = connection

    def request(self, method, path, json_body=None, params=None):
        response = self.client.request(
            method, PREFIX + path, json=json_body, params=params
        )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body

    def fetch_row(self, sql, **params):
        row = self.connection.execute(text(sql), params).first()
        if row is None:
            return None
        return tuple(_convert(value) for value in row)


def patch_quota() -> None:
    import app.core.quota_manager as quota_manager

    quota_manager._get_quota_config = lambda db, tenant_id: None  # type: ignore[assignment]
    quota_manager._get_quota_config_async = (  # type: ignore[assignment]
        lambda db, tenant_id: None
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=os.environ.get("P1_PARITY_OUT", DEFAULT_OUT),
        help="where to write the monolith recording",
    )
    args = parser.parse_args()

    engine = create_engine(db_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            target = connection.execute(text("SELECT current_database()")).scalar()
            if target != os.getenv("DB_NAME"):
                print(
                    f"[parity] connected database {target!r} != DB_NAME "
                    f"{os.getenv('DB_NAME')!r}; refusing to run",
                    file=sys.stderr,
                )
                return 2
            print(f"[parity] monolith target database: {target}")

        with engine.begin() as connection:
            for sql, params in parity_common.SEED_STATEMENTS:
                connection.execute(text(sql), params)

        connection = engine.connect()
        outer = connection.begin()

        def override_get_db():
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                session.close()

        def override_get_current_user(db: Annotated[Session, Depends(get_db)]):
            return db.get(User, uuid.UUID(parity_common.SEED["actor_id"]))

        patch_quota()
        client = TestClient(monolith_app, raise_server_exceptions=False)
        monolith_app.dependency_overrides[get_db] = override_get_db
        monolith_app.dependency_overrides[get_current_user] = override_get_current_user
        try:
            steps = parity_common.run_scenario(MonolithRunner(client, connection))
        finally:
            monolith_app.dependency_overrides.clear()
            outer.rollback()
            connection.close()
    finally:
        engine.dispose()

    payload = {
        "side": "monolith",
        "database": os.getenv("DB_NAME"),
        "steps": steps,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    failures = [
        step["step"]
        for step in steps
        if step["status"] is not None and step["status"] != 200
    ]
    print(f"[parity] monolith recorded {len(steps)} steps -> {args.out}")
    if failures:
        print(f"[parity] non-200 steps (kept as evidence): {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
