#!/usr/bin/env python
"""Parity battery, monolith side: run ``parity_common.run_scenario`` against
the core FastAPI app (``/api`` user-JWT plane and ``/v1`` API-key plane) on
the scratch database.

Harness notes:
  * Every request session is joined to one outer transaction through
    savepoints, so the scenario's commits roll back at the end and the
    scratch database keeps no rows (only the shared identity seed stays).
  * Session plumbing: ``get_db`` / ``get_async_db`` are dependency-overridden
    and every module attribute bound to ``get_db_context`` /
    ``get_async_db_context`` / ``get_db_read`` is swapped for a savepoint
    factory, because the P2 handlers open sessions inside their bodies (some
    of them async). Async handlers receive a sync Session behind an awaitable
    adapter (``ScenarioAsyncSession``) so their commits stay inside the
    rollback boundary.
  * The v1 plane runs its real API-key chain against the ``api_keys`` row the
    scenario seeds inside the transaction (type pure_workflow, scopes
    app+memory, rate limiting disabled): the runner sends the seeded key
    value, ``ApiKeyAuthService`` validates it, and the app-service resolves
    the same row through the binding id. Only ``get_current_user_async``
    (JWT) is overridden, and the quota lookup is stubbed because the
    community fallback free plan would cap the scenario at two apps.

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
from contextlib import contextmanager
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

# Pin the share-URL base ahead of app imports so both recordings agree
# regardless of a local .env (dotenv loads with override disabled).
os.environ["FILE_LOCAL_SERVER_URL"] = "http://parity.local"

sys.path.insert(0, str(REPO_ROOT / "core/api"))

import app.db as app_db  # noqa: E402
import parity_common  # noqa: E402
from app.db import get_async_db, get_db  # noqa: E402
from app.dependencies import (  # noqa: E402
    CurrentUserSnapshot,
    get_current_user,
    get_current_user_async,
)
from app.main import app as monolith_app  # noqa: E402
from app.models.user_model import User  # noqa: E402
from fastapi import Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

DEFAULT_OUT = "/tmp/app_split_p1_parity_monolith.json"
PREFIX_MANAGER = "/api"
PREFIX_V1 = "/v1"
MODULE_NAME = Path(__file__).resolve().stem


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


def _first_row(connection, sql: str, params: dict):
    row = connection.execute(text(sql), params).first()
    if row is None:
        return None
    return tuple(_convert(value) for value in row)


def release_clean_session(session: Session) -> None:
    """Close a scenario session without destroying later savepoints.

    Every harness session joins the scenario transaction through a savepoint.
    A session that only read (never committed) still holds that savepoint
    open, and the plain ``close()`` path issues ROLLBACK TO it - wiping every
    savepoint or direct write other sessions made after it (the monolith's
    end-user create is one handler that writes through a second, body-local
    session while a read-only dependency session outlives it). Releasing a
    clean session's savepoint first keeps teardown a state no-op; sessions
    with uncommitted work still roll their own savepoint back as before.
    """

    try:
        if session.in_transaction() and not (
            session.new or session.dirty or session.deleted
        ):
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


class ScenarioAsyncSession:
    """Awaitable facade over a savepoint-bound sync Session.

    The monolith's async handlers expect an AsyncSession, but every session in
    the battery must join one outer connection so commits roll back and all
    handler layers observe the same uncommitted state. The awaited methods
    below are thin sync wrappers; non-awaited calls (``add`` and friends) fall
    through to the sync Session unchanged.
    """

    def __init__(self, session: Session):
        self._session = session

    async def execute(self, *args, **kwargs):
        return self._session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self._session.scalar(*args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return self._session.scalars(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return self._session.get(*args, **kwargs)

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def flush(self, *args, **kwargs):
        return self._session.flush(*args, **kwargs)

    async def refresh(self, *args, **kwargs):
        self._session.refresh(*args, **kwargs)

    async def delete(self, *args, **kwargs):
        return self._session.delete(*args, **kwargs)

    async def run_sync(self, fn, *args, **kwargs):
        return fn(self._session, *args, **kwargs)

    async def close(self):
        release_clean_session(self._session)

    def __getattr__(self, name):
        return getattr(self._session, name)


class ScenarioAsyncDbContext:
    """async-with twin of ``get_async_db_context`` on the scenario connection."""

    def __init__(self, connection):
        self._connection = connection
        self._session: Session | None = None

    async def __aenter__(self):
        self._session = Session(
            bind=self._connection, join_transaction_mode="create_savepoint"
        )
        return ScenarioAsyncSession(self._session)

    async def __aexit__(self, *exc_info):
        if self._session is not None:
            release_clean_session(self._session)
        return False


def make_scenario_context(connection, expire_on_commit: bool = True):
    @contextmanager
    def scenario_context():
        session = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=expire_on_commit,
        )
        try:
            yield session
        finally:
            release_clean_session(session)

    return scenario_context


def sweep_module_attributes(replacements: dict, skip_module: str) -> list[str]:
    """Swap every loaded module attribute whose value is one of the keys.

    Handlers reference the session factories both as module-level imports and
    as call-time imports inside function bodies; replacing the attribute
    object across ``sys.modules`` (including ``app.db`` itself) covers both
    forms, and modules imported later read the patched ``app.db`` attributes.
    """
    patched = []
    for module in list(sys.modules.values()):
        if module is None or getattr(module, "__name__", None) == skip_module:
            continue
        try:
            namespace = vars(module)
        except TypeError:
            continue
        for name, value in list(namespace.items()):
            for original, replacement in replacements.items():
                if value is original:
                    setattr(module, name, replacement)
                    patched.append(f"{module.__name__}.{name}")
    return patched


class MonolithRunner:
    """Runner facade for the monolith's manager and v1 planes.

    ``manager`` targets ``/api`` (identity comes from the dependency override)
    and ``v1`` targets ``/v1``, sending the seeded API-key value so the real
    validator resolves the same binding row the application-service reads.
    """

    def __init__(self, client: TestClient, connection):
        self.client = client
        self.connection = connection

    def request(self, method, path, json_body=None, params=None, face="manager",
                files=None, headers=None):
        prefix = PREFIX_V1 if face == "v1" else PREFIX_MANAGER
        request_headers = dict(headers or {})
        if face == "v1":
            request_headers.setdefault("X-API-Key", parity_common.API_KEY["value"])
        if files is not None:
            response = self.client.request(
                method, prefix + path, files=files, params=params,
                headers=request_headers,
            )
        else:
            response = self.client.request(
                method, prefix + path, json=json_body, params=params,
                headers=request_headers,
            )
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body

    def fetch_row(self, sql, **params):
        return _first_row(self.connection, sql, params)

    def exec_sql(self, sql, **params):
        return _first_row(self.connection, sql, params)


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
                release_clean_session(session)

        async def override_get_async_db():
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield ScenarioAsyncSession(session)
            finally:
                release_clean_session(session)

        def override_get_current_user(db: Annotated[Session, Depends(get_db)]):
            return db.get(User, uuid.UUID(parity_common.SEED["actor_id"]))

        async def override_get_current_user_async() -> CurrentUserSnapshot:
            return CurrentUserSnapshot(
                id=uuid.UUID(parity_common.SEED["actor_id"]),
                username="parity-user",
                email="parity-user@example.com",
                is_active=True,
                is_superuser=False,
                current_workspace_id=uuid.UUID(parity_common.SEED["workspace_id"]),
                tenant_id=uuid.UUID(parity_common.SEED["tenant_id"]),
            )

        patch_quota()
        patched_attributes = sweep_module_attributes(
            {
                app_db.get_db_context: make_scenario_context(connection),
                app_db.get_db_read: make_scenario_context(
                    connection, expire_on_commit=False
                ),
                app_db.get_async_db_context: lambda: ScenarioAsyncDbContext(connection),
            },
            skip_module=MODULE_NAME,
        )
        print(f"[parity] patched {len(patched_attributes)} session-factory attributes")

        client = TestClient(monolith_app, raise_server_exceptions=False)
        monolith_app.dependency_overrides[get_db] = override_get_db
        monolith_app.dependency_overrides[get_async_db] = override_get_async_db
        monolith_app.dependency_overrides[get_current_user] = override_get_current_user
        monolith_app.dependency_overrides[get_current_user_async] = (
            override_get_current_user_async
        )
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
