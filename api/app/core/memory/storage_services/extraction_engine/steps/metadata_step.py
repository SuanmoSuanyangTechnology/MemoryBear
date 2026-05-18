"""MetadataExtractionStep — 用户实体元数据提取 step。

从用户实体的 description 中提取结构化元数据（core_facts、traits、relations 等），
通过 Celery 异步任务在去重消歧完成后执行，结果回写到 Neo4j ExtractedEntity 节点。

不注册为 SidecarStepFactory 的自动旁路（因为它在去重后异步执行，不在主萃取流程中），
而是由 Celery 任务直接实例化调用。
"""

import json
import logging
from typing import Any

from .base import ExtractionStep, StepContext
from .schema import MetadataStepInput, MetadataStepOutput

logger = logging.getLogger(__name__)


class MetadataExtractionStep(ExtractionStep[MetadataStepInput, MetadataStepOutput]):
    """从用户实体 description 中提取结构化元数据。

    非 critical step — 失败返回空默认值，不中断流程。
    """

    def __init__(self, context: StepContext) -> None:
        super().__init__(context)

    @property
    def name(self) -> str:
        return "metadata_extraction"

    @property
    def is_critical(self) -> bool:
        return False

    @property
    def max_retries(self) -> int:
        return 1

    async def render_prompt(self, input_data: MetadataStepInput) -> str:
        """使用 Jinja2 模板渲染元数据提取 prompt。"""
        from app.core.memory.utils.prompt.prompt_utils import prompt_env
        from app.core.language_utils import detect_text_language

        template = prompt_env.get_template("extract_user_metadata.jinja2")

        input_json = json.dumps(
            {
                "description": input_data.descriptions,
                "existing_metadata": input_data.existing_metadata,
            },
            ensure_ascii=False,
            indent=2,
        )

        # 根据输入内容检测语言，确保 prompt 指令语言与输入一致
        detect_source = " ".join(input_data.descriptions) if input_data.descriptions else ""
        detected_language = detect_text_language(detect_source, fallback=self.language)

        return template.render(
            language=detected_language,
            input_json=input_json,
        )

    async def call_llm(self, prompt: Any) -> Any:
        """调用 LLM 进行结构化输出。"""
        from app.core.memory.models.metadata_models import MetadataExtractionResponse

        messages = [{"role": "user", "content": prompt}]
        return await self.call_structured(
            messages, MetadataExtractionResponse
        )

    async def parse_response(
        self, raw_response: Any, input_data: MetadataStepInput
    ) -> MetadataStepOutput:
        """将 LLM 响应解析为 MetadataStepOutput。"""
        if raw_response is None:
            return self.get_default_output()

        return MetadataStepOutput(
            core_facts=getattr(raw_response, "core_facts", []) or [],
            traits=getattr(raw_response, "traits", []) or [],
            relations=getattr(raw_response, "relations", []) or [],
            goals=getattr(raw_response, "goals", []) or [],
            interests=getattr(raw_response, "interests", []) or [],
            beliefs_or_stances=getattr(raw_response, "beliefs_or_stances", []) or [],
            anchors=getattr(raw_response, "anchors", []) or [],
            events=getattr(raw_response, "events", []) or [],
        )

    def get_default_output(self) -> MetadataStepOutput:
        return MetadataStepOutput()
