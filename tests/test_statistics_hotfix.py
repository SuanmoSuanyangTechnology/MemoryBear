"""Statistics boundary and audit regressions; optional PostgreSQL test uses temp tables only."""
import asyncio
import importlib.util
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import column, func, select, table
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location("statistics_test_module", ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["api/app/core/utils/statistics_window.py", "application-service/src/utils/statistics_window.py"])
def window(request):
    return load(request.param)


def test_utc_boundaries_and_daily_zero_fill(window):
    start, end = window.utc_window(1794326400000, 1794931199999)
    assert start == datetime(2026, 11, 10, 16)
    assert end == datetime(2026, 11, 17, 16)
    assert start <= datetime(2026, 11, 10, 17) < end
    assert not start <= datetime(2026, 11, 18, 2) < end
    rows = window.fill_days([{"date": "2026-11-17", "count": 3}], start, end, "Asia/Shanghai")
    assert len(rows) == 7
    assert rows[0] == {"date": "2026-11-11", "count": 0}
    assert rows[-1] == {"date": "2026-11-17", "count": 3}
    with pytest.raises(ValueError):
        window.utc_window(2, 1)


def test_redis_audit_fields_are_serializable():
    publisher = load("packages/auth-sdk/src/auth_sdk/audit.py")
    class Redis:
        async def xadd(self, key, payload, **kwargs):
            assert all(isinstance(v, (str, bytes, int, float)) for v in payload.values())
            assert json.loads(payload["detail"]) == {"api_key_id": "key"}
    asyncio.run(publisher.AuditLogger(Redis()).audit(SimpleNamespace(
        event_id=None, event_type="api_key.request", actor_id="actor", tenant_id="tenant",
        target="POST /v1/app/chat", result="ok", detail={"api_key_id": "key"}, ts=None,
    )))


def test_postgres_usage_redelivery_and_local_date(window):
    config_file = os.getenv("STATISTICS_TEST_ENV")
    if not config_file:
        pytest.skip("Set STATISTICS_TEST_ENV for PostgreSQL integration coverage")
    import psycopg2
    from dotenv import dotenv_values
    c = dotenv_values(config_file)
    db = psycopg2.connect(host=c["DB_HOST"], port=c["DB_PORT"], dbname=c["DB_NAME"],
                          user=c["DB_USER"], password=c["DB_PASSWORD"], connect_timeout=4)
    try:
        cur = db.cursor()
        cur.execute("CREATE TEMP TABLE api_keys (id uuid PRIMARY KEY)")
        cur.execute("CREATE TEMP TABLE api_key_logs (id uuid PRIMARY KEY, api_key_id uuid, endpoint text, method text, status_code integer, response_time integer, created_at timestamp)")
        cur.execute("CREATE TEMP TABLE audit_logs (event_type text, actor_id text, tenant_id text, target text, result text, detail jsonb, ts timestamptz, event_id uuid UNIQUE)")
        key = str(uuid.uuid4())
        cur.execute("INSERT INTO api_keys VALUES (%s)", (key,))
        class Session:
            async def execute(self, sql, params):
                cur.execute(str(sql).replace(":rows", "%(rows)s"), params)
            async def commit(self):
                pass  # The test always rolls back, including all temporary DDL.
        item = {"event_type": "api_key.request", "actor_id": None, "tenant_id": None,
                "target": "POST /v1/app/chat", "result": "error", "event_id": str(uuid.uuid4()),
                "ts": "2026-11-16T17:00:00+00:00", "detail": json.dumps({"api_key_id": key,
                "endpoint": "/v1/app/chat", "method": "POST", "status_code": 500, "response_time": 12})}
        repo = load("identity-service/src/repositories/audit.py")
        asyncio.run(repo.insert_audit_logs(Session(), [item]))
        asyncio.run(repo.insert_audit_logs(Session(), [item]))
        cur.execute("SELECT count(*), min(status_code) FROM api_key_logs")
        assert cur.fetchone() == (1, 500)
        bucket = window.day_bucket(column("created_at"), "Asia/Shanghai")
        query = select(bucket, func.count()).select_from(table("api_key_logs")).group_by(bucket)
        compiled = query.compile(dialect=postgresql.dialect())
        cur.execute(str(compiled), compiled.params)
        day, count = cur.fetchone()
        assert str(day) == "2026-11-17" and count == 1
    finally:
        db.rollback()
        db.close()
