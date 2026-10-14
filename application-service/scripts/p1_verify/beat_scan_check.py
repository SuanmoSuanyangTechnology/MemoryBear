#!/usr/bin/env python
"""Beat acceptance, part 2: run the monolith beat scan against the workflow app
created by ``beat_create_workflow_app.py`` and verify the acceptance evidence.

This runs in the MONOLITH venv (the celery app lives in ``core/api``) and calls
the real registered task ``app.tasks.scan_workflow_schedule_triggers``, so the
scan predicates, the published-release read path and the broker enqueue are the
production code, not a re-implementation.

Checks:
  1. the created app is visible to the scan predicates (active, current release
     set, type workflow/pure_workflow) and its release config carries the
     schedule trigger
  2. ``scan_workflow_schedule_triggers()`` reports at least one triggered app
  3. the release config trigger runtime is written back (dispatch_status=queued,
     last_dispatched_at/last_scheduled_at set, last_error cleared)
  4. the workflow_configs trigger runtime is written back the same way
  5. a stale shared workflow-config cache key is invalidated by the scan
  6. the broker queue workflow_trigger_tasks carries a
     run_workflow_schedule_trigger message for this app/release/trigger

Environment (point every monolith dependency at the scratch stack):
  DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME
  REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB (cache, default 0 here)
  REDIS_DB_CELERY_BROKER (broker queue db, default 3)
  PYTHONPATH=core/api

Run (from the enterprise repo root):
  DB_HOST=127.0.0.1 DB_PORT=5434 DB_USER=<user> DB_PASSWORD=<password> \
  DB_NAME=app_split_p1_verify REDIS_HOST=127.0.0.1 REDIS_PORT=6379 \
  REDIS_PASSWORD="" REDIS_DB=0 PYTHONPATH=core/api \
  uv run python core/application-service/scripts/p1_verify/beat_scan_check.py
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import redis as redis_lib
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "core/api"))

DEFAULT_IDS_FILE = "/tmp/app_split_p1_verify_ids.json"
QUEUE_NAME = "workflow_trigger_tasks"
TASK_NAME = "app.tasks.run_workflow_schedule_trigger"


class CheckFailed(Exception):
    """An acceptance check failed; the message states the observed evidence."""


def db_url() -> str:
    user = quote(os.getenv("DB_USER", "postgres"))
    password = quote(os.getenv("DB_PASSWORD", ""))
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "redbear-mem")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def load_ids(path: str) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CheckFailed(f"ids file {path} not found; run part 1 first") from None
    missing = [k for k in ("app_id", "release_id", "trigger_id") if not payload.get(k)]
    if missing:
        raise CheckFailed(f"ids file {path} is missing keys: {missing}")
    return payload


def find_trigger_node(nodes: list | None, trigger_id: str) -> dict | None:
    for node in nodes or []:
        if node.get("id") == trigger_id and node.get("type") == "trigger":
            return node
    return None


def assert_due_predicate(engine, ids: dict) -> str:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT is_active, current_release_id, type FROM apps WHERE id = :id"
            ),
            {"id": uuid.UUID(ids["app_id"])},
        ).first()
        if row is None:
            raise CheckFailed(f"app {ids['app_id']} not found in the scratch database")
        is_active, current_release_id, app_type = row
        if not is_active or app_type not in ("workflow", "pure_workflow"):
            raise CheckFailed(
                f"app does not match scan predicates: is_active={is_active}, type={app_type!r}"
            )
        if current_release_id is None or str(current_release_id) != ids["release_id"]:
            raise CheckFailed(
                f"app.current_release_id={current_release_id} does not match the "
                f"published release {ids['release_id']}"
            )

        config = connection.execute(
            text("SELECT config FROM app_releases WHERE id = :id"),
            {"id": uuid.UUID(ids["release_id"])},
        ).scalar()
        node = find_trigger_node((config or {}).get("nodes"), ids["trigger_id"])
        if node is None:
            raise CheckFailed(
                f"release {ids['release_id']} config has no trigger node {ids['trigger_id']!r}"
            )
        node_config = node.get("config") or {}
        if node_config.get("trigger_type") != "schedule" or not node_config.get("enabled", True):
            raise CheckFailed(f"release trigger node is not an enabled schedule: {node_config}")
    return (
        f"app active/{app_type} with current release carrying schedule trigger "
        f"(interval_seconds={node_config.get('interval_seconds')})"
    )


def run_scan() -> dict:
    from app.tasks import scan_workflow_schedule_triggers

    result = scan_workflow_schedule_triggers()
    if not isinstance(result, dict):
        raise CheckFailed(f"scan returned an unexpected value: {result!r}")
    if not result.get("triggered"):
        raise CheckFailed(
            "scan triggered nothing - either the app was not due or the scan did "
            f"not see it (result={result!r})"
        )
    return result


def check_release_runtime(engine, ids: dict) -> str:
    with engine.connect() as connection:
        config = connection.execute(
            text("SELECT config FROM app_releases WHERE id = :id"),
            {"id": uuid.UUID(ids["release_id"])},
        ).scalar()
    node = find_trigger_node((config or {}).get("nodes"), ids["trigger_id"])
    runtime = (node or {}).get("runtime") or {}
    if runtime.get("dispatch_status") != "queued":
        raise CheckFailed(f"release trigger runtime not queued: {runtime}")
    if not runtime.get("last_dispatched_at") or not runtime.get("last_scheduled_at"):
        raise CheckFailed(f"release trigger runtime misses dispatch timestamps: {runtime}")
    if runtime.get("last_error") is not None:
        raise CheckFailed(f"release trigger runtime carries last_error: {runtime}")
    return f"dispatch_status=queued, last_dispatched_at={runtime['last_dispatched_at']}"


def check_workflow_config_runtime(engine, ids: dict) -> str:
    with engine.connect() as connection:
        nodes = connection.execute(
            text("SELECT nodes FROM workflow_configs WHERE app_id = :id"),
            {"id": uuid.UUID(ids["app_id"])},
        ).scalar()
        if nodes is None:
            raise CheckFailed(f"workflow_configs row for app {ids['app_id']} not found")
    node = find_trigger_node(nodes, ids["trigger_id"])
    runtime = (node or {}).get("runtime") or {}
    if runtime.get("dispatch_status") != "queued":
        raise CheckFailed(f"workflow_configs trigger runtime not queued: {runtime}")
    return f"dispatch_status=queued, last_scheduled_at={runtime.get('last_scheduled_at')}"


def cache_key(app_id: str) -> str:
    # The host builds this key in app.utils.redis_cache.workflow_config_key.
    return f"cache:workflow-config:v1:{app_id}"


def check_cache_invalidated(redis_cache: redis_lib.Redis, ids: dict) -> str:
    if redis_cache.exists(cache_key(ids["app_id"])):
        raise CheckFailed(
            f"stale cache key {cache_key(ids['app_id'])} survived the scan - "
            "the runtime write-back did not invalidate the shared workflow-config key"
        )
    return "stale cache key deleted by the scan write-back"


def check_broker_message(ids: dict) -> str:
    broker_db = int(os.getenv("REDIS_DB_CELERY_BROKER", "3"))
    broker = redis_lib.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=broker_db,
    )
    try:
        raw_messages = broker.lrange(QUEUE_NAME, 0, -1)
    finally:
        broker.close()
    if not raw_messages:
        raise CheckFailed(f"broker queue {QUEUE_NAME!r} (redis db {broker_db}) is empty")

    decoded_errors: list[str] = []
    for raw in raw_messages:
        try:
            envelope = json.loads(raw)
            headers = envelope.get("headers") or {}
            if headers.get("task") != TASK_NAME:
                continue
            body = json.loads(base64.b64decode(envelope["body"]))
            kwargs = body[1] if len(body) > 1 else {}
        except Exception as exc:
            decoded_errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if (
            kwargs.get("app_id") == ids["app_id"]
            and kwargs.get("release_id") == ids["release_id"]
            and kwargs.get("trigger_id") == ids["trigger_id"]
        ):
            return (
                f"{len(raw_messages)} message(s) on {QUEUE_NAME!r}; "
                f"found {TASK_NAME} for app {ids['app_id'][:8]}..."
            )
    detail = f" decode errors: {decoded_errors}" if decoded_errors else ""
    raise CheckFailed(
        f"{len(raw_messages)} message(s) on {QUEUE_NAME!r} but none carries this "
        f"app/release/trigger ({TASK_NAME}).{detail}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--ids-file",
        default=os.environ.get("P1_VERIFY_IDS_FILE", DEFAULT_IDS_FILE),
        help="ids written by beat_create_workflow_app.py",
    )
    args = parser.parse_args()

    try:
        ids = load_ids(args.ids_file)
    except CheckFailed as exc:
        print(f"[setup] FAIL: {exc}")
        return 1

    engine = create_engine(db_url(), pool_pre_ping=True)
    redis_cache = redis_lib.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )
    try:
        # Seed a stale entry the scan must invalidate through its write-back
        # (the scan check below runs after this seed).
        redis_cache.set(cache_key(ids["app_id"]), json.dumps({"nodes": "stale"}), ex=600)

        scan_result: dict = {}

        def do_scan() -> str:
            scan_result.update(run_scan())
            return (
                f"triggered={scan_result.get('triggered')} "
                f"scanned_at={scan_result.get('scanned_at')}"
            )

        checks = [
            ("scan predicates + release config", lambda: assert_due_predicate(engine, ids)),
            ("beat scan reports the app", do_scan),
            ("release config runtime write-back", lambda: check_release_runtime(engine, ids)),
            (
                "workflow_configs runtime write-back",
                lambda: check_workflow_config_runtime(engine, ids),
            ),
            ("shared cache key invalidated", lambda: check_cache_invalidated(redis_cache, ids)),
            ("broker message enqueued", lambda: check_broker_message(ids)),
        ]

        failures = 0
        for index, (name, run) in enumerate(checks, start=1):
            try:
                detail = run()
            except CheckFailed as exc:
                failures += 1
                print(f"[{index}/{len(checks)}] FAIL {name}: {exc}")
            except Exception as exc:  # unexpected shapes, transport errors
                failures += 1
                print(f"[{index}/{len(checks)}] FAIL {name}: {type(exc).__name__}: {exc}")
            else:
                print(f"[{index}/{len(checks)}] OK   {name}: {detail}")
    finally:
        redis_cache.close()
        engine.dispose()

    if failures:
        print(f"[beat-acceptance] FAILED: {failures} check(s) failed")
        return 1
    print("[beat-acceptance] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
