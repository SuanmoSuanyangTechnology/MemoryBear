"""子问题 6 · LLM 层：描述合并"""
import json
import logging
import os
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
        from app.core.memory.storage_services.extraction_engine.steps.base import call_structured

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
        response = await call_structured(llm_client, messages, DescriptionMergeOutput)

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
    """单个事件"""
    valid_at: str
    invalid_at: str
    fact: str


class SummarizeExtractRenameOutput(BaseModel):
    """LLM 输出：合并摘要 + 事件列表 + 更名判断"""
    description_summary: str
    new_events: List[EventItem] = []
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


def filter_events(
    new_events: List[EventItem],
) -> List[EventItem]:
    """过滤无效事件

    Args:
        new_events: LLM 输出的新事件列表

    Returns:
        过滤后的有效事件列表
    """
    valid_events = []
    seen_facts = set()

    for event in new_events:
        # 过滤 fact 为空
        if not event.fact or not event.fact.strip():
            continue

        # 过滤"说话者"后缀
        if "的说话者" in event.fact:
            continue

        # 本轮内去重
        fact_lower = event.fact.strip().lower()
        if fact_lower in seen_facts:
            continue
        seen_facts.add(fact_lower)

        valid_events.append(event)

    return valid_events


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
        event_timeline: 已有的 event_timeline（全量，用于去重）
        language: 语言类型

    Returns:
        SummarizeExtractRenameOutput 实例，失败返回 None
    """
    try:
        from app.core.memory.storage_services.extraction_engine.steps.base import call_structured

        template = _prompt_env.get_template("reflection_summary_timeline.prompt.jinja2")

        input_data = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "description": description,
            "description_summary": summary or "",
            "event_timeline": event_timeline or "",
        }

        rendered_prompt = template.render(
            input_json=json.dumps(input_data, ensure_ascii=False, indent=2),
            language=language,
        )

        messages = [{"role": "user", "content": rendered_prompt}]
        response = await call_structured(llm_client, messages, SummarizeExtractRenameOutput)

        if isinstance(response, SummarizeExtractRenameOutput):
            result = response
        elif isinstance(response, dict):
            result = SummarizeExtractRenameOutput(
                description_summary=response.get("description_summary", ""),
                new_events=[EventItem(**e) for e in response.get("new_events", [])],
                should_rename_entity=response.get("should_rename_entity", False),
                suggested_entity_name=response.get("suggested_entity_name"),
            )
        elif isinstance(response, BaseModel):
            data = response.model_dump()
            result = SummarizeExtractRenameOutput(
                description_summary=data.get("description_summary", ""),
                new_events=[EventItem(**e) for e in data.get("new_events", [])],
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
