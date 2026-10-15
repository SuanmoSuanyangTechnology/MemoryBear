#!/usr/bin/env python
"""Parity battery, application-service side: run ``parity_common.run_scenario``
against the application-service internal API on the scratch database.

Harness notes:
  * Same savepoint discipline as the monolith side: every request session
    joins one outer transaction, which rolls back at the end (only the
    shared identity seed stays in the scratch database).
  * Session plumbing: ``get_sync_db`` is dependency-overridden and every
    module attribute bound to ``src.db.get_db_context`` / ``get_db_read`` /
    ``get_async_db_context`` is swapped for a savepoint factory, because the
    P3 handlers open sessions inside their bodies (some of them async);
    async handlers receive a sync Session behind an awaitable adapter
    (``ScenarioAsyncSession``, ``run_sync`` included) so their commits stay
    inside the rollback boundary.
  * Auth is the internal-API header triple (the gateway contract); v1 steps
    add the API-key binding id header, public steps carry none. The quota
    lookup is stubbed the same way the L4 suite does it (the default free
    plan would cap the scenario at two apps).

Environment (the same L4_* variables as the integration tests):
  L4_DB_HOST / L4_DB_PORT / L4_DB_USER / L4_DB_PASSWORD / L4_DB_NAME
  L4_REDIS_HOST / L4_REDIS_PORT / L4_REDIS_PASSWORD / L4_REDIS_DB (optional)

Run (application-service venv, from the enterprise repo root):
  L4_DB_NAME=app_split_p1_verify L4_DB_HOST=127.0.0.1 L4_DB_PORT=5434 \
  L4_DB_USER=<user> L4_DB_PASSWORD=<password> \
  uv run --project core/application-service python \
    core/application-service/scripts/p1_verify/parity_app_service.py
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

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Pin the share-URL base so both recordings agree regardless of a local .env.
os.environ["FILE_LOCAL_SERVER_URL"] = "http://parity.local"

import parity_common  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import src.db as src_db  # noqa: E402
from src.api.dependencies import (  # noqa: E402
    ACTOR_ID_HEADER,
    API_KEY_ID_HEADER,
    TENANT_ID_HEADER,
    WORKSPACE_ID_HEADER,
    get_sync_db,
)
from src.config import AppServiceSettings  # noqa: E402
from src.main import create_app  # noqa: E402
from src.quota import usage as quota_usage  # noqa: E402

DEFAULT_OUT = "/tmp/app_split_p1_parity_app_service.json"
PREFIX = "/internal/v1"
MODULE_NAME = Path(__file__).resolve().stem


def build_settings() -> AppServiceSettings:
    db_name = os.getenv("L4_DB_NAME")
    if not db_name:
        raise SystemExit(
            "[parity] L4_DB_NAME is not set; point it at the scratch database"
        )
    return AppServiceSettings(
        db_host=os.getenv("L4_DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("L4_DB_PORT", "5432")),
        db_user=os.getenv("L4_DB_USER", "postgres"),
        db_password=os.getenv("L4_DB_PASSWORD", ""),
        db_name=db_name,
        redis_host=os.getenv("L4_REDIS_HOST", "127.0.0.1"),
        redis_port=int(os.getenv("L4_REDIS_PORT", "6379")),
        redis_password=os.getenv("L4_REDIS_PASSWORD", ""),
        redis_db=int(os.getenv("L4_REDIS_DB", "0")),
        # The env-file bootstrap is bypassed by this direct construction, so
        # the share-URL base must be threaded through explicitly (the module
        # top pins the same value both runners agree on).
        file_local_server_url=os.environ["FILE_LOCAL_SERVER_URL"],
    )


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


def release_clean_session(session: Session) -> None:
    """Close a scenario session without destroying later savepoints.

    Every harness session joins the scenario transaction through a savepoint.
    A session that only read (never committed) still holds that savepoint
    open, and the plain ``close()`` path issues ROLLBACK TO it - wiping every
    savepoint or direct write other sessions made after it. Releasing a clean
    session's savepoint first keeps teardown a state no-op; sessions with
    uncommitted work still roll their own savepoint back as before.
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

    Async handlers expect an AsyncSession, but every session in the battery
    must join one outer connection so commits roll back and all handler
    layers observe the same uncommitted state. The awaited methods below are
    sync wrappers; ``run_sync`` hands the sync Session to the wrapped
    callable (``AsyncSession.run_sync`` semantics), and non-awaited calls
    (``add`` and friends) fall through to the sync Session unchanged.
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
    object across ``sys.modules`` (including ``src.db`` itself) covers both
    forms, and modules imported later read the patched ``src.db`` attributes.
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


