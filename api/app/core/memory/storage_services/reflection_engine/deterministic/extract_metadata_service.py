"""
Metadata extraction service.

Provides async functions for extracting structured metadata from user entities
and writing patch results back to Neo4j + PostgreSQL. Designed to be called as
a function within the Reflection Layer2 pipeline rather than as a standalone
Celery task.

The entry point ``extract_metadata_for_user`` scans Neo4j for User entities
and runs LLM extraction + patch. Description minimum fragment count is gated
upstream by the reflection pipeline (description_merge.min_fragments=5).
"""

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


# ── Module-level helpers ──


def _filter_invalid_old_values(
    operations: List[Any],
    existing_metadata: Dict[str, List[str]],
    entity_name: str,
    entity_id: str,
) -> int:
    """过滤 old_value 不在 Neo4j 当前列表的 delete/update op，就地移除。

    防止 Cypher 列表推导静默失败：匹配不到 old_value 时不会报错也不会改值。
    """
    valid_ops: List[Any] = []
    skipped = 0
    for op in operations:
        if op.op in ("delete", "update"):
            cur_list = existing_metadata.get(op.field) or []
            if op.old_value not in cur_list:
                skipped += 1
                logger.warning(
                    f"[Metadata] 实体 {entity_name}({entity_id}) "
                    f"丢弃 {op.op} op：old_value 在当前 {op.field} 中不存在: "
                    f"old_value={op.old_value!r}"
                )
                continue
        valid_ops.append(op)
    # 就地替换（保持与旧闭包写法一致）
    operations.clear()
    operations.extend(valid_ops)
    return skipped


def _build_patch_params(
    result: Any,
    entity_id: str,
    existing_metadata: Dict[str, List[str]],
    entity_name: str,
    allowed_fields: Tuple[str, ...],
    max_list_len_per_field: int,
) -> Dict[str, Any]:
    """按字段把 add / delete / update 拼成 ENTITY_METADATA_PATCH 的参数。

    对 add 路径做长度保护。
    """
    adds = result.adds_by_field()
    deletes = result.deletes_by_field()
    updates = result.updates_by_field()
    params: Dict[str, Any] = {"entity_id": entity_id}
    for field in allowed_fields:
        params[f"{field}_delete"] = deletes.get(field, [])
        params[f"{field}_update"] = [
            {"old": old, "new": new}
            for (old, new) in updates.get(field, [])
        ]
        field_adds = adds.get(field, [])
        current_len = len(existing_metadata.get(field) or [])
        capacity = max(0, max_list_len_per_field - current_len)
        if len(field_adds) > capacity:
            overflow = len(field_adds) - capacity
            field_adds = field_adds[:capacity]
            logger.warning(
                f"[Metadata] 实体 {entity_name}({entity_id}) "
                f"字段 {field} 长度将超上限({max_list_len_per_field})，"
                f"截断 {overflow} 条 add"
            )
        params[f"{field}_add"] = field_adds
    return params


def _extract_post_state(
    patch_records: List[Dict[str, Any]],
    allowed_fields: Tuple[str, ...],
) -> Dict[str, List[str]]:
    """从 patch RETURN 中取出权威字段值用于覆盖式同步到 PG。"""
    if not patch_records:
        return {}
    rec = patch_records[0]
    return {field: list(rec.get(field) or []) for field in allowed_fields}


# ── Public API ──


