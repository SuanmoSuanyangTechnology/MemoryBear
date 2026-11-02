from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from json_repair import json_repair
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.memory.models.metadata_models import (
    IDENTITY_BACKGROUND_SUBCATEGORIES,
    RELATION_TYPES,
    parse_entry_triplet,
)
from app.core.memory.utils.prompt.template_render import prompt_env
from app.core.utils.datetime_utils import as_utc_aware

logger = logging.getLogger(__name__)


# ==================== 常量与枚举 ====================

AI_PERSONALITY_DIMENSIONS = (
    "expression",
    "judgment",
    "collaboration",
    "emotion_response",
    "relationship_boundary",
    "commitment_habit",
)
_VALID_MODES = frozenset({"positive", "negative", "relative"})
_ENTRY_ID = re.compile(r"^A-\d{2}$")
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class PersonalityDimension(StrEnum):
    EXPRESSION = "expression"
    JUDGMENT = "judgment"
    COLLABORATION = "collaboration"
    EMOTION_RESPONSE = "emotion_response"
    RELATIONSHIP_BOUNDARY = "relationship_boundary"
    COMMITMENT_HABIT = "commitment_habit"


# ==================== 输入/输出模型 ====================

class AtomicEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    source_type: Literal["metadata", "preference"]
    content: str
    source_field: str | None = None
    category: str | None = None
    subcategory: str | None = None
    source_id: str | None = None
    domain: str | None = None
    subject: str | None = None
    situation_key: str | None = None
    mode: Literal["positive", "negative", "relative"] | None = None


class PersonalityUserPreference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    preference_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    situation_key: str = Field(min_length=1)
    mode: Literal["positive", "negative", "relative"]


class PersonalityEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str = Field(min_length=1)
    source_type: Literal["metadata", "preference"]
    content: str = Field(min_length=1)
    source_field: str | None = None
    category: str | None = None
    subcategory: str | None = None
    source_id: str | None = None
    domain: str | None = None
    subject: str | None = None
    situation_key: str | None = None
    mode: Literal["positive", "negative", "relative"] | None = None


class PersonalityEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entry_id: str = Field(min_length=1)
    word: str = Field(min_length=1)
    dimension: PersonalityDimension
    ai_interaction_rule: str = Field(min_length=1)
    applicable_scope: str = Field(min_length=1)
    formation_basis: str = Field(min_length=1)
    user_preferences: list[PersonalityUserPreference] = Field(min_length=1)
    evidence: list[PersonalityEvidenceReference] = Field(min_length=1)


class AIPersonalityOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expression: list[PersonalityEntry] = Field(default_factory=list)
    judgment: list[PersonalityEntry] = Field(default_factory=list)
    collaboration: list[PersonalityEntry] = Field(default_factory=list)
    emotion_response: list[PersonalityEntry] = Field(default_factory=list)
    relationship_boundary: list[PersonalityEntry] = Field(default_factory=list)
    commitment_habit: list[PersonalityEntry] = Field(default_factory=list)


# ==================== 文本规范化 ====================

def _clean_text(value: object) -> str:
    """压缩字符串中的连续空白；非字符串统一视为空文本。"""
    return " ".join(value.split()) if isinstance(value, str) else ""


# ==================== metadata 证据规范化 ====================