class AppServiceRunner:
    """Runner facade for the internal API.

    Every face maps onto the ``/internal/v1`` prefix (the gateway strips the
    host ``/api`` or ``/v1`` before forwarding): ``manager`` and ``v1`` send
    the internal identity headers, ``v1`` additionally sends the API-key
    binding id (the split resolves the app through the binding row), and
    ``public`` sends no credentials at all (anonymous share view).
    """

    def __init__(self, client: TestClient, connection, headers: dict):
        self.client = client
        self.connection = connection
        self.headers = headers

    def request(self, method, path, json_body=None, params=None, face="manager",
                files=None, headers=None):
        if face == "public":
            request_headers = dict(headers or {})
        else:
            request_headers = dict(self.headers)
            request_headers.update(headers or {})
            if face == "v1":
                request_headers.setdefault(API_KEY_ID_HEADER, parity_common.API_KEY["id"])
        if files is not None:
            response = self.client.request(
                method, PREFIX + path, files=files, params=params,
                headers=request_headers,
            )
        else:
            response = self.client.request(
                method, PREFIX + path, json=json_body, params=params,
                headers=request_headers,
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

    def exec_sql(self, sql, **params):
        return self.fetch_row(sql, **params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=os.environ.get("P1_PARITY_OUT", DEFAULT_OUT),
        help="where to write the application-service recording",
    )
    args = parser.parse_args()

    settings = build_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            target = connection.execute(text("SELECT current_database()")).scalar()
            if target != settings.db_name:
                print(
                    f"[parity] connected database {target!r} != L4_DB_NAME "
                    f"{settings.db_name!r}; refusing to run",
                    file=sys.stderr,
                )
                return 2
            print(f"[parity] application-service target database: {target}")

        with engine.begin() as connection:
            for sql, params in parity_common.SEED_STATEMENTS:
                connection.execute(text(sql), params)

        connection = engine.connect()
        outer = connection.begin()

        def override_get_sync_db():
            session = Session(bind=connection, join_transaction_mode="create_savepoint")
            try:
                yield session
            finally:
                release_clean_session(session)

        quota_usage.get_quota_config = (  # type: ignore[assignment]
            lambda _db, _tenant_id: {"app_quota": 100}
        )
        patched_attributes = sweep_module_attributes(
            {
                src_db.get_db_context: make_scenario_context(connection),
                src_db.get_db_read: make_scenario_context(
                    connection, expire_on_commit=False
                ),
                src_db.get_async_db_context: lambda: ScenarioAsyncDbContext(connection),
            },
            skip_module=MODULE_NAME,
        )
        print(f"[parity] patched {len(patched_attributes)} session-factory attributes")

        application = create_app(settings)
        client = TestClient(application, raise_server_exceptions=False)
        application.dependency_overrides[get_sync_db] = override_get_sync_db
        headers = {
            ACTOR_ID_HEADER: parity_common.SEED["actor_id"],
            TENANT_ID_HEADER: parity_common.SEED["tenant_id"],
            WORKSPACE_ID_HEADER: parity_common.SEED["workspace_id"],
        }
        try:
            steps = parity_common.run_scenario(
                AppServiceRunner(client, connection, headers)
            )
        finally:
            application.dependency_overrides.clear()
            outer.rollback()
            connection.close()
    finally:
        engine.dispose()

    payload = {
        "side": "application-service",
        "database": settings.db_name,
        "steps": steps,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    failures = [
        step["step"]
        for step in steps
        if step["status"] is not None and step["status"] != 200
    ]
    print(f"[parity] application-service recorded {len(steps)} steps -> {args.out}")
    if failures:
        print(f"[parity] non-200 steps (kept as evidence): {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
