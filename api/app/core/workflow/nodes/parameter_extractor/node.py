import os

import json_repair
from typing import Any

from jinja2 import Template

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.parameter_extractor.config import ParameterExtractorNodeConfig
from app.db import get_db_read
from app.models import ModelType
from app.services.model_service import ModelConfigService


class ParameterExtractorNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = ParameterExtractorNodeConfig(**self.config)

    @staticmethod
    def _get_prompt():
        """
        Load system and user prompt templates from local prompt files.

        Notes:
        - Templates are expected to be Jinja2 files.
        - Reading from disk each time ensures the latest template is used (could be cached if performance-critical).
        - Both templates must exist, otherwise an exception will be raised.

        Returns:
            Tuple[str, str]: system_prompt, user_prompt
        """
        current_dir = os.path.dirname(os.path.abspath(__file__))
        with open(
                os.path.join(current_dir, "prompt", "system_prompt.jinja2"),
                encoding='utf-8'
        ) as f:
            system_prompt = f.read()
        with open(os.path.join(
                current_dir, "prompt", "user_prompt.jinja2"),
                encoding='utf-8'
        ) as f:
            user_prompt = f.read()
        return system_prompt, user_prompt

    def _get_llm_instance(self) -> RedBearLLM:
        """
        Retrieve a configured LLM instance based on the model ID from database.

        Responsibilities:
        - Validate that the model exists and has at least one API key configured.
        - Construct RedBearLLM instance with proper credentials and model type.
        - Raise clear BusinessException if configuration is invalid.

        Returns:
            RedBearLLM: Configured LLM instance ready to be invoked.

        Raises:
            BusinessException: If the model is missing or lacks valid API key.
        """
        model_id = self.typed_config.model_id

        with get_db_read() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)

            if not config:
                raise BusinessException("配置的模型不存在", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

            api_config = config.api_keys[0]
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            api_base = api_config.api_base
            model_type = config.type

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
            ),
            type=ModelType(model_type)
        )
        return llm

    def _get_field_desc(self) -> dict[str, str]:
        """
        Build a dictionary mapping each parameter name to its description.
        Useful for dynamically generating prompts for LLM.

        Returns:
            dict[str, str]: Mapping of parameter names to descriptions.
        """
        field_desc = {}
        for param in self.typed_config.params:
            field_desc[param.name] = param.desc
        return field_desc

    def _get_field_type(self) -> dict[str, str]:
        """
        Build a dictionary mapping each parameter name to its description.
        Useful for dynamically generating prompts for LLM.

        Returns:
            dict[str, str]: Mapping of parameter names to descriptions.
        """
        field_type = {}
        for param in self.typed_config.params:
            field_type[param.name] = param.type
        return field_type

    async def execute(self, state: WorkflowState) -> Any:
        """
        Main execution function for this node.

        Workflow:
        1. Retrieve LLM instance with valid credentials.
        2. Render user prompt template with field descriptions, types, and input text.
        3. Send system and user prompts to LLM asynchronously.
        4. Repair LLM JSON output safely.
        5. Return output dictionary.

        Notes:
        - JSON repair is used to handle minor formatting errors in LLM output.
        - Exceptions are raised explicitly if parsing fails, to prevent silent workflow failures.
        - Rendering uses self._render_template for dynamic substitution from workflow state.

        Args:
            state (WorkflowState): Current state of the workflow, used for template rendering.

        Returns:
            dict[str, Any]: Dictionary containing extracted parameters under the "output" key.

        Raises:
            BusinessException: If LLM output cannot be parsed as valid JSON.
        """
        llm = self._get_llm_instance()
        system_prompt, user_prompt = self._get_prompt()

        user_prompt_teplate = Template(user_prompt)
        rendered_user_prompt = user_prompt_teplate.render(
            field_descriptions=str(self._get_field_desc()),
            field_type=str(self._get_field_type()),
            text_input=self._render_template(self.typed_config.text, state)
        )

        messages = [
            ("system", system_prompt),
            ("user", rendered_user_prompt),
        ]

        model_resp = await llm.ainvoke(messages)
        result = json_repair.repair_json(model_resp.content)

        return {
            "output": result,
        }
