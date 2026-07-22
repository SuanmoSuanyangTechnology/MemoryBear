from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.logging_config import get_logger
from app.core.memory.pipelines.base_pipeline import ModelClientMixin
from app.core.memory.utils.prompt.template_render import prompt_env
from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_db_context
from app.repositories.end_user_repository import EndUserRepository
from app.services.memory_config_service import MemoryConfigService

logger = get_logger(__name__)

USER_CARD_TAG_META_FIELDS = (
    "interests",
    "traits",
    "goals",
)
USER_CARD_TAG_CANDIDATE_LIMIT = 6
USER_CARD_TAG_DISPLAY_LIMIT = 5
USER_CARD_TAG_CATEGORY_LIMIT = 2
USER_CARD_TAG_MAX_LENGTH = 12
USER_CARD_TAG_LLM_TIMEOUT_SECONDS = 12


class UserCardTagCandidate(BaseModel):
    """LLM 返回的单个候选 Tag，category 用于限制各类 Tag 的展示数量。"""

    name: str = Field(min_length=1)
    category: Literal["interests", "traits", "goals"]


class UserCardTagOutput(BaseModel):
    """LLM 结构化输出，候选数量可以略多于最终展示数量。"""

    tags: list[UserCardTagCandidate] = Field(
        default_factory=list,
        max_length=USER_CARD_TAG_CANDIDATE_LIMIT,
    )

    @model_validator(mode="before")
    @classmethod
    def accept_top_level_tag_list(cls, value: Any) -> Any:
        """兼容模型直接返回 Tag 数组的情况，统一转换为标准对象结构。"""
        if isinstance(value, list):
            return {"tags": value}
        return value


def build_user_card_tag_input(meta_data: object) -> dict[str, list[str]]:
    """只提取允许生成名片 Tag 的 metadata 字段，并清理字符串空白。"""
    if not isinstance(meta_data, dict):
        return {field: [] for field in USER_CARD_TAG_META_FIELDS}

    result: dict[str, list[str]] = {}
    for field in USER_CARD_TAG_META_FIELDS:
        values = meta_data.get(field)
        if not isinstance(values, list):
            result[field] = []
            continue
        result[field] = [
            normalized
            for value in values
            if isinstance(value, str)
            if (normalized := " ".join(value.split()))
        ]
    return result


