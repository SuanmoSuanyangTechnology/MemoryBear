"""Redis-backed task state shared by worker and API dispatch paths."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

PARSE_TASK_KEY = "doc:{doc_id}:parse_task"
PARSE_CANCEL_KEY = "doc:{doc_id}:parse_cancel"
PARSE_TASK_TTL = 7200
PARSE_CANCEL_TTL = 60

REBUILD_TASK_GUARD_TTL_SECONDS = 7200
_REBUILD_JOB_KEY_PREFIX = "evidence_graph:rebuild:job"
_REBUILD_EXECUTION_KEY_PREFIX = "evidence_graph:rebuild:execution"
_REBUILD_TERMINAL_KEY_PREFIX = "evidence_graph:rebuild:terminal"

_COMPARE_AND_DELETE_SCRIPT = """
local current_value = redis.call('get', KEYS[1])
if current_value and current_value == ARGV[1] then
    redis.call('del', KEYS[1])
    return 1
end
return 0
"""

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


def _canonical_knowledge_id(knowledge_id: str | uuid.UUID) -> str:
    return str(uuid.UUID(str(knowledge_id)))


def _decode_redis_value(value: bytes | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def rebuild_job_key(knowledge_id: str | uuid.UUID) -> str:
    return f"{_REBUILD_JOB_KEY_PREFIX}:{_canonical_knowledge_id(knowledge_id)}"


def rebuild_execution_key(knowledge_id: str | uuid.UUID) -> str:
    return f"{_REBUILD_EXECUTION_KEY_PREFIX}:{_canonical_knowledge_id(knowledge_id)}"


def rebuild_terminal_key(task_id: str) -> str:
    return f"{_REBUILD_TERMINAL_KEY_PREFIX}:{task_id}"


def claim_parse_task(redis: Any, doc_id: str | uuid.UUID) -> bool:
    return bool(
        redis.set(
            PARSE_TASK_KEY.format(doc_id=doc_id),
            "CLAIMED",
            ex=PARSE_TASK_TTL,
            nx=True,
        )
    )


def get_parse_task(redis: Any, doc_id: str | uuid.UUID) -> str | None:
    return _decode_redis_value(redis.get(PARSE_TASK_KEY.format(doc_id=doc_id)))


def record_parse_task(redis: Any, doc_id: str | uuid.UUID, task_id: str) -> bool:
    return bool(
        redis.set(
            PARSE_TASK_KEY.format(doc_id=doc_id),
            task_id,
            ex=PARSE_TASK_TTL,
        )
    )


def request_parse_cancel(redis: Any, doc_id: str | uuid.UUID) -> bool:
    return bool(
        redis.set(
            PARSE_CANCEL_KEY.format(doc_id=doc_id),
            "1",
            ex=PARSE_CANCEL_TTL,
        )
    )


def is_parse_cancelled(redis: Any, doc_id: str | uuid.UUID) -> bool:
    return redis.get(PARSE_CANCEL_KEY.format(doc_id=doc_id)) is not None


def clear_parse_state(redis: Any, doc_id: str | uuid.UUID) -> int:
    return int(
        redis.delete(
            PARSE_TASK_KEY.format(doc_id=doc_id),
            PARSE_CANCEL_KEY.format(doc_id=doc_id),
        )
    )


def claim_or_get_rebuild_job(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    proposed_task_id: str,
) -> RebuildJobClaim:
    key = rebuild_job_key(knowledge_id)
    if redis.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(redis.get(key))
    if existing_task_id is not None:
        return RebuildJobClaim(task_id=existing_task_id, claimed=False)

    if redis.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(redis.get(key))
    if existing_task_id is None:
        raise RuntimeError("rebuild job claim changed without an owner")
    return RebuildJobClaim(task_id=existing_task_id, claimed=False)


async def claim_or_get_rebuild_job_async(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    proposed_task_id: str,
) -> RebuildJobClaim:
    key = rebuild_job_key(knowledge_id)
    if await redis.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(await redis.get(key))
    if existing_task_id is not None:
        return RebuildJobClaim(task_id=existing_task_id, claimed=False)

    if await redis.set(
        key,
        proposed_task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return RebuildJobClaim(task_id=proposed_task_id, claimed=True)

    existing_task_id = _decode_redis_value(await redis.get(key))
    if existing_task_id is None:
        raise RuntimeError("rebuild job claim changed without an owner")
    return RebuildJobClaim(task_id=existing_task_id, claimed=False)


def refresh_rebuild_job(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    task_id: str,
) -> bool:
    key = rebuild_job_key(knowledge_id)
    if redis.set(
        key,
        task_id,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
        nx=True,
    ):
        return True
    return bool(
        redis.eval(
            _COMPARE_AND_EXPIRE_SCRIPT,
            1,
            key,
            task_id,
            REBUILD_TASK_GUARD_TTL_SECONDS,
        )
    )


def release_rebuild_job(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    task_id: str,
) -> bool:
    return bool(
        redis.eval(
            _COMPARE_AND_DELETE_SCRIPT,
            1,
            rebuild_job_key(knowledge_id),
            task_id,
        )
    )


async def release_rebuild_job_async(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    task_id: str,
) -> bool:
    return bool(
        await redis.eval(
            _COMPARE_AND_DELETE_SCRIPT,
            1,
            rebuild_job_key(knowledge_id),
            task_id,
        )
    )


def acquire_rebuild_execution(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    owner_token: str,
) -> bool:
    return bool(
        redis.set(
            rebuild_execution_key(knowledge_id),
            owner_token,
            ex=REBUILD_TASK_GUARD_TTL_SECONDS,
            nx=True,
        )
    )


def release_rebuild_execution(
    redis: Any,
    knowledge_id: str | uuid.UUID,
    owner_token: str,
) -> bool:
    return bool(
        redis.eval(
            _COMPARE_AND_DELETE_SCRIPT,
            1,
            rebuild_execution_key(knowledge_id),
            owner_token,
        )
    )


def has_rebuild_terminal(redis: Any, task_id: str) -> bool:
    return redis.get(rebuild_terminal_key(task_id)) is not None


def mark_rebuild_terminal(redis: Any, task_id: str, terminal: str) -> None:
    stored = redis.set(
        rebuild_terminal_key(task_id),
        terminal,
        ex=REBUILD_TASK_GUARD_TTL_SECONDS,
    )
    if not stored:
        raise RuntimeError("failed to store rebuild task terminal marker")


__all__ = [
    "PARSE_CANCEL_KEY",
    "PARSE_CANCEL_TTL",
    "PARSE_TASK_KEY",
    "PARSE_TASK_TTL",
    "REBUILD_TASK_GUARD_TTL_SECONDS",
    "RebuildJobClaim",
    "acquire_rebuild_execution",
    "claim_or_get_rebuild_job",
    "claim_or_get_rebuild_job_async",
    "claim_parse_task",
    "clear_parse_state",
    "get_parse_task",
    "has_rebuild_terminal",
    "is_parse_cancelled",
    "mark_rebuild_terminal",
    "rebuild_execution_key",
    "rebuild_job_key",
    "rebuild_terminal_key",
    "record_parse_task",
    "refresh_rebuild_job",
    "release_rebuild_execution",
    "release_rebuild_job",
    "release_rebuild_job_async",
    "request_parse_cancel",
]
