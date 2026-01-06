import logging
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.question_classifier.config import QuestionClassifierNodeConfig
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.db import get_db_read
from app.models import ModelType
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)

DEFAULT_CASE_PREFIX = "CASE"
DEFAULT_EMPTY_QUESTION_CASE = f"{DEFAULT_CASE_PREFIX}1"


class QuestionClassifierNode(BaseNode):
    """问题分类器节点"""
    
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = QuestionClassifierNodeConfig(**self.config)
        self.category_to_case_map = self._build_category_case_map()
    
    def _get_llm_instance(self) -> RedBearLLM:
        """获取LLM实例"""
        with get_db_read() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=self.typed_config.model_id)
            
            if not config:
                raise BusinessException("配置的模型不存在", BizCode.NOT_FOUND)
            
            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)
            
            api_config = config.api_keys[0]
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            base_url = api_config.api_base
            model_type = config.type
        
        return RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=base_url,
            ),
            type=ModelType(model_type)
        )

    def _build_category_case_map(self) -> dict[str, str]:
        """
        预构建 分类名称 -> CASE标识 的映射字典
        示例：{"产品咨询": "CASE1", "售后问题": "CASE2"}
        """
        category_map = {}
        categories = self.typed_config.categories or []
        for idx, class_item in enumerate(categories, start=1):
            category_name = class_item.class_name.strip()
            case_tag = f"{DEFAULT_CASE_PREFIX}{idx}"
            category_map[category_name] = case_tag
        return category_map
    
    async def execute(self, state: WorkflowState) -> dict:
        """执行问题分类"""
        question = self.typed_config.input_variable
        supplement_prompt = self.typed_config.user_supplement_prompt or ""
        categories = self.typed_config.categories or []
        category_names = [class_item.class_name.strip() for class_item in categories]
        category_count = len(category_names)
        
        if not question:
            logger.warning(
                f"节点 {self.node_id} 未获取到输入问题，使用默认分支"
                f"（默认分支：{DEFAULT_EMPTY_QUESTION_CASE}，分类总数：{category_count}）"
            )
            # 若分类列表为空，返回默认unknown分支，否则返回CASE1
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
            llm = self._get_llm_instance()

            # 渲染用户提示词模板，支持工作流变量
            user_prompt = self._render_template(
                self.typed_config.user_prompt.format(
                    question=question,
                    categories=", ".join(category_names),
                    supplement_prompt=supplement_prompt
                ),
                state
            )

            messages = [
                ("system", self.typed_config.system_prompt),
                ("user", user_prompt),
            ]

            response = await llm.ainvoke(messages)
            result = response.content.strip()

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
                exc_info=True  # 打印堆栈信息，便于调试
            )
            # 异常时返回默认分支，保证工作流容错性
            if category_count > 0:
                return {
                    "class_name": category_names[0],
                    "output": DEFAULT_EMPTY_QUESTION_CASE
                }
            return {
                "class_name": "unknown",
                "output": DEFAULT_EMPTY_QUESTION_CASE
            }
