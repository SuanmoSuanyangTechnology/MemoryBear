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