async def extract_metadata_for_user(
    connector: Any,
    llm_client: Any,
    end_user_id: str,
    language: str = "zh",
    max_list_len_per_field: int = 200,
) -> Dict[str, Any]:
    """对指定用户的 User 实体执行元数据提取 + Neo4j 回写 + PostgreSQL 同步。

    从 Neo4j 中读取当前用户的 User 实体及其 description，
    调用 MetadataExtractionStep 进行 LLM 结构化提取，
    将 patch operations 回写 Neo4j 并同步 PostgreSQL。

    Args:
        connector: Neo4j 连接器
        llm_client: LLM 客户端
        end_user_id: 终端用户 ID
        language: 语言 ("zh" / "en")
        max_list_len_per_field: 单字段（Neo4j 列表属性）最大长度

    Returns:
        {"extracted": N, "failed": N}
    """
    from app.core.memory.models.metadata_models import ALLOWED_METADATA_FIELDS
    from app.core.memory.models.variate_config import ExtractionPipelineConfig
    from app.core.memory.storage_services.extraction_engine.steps.base import StepContext
    from app.core.memory.storage_services.extraction_engine.steps.metadata_step import MetadataExtractionStep
    from app.repositories.neo4j.cypher_queries import (
        ENTITY_METADATA_PATCH,
        ENTITY_METADATA_QUERY,
        USER_ENTITY_FOR_METADATA,
    )

    # ── 1. 扫描 User 实体 ──
    try:
        records = await connector.execute_query(
            USER_ENTITY_FOR_METADATA, end_user_id=end_user_id
        )
    except Exception as e:
        logger.warning(f"[Metadata] 查询 User 实体失败: {e}")
        return {"extracted": 0, "failed": 0}

    if not records:
        logger.debug(f"[Metadata] 未找到 User 实体，跳过: end_user_id={end_user_id}")
        return {"extracted": 0, "failed": 0}

    # 过滤无 description 的实体（门控由反思巡检上游保证）
    candidates: List[Dict[str, Any]] = []
    for rec in records:
        desc = (rec.get("description") or "").strip()
        if not desc:
            continue
        descriptions = [d.strip() for d in desc.replace("；", ";").split(";") if d.strip()]
        if not descriptions:
            continue
        candidates.append({
            "entity_id": rec["entity_id"],
            "entity_name": rec.get("entity_name", ""),
            "descriptions": descriptions,
            "end_user_id": rec.get("end_user_id", ""),
        })

    if not candidates:
        logger.debug(f"[Metadata] 无有 description 的 User 实体，跳过")
        return {"extracted": 0, "failed": 0}

    logger.info(f"[Metadata] 扫描到 {len(candidates)} 个候选 User 实体")

    # ── 2. 构建 step ──
    pipeline_config = ExtractionPipelineConfig()
    context = StepContext(
        llm_client=llm_client,
        language=language,
        config=pipeline_config,
    )
    step = MetadataExtractionStep(context)

    extracted = 0
    failed = 0

    # ── 3. 遍历候选实体 ──
    for entity_dict in candidates:
        entity_id = entity_dict["entity_id"]
        entity_name = entity_dict.get("entity_name", "")

        try:
            patched = await _extract_single_entity(
                connector=connector,
                step=step,
                entity_id=entity_id,
                entity_name=entity_name,
                descriptions=entity_dict.get("descriptions", []),
                allowed_fields=ALLOWED_METADATA_FIELDS,
                metadata_query=ENTITY_METADATA_QUERY,
                metadata_patch=ENTITY_METADATA_PATCH,
                max_list_len_per_field=max_list_len_per_field,
            )
            if patched:
                extracted += 1
                if entity_dict.get("end_user_id") and patched.get("post_state"):
                    _sync_metadata_to_pg(
                        end_user_id=entity_dict["end_user_id"],
                        metadata=patched["post_state"],
                    )
        except Exception as e:
            failed += 1
            logger.warning(f"[Metadata] 实体 {entity_id} 元数据提取失败: {e}")

    return {"extracted": extracted, "failed": failed}


