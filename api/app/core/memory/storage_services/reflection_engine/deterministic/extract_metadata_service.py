"""
Metadata extraction service.

Provides async functions for extracting structured metadata from user entities
and writing patch results back to Neo4j + PostgreSQL. Designed to be called as
a function within the Reflection Layer2 pipeline rather than as a standalone
Celery task.

The entry point ``extract_metadata_for_user`` scans Neo4j for User entities
and runs LLM extraction + patch. Description fragment count is gated by the
``min_fragments`` parameter (default 5, shared with description_merge config).
"""

import json
import logging
from typing import Any, Dict, List, Tuple

from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionBusinessError,
    ReflectionFailureReason,
    ReflectionModelType,
)

logger = logging.getLogger(__name__)


# ── Module-level helpers ──


def _filter_invalid_old_values(
    operations: List[Any],
    existing_metadata: Dict[str, List[str]],
    entity_name: str,
    entity_id: str,
    skipped_sink: "List[dict] | None" = None,
) -> int:
    """过滤 old_value 不在 Neo4j 当前列表的 delete/update op，就地移除。

    防止 Cypher 列表推导静默失败：匹配不到 old_value 时不会报错也不会改值。
    skipped_sink 非空时，把被丢弃的 op 逐条记入（{field, op, old_value}），供快照采集。
    """
    valid_ops: List[Any] = []
    skipped = 0
    for op in operations:
        if op.op in ("delete", "update"):
            cur_list = existing_metadata.get(op.field) or []
            if op.old_value not in cur_list:
                skipped += 1
                if skipped_sink is not None:
                    skipped_sink.append({"field": op.field, "op": op.op,
                                         "old_value": op.old_value})
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
    truncated_sink: "List[dict] | None" = None,
) -> Dict[str, Any]:
    """按字段把 add / delete / update 拼成 ENTITY_METADATA_PATCH 的参数。

    对 add 路径做长度保护。truncated_sink 非空时，把被截断的 add 值逐条记入
    （{field, value}），供快照采集。
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
            if truncated_sink is not None:
                for v in field_adds[capacity:]:
                    truncated_sink.append({"field": field, "value": v})
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


def _build_operations_detail(result: Any) -> List[Dict[str, Any]]:
    """把 operations 转为简洁的变更明细列表，每条包含 op/field/描述。"""
    details: List[Dict[str, Any]] = []
    for op in result.operations:
        if op.op == "add":
            details.append({"op": "add", "field": op.field, "value": op.value})
        elif op.op == "delete":
            details.append({"op": "delete", "field": op.field, "value": op.old_value})
        elif op.op == "update":
            details.append({"op": "update", "field": op.field,
                            "old": op.old_value, "new": op.new_value})
    return details


def _build_meta_trace(
    entity_id: str,
    entity_name: str,
    descriptions: List[str],
    existing: Dict[str, List[str]],
    result: Any,
    applied_ops: List[Any],
    skipped_recs: List[dict],
    truncated_recs: List[dict],
) -> Dict[str, Any]:
    """组装单个 User 实体的快照 trace 片段（input / llm_raw / changes），供 Pipeline 聚合落盘。"""
    act_map = {"add": "add_field", "update": "update_field", "delete": "delete_field"}
    changes: List[dict] = []
    for op in applied_ops:
        if op.op == "add":
            fc = {"field": op.field, "old": None, "new": op.value}
        elif op.op == "update":
            fc = {"field": op.field, "old": op.old_value, "new": op.new_value}
        else:  # delete
            fc = {"field": op.field, "old": op.old_value, "new": None}
        changes.append({"target_type": "metadata_field", "target_id": entity_id,
                        "target_name": entity_name, "action": act_map.get(op.op, "update_field"),
                        "field_changes": [fc], "status": "applied", "reason": None, "extra": {}})
    for r in skipped_recs:
        changes.append({"target_type": "metadata_field", "target_id": entity_id,
                        "target_name": entity_name, "action": act_map.get(r["op"], "update_field"),
                        "field_changes": [{"field": r["field"], "old": r["old_value"], "new": None}],
                        "status": "skipped", "reason": "old_value_not_found", "extra": {}})
    for r in truncated_recs:
        changes.append({"target_type": "metadata_field", "target_id": entity_id,
                        "target_name": entity_name, "action": "add_field",
                        "field_changes": [{"field": r["field"], "old": None, "new": r["value"]}],
                        "status": "truncated", "reason": "max_list_len", "extra": {}})
    return {
        "input": {"entity_id": entity_id, "entity_name": entity_name,
                  "fragment_count": len(descriptions), "descriptions": descriptions,
                  "existing_metadata": existing},
        "llm_raw": {"entity_id": entity_id,
                    "operations": [o.model_dump() for o in applied_ops],
                    "dropped_ops_count": getattr(result, "dropped_ops_count", 0)},
        "changes": changes,
    }


# ── Public API ──


async def extract_metadata_for_user(
    connector: Any,
    llm_client: Any,
    end_user_id: str,
    language: str = "zh",
    min_fragments: int = 5,
    max_list_len_per_field: int = 200,
    collect_trace: bool = False,
) -> Dict[str, Any]:
    """对指定用户的 User 实体执行元数据提取 + Neo4j 回写 + PostgreSQL 同步。

    从 Neo4j 中读取当前用户的 User 实体及其 description，直接调 LLM 做
    结构化元数据提取，将 patch operations 回写 Neo4j 并同步 PostgreSQL。

    Args:
        connector: Neo4j 连接器
        llm_client: LLM 客户端
        end_user_id: 终端用户 ID
        language: 语言 ("zh" / "en")
        min_fragments: description 碎片数阈值（≥此值才触发提取）
        max_list_len_per_field: 单字段（Neo4j 列表属性）最大长度

    Returns:
        {"extracted": N, "failed": N}
    """
    from app.core.memory.models.metadata_models import ALLOWED_METADATA_FIELDS
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

    # 过滤无 description 的实体 + min_fragments 门控
    candidates: List[Dict[str, Any]] = []
    for rec in records:
        desc = (rec.get("description") or "").strip()
        if not desc:
            continue
        descriptions = [d.strip() for d in desc.replace("；", ";").split(";") if d.strip()]
        if len(descriptions) < min_fragments:
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

    # ── 2. 反思侧直接持有 llm_client + language 调 LLM，
    #      不复用 MetadataExtractionStep：其基类 call_structured 依赖
    #      llm_client.response_structured（OpenAIClient 接口），与反思注入的
    #      RedBearLLM 不兼容。模板与 schema 仍复用抽取引擎既有资源。

    extracted = 0
    failed = 0
    model_failure_count = 0
    reason_codes: List[str] = []
    model_types: List[str] = []
    failed_operations: List[str] = []
    details: List[Dict[str, Any]] = []  # 每个实体的详细变更记录
    trace_entities: List[dict] = []
    trace_llm: List[dict] = []
    trace_changes: List[dict] = []

    # ── 3. 遍历候选实体 ──
    for entity_dict in candidates:
        entity_id = entity_dict["entity_id"]
        entity_name = entity_dict.get("entity_name", "")

        try:
            patched = await _extract_single_entity(
                connector=connector,
                llm_client=llm_client,
                language=language,
                entity_id=entity_id,
                entity_name=entity_name,
                descriptions=entity_dict.get("descriptions", []),
                allowed_fields=ALLOWED_METADATA_FIELDS,
                metadata_query=ENTITY_METADATA_QUERY,
                metadata_patch=ENTITY_METADATA_PATCH,
                max_list_len_per_field=max_list_len_per_field,
                collect_trace=collect_trace,
            )
            if patched:
                extracted += 1
                details.append({
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                    "ops": patched.get("operations_detail", []),
                    "counts": patched.get("counts", {}),
                })
                if collect_trace and patched.get("_trace"):
                    tr = patched["_trace"]
                    trace_entities.append(tr["input"])
                    trace_llm.append(tr["llm_raw"])
                    trace_changes.extend(tr["changes"])
                if entity_dict.get("end_user_id") and patched.get("post_state"):
                    _sync_metadata_to_pg(
                        end_user_id=entity_dict["end_user_id"],
                        metadata=patched["post_state"],
                    )
                    # PG 同步作为一次副作用单列（仅在确有 post_state 时）
                    if collect_trace:
                        trace_changes.append({
                            "target_type": "metadata_field", "target_id": entity_id,
                            "target_name": entity_name, "action": "sync_pg",
                            "field_changes": [], "status": "applied", "reason": None,
                            "extra": {"synced_fields": list((patched["post_state"] or {}).keys())},
                        })
        except ReflectionBusinessError as exc:
            failed += 1
            model_failure_count += 1
            reason = exc.reason_code.value
            if reason not in reason_codes:
                reason_codes.append(reason)
                model_types.append(exc.model_type.value)
            if exc.failed_operation not in failed_operations:
                failed_operations.append(exc.failed_operation)
            logger.error(
                f"[Metadata] 实体 {entity_id} 元数据提取失败 "
                f"reason_code={reason} failed_operation={exc.failed_operation}",
                exc_info=True,
            )
        except Exception as e:
            failed += 1
            logger.warning(f"[Metadata] 实体 {entity_id} 元数据提取失败: {e}")

    out: Dict[str, Any] = {
        "status": "error" if model_failure_count else "success",
        "extracted": extracted,
        "failed": failed,
        "details": details,
        "business_failure_count": model_failure_count,
        "reason_codes": reason_codes,
        "model_types": model_types,
        "failed_operations": failed_operations,
    }
    if collect_trace:
        out["_trace"] = {
            "input": {"entities": trace_entities},
            "llm_raw": {"items": trace_llm},
            "changes": trace_changes,
        }
    return out


async def _run_metadata_llm(
    llm_client: Any,
    language: str,
    inp: "MetadataStepInput",
) -> "MetadataStepOutput":
    """反思侧直接调 LLM 完成元数据结构化提取，替代 MetadataExtractionStep.run。

    绕开 MetadataExtractionStep：其基类的 call_structured 依赖
    llm_client.response_structured（OpenAIClient 接口），反思注入的 RedBearLLM
    仅提供 call_structured 实例方法。此处复用抽取引擎的模板与 Pydantic schema，
    仅由反思侧接管调用协议。合法的空 operations 表示本轮无变更；模型调用或
    结构化结果异常必须抛出受控领域异常，由实体循环聚合，不能伪装成空结果。
    """
    from app.core.memory.utils.prompt.prompt_utils import prompt_env
    from app.core.memory.models.metadata_models import MetadataExtractionResponse
    from app.core.memory.storage_services.extraction_engine.steps.schema import (
        MetadataStepOutput,
    )

    template = prompt_env.get_template("extract_user_metadata.jinja2")
    prompt = template.render(
        language=language,
        input_json=json.dumps(
            {
                "description": inp.descriptions,
                "existing_metadata": inp.existing_metadata,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    try:
        raw = await llm_client.call_structured(
            [{"role": "user", "content": prompt}],
            MetadataExtractionResponse,
        )
    except Exception as exc:
        logger.error("[Metadata] LLM 提取失败", exc_info=True)
        raise ReflectionBusinessError(
            ReflectionFailureReason.MODEL_CALL_FAILED,
            "metadata_extraction_model_call",
            model_type=ReflectionModelType.LLM,
        ) from exc

    if raw is None:
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "metadata_extraction_result_parse",
            model_type=ReflectionModelType.LLM,
        )
    try:
        operations = list(raw.operations or [])
        dropped = getattr(raw, "_dropped_ops_count", 0) or 0
        return MetadataStepOutput(operations=operations, dropped_ops_count=dropped)
    except Exception as exc:
        logger.error("[Metadata] LLM 结构化结果解析失败", exc_info=True)
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "metadata_extraction_result_parse",
            model_type=ReflectionModelType.LLM,
        ) from exc


async def _extract_single_entity(
    connector: Any,
    llm_client: Any,
    language: str,
    entity_id: str,
    entity_name: str,
    descriptions: List[str],
    allowed_fields: Tuple[str, ...],
    metadata_query: str,
    metadata_patch: str,
    max_list_len_per_field: int,
    collect_trace: bool = False,
) -> Dict[str, Any] | None:
    """对单个 User 实体执行：读取已有元数据 → LLM 提取 → patch 回写。

    Returns:
        成功时返回 {"post_state": {...}}，跳过或无变更时返回 None。
        collect_trace=True 时额外带 "_trace"（input/llm_raw/changes 片段）。
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
    result = await _run_metadata_llm(llm_client, language, inp)

    if not result.has_any():
        logger.debug(f"[Metadata] 实体 {entity_name}({entity_id}) 无新增元数据")
        return None

    skipped_recs: List[dict] = []
    truncated_recs: List[dict] = []
    skipped_ops_count = _filter_invalid_old_values(
        result.operations, existing, entity_name, entity_id,
        skipped_sink=skipped_recs if collect_trace else None,
    )

    if not result.has_any():
        logger.info(f"[Metadata] 实体 {entity_name}({entity_id}) 所有 op 均被过滤，跳过 patch")
        # 全被过滤也产出 trace（只含 skipped），便于核对
        if collect_trace:
            return {"post_state": {}, "_trace": _build_meta_trace(
                entity_id, entity_name, descriptions, existing, result,
                applied_ops=[], skipped_recs=skipped_recs, truncated_recs=[])}
        return None

    # 构建详细变更列表（在 patch 前记录，因为 patch 后 operations 仍然有效）
    operations_detail = _build_operations_detail(result)

    patch_records = await connector.execute_query(
        metadata_patch,
        **_build_patch_params(
            result, entity_id, existing, entity_name,
            allowed_fields, max_list_len_per_field,
            truncated_sink=truncated_recs if collect_trace else None,
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
    out: Dict[str, Any] = {
        "post_state": post_state,
        "operations_detail": operations_detail,
        "counts": counts,
    }
    if collect_trace:
        out["_trace"] = _build_meta_trace(
            entity_id, entity_name, descriptions, existing, result,
            applied_ops=list(result.operations),
            skipped_recs=skipped_recs, truncated_recs=truncated_recs)
    return out


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
