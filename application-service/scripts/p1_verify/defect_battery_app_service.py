#!/usr/bin/env python
"""Defect battery, application-service side: run
``defect_common.run_defect_scenario`` against the application-service
internal API on the scratch database.

Same harness discipline as ``parity_app_service.py``: every request session
joins one outer transaction (rolled back at the end), auth is the internal
header triple, and the quota lookup is stubbed.

Environment (the same L4_* variables as the integration tests):
  L4_DB_HOST / L4_DB_PORT / L4_DB_USER / L4_DB_PASSWORD / L4_DB_NAME
  L4_REDIS_HOST / L4_REDIS_PORT / L4_REDIS_PASSWORD / L4_REDIS_DB (optional)

Run (application-service venv, from the enterprise repo root):
  L4_DB_NAME=app_split_p1_verify L4_DB_HOST=127.0.0.1 L4_DB_PORT=5434 \
  L4_DB_USER=<user> L4_DB_PASSWORD=<password> \
  uv run --project core/application-service python \
    core/application-service/scripts/p1_verify/defect_battery_app_service.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import defect_common  # noqa: E402
import parity_common  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.api.dependencies import (  # noqa: E402
    ACTOR_ID_HEADER,
    TENANT_ID_HEADER,
    WORKSPACE_ID_HEADER,
    get_sync_db,
)
from src.config import AppServiceSettings  # noqa: E402
from src.main import create_app  # noqa: E402
from src.quota import usage as quota_usage  # noqa: E402

DEFAULT_OUT = "/tmp/app_split_defect_app_service.json"
PREFIX = "/internal/v1"


def build_settings() -> AppServiceSettings:
    db_name = os.getenv("L4_DB_NAME")
    if not db_name:
        raise SystemExit(
            "[defect] L4_DB_NAME is not set; point it at the scratch database"
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


class AppServiceRunner:
    def __init__(self, client: TestClient, connection, headers: dict):
        self.client = client
        self.connection = connection
        self.headers = headers

    def request(self, method, path, json_body=None, params=None):
        response = self.client.request(
            method, PREFIX + path, json=json_body, params=params, headers=self.headers
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

    def insert(self, sql, **params):
        self.connection.execute(text(sql), params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=os.environ.get("P1_DEFECT_OUT", DEFAULT_OUT),
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
                    f"[defect] connected database {target!r} != L4_DB_NAME "
                    f"{settings.db_name!r}; refusing to run",
                    file=sys.stderr,
                )
                return 2
            print(f"[defect] application-service target database: {target}")

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
                session.close()

        quota_usage.get_quota_config = (  # type: ignore[assignment]
            lambda _db, _tenant_id: {"app_quota": 100}
        )

        application = create_app(settings)
        client = TestClient(application, raise_server_exceptions=False)
        application.dependency_overrides[get_sync_db] = override_get_sync_db
        headers = {
            ACTOR_ID_HEADER: parity_common.SEED["actor_id"],
            TENANT_ID_HEADER: parity_common.SEED["tenant_id"],
            WORKSPACE_ID_HEADER: parity_common.SEED["workspace_id"],
        }
        try:
            steps = defect_common.run_defect_scenario(
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
        if step["status"] is not None and step["status"] not in (200, 400, 403)
    ]
    print(f"[defect] application-service recorded {len(steps)} steps -> {args.out}")
    if failures:
        print(f"[defect] unexpected statuses (kept as evidence): {failures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