async def _extract_single_entity(
    connector: Any,
    step: Any,
    entity_id: str,
    entity_name: str,
    descriptions: List[str],
    allowed_fields: Tuple[str, ...],
    metadata_query: str,
    metadata_patch: str,
    max_list_len_per_field: int,
) -> Dict[str, Any] | None:
    """对单个 User 实体执行：读取已有元数据 → LLM 提取 → patch 回写。

    Returns:
        成功时返回 {"post_state": {...}}，跳过或无变更时返回 None。
    """
    from app.core.memory.storage_services.extraction_engine.steps.schema import (
        MetadataStepInput,
    )

    # 读取已有元数据
    existing: Dict[str, List[str]] = {f: [] for f in allowed_fields}
    try:
        records = await connector.execute_query(metadata_query, entity_id=entity_id)
        if records:
            rec = records[0]
            for field in allowed_fields:
                val = rec.get(field)
                existing[field] = list(val) if val else []
    except Exception as e:
        logger.warning(f"[Metadata] 查询已有元数据失败: {e}")

    inp = MetadataStepInput(
        entity_id=entity_id,
        entity_name=entity_name,
        descriptions=descriptions,
        existing_metadata=existing,
    )
    result = await step.run(inp)

    if not result.has_any():
        logger.debug(f"[Metadata] 实体 {entity_name}({entity_id}) 无新增元数据")
        return None

    skipped_ops_count = _filter_invalid_old_values(
        result.operations, existing, entity_name, entity_id
    )

    if not result.has_any():
        logger.info(f"[Metadata] 实体 {entity_name}({entity_id}) 所有 op 均被过滤，跳过 patch")
        return None

    patch_records = await connector.execute_query(
        metadata_patch,
        **_build_patch_params(
            result, entity_id, existing, entity_name,
            allowed_fields, max_list_len_per_field,
        ),
    )

    counts = result.counts()
    logger.info(
        f"[Metadata] 实体 {entity_name}({entity_id}) patch 完成: "
        f"add={counts['add']}, delete={counts['delete']}, "
        f"update={counts['update']}, skipped={skipped_ops_count}, "
        f"dropped_by_validator={result.dropped_ops_count}"
    )

    post_state = _extract_post_state(patch_records, allowed_fields)
    return {"post_state": post_state}


def _sync_metadata_to_pg(
    end_user_id: str,
    metadata: Dict[str, List[str]],
) -> None:
    """以 Neo4j patch 后的最新值覆盖 PG end_user_info.meta_data 中对应 key。

    此函数仅处理 8 个被 metadata patch 管理的字段（core_facts、traits、
    relations、goals、interests、beliefs_or_stances、anchors、events）。
    `aliases` 和 `other_name` 不在本函数管辖范围内（保持原有别名同步链路）。

    覆盖语义但不丢失历史的两道保护：
        1. 入参 metadata 来自 Neo4j patch 后的最新读回值——它已经叠加了历史
        2. 仅覆盖 metadata 中显式提供的 key，``meta_data`` 里其它 key 原样保留

    早返回与副作用：
        - 当 ``metadata`` 为空 dict 时直接返回，不读取也不更新 PG，
          因此不会刷新 ``end_user_info`` 的 ``updated_at``。下游若依赖该
          时间戳判断"上次同步时间"，需要自行处理"零变更"场景。
        - 失败只记日志，不抛异常，不影响主流程。
    """
    if not metadata:
        return
    try:
        import uuid as _uuid
        from app.db import get_db_context
        from app.repositories.end_user_info_repository import EndUserInfoRepository

        eu_uuid = _uuid.UUID(end_user_id)

        with get_db_context() as db:
            info_repo = EndUserInfoRepository(db)
            info = info_repo.replace_metadata_fields(
                end_user_id=eu_uuid,
                metadata=metadata,
            )
            if info is None:
                logger.warning(
                    f"[Metadata][PG] end_user_info 记录不存在，跳过 metadata 覆盖: "
                    f"end_user_id={end_user_id}"
                )
                return

        logger.info(
            f"[Metadata][PG] end_user_info.meta_data 覆盖完成: "
            f"end_user_id={end_user_id}, fields={list(metadata.keys())}"
        )
    except Exception as e:
        logger.warning(
            f"[Metadata][PG] 覆盖 end_user_info.meta_data 失败（不影响主流程）: "
            f"end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )
