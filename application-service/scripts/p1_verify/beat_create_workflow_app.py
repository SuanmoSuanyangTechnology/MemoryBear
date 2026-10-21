#!/usr/bin/env python
"""Beat acceptance, part 1: create and publish a schedule-triggered workflow
through the application-service internal API with real commits.

The monolith beat scan (part 2, ``beat_scan_check.py``) runs in a separate
process and must pick this app up through the published release's
``current_release.config``, so nothing here is rolled back: the scratch
database accumulates these rows on purpose (drop the scratch database after
verification). The created ids are written to the ids file for part 2.

Environment (the same L4_* variables as the integration tests):
  L4_DB_HOST / L4_DB_PORT / L4_DB_USER / L4_DB_PASSWORD / L4_DB_NAME
  L4_REDIS_HOST / L4_REDIS_PORT / L4_REDIS_PASSWORD / L4_REDIS_DB (optional)

Run (application-service venv, from the enterprise repo root):
  L4_DB_NAME=app_split_p1_verify L4_DB_HOST=127.0.0.1 L4_DB_PORT=5434 \
  L4_DB_USER=<user> L4_DB_PASSWORD=<password> \
  uv run --project core/application-service python \
    core/application-service/scripts/p1_verify/beat_create_workflow_app.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.api.dependencies import (  # noqa: E402
    ACTOR_ID_HEADER,
    TENANT_ID_HEADER,
    WORKSPACE_ID_HEADER,
)
from src.config import AppServiceSettings  # noqa: E402
from src.main import create_app  # noqa: E402

TRIGGER_ID = "trigger"
DEFAULT_IDS_FILE = "/tmp/app_split_p1_verify_ids.json"

# interval_seconds without a last_triggered_at makes the trigger due on the
# first scan; the beat due-check only skips while a dispatch lease is held.
WORKFLOW_PAYLOAD = {
    "nodes": [
        {
            "id": TRIGGER_ID,
            "type": "trigger",
            "name": "beat acceptance schedule",
            "config": {
                "trigger_type": "schedule",
                "enabled": True,
                "interval_seconds": 60,
                "timezone": "UTC",
            },
        },
        {"id": "end", "type": "end", "name": "end"},
    ],
    "edges": [{"source": TRIGGER_ID, "target": "end"}],
    "workflow_type": "pure_workflow",
}


class StepFailed(Exception):
    """A step produced a response the acceptance flow cannot proceed from."""


def build_settings() -> AppServiceSettings:
    db_name = os.getenv("L4_DB_NAME")
    if not db_name:
        raise StepFailed("L4_DB_NAME is not set; point it at the scratch database")
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


def seed_identity(engine) -> dict:
    """Insert the tenancy chain the API writes reference (committed on exit)."""
    actor_id, tenant_id, workspace_id, member_id = (uuid.uuid4() for _ in range(4))
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": "beat acceptance tenant"},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, username, email, hashed_password, is_active,"
                " is_superuser, tenant_id) VALUES (:id, :username, :email, 'x', true,"
                " false, :tenant_id)"
            ),
            {
                "id": actor_id,
                "username": f"beat-{actor_id.hex[:12]}",
                "email": f"beat-{actor_id.hex[:12]}@example.com",
                "tenant_id": tenant_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO workspaces (id, name, tenant_id, storage_type)"
                " VALUES (:id, :name, :tenant_id, 'local')"
            ),
            {"id": workspace_id, "name": "beat acceptance space", "tenant_id": tenant_id},
        )
        # workspace_members.is_active is nullable without a default and member
        # lookups filter on it, so an explicit true is required.
        connection.execute(
            text(
                "INSERT INTO workspace_members (id, user_id, workspace_id, role, is_active)"
                " VALUES (:id, :user_id, :workspace_id, 'owner', true)"
            ),
            {"id": member_id, "user_id": actor_id, "workspace_id": workspace_id},
        )
    return {
        "actor_id": str(actor_id),
        "tenant_id": str(tenant_id),
        "workspace_id": str(workspace_id),
    }


def create_and_publish(client: TestClient, ids: dict) -> dict:
    headers = {
        ACTOR_ID_HEADER: ids["actor_id"],
        TENANT_ID_HEADER: ids["tenant_id"],
        WORKSPACE_ID_HEADER: ids["workspace_id"],
    }
    app_name = f"beat-acceptance-wf-{uuid.uuid4().hex[:8]}"

    created = client.post(
        "/internal/v1/apps", json={"name": app_name, "type": "pure_workflow"}, headers=headers
    )
    if created.status_code != 200 or created.json().get("code") != 0:
        raise StepFailed(f"app create failed: {created.status_code} {created.text[:300]}")
    app_id = created.json()["data"]["id"]

    workflow = client.put(
        f"/internal/v1/apps/{app_id}/workflow", json=WORKFLOW_PAYLOAD, headers=headers
    )
    if workflow.status_code != 200 or workflow.json().get("code") != 0:
        raise StepFailed(f"workflow PUT failed: {workflow.status_code} {workflow.text[:300]}")

    published = client.post(
        f"/internal/v1/apps/{app_id}/publish",
        json={"version_name": "v1", "release_notes": "beat acceptance", "name": app_name},
        headers=headers,
    )
    if published.status_code != 200 or published.json().get("code") != 0:
        raise StepFailed(f"publish failed: {published.status_code} {published.text[:300]}")
    release_id = published.json()["data"]["id"]

    return {"app_id": app_id, "release_id": release_id, "app_name": app_name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ids-file",
        default=os.environ.get("P1_VERIFY_IDS_FILE", DEFAULT_IDS_FILE),
        help="where to write the created ids for part 2",
    )
    args = parser.parse_args()

    try:
        settings = build_settings()
    except StepFailed as exc:
        print(f"[1/3] FAIL settings: {exc}")
        return 1

    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    try:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception as exc:
            print(f"[1/3] FAIL scratch database unreachable: {type(exc).__name__}: {exc}")
            return 1
        print(f"[1/3] OK   scratch database reachable ({settings.db_host}:{settings.db_port})")

        try:
            ids = seed_identity(engine)
        except Exception as exc:
            print(f"[2/3] FAIL identity seed: {type(exc).__name__}: {exc}")
            return 1
        print(f"[2/3] OK   identity seeded (tenant={ids['tenant_id'][:8]}...)")

        client = TestClient(create_app(settings), raise_server_exceptions=False)
        try:
            created = create_and_publish(client, ids)
        except StepFailed as exc:
            print(f"[3/3] FAIL create+publish: {exc}")
            return 1
        print(
            f"[3/3] OK   created+publish: app={created['app_id']} "
            f"release={created['release_id']} trigger={TRIGGER_ID}"
        )
    finally:
        engine.dispose()

    payload = {**ids, **created, "trigger_id": TRIGGER_ID}
    Path(args.ids_file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[ids] written to {args.ids_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