def normalize_metadata_evidence(meta_data: object) -> list[AtomicEvidence]:
    """将用户 metadata 转为稳定排序的原子证据。

    参数：meta_data 为用户 metadata；非字典输入直接返回空列表。
    返回：仅包含身份背景 9 类与关系 4 类的去重证据，并按分类固定顺序生成稳定 ID。
    """
    if not isinstance(meta_data, dict):
        return []

    allowed = {
        "identity_background": frozenset(IDENTITY_BACKGROUND_SUBCATEGORIES),
        "relationships": frozenset(RELATION_TYPES),
    }
    normalized: set[tuple[str, str, str, str]] = set()
    for source_field, categories in allowed.items():
        values = meta_data.get(source_field)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            parsed = parse_entry_triplet(value)
            if parsed is None:
                continue
            category, subcategory, content = parsed
            content = _clean_text(content)
            # 只接受当前顶层字段对应的分类白名单，防止跨字段或未知分类进入证据集。
            if category not in categories or not content:
                continue
            normalized.add((source_field, category, _clean_text(subcategory), content))

    # 固定分类与文本排序，使证据 ID 和后续指纹不受原始列表顺序影响。
    category_order = {
        category: index
        for index, category in enumerate((*IDENTITY_BACKGROUND_SUBCATEGORIES, *RELATION_TYPES))
    }
    rows = sorted(
        normalized,
        key=lambda row: (category_order[row[1]], row[2], row[3], row[0]),
    )
    category_counts: defaultdict[str, int] = defaultdict(int)
    evidence: list[AtomicEvidence] = []
    for source_field, category, subcategory, content in rows:
        category_counts[category] += 1
        evidence.append(
            AtomicEvidence(
                evidence_id=f"M-{category}-{category_counts[category]}",
                source_type="metadata",
                source_field=source_field,
                category=category,
                subcategory=subcategory,
                content=content,
            )
        )
    return evidence


# ==================== Preference 证据规范化 ====================

def _preference_value(node: object, field: str, default: object = None) -> object:
    """兼容字典和对象形式的 Preference 快照节点。"""
    if isinstance(node, dict):
        return node.get(field, default)
    return getattr(node, field, default)


def _normalize_updated_at(value: object) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min
    return as_utc_aware(value).replace(tzinfo=None)


def normalize_preference_evidence(nodes: list[object]) -> list[AtomicEvidence]:
    """将 Preference 快照转为可追溯的原子证据。

    参数：nodes 为 Preference 节点列表，可同时包含字典或属性对象。
    返回：仅保留 active coding 节点中结构完整、模式合法的条目；同文案冲突时取更新时间最新者。
    """
    candidates: list[tuple[datetime, str, int, AtomicEvidence]] = []
    for node in nodes:
        # AI 人格只以生效中的 coding Preference 为依据，其他领域和历史节点不参与推断。
        if _preference_value(node, "domain") != "coding" or _preference_value(node, "status") != "active":
            continue
        modes = _preference_value(node, "mode", [])
        texts = _preference_value(node, "preference_text", [])
        # mode 与文本必须逐项对应，结构损坏的节点整体丢弃，避免错误配对。
        if not isinstance(modes, list) or not isinstance(texts, list) or len(modes) != len(texts):
            continue
        node_id = _clean_text(_preference_value(node, "id"))
        subject = _clean_text(_preference_value(node, "subject"))
        situation_key = _clean_text(_preference_value(node, "situation_key"))
        if not node_id or not subject or not situation_key:
            continue
        updated_at = _normalize_updated_at(_preference_value(node, "updated_at"))
        for index, (mode, text) in enumerate(zip(modes, texts)):
            content = _clean_text(text)
            if mode not in _VALID_MODES or not content:
                continue
            candidates.append(
                (
                    updated_at,
                    node_id,
                    index,
                    AtomicEvidence(
                        evidence_id=f"P-{node_id}-{index}",
                        source_type="preference",
                        source_id=node_id,
                        domain="coding",
                        subject=subject,
                        situation_key=situation_key,
                        mode=mode,
                        content=content,
                    ),
                )
            )

    # 同文案只保留最新证据；节点 ID 与条目序号作为更新时间相同时的稳定决胜项。
    selected: dict[str, tuple[datetime, str, int, AtomicEvidence]] = {}
    for candidate in candidates:
        key = candidate[3].content.casefold()
        previous = selected.get(key)
        if previous is None or (candidate[0], candidate[1], candidate[2]) > (previous[0], previous[1], previous[2]):
            selected[key] = candidate
    return sorted(
        (candidate[3] for candidate in selected.values()),
        key=lambda item: (item.domain or "", item.subject or "", item.situation_key or "", item.source_id or "", item.evidence_id),
    )


# ==================== 联合证据与指纹 ====================

