"""子问题 6 · LLM 层：描述合并"""
import json
import logging
import os
import re
from typing import List, Optional
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_prompt_dir = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "utils", "prompt", "prompts"
)
_prompt_env = Environment(loader=FileSystemLoader(_prompt_dir))


class DescriptionMergeOutput(BaseModel):
    """LLM 输出合并后的描述内容"""
    merged_description: str


async def merge_description(
    llm_client,
    entity_name: str,
    entity_type: str,
    summary: Optional[str],    # 上次摘要，首次为 None
    fragments: List[str],      # description 拆分后的碎片列表
    language: str = "zh",
) -> Optional[str]:
    """调用 LLM 执行描述合并，返回合并后的纯文本摘要

    使用项目统一的 call_structured 工具函数：
    - 优先 response_structured（支持 structured output 的模型）
    - 自动降级到 chat + StructResponse + json_repair（兼容 qwen 等模型）

    Args:
        llm_client: OpenAIClient 实例
        entity_name: 实体名称
        entity_type: 实体类型
        summary: 上次的摘要（首次为 None，模板自动判断）
        fragments: description 按分号拆分的碎片列表
        language: 语言类型 "zh" | "en"

    Returns:
        合并后的纯文本摘要，失败返回 None
    """
    try:
        template = _prompt_env.get_template("description_merge.jinja2")
        json_schema = json.dumps(DescriptionMergeOutput.model_json_schema(), indent=2)

        rendered_prompt = template.render(
            entity_name=entity_name,
            entity_type=entity_type,
            summary=summary,
            fragments=fragments,
            parts_count=len(fragments) + (1 if summary else 0),
            json_schema=json_schema,
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await llm_client.call_structured(messages, DescriptionMergeOutput)

        if isinstance(response, DescriptionMergeOutput):
            result = response.merged_description
        elif isinstance(response, dict):
            result = response.get("merged_description")
        elif isinstance(response, BaseModel):
            result = response.model_dump().get("merged_description")
        else:
            return None

        # 后处理：将中文分号替换为逗号，避免与碎片分隔符 ；混淆
        if result:
            result = result.replace('；', '，')
        return result or None

    except Exception as e:
        logger.error(f"LLM 描述合并失败 entity={entity_name}: {e}", exc_info=True)
        return None


# ===== 新增：描述合并 + 事件提取 + 更名判断（一次调用） =====

class EventItem(BaseModel):
    """单条事件对象（add.value / delete.old_value / update.old_value / update.new_value 通用）"""
    title: str = "NULL"
    category_id: str = "NULL"   # 稳定分类ID，直接存、不做枚举校验
    category: str = "NULL"
    valid_at: str = "NULL"
    invalid_at: str = "NULL"
    fact: str = "NULL"


class EventOperation(BaseModel):
    """单条事件操作：add / delete / update

    - add:    仅 value
    - delete: 仅 old_value（定位旧事件）
    - update: old_value（定位）+ new_value（覆盖值）
    """
    op: str
    value: Optional[EventItem] = None
    old_value: Optional[EventItem] = None
    new_value: Optional[EventItem] = None


class SummarizeExtractRenameOutput(BaseModel):
    """LLM 输出：合并摘要 + 事件操作列表 + 更名判断"""
    description_summary: str
    operations: List[EventOperation] = []
    should_rename_entity: bool = False
    suggested_entity_name: Optional[str] = None


def validate_summary_output(
    existing_summary: Optional[str],
    result: SummarizeExtractRenameOutput,
) -> tuple:
    """校验 LLM 输出的 summary 是否合理

    Returns:
        (is_valid, reason)
    """
    if not result.description_summary or not result.description_summary.strip():
        if existing_summary and existing_summary.strip():
            return False, "summary_was_cleared"
        return False, "summary_empty"

    if len(result.description_summary.strip()) < 5:
        return False, "summary_too_short"

    return True, "ok"


# ===== 事件时间线 patch：解析 / 匹配 / 应用 =====

# 单条事件格式：[valid_at|invalid_at] fact|title|category|category_id
_TIMELINE_HEAD_RE = re.compile(r'^\[([^|]*)\|([^\]]*)\]\s*(.*)$')


def _parse_timeline_full(event_timeline: str) -> List[dict]:
    """解析 event_timeline 为全字段 dict 列表，不过滤、不转换，保证写回无损。

    旧格式（仅 fact）按 fact=正文、其余 NULL 保留。
    """
    if not event_timeline or not event_timeline.strip():
        return []

    events: List[dict] = []
    for item in event_timeline.split('；'):
        item = item.strip()
        if not item:
            continue

        m = _TIMELINE_HEAD_RE.match(item)
        if m:
            valid_at = m.group(1).strip() or "NULL"
            invalid_at = m.group(2).strip() or "NULL"
            body = m.group(3)
        else:
            valid_at = "NULL"
            invalid_at = "NULL"
            body = item

        parts = body.split('|')
        fact = parts[0].strip() if len(parts) > 0 else ""
        title = parts[1].strip() if len(parts) > 1 else "NULL"
        category = parts[2].strip() if len(parts) > 2 else "NULL"
        category_id = parts[3].strip() if len(parts) > 3 else "NULL"

        events.append({
            "valid_at": valid_at or "NULL",
            "invalid_at": invalid_at or "NULL",
            "fact": fact,
            "title": title or "NULL",
            "category": category or "NULL",
            "category_id": category_id or "NULL",
        })
    return events


async def summarize_extract_and_rename(
    llm_client,
    entity_name: str,
    entity_type: str,
    description: str,
    summary: Optional[str],
    event_timeline: Optional[str] = None,
    language: str = "zh",
) -> Optional[SummarizeExtractRenameOutput]:
    """一次 LLM 调用，同时合并描述 + 提取事件 + 判断更名

    Args:
        llm_client: OpenAIClient 实例
        entity_name: 实体名称
        entity_type: 实体类型
        description: 当前 description 碎片（；分隔字符串）
        summary: 上次的摘要（首次为 None）
        event_timeline: 已有的 event_timeline（全量，作为 patch 基准与去重依据）
        language: 语言类型

    Returns:
        SummarizeExtractRenameOutput 实例，失败返回 None
    """
    try:
        template = _prompt_env.get_template("reflection_summary_timeline.prompt.jinja2")

        input_data = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "description": description,
            "description_summary": summary or "",
            "event_timeline": _parse_timeline_full(event_timeline) if event_timeline else [],
        }

        rendered_prompt = template.render(
            input_json=json.dumps(input_data, ensure_ascii=False, indent=2),
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await llm_client.call_structured(messages, SummarizeExtractRenameOutput)

        if isinstance(response, SummarizeExtractRenameOutput):
            result = response
        elif isinstance(response, dict):
            result = SummarizeExtractRenameOutput(
                description_summary=response.get("description_summary", ""),
                operations=[EventOperation(**o) for o in response.get("operations", [])],
                should_rename_entity=response.get("should_rename_entity", False),
                suggested_entity_name=response.get("suggested_entity_name"),
            )
        elif isinstance(response, BaseModel):
            data = response.model_dump()
            result = SummarizeExtractRenameOutput(
                description_summary=data.get("description_summary", ""),
                operations=[EventOperation(**o) for o in data.get("operations", [])],
                should_rename_entity=data.get("should_rename_entity", False),
                suggested_entity_name=data.get("suggested_entity_name"),
            )
        else:
            return None

        # 后处理：summary 中的中文分号替换为逗号
        if result.description_summary:
            result.description_summary = result.description_summary.replace('；', '，')

        # 后处理：suggested_entity_name 为 "NULL" 字符串时转为 None
        if result.suggested_entity_name and result.suggested_entity_name.upper() == "NULL":
            result.suggested_entity_name = None

        return result

    except Exception as e:
        logger.error(f"LLM 描述合并+事件提取+更名失败 entity={entity_name}: {e}", exc_info=True)
        return None
