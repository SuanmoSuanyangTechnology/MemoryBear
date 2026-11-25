# -*- coding: utf-8 -*-
"""元数据提取服务 (Metadata Extraction Service).

Layer 2 反思引擎第 4 阶段：
从用户实体的 description 碎片中提取结构化元数据，按分片串行增量 Patch，结果写入 PostgreSQL。

规范：
1. 存储：只写 PostgreSQL；已迁移/新用户写 meta_data_new，未迁移老用户写旧列新三键；
2. 结构：只调用 extract_user_metadata_coding_v1.jinja2，不再生成旧 8 字段；
3. 短会话：数据库读写使用独立短会话，LLM 调用期间不持有 DB 连接；
4. 门控与分片：碎片数 >= min_fragments（默认 5）才触发；碎片按 description 中的存储顺序分片，
   每个分片以上一分片的结果作为 existing_metadata 输入。
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.core.memory.models.metadata_models import (
    MetadataExtractionResponse,
    MetadataOperation,
    apply_metadata_operations,
    filter_valid_metadata,
    normalize_domain_ids,
    project_domain_names,
)
from app.core.memory.storage_services.reflection_engine.deterministic.metadata_migration import (
    MetadataBaseline,
    ensure_write_lock,
    read_metadata_baseline,
)
from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionBusinessError,
    ReflectionFailureReason,
    ReflectionModelType,
)
from app.core.memory.utils.prompt.prompt_utils import prompt_env
from app.plugins import get_plugin

logger = logging.getLogger(__name__)

# 默认每 10 条碎片形成一个分片
METADATA_CHUNK_SIZE: int = 10


# ── 1. 数据库短会话辅助函数 ──

def _fetch_special_domains(db: Any, end_user_id: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """联查用户使用过的全部 App 所绑定的特殊场景及字段，组装 special_domains。

    - 以 scene_name 作为场景键（special_domains / domain_specific_info 的键），多个 App 绑定同一场景只组装一次；
    - 场景内按 field_key 去重。
    调用方负责异常处理（rollback 并按无特殊场景处理）。
    本体表的查询由企业版插件承接；未注册插件时返回空配置。
    """
    special_domains: Dict[str, Any] = {}
    name_to_id_map: Dict[str, str] = {}
    plugin = get_plugin("memory.ontology_query")
    if plugin is None:
        return special_domains, name_to_id_map
    rows = plugin.get_profile_fields_for_user(db, end_user_id)

    # 场景名在租户内唯一 (UniqueConstraint(tenant_id, scene_name))，同一场景只组装一次
    for r in rows:
        domain_key = r.scene_name
        name_to_id_map[r.scene_name] = str(r.scene_type_id)
        if domain_key not in special_domains:
            special_domains[domain_key] = {
                "description": r.scene_description or "",
                "fields": [],
            }

        existing_fields = special_domains[domain_key]["fields"]
        if not any(f.get("field") == r.field_key for f in existing_fields):
            inc_ex = r.include_examples if isinstance(r.include_examples, list) else []
            exc_ex = r.exclude_examples if isinstance(r.exclude_examples, list) else []
            existing_fields.append({
                "field": r.field_key,
                "name_cn": r.field_label or "",
                "description": r.field_definition or "",
                "format": r.value_format or "对象：内容",
                "include_examples": inc_ex,
                "exclude_examples": exc_ex,
            })

    return special_domains, name_to_id_map


def _fetch_user_context_pg(
    end_user_id: str, baseline: MetadataBaseline,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    """独立短会话：读取已有元数据并动态加载关联应用的特殊领域 Schema。

    已声明场景的存储 ID 转为本轮 Schema 中的名称，供模型增量更新；
    未声明场景保留原键，继续交给 frozen_ds 保护。

    Returns:
        (existing_new_meta, fields_schema, name_to_id_map)
    """
    from app.db import get_db_context

    existing_meta_model: Dict[str, Any] = {
        "identity_background": [],
        "relationships": [],
        "domain_specific_info": {},
    }
    special_domains: Dict[str, Any] = {}
    name_to_id_map: Dict[str, str] = {}

    with get_db_context() as db:
        # 基线与写入列在调用模型前固定，最终 CAS 防止并发覆盖。
        raw_meta = baseline.metadata
        if raw_meta:
            ib = raw_meta.get("identity_background")
            if isinstance(ib, list):
                existing_meta_model["identity_background"] = [str(x).strip() for x in ib if str(x).strip()]
            rel = raw_meta.get("relationships")
            if isinstance(rel, list):
                existing_meta_model["relationships"] = [str(x).strip() for x in rel if str(x).strip()]
            ds = raw_meta.get("domain_specific_info")
            if isinstance(ds, dict):
                existing_meta_model["domain_specific_info"] = {
                    key: list(values) for key, values in ds.items() if isinstance(values, list)
                }

        # 2. 通过企业版插件加载特殊场景；社区版未注册插件时返回空配置。
        try:
            special_domains, name_to_id_map = _fetch_special_domains(db, end_user_id)
        except Exception as e:
            # 查询失败：回滚后按无特殊场景处理，只提取默认字段
            logger.warning(
                f"[Metadata] 特殊场景联查异常，按无特殊场景处理 end_user_id={end_user_id}: {e}"
            )
            try:
                db.rollback()
            except Exception:
                pass
            special_domains = {}

    # 使用与本轮 Schema 相同的映射快照，避免另查名称导致改名期间不一致。
    id_to_name_map = {scene_id: name for name, scene_id in name_to_id_map.items()}
    existing_meta_model["domain_specific_info"] = project_domain_names(
        existing_meta_model["domain_specific_info"], id_to_name_map, preserve_unmapped=True,
    )

    fields_schema = {"special_domains": special_domains}
    return existing_meta_model, fields_schema, name_to_id_map


def _build_metadata_write_payload(model_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """仅生成新三键；合法空结果压缩为 {}，与 NULL 严格区分。"""
    payload = {
        "identity_background": list(model_metadata.get("identity_background") or []),
        "relationships": list(model_metadata.get("relationships") or []),
        "domain_specific_info": dict(model_metadata.get("domain_specific_info") or {}),
    }
    return payload if any(payload.values()) else {}


def _save_metadata_to_pg(
    end_user_id: str, model_metadata: Dict[str, Any], name_to_id_map: Dict[str, str],
    baseline: MetadataBaseline,
) -> bool:
    """按模型调用前的基线路由 + CAS；老用户未补迁时绝不写新列。"""
    from app.db import get_db_context
    from sqlalchemy import text

    new_keys = _build_metadata_write_payload(model_metadata)
    if new_keys:
        new_keys["domain_specific_info"] = normalize_domain_ids(
            new_keys.get("domain_specific_info") or {}, name_to_id_map, preserve_unmapped=True,
        )
    column = baseline.write_column  # 仅来自受控策略，禁止外部输入 SQL 标识符
    if column == "meta_data" and not new_keys:
        # 旧列按键合并时，空结果必须显式清空新三键，不能 || {} 留下旧值。
        new_keys = {"identity_background": [], "relationships": [], "domain_specific_info": {}}
    assignment = (
        "meta_data = coalesce(info.meta_data, '{}'::jsonb) || CAST(:new_keys AS jsonb)"
        if column == "meta_data" else "meta_data_new = CAST(:new_keys AS jsonb)"
    )
    try:
        if not ensure_write_lock():
            return False
        with get_db_context() as db:
            result = db.execute(text(f"""
                UPDATE end_user_info AS info SET {assignment}, updated_at = NOW()
                WHERE info.end_user_id = :end_user_id
                  AND info.meta_data IS NOT DISTINCT FROM CAST(:old AS jsonb)
                  AND info.meta_data_new IS NOT DISTINCT FROM CAST(:new AS jsonb)
                  AND EXISTS (
                    SELECT 1 FROM end_users AS eu JOIN workspaces AS ws ON ws.id = eu.workspace_id
                    WHERE eu.id = info.end_user_id AND eu.is_active IS TRUE AND ws.is_active IS TRUE
                  )
            """), {
                "end_user_id": uuid.UUID(end_user_id),
                "new_keys": json.dumps(new_keys, ensure_ascii=False),
                "old": None if baseline.meta_data is None else json.dumps(baseline.meta_data),
                "new": None if baseline.meta_data_new is None else json.dumps(baseline.meta_data_new),
            })
            db.commit()
            return bool(result.rowcount)
    except Exception:
        logger.exception("[Metadata] 写入失败 end_user_id=%s column=%s", end_user_id, column)
        return False


def _build_meta_changes(
    entity_id: str,
    entity_name: str,
    records: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 apply / filter 收集的逐条记录转换为快照 changes（字段级 ChangeRecord）。

    - op 执行结果：status 为 applied / skipped（reason 说明跳过原因）；
    - 清洗剔除 / 去重合并的条目：action=delete_field，reason 说明原因；
    - extra.chunk_index：所属分片序号，None 表示提取前的清洗。
    """
    return [
        {"target_type": "metadata_field", "target_id": entity_id,
         "target_name": entity_name, "action": r["action"],
         "field_changes": [{"field": r["field"], "old": r["old"], "new": r["new"]}],
         "status": r["status"], "reason": r["reason"],
         "extra": {"chunk_index": r["chunk_index"]}}
        for r in records
    ]


