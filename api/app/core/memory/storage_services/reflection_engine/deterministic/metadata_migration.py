"""API 反思补迁适配器：复用已持有的用户写锁，模型调用不占 DB 连接。"""

import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.core.config import settings
from pydantic import BaseModel, ConfigDict
from task_bridge.metadata_migration import (
    apply_migration_response_operations,
    legacy_metadata_input,
    metadata_write_column,
    new_metadata,
)

logger = logging.getLogger(__name__)


class MigrationOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    field: str
    value: str


class MigrationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str
    operations: list[MigrationOperation]
    dropped_ops_count: Literal[0]


class MigrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[MigrationItem]


def apply_migration_response(existing: object, raw: object, user_id: str):
    response = raw if isinstance(raw, MigrationResponse) else MigrationResponse.model_validate(raw)
    if len(response.items) != 1:
        raise ValueError("迁移返回用户不匹配")
    return apply_migration_response_operations(
        existing,
        [operation.model_dump() for operation in response.items[0].operations],
        response.items[0].entity_id,
        user_id,
    )


@dataclass(frozen=True)
class MetadataBaseline:
    meta_data: object
    meta_data_new: object
    created_at: datetime | None

    @property
    def write_column(self) -> str:
        return metadata_write_column(self.created_at, self.meta_data_new)

    @property
    def metadata(self) -> dict:
        # 新用户首次生成不读旧列；未迁移老用户才回落旧列。
        value = (
            self.meta_data if self.write_column == "meta_data" else self.meta_data_new
        )
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("用户 metadata 类型损坏")
        return value


def read_metadata_baseline(end_user_id: str) -> MetadataBaseline | None:
    from app.db import get_db_context
    from app.models import EndUser, EndUserInfo, Workspace

    with get_db_context() as db:
        row = (
            db.query(
                EndUserInfo.meta_data, EndUserInfo.meta_data_new, EndUser.created_at
            )
            .join(EndUser, EndUser.id == EndUserInfo.end_user_id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .filter(
                EndUser.id == uuid.UUID(end_user_id),
                EndUser.is_active.is_(True),
                Workspace.is_active.is_(True),
            )
            .first()
        )
        return None if row is None else MetadataBaseline(*row)


_write_guard: ContextVar[Any] = ContextVar(
    "metadata_reflection_write_guard", default=None
)


@contextmanager
def metadata_reflection_lock(lock):
    """将任务实际持有的锁传入本轮异步反思，不重入、不重新抢锁。"""
    token = _write_guard.set(lock)
    try:
        yield
    finally:
        _write_guard.reset(token)


def ensure_write_lock() -> bool:
    lock = _write_guard.get()
    if lock is None:
        return False
    lock.ensure_valid()
    if lock.redis.get(lock.key) != lock.value:
        raise RuntimeError("反思用户写锁已丢失")
    return True


def _commit_migration(end_user_id: str, original: object, merged: dict) -> bool:
    from app.db import get_db_context
    from sqlalchemy import text

    with get_db_context() as db:
        result = db.execute(
            text("""
            UPDATE end_user_info AS info
            SET meta_data_new = CAST(:merged AS jsonb), updated_at = NOW()
            WHERE info.end_user_id = :user_id AND info.meta_data_new IS NULL
              AND info.meta_data IS NOT DISTINCT FROM CAST(:original AS jsonb)
              AND EXISTS (
                SELECT 1 FROM end_users AS eu JOIN workspaces AS ws ON ws.id = eu.workspace_id
                WHERE eu.id = info.end_user_id AND eu.is_active IS TRUE AND ws.is_active IS TRUE
              )
        """),
            {
                "user_id": uuid.UUID(end_user_id),
                "original": None
                if original is None
                else json.dumps(original, ensure_ascii=False),
                "merged": json.dumps(merged, ensure_ascii=False),
            },
        )
        saved = bool(result.rowcount)
        if saved:
            db.execute(
                text("""
            UPDATE end_users SET memory_tags = NULL, memory_tags_updated_at = NULL,
                memory_tags_source_fingerprint = NULL,
                memory_insight = NULL, key_findings = NULL,
                behavior_pattern = NULL, growth_trajectory = NULL,
                memory_insight_updated_at = NULL,
                user_summary = NULL, personality_traits = NULL, core_values = NULL,
                one_sentence_summary = NULL, user_summary_updated_at = NULL
            WHERE id = :user_id AND is_active IS TRUE
"""),
                {"user_id": uuid.UUID(end_user_id)},
            )
        db.commit()
        return saved


async def migrate_before_reflection(
    end_user_id: str,
    llm_client: Any,
    language: str = "zh",
) -> dict[str, object]:
    """调用方必须持有 memory_write 锁；失败保持 NULL，本轮反思仍可继续。"""
    baseline = read_metadata_baseline(end_user_id)
    if baseline is None:
        return {"status": "skipped", "reason": "inactive_or_missing_info"}
    if baseline.meta_data_new is not None:
        return {"status": "already_migrated"}
    if baseline.write_column == "meta_data_new":
        return {"status": "skipped", "reason": "new_user"}
    if not settings.METADATA_MIGRATION_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    if not ensure_write_lock():
        return {"status": "deferred", "reason": "write_lock_unavailable"}
    original = baseline.meta_data
    merged, applied, rejected = await prepare_migration_metadata(
        end_user_id, original, llm_client, language
    )
    ensure_write_lock()
    saved = _commit_migration(end_user_id, original, merged)
    if not saved:
        return {"status": "deferred", "reason": "metadata_changed"}
    try:
        from app.utils.redis_cache import invalidate_cache

        await invalidate_cache(prefix=f"memory:{end_user_id}")
    except Exception:
        logger.warning("迁移成功后清理画像缓存失败 user=%s", end_user_id, exc_info=True)
    return {
        "status": "migrated",
        "operations_applied": applied,
        "operations_rejected": rejected,
    }


async def prepare_migration_metadata(
    end_user_id: str,
    original: object,
    llm_client: Any,
    language: str = "zh",
) -> tuple[dict, int, int]:
    """只转换、不提交；用户归并可以在自身事务中保存全部补迁结果。"""
    # NULL 是合法空历史数据；其他非对象类型不得伪装成 no_legacy。
    if original is not None and not isinstance(original, dict):
        raise ValueError("历史 metadata 类型损坏")
    existing = original if original is not None else {}
    legacy = legacy_metadata_input(existing)
    if not legacy:
        return new_metadata(existing), 0, 0

    from app.core.memory.utils.prompt.prompt_utils import prompt_env

    prompt = prompt_env.get_template("migrate_user_metadata_v1.jinja2").render(
        entity_id=end_user_id,
        language=language,
        input_json=json.dumps({"entity_id": end_user_id, **legacy}, ensure_ascii=False),
    )
    raw = await llm_client.call_structured(
        [{"role": "user", "content": prompt}],
        MigrationResponse,
    )
    return apply_migration_response(existing, raw, end_user_id)
