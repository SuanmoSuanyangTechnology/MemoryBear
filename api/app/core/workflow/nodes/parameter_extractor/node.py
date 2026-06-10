import logging
import os
from typing import Any

import json_repair
from jinja2 import Template

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.parameter_extractor.config import ParameterExtractorNodeConfig, InferenceMode
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE
from app.db import get_db_read
from app.models import ModelType
from app.models.models_model import ModelCapability
from app.services.model_service import ModelConfigService

logger = logging.getLogger(__name__)


class ParameterExtractorNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: ParameterExtractorNodeConfig | None = None
        self.response_metadata = {}
        self._last_messages: list = []
        self._model_capability: list[str] = []

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

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        return {
            "text": self._render_template(self.typed_config.text, variable_pool),
            "prompt": self._render_template(self.typed_config.prompt, variable_pool),
            "params": [param.model_dump(mode="json") for param in self.typed_config.params],
            "model_id": str(self.typed_config.model_id),
            "inference_mode": self.typed_config.inference_mode,
        }

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": {
            "messages": self._last_messages,
            "model_id": str(self.typed_config.model_id) if self.typed_config else None,
            "inference_mode": str(self.typed_config.inference_mode) if self.typed_config else None,
        }}

    def _extract_output(self, business_result: Any) -> Any:
        final_output = {}
        for param in self.typed_config.params:
            final_output[param.name] = business_result.get(param.name) or DEFAULT_VALUE(self.output_types[param.name])
        return final_output

    def _output_types(self) -> dict[str, VariableType]:
        outputs = {}
        for param in self.config.get("params", []):
            outputs[param["name"]] = VariableType(param["type"])
        return outputs

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
                raise BusinessException("Configured model does not exist", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("Model configuration is missing API Key", BizCode.INVALID_PARAMETER)

            api_config = self.model_balance(config)
            model_name = api_config.model_name
            provider = api_config.provider
            api_key = api_config.api_key
            api_base = api_config.api_base
            is_omni = api_config.is_omni
            capability = api_config.capability
            model_type = config.type

        self._model_capability = capability or []

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                is_omni=is_omni
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
            field_type[param.name] = f'{param.type}, required:{str(param.required)}'
        return field_type

    def _build_tool_schema(self) -> dict:
        properties = {}
        required = []
        type_mapping = {
            "string": "string",
            "number": "number",
            "boolean": "boolean",
            "array[string]": ("array", "string"),
            "array[number]": ("array", "number"),
            "array[boolean]": ("array", "boolean"),
            "array[object]": ("array", "object"),
        }
        for param in self.typed_config.params:
            mapped = type_mapping.get(param.type, "string")
            if isinstance(mapped, tuple):
                prop = {"type": mapped[0], "items": {"type": mapped[1]}, "description": param.desc}
            else:
                prop = {"type": mapped, "description": param.desc}
            properties[param.name] = prop
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": "extract_parameters",
                "description": "Extract structured parameters from the input text",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def _resolve_inference_mode(self) -> InferenceMode:
        mode = self.typed_config.inference_mode
        if mode == InferenceMode.FUNCTION_CALLING:
            if ModelCapability.FUNCTION_CALL not in self._model_capability:
                logger.warning(
                    f"node: {self.node_id} model does not support function_call, "
                    f"falling back to prompt mode"
                )
                return InferenceMode.PROMPT
        return mode

    async def _execute_function_calling(self, llm: RedBearLLM, variable_pool: VariablePool) -> dict:
        tool_schema = self._build_tool_schema()
        llm_with_tools = llm.bind_tools([tool_schema])

        text_input = self._render_template(self.typed_config.text, variable_pool)
        messages = [
            ("system", "You are a parameter extraction engine. Extract the required parameters from the user's text by calling the extract_parameters tool."),
        ]
        if self.typed_config.prompt:
            messages.append(("user", self._render_template(self.typed_config.prompt, variable_pool)))
        messages.append(("user", text_input))

        self._last_messages = [{"role": r, "content": c} for r, c in messages]

        response = await llm_with_tools.ainvoke(messages)
        self.response_metadata = {
            **response.response_metadata,
            "token_usage": getattr(response, 'usage_metadata', None) or response.response_metadata.get('token_usage')
        }

        if hasattr(response, 'tool_calls') and response.tool_calls:
            result = response.tool_calls[0].get("args", {})
            logger.info(f"node: {self.node_id} function_calling params:{result}")
            return result

        logger.warning(f"node: {self.node_id} function_calling returned no tool_calls, falling back to content parsing")
        content = self.process_model_output(response.content)
        result = json_repair.repair_json(content, return_objects=True)
        return result

    async def _execute_prompt(self, llm: RedBearLLM, variable_pool: VariablePool) -> dict:
        system_prompt, user_prompt = self._get_prompt()

        user_prompt_template = Template(user_prompt)
        rendered_user_prompt = user_prompt_template.render(
            field_descriptions=str(self._get_field_desc()),
            field_type=str(self._get_field_type()),
            text_input=self._render_template(self.typed_config.text, variable_pool)
        )

        messages = [("system", system_prompt)]
        if self.typed_config.prompt:
            messages.extend([
                ("user", self._render_template(self.typed_config.prompt, variable_pool)),
                ("user", rendered_user_prompt),
            ])
        else:
            messages.append(("user", rendered_user_prompt))

        self._last_messages = [{"role": r, "content": c} for r, c in messages]

        model_resp = await llm.ainvoke(messages)
        self.response_metadata = {
            **model_resp.response_metadata,
            "token_usage": getattr(model_resp, 'usage_metadata', None) or model_resp.response_metadata.get('token_usage')
        }
        model_message = self.process_model_output(model_resp.content)
        result = json_repair.repair_json(model_message, return_objects=True)
        logger.info(f"node: {self.node_id} prompt mode params:{result}")
        return result

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = ParameterExtractorNodeConfig(**self.config)
        llm = self._get_llm_instance()

        actual_mode = self._resolve_inference_mode()
        logger.info(f"node: {self.node_id} inference_mode={actual_mode}")

        if actual_mode == InferenceMode.FUNCTION_CALLING:
            return await self._execute_function_calling(llm, variable_pool)
        return await self._execute_prompt(llm, variable_pool)
