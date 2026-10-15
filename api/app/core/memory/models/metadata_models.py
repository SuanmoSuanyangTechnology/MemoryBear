"""Models for user metadata extraction.

Independent from triplet_models.py - these models are used by the
metadata extraction and reflection pipeline.

Field definitions align with Jinja2 prompt template:
- extract_user_metadata_coding_v1.jinja2 (增量 Patch 提取)

Storage schema: identity_background / relationships 为 list[str]，
domain_specific_info 为 dict[str, list[str]]。
"""

import json
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

# 显式过滤/禁止作为独立 operation 输出的字段
FILTERED_METADATA_FIELDS: tuple[str, ...] = (
    "aliases",
    "domain_specific_info",
)

OperationLiteral = Literal["add", "delete", "update"]

# 历史旧 8 字段：停止生成，读取和合并继续保留，待迁移后统一删除
LEGACY_METADATA_FIELDS: tuple[str, ...] = (
    "core_facts",
    "traits",
    "relations",
    "goals",
    "interests",
    "beliefs_or_stances",
    "anchors",
    "events",
)

# 新结构 3 个顶层键
NEW_METADATA_FIELDS: tuple[str, ...] = (
    "identity_background",
    "relationships",
    "domain_specific_info",
)

# 展示投影 / L0 投影使用的 13 个通用二级键，顺序固定
PROFILE_DISPLAY_KEYS: tuple[str, ...] = IDENTITY_BACKGROUND_SUBCATEGORIES + RELATION_TYPES


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


def split_metadata_entry(item: Any) -> Optional[Tuple[str, str, str]]:
    """读链路条目解析：规则同 parse_entry_triplet，但不打日志，非字符串条目返回 None。"""
    if not isinstance(item, str):
        return None
    parts = [p.strip() for p in item.strip().split("|", 2)]
    if len(parts) == 2:
        parts = [parts[0], "", parts[1]]
    if len(parts) != 3 or not parts[2]:
        return None
    return parts[0], parts[1], parts[2]


