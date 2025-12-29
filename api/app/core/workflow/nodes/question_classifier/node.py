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


class QuestionClassifierNode(BaseNode):
    """问题分类器节点"""
    
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = QuestionClassifierNodeConfig(**self.config)
    
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
    
    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """执行问题分类"""
        question = self.typed_config.input_variable

        supplement_prompt = ""
        if self.typed_config.user_supplement_prompt is not None:
            supplement_prompt = self.typed_config.user_supplement_prompt
        
        category_names = [class_item.class_name for class_item in self.typed_config.categories]
        
        if not question:
            logger.warning(f"节点 {self.node_id} 未获取到输入问题")
            return {self.typed_config.output_variable: category_names[0] if category_names else "unknown"}
        
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
        
        return {self.typed_config.output_variable: category}