# ── 2. LLM 单分片调用 ──


async def _run_chunk_llm(
    llm_client: Any,
    language: str,
    entity_id: str,
    descriptions: List[str],
    existing_meta_model: Dict[str, Any],
    fields_schema: Dict[str, Any],
) -> List[MetadataOperation]:
    template = prompt_env.get_template("extract_user_metadata_coding_v1.jinja2")
    prompt = template.render(
        language=language,
        entity_id=entity_id,
        fields_schema=fields_schema,
        existing_metadata_json=json.dumps(existing_meta_model, ensure_ascii=False, indent=2),
        input_json=json.dumps(
            {"entity_id": entity_id, "descriptions": descriptions},
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
        logger.warning(f"[Metadata] 分片提取调用异常: {exc}")
        raise ReflectionBusinessError(
            ReflectionFailureReason.MODEL_CALL_FAILED,
            "metadata_chunk_call",
            model_type=ReflectionModelType.LLM,
        ) from exc

    if raw is None:
        logger.warning("[Metadata] 分片提取结构化结果为空")
        raise ReflectionBusinessError(
            ReflectionFailureReason.RESULT_PARSE_FAILED,
            "metadata_chunk_parse",
            model_type=ReflectionModelType.LLM,
        )
    return list(raw.operations or [])


# ── 3. 主服务入口 ──


async def extract_metadata_for_user(
    connector: Any,
    llm_client: Any,
    end_user_id: str,
    language: str = "zh",
    min_fragments: int = 5,
    collect_trace: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    """对指定用户的 User 实体执行时序串行增量元数据提取并持久化到 PostgreSQL。

    流程：活动用户/信息行校验 → 原有碎片门控 → 读取路由列基线 →
    仅调用新三键提示词 → 保护未声明领域 → CAS 写入对应列。
    迁移补偿由 Layer2Inspector 在本阶段前完成，失败不影响旧列新三键继续更新。
    """
    from app.repositories.neo4j.cypher_queries import USER_ENTITY_FOR_METADATA

    baseline = read_metadata_baseline(end_user_id)
    if baseline is None:
        return {"status": "skipped", "reason": "inactive_or_missing_info", "extracted": 0, "failed": 0, "details": []}

    # ── Step 1. 扫描 Neo4j User 实体 ──
    try:
        records = await connector.execute_query(
            USER_ENTITY_FOR_METADATA, end_user_id=end_user_id
        )
    except Exception as e:
        logger.warning(f"[Metadata] 查询 User 实体失败: {e}")
        return {"status": "error", "extracted": 0, "failed": 1, "details": []}

    if not records:
        logger.debug(f"[Metadata] 未找到 User 实体，跳过: end_user_id={end_user_id}")
        return {"extracted": 0, "failed": 0, "details": []}

    # 过滤与碎片切分
    target_entity = None
    target_fragments: List[str] = []
    for rec in records:
        desc = (rec.get("description") or "").strip()
        if not desc:
            continue
        # 碎片写入与描述合并均以全角分号 '；' 分隔，此处保持一致
        fragments = [d.strip() for d in desc.split("；") if d.strip()]
        if len(fragments) >= min_fragments:
            target_entity = rec
            target_fragments = fragments
            break

    # ── Step 2. 门控判断 (>= min_fragments 才触发) ──
    if not target_entity or not target_fragments:
        logger.debug(
            f"[Metadata] 碎片数未达门控阈值 ({min_fragments})，跳过元数据提取: "
            f"end_user_id={end_user_id}"
        )
        return {"extracted": 0, "failed": 0, "details": []}

    entity_id = target_entity["entity_id"]
    entity_name = target_entity.get("entity_name", "用户")

    logger.info(
        f"[Metadata] 实体 {entity_name}({entity_id}) 触发提取，"
        f"碎片总数: {len(target_fragments)}"
    )

    try:
        # ── Step 3. 独立短会话读 PG ──
        existing_meta_model, fields_schema, name_to_id_map = _fetch_user_context_pg(end_user_id, baseline)
        active_domains = list(fields_schema.get("special_domains", {}).keys())
        logger.info(f"[Metadata] 加载已有元数据与特殊领域: special_domains={active_domains}")

        # ── Step 4. 串行迭代提取阶段 ──
        chunk_size = kwargs.get("chunk_size", METADATA_CHUNK_SIZE)
        if not isinstance(chunk_size, int) or chunk_size < 1:
            raise ValueError("metadata chunk_size must be positive")
        chunks = [
            target_fragments[i : i + chunk_size]
            for i in range(0, len(target_fragments), chunk_size)
        ]
        logger.info(
            f"[Metadata] 碎片切分为 {len(chunks)} 个分片串行迭代提取 (步长 {chunk_size})"
        )

        current_meta: Dict[str, Any] = existing_meta_model
        all_applied_ops: List[MetadataOperation] = []
        trace_llm: List[Dict[str, Any]] = []
        # 开启快照时收集逐条变更记录；未开启时为 None，apply / filter 不做任何记录
        trace_records: Optional[List[Dict[str, Any]]] = [] if collect_trace else None

        # 未在当前声明中的特殊场景（换绑/解绑/改名/联查失败）：不传给模型、不参与 Patch，写回前原样合并回去
        declared_domains = set(fields_schema.get("special_domains", {}).keys())
        existing_ds = current_meta.get("domain_specific_info") or {}
        frozen_ds = {k: v for k, v in existing_ds.items() if k not in declared_domains}
        current_meta = {
            **current_meta,
            "domain_specific_info": {k: v for k, v in existing_ds.items() if k in declared_domains},
        }
        # 提取前清洗：剔除非法条目与已删除字段的条目，结果作为第一个分片的输入
        current_meta = filter_valid_metadata(current_meta, fields_schema, trace_records)
        if frozen_ds:
            logger.info(f"[Metadata] 保留未声明特殊场景数据，不参与本次提取: {list(frozen_ds.keys())}")

        for chunk_idx, chunk in enumerate(chunks):
            chunk_ops = await _run_chunk_llm(
                llm_client=llm_client,
                language=language,
                entity_id=entity_id,
                descriptions=chunk,
                existing_meta_model=current_meta,
                fields_schema=fields_schema,
            )
            if collect_trace:
                trace_llm.append({
                    "chunk_index": chunk_idx,
                    "operations": [op.model_dump() for op in chunk_ops],
                })

            if chunk_ops:
                mark = len(trace_records) if trace_records is not None else 0
                # 1. 在内存中应用增量 Patch（按字段路由，条目按三段式规范化后比对）
                current_meta = apply_metadata_operations(current_meta, chunk_ops, fields_schema, trace_records)
                # 2. 白名单校验清洗（二级键、三级键与场景字段）
                current_meta = filter_valid_metadata(current_meta, fields_schema, trace_records)
                all_applied_ops.extend(chunk_ops)
                if trace_records is not None:
                    for rec in trace_records[mark:]:
                        rec["chunk_index"] = chunk_idx

            logger.info(
                f"[Metadata] 分片 {chunk_idx + 1}/{len(chunks)} 处理完成 (碎片数: {len(chunk)}): "
                f"新字段 operations={len(chunk_ops)} 条, "
                f"累计生效 operations={len(all_applied_ops)} 条"
            )

        # ── Step 5. 合并未声明场景的原有数据 ──
        current_meta["domain_specific_info"] = {**frozen_ds, **current_meta["domain_specific_info"]}

        # 独立短会话写入路由列，旧 8 字段不再生成或更新
        current_state = _build_metadata_write_payload(current_meta)
        saved = _save_metadata_to_pg(end_user_id, current_state, name_to_id_map, baseline)
        if not saved:
            return {"status": "error", "extracted": 0, "failed": 1, "details": []}

        # 组装变更详情
        applied_ops_detail: List[Dict[str, Any]] = [
            {"op": op.op, "field": op.field, "value": op.value, "old": op.old_value, "new": op.new_value}
            for op in all_applied_ops
        ]
        details = [{
            "entity_id": entity_id,
            "entity_name": entity_name,
            "ops": applied_ops_detail,
            "final_domains": (
                ["identity_background", "relationships"]
                + list(current_meta.get("domain_specific_info", {}).keys())
            ),
        }]

        out: Dict[str, Any] = {
            "status": "success",
            "extracted": 1,
            "failed": 0,
            "details": details,
            "post_state": current_state,
        }
        if collect_trace:
            out["_trace"] = {
                "input": {
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                    "fragment_count": len(target_fragments),
                    "chunks_count": len(chunks),
                    "descriptions": target_fragments,
                    "existing_metadata": existing_meta_model,
                    "special_domains": active_domains,
                },
                "llm_raw": {"entity_id": entity_id, "chunks": trace_llm},
                "changes": _build_meta_changes(entity_id, entity_name, trace_records or []),
            }
        return out

    except ReflectionBusinessError as rbe:
        logger.error(
            f"[Metadata] 元数据提取失败 reason_code={rbe.reason_code.value} "
            f"failed_operation={rbe.failed_operation}",
            exc_info=True,
        )
        return {
            "status": "error",
            "extracted": 0,
            "failed": 1,
            "details": [],
            "business_failure_count": 1,
            "reason_codes": [rbe.reason_code.value],
            "model_types": [rbe.model_type.value],
            "failed_operations": [rbe.failed_operation],
        }
    except Exception as e:
        logger.error(f"[Metadata] 元数据提取未预期异常: {e}", exc_info=True)
        return {"status": "error", "extracted": 0, "failed": 1, "details": []}
