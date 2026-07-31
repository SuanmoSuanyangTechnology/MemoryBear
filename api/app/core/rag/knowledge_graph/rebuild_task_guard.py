from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.rag.utils.redis_conn import REDIS_CONN

REBUILD_TASK_GUARD_TTL_SECONDS = 7200
_REBUILD_JOB_KEY_PREFIX = "evidence_graph:rebuild:job"
_REBUILD_EXECUTION_KEY_PREFIX = "evidence_graph:rebuild:execution"
_REBUILD_TERMINAL_KEY_PREFIX = "evidence_graph:rebuild:terminal"
_COMPARE_AND_EXPIRE_SCRIPT = """
local current_value = redis.call('get', KEYS[1])
if current_value and current_value == ARGV[1] then
    redis.call('expire', KEYS[1], ARGV[2])
    return 1
end
return 0
"""


@dataclass(frozen=True)
class RebuildJobClaim:
    task_id: str
    claimed: bool


def _canonical_knowledge_id(knowledge_id: str) -> str:
    return str(uuid.UUID(str(knowledge_id)))


def _decode_redis_value(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def rebuild_job_key(knowledge_id: str) -> str:
    return f"{_REBUILD_JOB_KEY_PREFIX}:{_canonical_knowledge_id(knowledge_id)}"


def rebuild_execution_key(knowledge_id: str) -> str:
    return (
        f"{_REBUILD_EXECUTION_KEY_PREFIX}:"
        f"{_canonical_knowledge_id(knowledge_id)}"
    )


def rebuild_terminal_key(task_id: str) -> str:
    return f"{_REBUILD_TERMINAL_KEY_PREFIX}:{task_id}"


def claim_or_get_rebuild_job(
    knowledge_id: str,
    proposed_task_id: str,
) -> RebuildJobClaim:
    key = rebuild_job_key(knowledge_id)
    client = REDIS_CONN.REDIS
    if client.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(client.get(key))
    if existing_task_id is not None:
        return RebuildJobClaim(task_id=existing_task_id, claimed=False)

    if client.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(client.get(key))
    if existing_task_id is None:
        raise RuntimeError("rebuild job claim changed without an owner")
    return RebuildJobClaim(task_id=existing_task_id, claimed=False)


def refresh_rebuild_job(knowledge_id: str, task_id: str) -> bool:
    key = rebuild_job_key(knowledge_id)
    client = REDIS_CONN.REDIS
    if client.set(
        key,
        task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return True
    return bool(
        client.eval(
            _COMPARE_AND_EXPIRE_SCRIPT,
            1,
            key,
            task_id,
            REBUILD_TASK_GUARD_TTL_SECONDS,
        )
    )


def release_rebuild_job(knowledge_id: str, task_id: str) -> bool:
    return REDIS_CONN.delete_if_equal(
        rebuild_job_key(knowledge_id),
        task_id,
    )


def acquire_rebuild_execution(
    knowledge_id: str,
    owner_token: str,
) -> bool:
    return bool(
        REDIS_CONN.REDIS.set(
            rebuild_execution_key(knowledge_id),
            owner_token,
            ex=REBUILD_TASK_GUARD_TTL_SECONDS,
            nx=True,
        )
    )


def release_rebuild_execution(
    knowledge_id: str,
    owner_token: str,
) -> bool:
    return REDIS_CONN.delete_if_equal(
        rebuild_execution_key(knowledge_id),
        owner_token,
    )


def has_rebuild_terminal(task_id: str) -> bool:
    return REDIS_CONN.REDIS.get(rebuild_terminal_key(task_id)) is not None


def mark_rebuild_terminal(task_id: str, terminal: str) -> None:
    stored = REDIS_CONN.REDIS.set(
        rebuild_terminal_key(task_id),
        terminal,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
    )
    if not stored:
        raise RuntimeError("failed to store rebuild task terminal marker")
