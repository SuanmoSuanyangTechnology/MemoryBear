"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
metadata extraction and reflection pipeline.

Field definitions align with Jinja2 prompt template:
- extract_user_metadata_coding_v1.jinja2 (增量 Patch 提取)

Storage schema: identity_background / relationships 为 list[str]，
domain_specific_info 为 dict[str, list[str]]。
"""

import logging
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

# 9+4 基础字段定义与白名单
IDENTITY_BACKGROUND_SUBCATEGORIES: tuple[str, ...] = (
    "name",
    "age",
    "from",
    "residence",
    "education",
    "occupation",
    "family",
    "religion_or_politics",
    "health",
)

# 三级键白名单（设计文档 2.2）
IDENTITY_BACKGROUND_LEVEL3_WHITELIST: Dict[str, set[str]] = {
    "name": {"full_name", "preferred_alias"},
    "age": {"age_or_age_range", "date_of_birth"},
    "from": {"birthplace", "native_place"},
    "residence": {""},
    "education": {""},
    "occupation": {""},
    "family": {
        "living_status", "marital_status", "father", "mother",
        "partner", "child", "sibling", "relative",
    },
    "religion_or_politics": {"religion", "politics"},
    "health": {"physical", "mental"},
}

RELATION_TYPES: tuple[str, ...] = (
    "person",
    "group",
    "object",
    "pet",
)

# 旧 8 字段白名单，供 sidecar_step_schema / MetadataExtractionStep 使用
ALLOWED_METADATA_FIELDS: tuple[str, ...] = (
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)

# 显式过滤/禁止作为独立 operation 输出的字段
FILTERED_METADATA_FIELDS: tuple[str, ...] = (
    "aliases",
    "domain_specific_info",
)

OperationLiteral = Literal["add", "delete", "update"]


def parse_entry_triplet(item: str) -> Optional[Tuple[str, str, str]]:
    """按 '|' 切分最多两次 (split('|', 2))，去除首尾空白，得到 (二级键, 三级键, 正文)。

    规则：
    1. 只有两段 (a | b) 时视为三级键为空，返回 (a, "", b)。
    2. 三段 (a | b | c) 返回 (a, b, c)。
    3. 正文为空时返回 None。
    """
    raw = (item or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("|", 2)]
    if len(parts) == 3:
        l2, l3, content = parts[0], parts[1], parts[2]
    elif len(parts) == 2:
        l2, l3, content = parts[0], "", parts[1]
        logger.warning(f"[Metadata] 两段式条目已规范化为三段式（三级键置空）: {raw!r}")
    else:
        return None
    if not content:
        return None
    return l2, l3, content


def format_entry_triplet(l2: str, l3: str, content: str) -> str:
    """格式化为三段式字符串。三级键为空时格式为 '<二级键> | | <正文>'。"""
    l2_clean = (l2 or "").strip()
    l3_clean = (l3 or "").strip()
    content_clean = (content or "").strip()
    return f"{l2_clean} | {l3_clean} | {content_clean}" if l3_clean else f"{l2_clean} | | {content_clean}"


def _entry_key(item: str) -> str:
    """条目比对键：按 '|' 切分最多两次并去除各段首尾空白后重新拼接（不打日志）。

    使 'a|b|c'、'a | b | c' 视为同一条目；两段式 'a | c' 视为三级键为空，即等同 'a | | c'。
    """
    parts = [p.strip() for p in (item or "").strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    return "|".join(parts)


def _dedupe_entries(
    items: List[str],
    field: str = "",
    trace_sink: Optional[List[Dict[str, Any]]] = None,
) -> List[str]:
    """按比对键去重，保留首次出现的条目顺序。"""
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        key = _entry_key(item)
        if key in seen:
            _trace(trace_sink, "delete_field", field, item, None, reason="dedup")
            continue
        seen.add(key)
        result.append(item)
    return result


def _normalize_entry(item: str) -> str:
    """规范化为三段式写法（不打日志），用于快照记录与库内存储写法保持一致。"""
    parts = _entry_key(item).split("|", 2)
    return format_entry_triplet(*parts) if len(parts) == 3 else (item or "").strip()


def _trace(
    sink: Optional[List[Dict[str, Any]]],
    action: str,
    field: str,
    old: Optional[str],
    new: Optional[str],
    status: str = "applied",
    reason: Optional[str] = None,
) -> None:
    """向快照 sink 追加一条变更记录；sink 为 None（未开启快照）时不做任何事。"""
    if sink is None:
        return
    sink.append({
        "chunk_index": None,
        "action": action,
        "field": field,
        "old": _normalize_entry(old) if old else None,
        "new": _normalize_entry(new) if new else None,
        "status": status,
        "reason": reason,
    })


class MetadataOperation(BaseModel):
    """单个增量 Patch 操作。

    格式规范：
    - add: op="add", field="...", value="..."
    - delete: op="delete", field="...", old_value="..." (或 value="...")
    - update: op="update", field="...", old_value="...", value="..." (或 new_value="...")
    """

    model_config = ConfigDict(extra="ignore")

    op: OperationLiteral
    field: str
    value: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None

    @model_validator(mode="after")
    def _validate_shape(self) -> "MetadataOperation":
        field = (self.field or "").strip()
        if not field:
            raise ValueError("field cannot be empty")
        if field in FILTERED_METADATA_FIELDS:
            raise ValueError(f"field {self.field!r} is filtered at runtime")

        self.field = field

        # 兼容处理：有些 LLM 在 update/delete 时使用 value / new_value 互相混淆
        if self.op == "add":
            v = (self.value or "").strip()
            if not v:
                raise ValueError("`add` operation requires non-empty `value`")
            self.value = v
        elif self.op == "delete":
            ov = (self.old_value or self.value or "").strip()
            if not ov:
                raise ValueError("`delete` operation requires non-empty `old_value`")
            self.old_value = ov
            self.value = ov  # 保证 value 与 old_value 同步
        else:  # update
            ov = (self.old_value or "").strip()
            nv = (self.new_value or self.value or "").strip()
            if not ov or not nv:
                raise ValueError("`update` operation requires both `old_value` and `new_value`/`value`")
            self.old_value = ov
            self.new_value = nv
            self.value = nv
        return self


class MetadataExtractionResponse(BaseModel):
    """LLM 元数据提取响应结构（单分片增量 Patch 输出）"""

    model_config = ConfigDict(extra="ignore")

    operations: List[MetadataOperation] = Field(
        default_factory=list,
        description="LLM 输出的元数据 patch 操作列表",
    )

    @model_validator(mode="before")
    @classmethod
    def _filter_operations(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        # 兼容 items 包装层：{"items": [{"operations": [...]}]}
        raw_ops = data.get("operations")
        if raw_ops is None and isinstance(data.get("items"), list) and len(data["items"]) > 0:
            first_item = data["items"][0]
            if isinstance(first_item, dict):
                raw_ops = first_item.get("operations")

        if not isinstance(raw_ops, list):
            return {**data, "operations": []}

        cleaned: List[MetadataOperation] = []
        dropped = 0
        dropped_details: List[str] = []
        for item in raw_ops:
            try:
                cleaned.append(MetadataOperation.model_validate(item))
            except Exception as e:
                dropped += 1
                dropped_details.append(f"item={item!r}, reason={e}")
                continue

        if dropped > 0:
            logger.warning(
                f"[Metadata] 丢弃了 {dropped}/{len(raw_ops)} 条无效 op: "
                + "; ".join(dropped_details[:5])
                + ("..." if dropped > 5 else "")
            )

        return {**data, "operations": cleaned, "_dropped_ops_count": dropped}


def apply_metadata_operations(
    current_meta: Dict[str, Any],
    operations: List[MetadataOperation],
    fields_schema: Optional[Dict[str, Any]] = None,
    trace_sink: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """在内存中将增量 Patch 操作序列有序应用到模型侧元数据字典。

    模型侧结构固定包含 3 个顶层键：
    - identity_background: List[str]
    - relationships: List[str]
    - domain_specific_info: Dict[str, List[str]]

    规则（条目比对均按三段式规范化后的写法进行，见 _entry_key）：
    1. delete: 匹配 old_value 或 value，从对应列表中移除；未匹配到则丢弃该操作；
    2. update: 匹配 old_value 并替换为 new_value/value；未匹配到则丢弃该操作，不追加新值；
    3. add: value 不在对应列表中时追加，已存在则跳过；
    4. 路由：identity_background、relationships 写入同名顶层数组；已声明的特殊场景写入 domain_specific_info.<scene_key>；
       其他 field 的操作丢弃。

    trace_sink 不为 None 时（开启快照），逐条记录 op 的执行结果（applied / skipped）。
    """
    special_domains: Dict[str, Any] = (fields_schema or {}).get("special_domains", {})
    allowed_domains = set(special_domains.keys())

    updated: Dict[str, Any] = {
        "identity_background": list(current_meta.get("identity_background") or []),
        "relationships": list(current_meta.get("relationships") or []),
        "domain_specific_info": {
            k: list(v)
            for k, v in (current_meta.get("domain_specific_info") or {}).items()
            if isinstance(v, list)
        },
    }

    act_map = {"add": "add_field", "update": "update_field", "delete": "delete_field"}

    for op in operations:
        field = op.field
        target_list: Optional[List[str]] = None
        action = act_map.get(op.op, "update_field")

        if field == "identity_background":
            target_list = updated["identity_background"]
            trace_field = field
        elif field == "relationships":
            target_list = updated["relationships"]
            trace_field = field
        elif field in allowed_domains:
            if field not in updated["domain_specific_info"]:
                updated["domain_specific_info"][field] = []
            target_list = updated["domain_specific_info"][field]
            trace_field = f"domain_specific_info.{field}"
        else:
            logger.warning(f"[Metadata] 丢弃未声明 field 的操作: op={op.op}, field={field}")
            _trace(trace_sink, action, field, op.old_value, op.new_value or op.value,
                   status="skipped", reason="undeclared_field")
            continue

        # 条目按三段式规范化后的键比对，忽略空格与分隔写法差异
        if op.op == "delete":
            target_val = op.old_value or op.value
            if not target_val:
                _trace(trace_sink, action, trace_field, None, None, status="skipped", reason="missing_value")
                continue
            target_key = _entry_key(target_val)
            updated_list = [x for x in target_list if _entry_key(x) != target_key]
            if len(updated_list) == len(target_list):
                logger.warning(f"[Metadata] delete 未匹配到已有条目，丢弃操作: {target_val}")
                _trace(trace_sink, action, trace_field, target_val, None,
                       status="skipped", reason="old_value_not_found")
                continue
            target_list.clear()
            target_list.extend(updated_list)
            _trace(trace_sink, action, trace_field, target_val, None)

        elif op.op == "update":
            old_val = op.old_value
            new_val = op.new_value or op.value
            if not old_val or not new_val:
                _trace(trace_sink, action, trace_field, old_val, new_val,
                       status="skipped", reason="missing_value")
                continue
            old_key = _entry_key(old_val)
            replaced = False
            new_list = []
            for item in target_list:
                if not replaced and _entry_key(item) == old_key:
                    new_list.append(new_val)
                    replaced = True
                else:
                    new_list.append(item)
            if replaced:
                target_list.clear()
                target_list.extend(new_list)
                _trace(trace_sink, action, trace_field, old_val, new_val)
            else:
                logger.warning(f"[Metadata] update.old_value 未匹配到已有条目，丢弃操作: old={old_val}")
                _trace(trace_sink, action, trace_field, old_val, new_val,
                       status="skipped", reason="old_value_not_found")

        elif op.op == "add":
            val = op.value
            if not val:
                _trace(trace_sink, action, trace_field, None, None, status="skipped", reason="missing_value")
                continue
            val_key = _entry_key(val)
            if all(_entry_key(x) != val_key for x in target_list):
                target_list.append(val)
                _trace(trace_sink, action, trace_field, None, val)
            else:
                _trace(trace_sink, action, trace_field, None, val, status="skipped", reason="duplicate")

    return updated


def filter_valid_metadata(
    raw_metadata: Optional[Dict[str, Any]],
    fields_schema: Optional[Dict[str, Any]],
    trace_sink: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """对模型侧元数据字典执行白名单校验与清洗。在提取前与每个分片应用 Patch 后调用。

    过滤规则：
    1. 顶层领域：模型侧仅保留 identity_background、relationships、domain_specific_info。
    2. identity_background：二级键必须在 9 大子类别中，三级键严格匹配白名单；非法条目剔除。
    3. relationships：二级键必须属于 4 类定义 (person, group, object, pet)，三级键必须为空；非法条目剔除。
    4. domain_specific_info：已声明场景的二级键必须在该场景 field_key 白名单中（字段被删除则剔除），三级键必须为空；
       未在 special_domains 中声明的场景（换绑/解绑/改名/联查失败）原样保留，不清理。
    5. 条目按三段式规范化存储为 <二级键> | <三级键> | <正文>，并按规范化键去重。

    trace_sink 不为 None 时（开启快照），被剔除 / 去重合并的条目记为 delete_field 并注明 reason。
    """
    if not raw_metadata or not isinstance(raw_metadata, dict):
        return {
            "identity_background": [],
            "relationships": [],
            "domain_specific_info": {},
        }

    special_domains: Dict[str, Any] = (fields_schema or {}).get("special_domains", {})

    # 提取每个特殊业务领域合法 field_key 集合
    domain_allowed_fields: Dict[str, set[str]] = {}
    for d_key, d_meta in special_domains.items():
        field_set = set()
        if isinstance(d_meta, dict) and "fields" in d_meta:
            for f in d_meta["fields"]:
                if isinstance(f, dict) and "field" in f:
                    field_set.add(f["field"])
        domain_allowed_fields[d_key] = field_set

    cleaned: Dict[str, Any] = {
        "identity_background": [],
        "relationships": [],
        "domain_specific_info": {},
    }

    def _drop(field: str, item: Any, reason: str) -> None:
        _trace(trace_sink, "delete_field", field, str(item), None, reason=reason)

    # 1. 校验 identity_background
    raw_ib = raw_metadata.get("identity_background")
    if isinstance(raw_ib, list):
        valid_ib: List[str] = []
        for item in raw_ib:
            triplet = parse_entry_triplet(str(item))
            if not triplet:
                logger.warning(f"[Metadata] 丢弃 identity_background 中格式非法条目: {item!r}")
                _drop("identity_background", item, "invalid_format")
                continue
            l2, l3, content = triplet
            if l2 not in IDENTITY_BACKGROUND_SUBCATEGORIES:
                logger.warning(f"[Metadata] 丢弃 identity_background 中未定义子分类条目: {item!r}")
                _drop("identity_background", item, "invalid_subcategory")
                continue
            allowed_l3 = IDENTITY_BACKGROUND_LEVEL3_WHITELIST.get(l2, set())
            if l3 not in allowed_l3:
                logger.warning(f"[Metadata] 丢弃 identity_background 中三级键不在白名单条目: l2={l2}, l3={l3!r}, item={item!r}")
                _drop("identity_background", item, "invalid_level3")
                continue
            valid_ib.append(format_entry_triplet(l2, l3, content))
        cleaned["identity_background"] = _dedupe_entries(valid_ib, "identity_background", trace_sink)

    # 2. 校验 relationships
    raw_rel = raw_metadata.get("relationships")
    if isinstance(raw_rel, list):
        valid_rel: List[str] = []
        for item in raw_rel:
            triplet = parse_entry_triplet(str(item))
            if not triplet:
                logger.warning(f"[Metadata] 丢弃 relationships 中格式非法条目: {item!r}")
                _drop("relationships", item, "invalid_format")
                continue
            l2, l3, content = triplet
            if l2 not in RELATION_TYPES:
                logger.warning(f"[Metadata] 丢弃 relationships 中未定义关系类型条目: {item!r}")
                _drop("relationships", item, "invalid_relation_type")
                continue
            if l3 != "":
                logger.warning(f"[Metadata] 丢弃 relationships 中三级键非空条目: {item!r}")
                _drop("relationships", item, "level3_not_empty")
                continue
            valid_rel.append(format_entry_triplet(l2, "", content))
        cleaned["relationships"] = _dedupe_entries(valid_rel, "relationships", trace_sink)

    # 3. 校验 domain_specific_info
    raw_ds = raw_metadata.get("domain_specific_info")
    if isinstance(raw_ds, dict):
        cleaned_ds: Dict[str, List[str]] = {}
        for scene_key, scene_items in raw_ds.items():
            if not isinstance(scene_items, list):
                continue
            if scene_key not in special_domains:
                # 未在当前声明中的场景：原样保留，不校验
                logger.debug(f"[Metadata] 保留未在当前声明中的特殊场景数据: {scene_key!r}")
                cleaned_ds[scene_key] = list(scene_items)
                continue
            allowed_fields = domain_allowed_fields.get(scene_key, set())
            scene_field = f"domain_specific_info.{scene_key}"
            valid_scene_items: List[str] = []
            for item in scene_items:
                triplet = parse_entry_triplet(str(item))
                if not triplet:
                    logger.warning(f"[Metadata] 丢弃场景 [{scene_key}] 中格式非法条目: {item!r}")
                    _drop(scene_field, item, "invalid_format")
                    continue
                l2, l3, content = triplet
                if l2 not in allowed_fields:
                    logger.warning(f"[Metadata] 丢弃场景 [{scene_key}] 中未配置字段条目: {item!r}")
                    _drop(scene_field, item, "field_removed")
                    continue
                if l3 != "":
                    logger.warning(f"[Metadata] 丢弃场景 [{scene_key}] 中三级键非空条目: {item!r}")
                    _drop(scene_field, item, "level3_not_empty")
                    continue
                valid_scene_items.append(format_entry_triplet(l2, "", content))
            cleaned_ds[scene_key] = _dedupe_entries(valid_scene_items, scene_field, trace_sink)
        cleaned["domain_specific_info"] = cleaned_ds

    return cleaned