def build_atomic_evidence(meta_data: object, preference_nodes: list[object]) -> list[AtomicEvidence]:
    """合并 metadata 与 Preference 证据。

    参数：meta_data 为用户 metadata，preference_nodes 为 Preference 快照。
    返回：按 metadata、Preference 顺序合并，并按不区分大小写的正文全局去重。
    """
    metadata = normalize_metadata_evidence(meta_data)
    preferences = normalize_preference_evidence(preference_nodes)
    seen: set[str] = set()
    result: list[AtomicEvidence] = []
    for evidence in (*metadata, *preferences):
        key = evidence.content.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(evidence)
    return result


def build_evidence_fingerprint(evidence: list[AtomicEvidence]) -> str:
    """对有序证据生成可复现的 SHA-256 指纹。

    参数：evidence 为已完成规范化和排序的原子证据。
    返回：带 ``sha256:`` 前缀的十六进制摘要；证据顺序属于指纹内容。
    """
    payload = [item.model_dump(exclude_none=True) for item in evidence]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


# ==================== LLM 输出二次校验 ====================

def validate_personality_output(
    output: AIPersonalityOutput,
    evidence: list[AtomicEvidence],
) -> list[dict[str, Any]]:
    """基于原始证据对结构化 LLM 输出做确定性复核。

    参数：output 为结构化模型输出，evidence 为本次调用允许引用的原子证据。
    返回：通过校验的扁平条目，单维最多 5 条、总计最多 30 条。
    关键规则：条目 ID/维度/文本必须合法且唯一，证据引用必须逐字段一致，Preference 必须可追溯或满足派生约束。
    """
    evidence_by_id = {item.evidence_id: item for item in evidence}
    valid: list[dict[str, Any]] = []
    seen_entry_ids: set[str] = set()
    seen_words: set[str] = set()
    seen_entries: set[tuple[str, str, str]] = set()

    for dimension in AI_PERSONALITY_DIMENSIONS:
        for entry in getattr(output, dimension)[:5]:
            entry_id = _clean_text(entry.entry_id)
            word = _clean_text(entry.word)
            rule = _clean_text(entry.ai_interaction_rule)
            scope = _clean_text(entry.applicable_scope)
            basis = _clean_text(entry.formation_basis)
            word_key = word.casefold()
            duplicate_key = (word_key, rule.casefold(), scope.casefold())
            # 先过滤标识、维度归属和核心文案，避免不完整条目进入证据核验。
            if (
                not _ENTRY_ID.fullmatch(entry_id)
                or entry_id in seen_entry_ids
                or entry.dimension.value != dimension
                or not all((word, rule, scope, basis))
                or len(word) > 10
            ):
                continue
            if word_key in seen_words or duplicate_key in seen_entries:
                continue

            referenced: list[dict[str, Any]] = []
            referenced_sources: list[AtomicEvidence] = []
            reference_valid = True
            for reference in entry.evidence:
                source = evidence_by_id.get(reference.evidence_id)
                # 引用必须来自本次证据集，且来源类型与所有提供字段均不得被模型改写。
                if source is None or source.source_type != reference.source_type:
                    reference_valid = False
                    break
                expected = source.model_dump(exclude_none=True)
                supplied = reference.model_dump(exclude_none=True)
                if any(supplied.get(key) != value for key, value in expected.items()):
                    reference_valid = False
                    break
                referenced.append(expected)
                referenced_sources.append(source)
            if not reference_valid or not referenced:
                continue

            preference_evidence = {
                source.evidence_id: source for source in referenced_sources if source.source_type == "preference"
            }
            preferences: list[dict[str, Any]] = []
            preference_valid = True
            for preference in entry.user_preferences:
                data = preference.model_dump()
                # 下游依赖三个定位字段作为稳定键，因此统一限制为 snake_case。
                for key in ("domain", "subject", "situation_key"):
                    if not _SNAKE_CASE.fullmatch(data[key]):
                        preference_valid = False
                        break
                if not preference_valid:
                    break
                source = preference_evidence.get(preference.preference_id)
                if source is not None:
                    # 可追溯 Preference 必须与被引用的原始证据完全一致。
                    if (
                        preference.content != source.content
                        or preference.domain != source.domain
                        or preference.subject != source.subject
                        or preference.situation_key != source.situation_key
                        or preference.mode != source.mode
                    ):
                        preference_valid = False
                        break
                # 无原始 Preference 对应时，只允许模型基于 metadata 派生正向或负向偏好。
                elif preference.mode == "relative" or preference.mode not in {"positive", "negative"}:
                    preference_valid = False
                    break
                preferences.append(data)
            if not preference_valid or not preferences:
                continue

            seen_entry_ids.add(entry_id)
            seen_words.add(word_key)
            seen_entries.add(duplicate_key)
            data = entry.model_dump(mode="json")
            data.update(
                entry_id=entry_id,
                word=word,
                ai_interaction_rule=rule,
                applicable_scope=scope,
                formation_basis=basis,
                evidence=referenced,
                user_preferences=preferences,
            )
            valid.append(data)
            if len(valid) >= 30:
                return valid
    return valid


