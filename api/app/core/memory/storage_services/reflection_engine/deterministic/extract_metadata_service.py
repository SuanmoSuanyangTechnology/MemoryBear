# -*- coding: utf-8 -*-
"""元数据提取服务 (Metadata Extraction Service).

Layer 2 反思引擎第 4 阶段：
从用户实体的 description 碎片中提取结构化元数据，按分片串行增量 Patch，结果写入 PostgreSQL。

规范：
1. 存储：结果只写入 PostgreSQL end_user_info.meta_data 的 identity_background、relationships、
   domain_specific_info 三个键，不写 Neo4j；
2. 结构：通用字段（identity_background, relationships）与 App 绑定的特殊场景（domain_specific_info.<场景名>）并列；
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
)
from app.core.memory.storage_services.reflection_engine.errors import (
    ReflectionBusinessError,
    ReflectionFailureReason,
    ReflectionModelType,
)
from app.core.memory.utils.prompt.prompt_utils import prompt_env

logger = logging.getLogger(__name__)

# 默认每 10 条碎片形成一个分片
METADATA_CHUNK_SIZE: int = 10


# ── 1. 数据库短会话辅助函数 ──

_ONTOLOGY_TABLES_AVAILABLE: Optional[bool] = None


def _check_ontology_tables_available(db: Any) -> bool:
    """检测本体工程 4 张表是否存在。检测成功时结果进程级缓存；检测异常时本次按不可用处理，不写缓存，下次调用重新检测。"""
    global _ONTOLOGY_TABLES_AVAILABLE
    if _ONTOLOGY_TABLES_AVAILABLE is not None:
        return _ONTOLOGY_TABLES_AVAILABLE

    from sqlalchemy import text
    try:
        sql = text("""
            SELECT to_regclass('app_ontology_binding') IS NOT NULL
               AND to_regclass('ontology') IS NOT NULL
               AND to_regclass('ontology_scene_type') IS NOT NULL
               AND to_regclass('ontology_profile_field') IS NOT NULL AS available;
        """)
        res = db.execute(sql).scalar()
        _ONTOLOGY_TABLES_AVAILABLE = bool(res)
        return _ONTOLOGY_TABLES_AVAILABLE
    except Exception as e:
        logger.warning(f"[Metadata] 检测本体表可用性异常，本次按不可用处理: {e}")
        try:
            db.rollback()
        except Exception:
            pass
        return False


def _fetch_special_domains(db: Any, end_user_id: str) -> Dict[str, Any]:
    """联查用户使用过的全部 App 所绑定的特殊场景及字段，组装 special_domains。

    - 以 scene_name 作为场景键（special_domains / domain_specific_info 的键），多个 App 绑定同一场景只组装一次；
    - 场景内按 field_key 去重。
    调用方负责异常处理（rollback 并按无特殊场景处理）。
    本体表的 ORM 模型由企业版定义，此处使用原生 SQL 查询。
    """
    from app.models import Conversation
    from sqlalchemy import bindparam, select, text

    special_domains: Dict[str, Any] = {}

    # 查关联的去重 app_ids
    stmt_apps = (
        select(Conversation.app_id)
        .where(Conversation.user_id == end_user_id)
        .distinct()
    )
    app_ids = [r[0] for r in db.execute(stmt_apps).all() if r[0]]
    if not app_ids:
        return special_domains

    stmt_fields = text("""
        SELECT st.scene_name,
               st.scene_description,
               pf.field_key,
               pf.field_label,
               pf.field_definition,
               pf.value_format,
               pf.include_examples,
               pf.exclude_examples
        FROM app_ontology_binding AS b
        JOIN ontology AS o ON b.ontology_id = o.ontology_id
        JOIN ontology_scene_type AS st ON o.scene_type_id = st.scene_type_id
        JOIN ontology_profile_field AS pf ON st.scene_type_id = pf.scene_type_id
        WHERE b.app_id IN :app_ids
          AND st.is_system_default IS FALSE
          AND pf.is_system_default IS FALSE
        ORDER BY st.scene_name, pf.created_at, pf.field_key
    """).bindparams(bindparam("app_ids", expanding=True))
    rows = db.execute(stmt_fields, {"app_ids": app_ids}).all()

    # 场景名在租户内唯一 (UniqueConstraint(tenant_id, scene_name))，同一场景只组装一次
    for r in rows:
        domain_key = r.scene_name
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
                "format": r.value_format,
                "include_examples": inc_ex,
                "exclude_examples": exc_ex,
            })

    return special_domains


def _fetch_user_context_pg(end_user_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """独立短会话：读取已有元数据并动态加载关联应用的特殊领域 Schema。

    Returns:
        (existing_meta_model, fields_schema)
    """
    from app.db import get_db_context
    from app.models import EndUserInfo

    existing_meta_model: Dict[str, Any] = {
        "identity_background": [],
        "relationships": [],
        "domain_specific_info": {},
    }
    special_domains: Dict[str, Any] = {}

    try:
        eu_uuid = uuid.UUID(end_user_id)
    except Exception:
        logger.warning(f"[Metadata] 非法 end_user_id 格式: {end_user_id}")
        return existing_meta_model, {"special_domains": {}}

    with get_db_context() as db:
        # 1. 读取已有元数据：只读 identity_background、relationships、domain_specific_info 三个键
        info = db.query(EndUserInfo).filter_by(end_user_id=eu_uuid).first()
        if info and isinstance(info.meta_data, dict):
            raw_meta = info.meta_data
            ib = raw_meta.get("identity_background")
            if isinstance(ib, list):
                existing_meta_model["identity_background"] = [str(x).strip() for x in ib if str(x).strip()]
            rel = raw_meta.get("relationships")
            if isinstance(rel, list):
                existing_meta_model["relationships"] = [str(x).strip() for x in rel if str(x).strip()]
            ds = raw_meta.get("domain_specific_info")
            if isinstance(ds, dict):
                cleaned_ds = {}
                for k, v in ds.items():
                    if isinstance(v, list):
                        cleaned_ds[k] = [str(x).strip() for x in v if str(x).strip()]
                existing_meta_model["domain_specific_info"] = cleaned_ds

        # 2. 本体表存在时查询特殊场景，不存在时跳过
        if _check_ontology_tables_available(db):
            try:
                special_domains = _fetch_special_domains(db, end_user_id)
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

    fields_schema = {"special_domains": special_domains}
    return existing_meta_model, fields_schema


def _save_metadata_to_pg(end_user_id: str, model_metadata: Dict[str, Any]) -> bool:
    """独立短会话：将模型侧元数据通过 JSONB 键合并原子写回 PG end_user_info.meta_data。

    规则：
    1. 只更新 identity_background, relationships, domain_specific_info 三个键。
    2. 使用 JSONB 键合并写入 (meta_data = coalesce(meta_data, '{}'::jsonb) || :new_keys::jsonb)，
       meta_data 中的其他键（含旧 8 字段）保持不变。
    3. end_user_info 记录不存在时跳过写入，只记 warning，不视为失败。
    """
    if not model_metadata:
        return True
    try:
        from app.db import get_db_context
        from app.models import EndUserInfo
        from sqlalchemy import text

        eu_uuid = uuid.UUID(end_user_id)
        new_keys = {
            "identity_background": list(model_metadata.get("identity_background") or []),
            "relationships": list(model_metadata.get("relationships") or []),
            "domain_specific_info": dict(model_metadata.get("domain_specific_info") or {}),
        }

        with get_db_context() as db:
            info = db.query(EndUserInfo).filter_by(end_user_id=eu_uuid).first()
            if not info:
                logger.warning(
                    f"[Metadata][PG] end_user_info 记录不存在，跳过写入: end_user_id={end_user_id}"
                )
                return True
            stmt = text("""
                UPDATE end_user_info
                SET meta_data = coalesce(meta_data, '{}'::jsonb) || CAST(:new_keys AS jsonb),
                    updated_at = NOW()
                WHERE end_user_id = :end_user_id
            """)
            db.execute(stmt, {
                "new_keys": json.dumps(new_keys, ensure_ascii=False),
                "end_user_id": eu_uuid,
            })
            db.commit()

        logger.info(
            f"[Metadata][PG] 成功持久化元数据 (新结构无损合并): end_user_id={end_user_id}, "
            f"special_domains={list(new_keys['domain_specific_info'].keys())}"
        )
        return True
    except Exception as e:
        logger.error(f"[Metadata][PG] 保存元数据失败: end_user_id={end_user_id}, error={e}", exc_info=True)
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

    流程：
    1. Neo4j 扫描 User 节点并获取 description；
    2. 碎片数 >= min_fragments (默认 5) 门控检查，< 5 时跳过；
    3. 碎片保持 description 中的存储顺序；
    4. 独立短会话读 PG：获取初始已有元数据 current_meta 与应用特殊领域 Schema；
    5. 切片并串行迭代：每轮输入当前分片碎片与上轮最新的 current_meta，调用 LLM 产出增量 operations；
    6. 内存中本地有序应用 Patch 并执行白名单校验清洗，结果作为下一分片的基准状态；
    7. 循环结束后，独立短会话将最终的 current_meta 写入 PG end_user_info.meta_data (按键无损合并)；
    8. 返回状态放行后续巡检。
    """
    from app.repositories.neo4j.cypher_queries import USER_ENTITY_FOR_METADATA

    # ── Step 1. 扫描 Neo4j User 实体 ──
    try:
        records = await connector.execute_query(
            USER_ENTITY_FOR_METADATA, end_user_id=end_user_id
        )
    except Exception as e:
        logger.warning(f"[Metadata] 查询 User 实体失败: {e}")
        return {"extracted": 0, "failed": 0, "details": []}

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
        existing_meta_model, fields_schema = _fetch_user_context_pg(end_user_id)
        active_domains = list(fields_schema.get("special_domains", {}).keys())
        logger.info(f"[Metadata] 加载已有元数据与特殊领域: special_domains={active_domains}")

        # ── Step 4. 串行迭代提取阶段 ──
        chunk_size = kwargs.get("chunk_size", METADATA_CHUNK_SIZE)
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
                f"产出 operations={len(chunk_ops)} 条, 累计生效 operations={len(all_applied_ops)} 条"
            )

        # ── Step 5. 合并未声明场景的原有数据 ──
        current_meta["domain_specific_info"] = {**frozen_ds, **current_meta["domain_specific_info"]}

        # 独立短会话写入 PG (无损合并)
        saved = _save_metadata_to_pg(end_user_id, current_meta)
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
            "final_domains": ["identity_background", "relationships"] + list(current_meta.get("domain_specific_info", {}).keys()),
        }]

        out: Dict[str, Any] = {
            "status": "success",
            "extracted": 1,
            "failed": 0,
            "details": details,
            "post_state": current_meta,
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
