import logging
from typing import Any

from ....model_clients import RedBearChatModel
from ...engine.state_manager import WorkflowState
from ...engine.variable_pool import VariablePool
from ..base_node import BaseNode
from .config import QuestionClassifierNodeConfig
from ...variable.base_variable import VariableType
from ...variable.variable_objects import ArrayVariable, FileVariable
from ....db import get_async_db_context
from ....schemas.model_schema import ModelInfo
from ....services.model_service import ModelConfigService

logger = logging.getLogger(__name__)

DEFAULT_CASE_PREFIX = "CASE"
DEFAULT_EMPTY_QUESTION_CASE = f"{DEFAULT_CASE_PREFIX}1"


class QuestionClassifierNode(BaseNode):
    """Question classifier node."""

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: QuestionClassifierNodeConfig | None = None
        self.category_to_case_map = {}
        self.response_metadata = {}
        self._last_messages: list = []
        self._classification_result: str = ""

    def _get_typed_config(self) -> QuestionClassifierNodeConfig:
        if self.typed_config is None:
            self.typed_config = QuestionClassifierNodeConfig(**self.config)
        return self.typed_config

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": {
            "messages": self._last_messages,
            "classification_result": self._classification_result,
            "model_id": str(self._get_typed_config().model_id),
        }}

    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        if self.response_metadata:
            usage = self.response_metadata.get('token_usage')
            if usage:
                return {
                    "prompt_tokens": usage.get('input_tokens', 0),
                    "completion_tokens": usage.get('output_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0)
                }
        return None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "class_name": VariableType.STRING,
            "output": VariableType.STRING
        }

    async def _load_model_info_async(self, variable_pool: VariablePool) -> ModelInfo:
        tenant_id = await self.resolve_tenant_id_async(variable_pool)

        async with get_async_db_context() as db:
            # Non-decrypting view: this node needs no credentials (calls go through model-service)
            return await ModelConfigService.get_runtime_model_view_async(
                db,
                self.typed_config.model_id,
                tenant_id=tenant_id,
            )

    @staticmethod
    def _build_llm_from_model_info(model_info: ModelInfo) -> RedBearChatModel:
        return RedBearChatModel.for_invoke(model_info)

    def _build_category_case_map(self) -> dict[str, str]:
        """
        Pre-build the category-name -> CASE-tag mapping dictionary.
        Example: {"产品咨询": "CASE1", "售后问题": "CASE2"}
        """
        category_map = {}
        categories = self.typed_config.categories or []
        for idx, class_item in enumerate(categories, start=1):
            category_name = class_item.class_name.strip()
            case_tag = f"{DEFAULT_CASE_PREFIX}{idx}"
            category_map[category_name] = case_tag
        return category_map

    async def _build_vision_message(
        self, 
        model_info: ModelInfo, 
        user_prompt: str, 
        variable_pool: VariablePool
    ) -> str | list:
        """Build the vision message.

        When vision is enabled and a vision input variable is configured,
        files are combined with the text into a multimodal message.
        Otherwise a plain text message is returned.
        """
        if not self.typed_config.vision or not self.typed_config.vision_input:
            return user_prompt
        
        try:
            files_instance = variable_pool.get_instance(self.typed_config.vision_input, default=None, strict=False)
            if isinstance(files_instance, ArrayVariable):
                file_list = files_instance.value
            elif isinstance(files_instance, FileVariable):
                file_list = [files_instance]
            else:
                file_list = []
            if not file_list:
                return user_prompt
        except Exception:
            logger.warning(f"节点 {self.node_id} 获取视觉变量失败: {self.typed_config.vision_input}")
            return user_prompt
        
        content = [{"type": "text", "text": user_prompt}]
        
        for file in file_list:
            file_content = await self.process_message(model_info, file.value, self.typed_config.vision)
            if file_content:
                if isinstance(file_content, list):
                    content.extend(file_content)
                else:
                    content.append(file_content)
        
        return content

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict:
        """Execute question classification."""
        self.typed_config = self._get_typed_config()
        self.category_to_case_map = self._build_category_case_map()
        question = self.typed_config.input_variable
        supplement_prompt = self.typed_config.user_supplement_prompt or ""
        categories = self.typed_config.categories or []
        category_names = [class_item.class_name.strip() for class_item in categories]
        category_count = len(category_names)

        if not question:
            logger.warning(
                f"节点 {self.node_id} 未获取到输入问题，使用默认分支"
                f"(默认分支:{DEFAULT_EMPTY_QUESTION_CASE}"
                f"分类总数: {category_count})"
            )
            # If the category list is empty, return the default unknown branch; otherwise return CASE1
            if category_count > 0:
                return {
                    "class_name": category_names[0],
                    "output": DEFAULT_EMPTY_QUESTION_CASE
                }
            return {
                "class_name": "unknown",
                "output": DEFAULT_EMPTY_QUESTION_CASE
            }

        try:
            model_info = await self._load_model_info_async(variable_pool)
            llm = self._build_llm_from_model_info(model_info)

            # Render the user prompt template with workflow variable support
            user_prompt = self._render_template(
                self.typed_config.user_prompt.format(
                    question=question,
                    categories=", ".join(category_names),
                    supplement_prompt=supplement_prompt
                ),
                variable_pool
            )

            # Build the vision message
            user_content = await self._build_vision_message(
                model_info, 
                user_prompt, 
                variable_pool
            )

            messages = [
                ("system", self.typed_config.system_prompt),
                ("user", user_content),
            ]
            self._last_messages = [{"role": r, "content": c} for r, c in messages]

            response = await llm.ainvoke(messages)
            result = self.process_model_output(response.content)
            self._classification_result = result
            self.response_metadata = {
                **response.response_metadata,
                "token_usage": getattr(response, 'usage_metadata', None) or response.response_metadata.get('token_usage')
            }

            if result in category_names:
                category = result
            else:
                logger.warning(f"LLM返回了未知类别: {result}")
                category = category_names[0] if category_names else "unknown"

            log_supplement = supplement_prompt if supplement_prompt else "无"
            logger.info(f"节点 {self.node_id} 分类结果: {category}, 用户补充提示词：{log_supplement}")

            return {
                "class_name": category,
                "output": f"CASE{category_names.index(category) + 1}",
            }
        except Exception as e:
            logger.error(
                f"节点 {self.node_id} 分类执行异常：{str(e)}",
                exc_info=True  # Print the stack trace for debugging
            )
            # Return the default branch on error to keep the workflow fault-tolerant
            if category_count > 0:
                return {
                    "class_name": category_names[0],
                    "output": DEFAULT_EMPTY_QUESTION_CASE
                }
            return {
                "class_name": "unknown",
                "output": DEFAULT_EMPTY_QUESTION_CASE
            }