def build_user_card_tag_source_fingerprint(tag_input: dict[str, list[str]]) -> str:
    """为规范化后的 LLM 输入生成稳定指纹，用于判断是否需要重新调用模型。"""
    source_json = json.dumps(
        tag_input,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(source_json.encode("utf-8")).hexdigest()


def validate_user_card_tags(candidates: list[UserCardTagCandidate]) -> list[str]:
    """清理候选 Tag，并执行长度、去重、分类数量和总展示数量限制。"""
    tags: list[str] = []
    seen: set[str] = set()
    category_counts = {field: 0 for field in USER_CARD_TAG_META_FIELDS}

    for candidate in candidates:
        name = " ".join(candidate.name.split())
        key = name.casefold()
        if not name or len(name) > USER_CARD_TAG_MAX_LENGTH:
            continue
        if key in seen:
            continue
        if category_counts[candidate.category] >= USER_CARD_TAG_CATEGORY_LIMIT:
            continue

        seen.add(key)
        category_counts[candidate.category] += 1
        tags.append(name)
        if len(tags) >= USER_CARD_TAG_DISPLAY_LIMIT:
            break

    return tags


def normalize_stored_user_card_tags(stored_tags: object) -> list[str]:
    """读取缓存时再次规范化，避免历史数据绕过当前展示约束。"""
    if not isinstance(stored_tags, list):
        return []

    tags: list[str] = []
    seen: set[str] = set()
    for value in stored_tags:
        if not isinstance(value, str):
            continue
        normalized = " ".join(value.split())
        key = normalized.casefold()
        if not normalized or len(normalized) > USER_CARD_TAG_MAX_LENGTH or key in seen:
            continue
        seen.add(key)
        tags.append(normalized)
        if len(tags) >= USER_CARD_TAG_DISPLAY_LIMIT:
            break
    return tags


async def generate_user_card_tags(
        llm_client: Any,
        meta_data: object,
        *,
        log_context: str | None = None,
) -> list[str]:
    """调用 LLM 生成候选 Tag，并返回通过展示规则校验的结果。"""
    tag_input = build_user_card_tag_input(meta_data)
    if not any(tag_input.values()):
        return []

    context = f" user={log_context}" if log_context else ""
    input_count = sum(len(values) for values in tag_input.values())
    prompt_started_at = time.monotonic()
    prompt = prompt_env.get_template("user_card_tags.jinja2").render(
        input_json=json.dumps(tag_input, ensure_ascii=False),
    )
    logger.info(
        "用户名片Tag阶段=prompt_render%s input_count=%s elapsed=%.3fs",
        context,
        input_count,
        time.monotonic() - prompt_started_at,
    )

    llm_started_at = time.monotonic()
    try:
        output = await asyncio.wait_for(
            llm_client.call_structured(
                [{"role": "user", "content": prompt}],
                UserCardTagOutput,
            ),
            timeout=USER_CARD_TAG_LLM_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.error(
            "用户名片Tag阶段=llm_call_failed%s input_count=%s elapsed=%.3fs",
            context,
            input_count,
            time.monotonic() - llm_started_at,
            exc_info=True,
        )
        raise
    logger.info(
        "用户名片Tag阶段=llm_call%s input_count=%s candidate_count=%s elapsed=%.3fs",
        context,
        input_count,
        len(output.tags),
        time.monotonic() - llm_started_at,
    )

    validate_started_at = time.monotonic()
    tags = validate_user_card_tags(output.tags)
    logger.info(
        "用户名片Tag阶段=output_validate%s candidate_count=%s tags_count=%s elapsed=%.3fs",
        context,
        len(output.tags),
        len(tags),
        time.monotonic() - validate_started_at,
    )
    return tags


async def refresh_user_card_tags(end_user_id: str, workspace_id: str) -> dict[str, Any]:
    """根据用户当前 metadata 刷新名片 Tag 缓存。

    PostgreSQL 使用多个同步短会话：先读取源数据，再加载模型配置，LLM 调用结束后才开启
    写回会话。这样等待 LLM 时不会占用数据库连接，写回时也能校验源数据是否仍是原版本。
    """
    total_started_at = time.monotonic()
    end_user_uuid = uuid.UUID(end_user_id)
    workspace_uuid = uuid.UUID(workspace_id)

    source_started_at = time.monotonic()
    # 只在短会话内读取生成所需的数据；离开 with 后连接立即归还连接池。
    with get_db_context() as db:
        source = EndUserRepository(db).get_scoped_user_tag_source(
            workspace_id=workspace_uuid,
            end_user_id=end_user_uuid,
        )
    logger.info(
        "用户名片Tag阶段=source_read user=%s workspace=%s found=%s elapsed=%.3fs",
        end_user_id,
        workspace_id,
        source is not None,
        time.monotonic() - source_started_at,
    )
    if source is None:
        logger.info(
            "用户名片Tag阶段=total user=%s status=skipped_source elapsed=%.3fs",
            end_user_id,
            time.monotonic() - total_started_at,
        )
        return {"status": "skipped_source", "tags_count": 0}

    input_started_at = time.monotonic()
    tag_input = build_user_card_tag_input(source["meta_data"])
    input_count = sum(len(values) for values in tag_input.values())
    logger.info(
        "用户名片Tag阶段=input_normalize user=%s input_count=%s elapsed=%.3fs",
        end_user_id,
        input_count,
        time.monotonic() - input_started_at,
    )

    fingerprint_started_at = time.monotonic()
    source_fingerprint = build_user_card_tag_source_fingerprint(tag_input)
    has_cached_tags = source.get("memory_tags") is not None
    fingerprint_matches = (
        has_cached_tags
        and source.get("memory_tags_source_fingerprint") == source_fingerprint
    )
    logger.info(
        "用户名片Tag阶段=fingerprint_compare user=%s has_cached_tags=%s matched=%s elapsed=%.3fs",
        end_user_id,
        has_cached_tags,
        fingerprint_matches,
        time.monotonic() - fingerprint_started_at,
    )

    if fingerprint_matches:
        tags = normalize_stored_user_card_tags(source["memory_tags"])
        logger.info(
            "用户名片Tag阶段=llm_skipped user=%s reason=source_unchanged tags_count=%s",
            end_user_id,
            len(tags),
        )
    elif any(tag_input.values()):
        config_started_at = time.monotonic()
        # 模型客户端创建完成后即关闭数据库会话，后续 await LLM 时不持有数据库连接。
        with get_db_context() as db:
            config_service = MemoryConfigService(db)
            config_id = config_service.get_config_id_by_end_user(end_user_uuid)
            memory_config = config_service.load_memory_config(config_id)
            llm_client = ModelClientMixin.get_llm_client(
                db,
                memory_config.llm_model_id,
                memory_config.tenant_id,
            )
        logger.info(
            "用户名片Tag阶段=config_and_llm_client user=%s elapsed=%.3fs",
            end_user_id,
            time.monotonic() - config_started_at,
        )

        generation_started_at = time.monotonic()
        try:
            tags = await generate_user_card_tags(
                llm_client,
                tag_input,
                log_context=end_user_id,
            )
        except Exception:
            logger.error(
                "用户名片Tag生成失败 user=%s input_count=%s elapsed=%.3fs",
                end_user_id,
                input_count,
                time.monotonic() - generation_started_at,
                exc_info=True,
            )
            raise
        logger.info(
            "用户名片Tag阶段=generation_total user=%s input_count=%s tags_count=%s elapsed=%.3fs",
            end_user_id,
            input_count,
            len(tags),
            time.monotonic() - generation_started_at,
        )
    else:
        tags = []
        logger.info("用户名片Tag阶段=generation_skipped user=%s reason=empty_input", end_user_id)

    update_started_at = time.monotonic()
    # LLM 执行期间 metadata 可能已更新，因此只允许原版本对应的结果写回。
    with get_db_context() as db:
        updated = EndUserRepository(db).update_user_tags_if_source_unchanged(
            workspace_id=workspace_uuid,
            end_user_id=end_user_uuid,
            expected_metadata_updated_at=source["metadata_updated_at"],
            tags=tags,
            source_fingerprint=source_fingerprint,
            refreshed_at=utcnow_naive(),
        )
    if not updated:
        status = "superseded"
    elif fingerprint_matches:
        status = "unchanged"
    else:
        status = "refreshed"
    logger.info(
        "用户名片Tag阶段=conditional_update user=%s status=%s tags_count=%s elapsed=%.3fs",
        end_user_id,
        status,
        len(tags),
        time.monotonic() - update_started_at,
    )

    logger.info(
        "用户名片Tag阶段=total user=%s status=%s tags_count=%s elapsed=%.3fs",
        end_user_id,
        status,
        len(tags),
        time.monotonic() - total_started_at,
    )
    return {
        "status": status,
        "tags_count": len(tags),
    }