def normalize_new_metadata(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """返回新三键的副本；缺失或类型不符时补默认值（list / list / dict[str, list]）。"""
    meta = meta if isinstance(meta, dict) else {}
    ib = meta.get("identity_background")
    rel = meta.get("relationships")
    ds = meta.get("domain_specific_info")
    return {
        "identity_background": list(ib) if isinstance(ib, list) else [],
        "relationships": list(rel) if isinstance(rel, list) else [],
        "domain_specific_info": (
            {k: list(v) for k, v in ds.items() if isinstance(v, list)}
            if isinstance(ds, dict) else {}
        ),
    }


def _iter_profile_entries(meta: Optional[Dict[str, Any]]):
    """依次产出 identity_background、relationships 中可解析且二级键属于 13 个通用键的条目。"""
    normalized = normalize_new_metadata(meta)
    for field in ("identity_background", "relationships"):
        for item in normalized[field]:
            triplet = split_metadata_entry(item)
            if triplet and triplet[0] in PROFILE_DISPLAY_KEYS:
                yield triplet


def project_display_profile(
    meta: Optional[Dict[str, Any]],
    limit_per_key: Optional[int] = None,
) -> Dict[str, List[str]]:
    """展示投影（GET 画像、Trace 画像）。

    取第 1 段为 key、第 3 段为 value，丢弃第 2 段三级键；同一 key 下正文相同的去重（保留首现）；
    按 PROFILE_DISPLAY_KEYS 顺序输出，无值的 key 不输出；limit_per_key 为 None 时不限条数。
    """
    grouped: Dict[str, List[str]] = {}
    for l2, _l3, content in _iter_profile_entries(meta):
        values = grouped.setdefault(l2, [])
        if content not in values:
            values.append(content)
    result: Dict[str, List[str]] = {}
    for key in PROFILE_DISPLAY_KEYS:
        values = grouped.get(key)
        if values:
            result[key] = values if limit_per_key is None else values[:limit_per_key]
    return result


def project_l0_profile(meta: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """L0 投影（给大模型的 <user-info>）。

    以二级键分组，值为规范化后的完整三段条目（保留三级键），按 _entry_key 去重、保留存储顺序；
    按 PROFILE_DISPLAY_KEYS 顺序输出，无值的 key 不输出，不限条数。
    """
    grouped: Dict[str, List[str]] = {}
    seen: Dict[str, set[str]] = {}
    for l2, l3, content in _iter_profile_entries(meta):
        entry = format_entry_triplet(l2, l3, content)
        key = _entry_key(entry)
        if key in seen.setdefault(l2, set()):
            continue
        seen[l2].add(key)
        grouped.setdefault(l2, []).append(entry)
    return {key: grouped[key] for key in PROFILE_DISPLAY_KEYS if key in grouped}


def flatten_domain_specific_info(domain_specific_info: Any) -> List[Tuple[str, str, str]]:
    """把 domain_specific_info 展平为 (scene_key, field_key, 正文) 列表，按存储顺序，跳过无法解析的条目。"""
    if not isinstance(domain_specific_info, dict):
        return []
    result: List[Tuple[str, str, str]] = []
    for scene_key, items in domain_specific_info.items():
        if not isinstance(items, list):
            continue
        for item in items:
            triplet = split_metadata_entry(item)
            if triplet:
                result.append((str(scene_key), triplet[0], triplet[2]))
    return result


def _union_entries(base: List[Any], extra: List[Any]) -> List[Any]:
    """字符串按 _entry_key 去重；非字符串 JSON 条目按类型和值去重，原样保留。

    保留 base 在前、首现顺序；重复归并不会追加相同的非字符串历史条目。
    """
    result = list(base)
    seen = {_entry_key(x) for x in result if isinstance(x, str)}
    seen_non_strings = {
        _legacy_metadata_entry_key(x) for x in result if not isinstance(x, str)
    }
    for item in extra:
        if not isinstance(item, str):
            non_string_key = _legacy_metadata_entry_key(item)
            if non_string_key not in seen_non_strings:
                seen_non_strings.add(non_string_key)
                result.append(item)
            continue
        key = _entry_key(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def normalize_legacy_metadata_values(value: Any) -> list[Any]:
    """历史字段按列表读取；单值整体保留，空值不生成条目。"""
    values = value if isinstance(value, list) else [value]
    return [item for item in values if item is not None and item != ""]


def _legacy_metadata_entry_key(value: Any) -> tuple[str, str]:
    # 字符串沿用忽略大小写去重；其他 JSON 值保留类型，避免 0 / False / "0" 混淆。
    if isinstance(value, str):
        return "str", value.lower()
    return type(value).__name__, json.dumps(value, sort_keys=True, ensure_ascii=False)


def merge_user_meta_data(target: Optional[Dict[str, Any]], source: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """用户归并时把 source 的 meta_data 合并进 target，返回新字典。

    - identity_background / relationships：按 _entry_key 去重并集；
    - domain_specific_info：按场景键合并，场景内按 _entry_key 去重并集；
    - 旧 8 字段：列表或历史单值均保留，字符串忽略大小写去重，无内容时不新增键；
    - 其他 list 字段：按 str(v).lower() 去重并集（历史行为）；
    - 类型不符的一侧忽略并记 warning；source 中非 list / dict 的其他字段不合并。
    """
    merged: Dict[str, Any] = dict(target or {})
    for key, values in (source or {}).items():
        if key in LEGACY_METADATA_FIELDS:
            items = normalize_legacy_metadata_values(merged.get(key))
            items += normalize_legacy_metadata_values(values)
            if items:
                unique: dict[tuple[str, str], Any] = {}
                for item in items:
                    unique.setdefault(_legacy_metadata_entry_key(item), item)
                merged[key] = list(unique.values())
        elif key in ("identity_background", "relationships"):
            if not isinstance(values, list):
                logger.warning(f"[Metadata] 归并忽略 source 中类型不符的字段: {key}={type(values).__name__}")
                continue
            existing = merged.get(key)
            if existing is not None and not isinstance(existing, list):
                logger.warning(f"[Metadata] 归并忽略 target 中类型不符的字段: {key}={type(existing).__name__}")
                existing = None
            merged[key] = _union_entries(existing or [], values)
        elif key == "domain_specific_info":
            if not isinstance(values, dict):
                logger.warning(f"[Metadata] 归并忽略 source 中类型不符的字段: {key}={type(values).__name__}")
                continue
            existing = merged.get(key)
            if existing is not None and not isinstance(existing, dict):
                logger.warning(f"[Metadata] 归并忽略 target 中类型不符的字段: {key}={type(existing).__name__}")
                existing = None
            scenes: Dict[str, Any] = dict(existing or {})
            for scene_key, scene_items in values.items():
                if not isinstance(scene_items, list):
                    logger.warning(f"[Metadata] 归并忽略 source 场景中类型不符的数据: {scene_key}")
                    continue
                current = scenes.get(scene_key)
                if current is not None and not isinstance(current, list):
                    logger.warning(f"[Metadata] 归并忽略 target 场景中类型不符的数据: {scene_key}")
                    current = None
                scenes[scene_key] = _union_entries(current or [], scene_items)
            merged[key] = scenes
        elif isinstance(values, list):
            existing_list = list(merged.get(key) or [])
            existing_set = {str(v).lower() for v in existing_list}
            for v in values:
                if str(v).lower() not in existing_set:
                    existing_list.append(v)
                    existing_set.add(str(v).lower())
            merged[key] = existing_list
    return merged


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