# ==================== Prompt 渲染与结构化调用 ====================

def _parse_personality_entries(raw: object) -> AIPersonalityOutput:
    if not isinstance(raw, dict):
        raise TypeError("AI personality model did not return tool arguments")
    dimensions: dict[str, list[PersonalityEntry]] = {}
    rejected: dict[str, int] = {}
    candidate_count = 0
    for dimension in AI_PERSONALITY_DIMENSIONS:
        items = raw.get(dimension, [])
        if not isinstance(items, list):
            raise TypeError(f"AI personality dimension {dimension} is not a list")
        accepted: list[PersonalityEntry] = []
        candidate_count += len(items)
        for item in items:
            try:
                accepted.append(PersonalityEntry.model_validate(item))
            except ValidationError as exc:
                for error in exc.errors(include_input=False):
                    reason = str(error["loc"][-1]) if error["loc"] else error["type"]
                    rejected[reason] = rejected.get(reason, 0) + 1
        dimensions[dimension] = accepted
    if rejected:
        logger.warning("AI人格模型输出丢弃无效条目: %s", rejected)
    if candidate_count and not any(dimensions.values()):
        raise ValueError("AI personality model returned no structurally valid entries")
    return AIPersonalityOutput.model_validate(dimensions)


async def extract_ai_personality(
    llm_client: Any,
    evidence: list[AtomicEvidence],
    *,
    timeout_seconds: int = 120,
) -> list[dict[str, Any]]:
    """调用 LLM 提取 AI 人格，并返回二次校验后的条目。

    参数：llm_client 为结构化调用客户端，evidence 为原子证据，timeout_seconds 为调用超时秒数。
    返回：通过证据一致性校验的条目列表；无输入证据时不调用 LLM，直接返回空列表。
    """
    if not evidence:
        return []
    prompt = prompt_env.get_template("ai_personality.jinja2").render(
        evidence_json=json.dumps(
            [item.model_dump(exclude_none=True) for item in evidence],
            ensure_ascii=False,
        )
    )
    response = await asyncio.wait_for(
        llm_client.call_structured(
            [{"role": "user", "content": prompt}],
            AIPersonalityOutput,
            include_raw=True,
        ),
        timeout=timeout_seconds,
    )
    result = response.get("parsed") if isinstance(response, dict) and "parsed" in response else response
    if result is None and isinstance(response, dict):
        raw = response.get("raw")
        for call in getattr(raw, "tool_calls", []) or []:
            if call.get("name") == AIPersonalityOutput.__name__ and isinstance(call.get("args"), dict):
                result = call["args"]
                break
        if result is None:
            for call in (getattr(raw, "additional_kwargs", None) or {}).get("tool_calls", []):
                function = call.get("function", {})
                if function.get("name") != AIPersonalityOutput.__name__:
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments.strip():
                    result = json_repair.repair_json(arguments, return_objects=True)
                    break
        content = getattr(raw, "content", None)
        if result is None and isinstance(content, str) and content.strip():
            result = json_repair.repair_json(content, return_objects=True)
    output = result if isinstance(result, AIPersonalityOutput) else _parse_personality_entries(result)
    return validate_personality_output(output, evidence)
