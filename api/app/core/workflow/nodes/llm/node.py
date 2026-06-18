"""
LLM 节点实现

调用 LLM 模型进行文本生成。
"""

import logging
import json
from copy import deepcopy
from typing import Any

from langchain_core.messages import AIMessage

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.enums import HttpErrorHandle
from app.core.workflow.nodes.llm.config import (
    JsonOutputFieldConfig,
    LLMNodeConfig,
    normalize_json_output_fields,
    validate_llm_param_constraints,
    strip_unsupported_llm_params,
    _MULTIMODAL_COMPATIBLE_PROVIDERS,
)
from app.core.workflow.variable.base_variable import VariableType
from app.db import get_db_context
from app.models import ModelType
from app.schemas.model_schema import ModelInfo
from app.services.model_service import ModelConfigService
from app.models.models_model import ModelCapability, ModelProvider

logger = logging.getLogger(__name__)


class LLMNode(BaseNode):
    """LLM 节点
    
    支持流式和非流式输出，使用 LangChain 标准的消息格式。
    
    配置示例（支持多种消息格式）:
    
    1. 简单文本格式：
    {
        "type": "llm",
        "config": {
            "model_id": "uuid",
            "prompt": "请分析：{{sys.message}}",
            "temperature": 0.7,
            "max_tokens": 1000
        }
    }
    
    2. LangChain 消息格式（推荐）：
    {
        "type": "llm",
        "config": {
            "model_id": "uuid",
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个专业的 AI 助手。"
                },
                {
                    "role": "user",
                    "content": "{{sys.message}}"
                }
            ],
            "temperature": 0.7,
            "max_tokens": 1000
        }
    }
    
    支持的角色类型：
    - system: 系统消息（SystemMessage）
    - user/human: 用户消息（HumanMessage）
    - ai/assistant: AI 消息（AIMessage）
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: LLMNodeConfig | None = None
        self.messages = []
        self.history_messages = []
        self.model_info: ModelInfo | None = None
        self._param_warnings: list[str] = []

    @staticmethod
    def _config_value(value: Any) -> Any:
        if isinstance(value, dict) and "defaultValue" in value:
            return value.get("defaultValue")
        return value

    @classmethod
    def _is_json_output_requested(cls, config: dict[str, Any]) -> bool:
        return bool(cls._config_value(config.get("json_output")))

    @classmethod
    def _is_structured_output_requested(cls, config: dict[str, Any]) -> bool:
        if not cls._is_json_output_requested(config):
            return False
        if "structured_output" in config:
            return bool(cls._config_value(config.get("structured_output")))
        return bool(normalize_json_output_fields(cls._config_value(config.get("json_output_fields"))))

    @staticmethod
    def _supports_json_output(capability_set: set[str]) -> bool:
        return ModelCapability.JSON_OUTPUT in capability_set

    @staticmethod
    def _should_inject_json_prompt(
            json_output: bool,
            response_format_json: bool,
            capability_set: set[str],
    ) -> bool:
        return json_output or (
            response_format_json and ModelCapability.JSON_OUTPUT in capability_set
        )

    def _json_output_fields(self) -> list[JsonOutputFieldConfig]:
        if not self._is_structured_output_requested(self.config):
            return []
        return normalize_json_output_fields(self._config_value(self.config.get("json_output_fields")))

    def _output_types(self) -> dict[str, VariableType]:
        output_types = {
            "output": VariableType.STRING,
            "branch_signal": VariableType.STRING,
            "reasoning_content": VariableType.STRING,
            "token_usage": VariableType.OBJECT,
            "param_warnings": VariableType.ARRAY_STRING,
            "history": VariableType.ARRAY_OBJECT,
        }
        if self._is_structured_output_requested(self.config):
            output_types["structured_output"] = VariableType.OBJECT
        return output_types

    @staticmethod
    def _strip_json_code_fence(content: str) -> str:
        stripped = content.strip()
        if not stripped.startswith("```") or not stripped.endswith("```"):
            return stripped
        lines = stripped.splitlines()
        if len(lines) < 2:
            return stripped
        return "\n".join(lines[1:-1]).strip()

    @classmethod
    def _parse_json_object(cls, content: str) -> dict[str, Any] | None:
        if not isinstance(content, str) or not content.strip():
            return None

        candidates = [content.strip(), cls._strip_json_code_fence(content)]
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        decoder = json.JSONDecoder()
        search_text = candidates[-1]
        for index, char in enumerate(search_text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(search_text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    @staticmethod
    def _collect_field_paths(
            fields: list[JsonOutputFieldConfig],
            parent_path: str = "",
    ) -> tuple[list[str], list[str]]:
        """Recursively collect all required and optional field dot-paths.

        Returns two lists: required_paths and optional_paths.
        For example, with parent_path="customer.address" and a field
        named "district" that is optional, the path would be
        "customer.address.district".
        """
        required_paths: list[str] = []
        optional_paths: list[str] = []
        for field in fields:
            path = f"{parent_path}.{field.name}" if parent_path else field.name
            if field.required:
                required_paths.append(path)
            else:
                optional_paths.append(path)
            # Recurse into children of object / array[object] fields
            if field.children and field.type in {VariableType.OBJECT, VariableType.ARRAY_OBJECT}:
                child_required, child_optional = LLMNode._collect_field_paths(
                    field.children, path,
                )
                required_paths.extend(child_required)
                optional_paths.extend(child_optional)
        return required_paths, optional_paths

    def _build_json_prompt_suffix(self) -> str:
        fields = self._json_output_fields()
        if not fields:
            return "\n请仅输出一个合法JSON对象，不要输出Markdown代码块或额外说明。"
        field_list = ", ".join(self._format_json_output_field(field) for field in fields)
        required_paths, optional_paths = self._collect_field_paths(fields)
        parts = [
            "\n请仅输出一个合法JSON对象。",
            f"顶层字段必须且只能包含: {field_list}。",
            "字段名必须完全一致。",
        ]
        if required_paths:
            parts.append(f"必填字段({', '.join(required_paths)})不可省略，无法确定时返回合理的占位值。")
        if optional_paths:
            parts.append(f"可选字段({', '.join(optional_paths)})可以省略或返回null。")
        parts.append("不要输出Markdown代码块或额外说明。")
        return " ".join(parts)

    @staticmethod
    def _format_json_output_field(field: JsonOutputFieldConfig) -> str:
        """Format a field for the prompt suffix, including description if present.

        Examples:
            - "name"(string, required)
            - "name"(string, optional, 用户全名)
            - "address"(object, required: "city"(string), "zip"(number))
            - "address"(object, optional, 地址信息: "city"(string), "zip"(number))
        """
        desc_part = f", {field.description}" if field.description else ""
        required_label = "required" if field.required else "optional"
        if field.children and field.type in {VariableType.OBJECT, VariableType.ARRAY_OBJECT}:
            children = ", ".join(LLMNode._format_json_output_field(child) for child in field.children)
            return f'"{field.name}"({field.type.value}, {required_label}{desc_part}: {children})'
        return f'"{field.name}"({field.type.value}, {required_label}{desc_part})'

    @staticmethod
    def _json_schema_for_field(field: JsonOutputFieldConfig, *, nullable: bool = True) -> dict[str, Any]:
        field_type = field.type
        # Optional (required=False) scalar fields allow null; required fields do not
        effective_nullable = nullable if field.required else True
        scalar_type_map = {
            VariableType.STRING: "string",
            VariableType.NUMBER: "number",
            VariableType.BOOLEAN: "boolean",
        }
        array_item_type_map = {
            VariableType.ARRAY_STRING: "string",
            VariableType.ARRAY_NUMBER: "number",
        }
        if field_type in scalar_type_map:
            json_type = scalar_type_map[field_type]
            schema = {"type": [json_type, "null"] if effective_nullable else json_type}
        elif field_type == VariableType.OBJECT:
            schema: dict[str, Any] = {"type": ["object", "null"] if effective_nullable else "object"}
            if field.children:
                schema["properties"] = {
                    child.name: LLMNode._json_schema_for_field(child, nullable=False)
                    for child in field.children
                }
                schema["required"] = [child.name for child in field.children if child.required]
                schema["additionalProperties"] = False
            else:
                schema["additionalProperties"] = True
        elif field_type in array_item_type_map:
            item_schema: dict[str, Any] = {"type": array_item_type_map[field_type]}
            schema = {"type": ["array", "null"], "items": item_schema}
        elif field_type == VariableType.ARRAY_OBJECT:
            # Build the items schema from children only — the items object
            # schema does NOT carry the array's own description.
            item_schema = LLMNode._json_schema_for_field(
                JsonOutputFieldConfig(name=field.name, type=VariableType.OBJECT, required=field.required, children=field.children),
                nullable=False,
            )
            schema = {"type": ["array", "null"], "items": item_schema}
        else:
            return {}
        # Inject description into JSON Schema for all field types
        if field.description:
            schema["description"] = field.description
        return schema

    def _build_structured_response_format(self) -> dict[str, Any] | None:
        if not self._is_structured_output_requested(self.config):
            return None
        fields = self._json_output_fields()
        if not fields:
            return None
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        field.name: self._json_schema_for_field(field, nullable=False)
                        for field in fields
                    },
                    "required": [field.name for field in fields if field.required],
                    "additionalProperties": False,
                },
            },
        }

    # ------------------------------------------------------------------
    # Structured output helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _schema_default(fields: list[JsonOutputFieldConfig]) -> dict[str, Any]:
        """Build a complete default dict from the schema definition.

        Every field defined in json_output_fields — including nested children
        inside ``object`` and ``array[object]`` — gets a null / empty default.
        The result serves as a template that can be merged with the model's
        actual response so that *all* schema-defined fields appear in
        ``structured_output``, even when the model omits them.

        - required=True, ``object`` → dict with all children (recursively)
        - required=True, ``array[object]`` → empty list ``[]``
        - required=True, other types → None
        - required=False, ``object`` with children → dict with all children
          (recursively, each child defaults to null) so the full schema
          structure is always visible in ``structured_output``
        - required=False, ``array[object]`` → empty list ``[]``
        - required=False, other types → None
        """
        result: dict[str, Any] = {}
        for field in fields:
            if field.type == VariableType.OBJECT:
                result[field.name] = LLMNode._schema_default(field.children) if field.children else {}
            elif field.type == VariableType.ARRAY_OBJECT:
                result[field.name] = []
            else:
                result[field.name] = None
        return result

    @staticmethod
    def _fill_object_defaults(
            value: Any,
            children: list[JsonOutputFieldConfig],
    ) -> Any:
        """Merge a model-returned object with the schema defaults.

        For each child field defined in the schema:
        - If the model returned a value, keep it (and recursively fill
          nested object/array[object] children).
        - If the model omitted it:
          - ``object`` with children → fill full schema default dict so
            all sub-fields are visible in ``structured_output``
          - ``array[object]`` → fill ``[]``
          - other types → fill ``None``

        This ensures that *every* schema-defined field appears in
        ``structured_output``, whether required or optional.
        """
        if not isinstance(value, dict):
            return value
        merged = dict(value)
        for child in children:
            if child.name in merged:
                if child.type == VariableType.OBJECT and child.children:
                    merged[child.name] = LLMNode._fill_object_defaults(
                        merged[child.name], child.children,
                    )
                elif child.type == VariableType.ARRAY_OBJECT and child.children:
                    merged[child.name] = LLMNode._fill_array_object_items(
                        merged[child.name], child.children,
                    )
            else:
                # Model omitted this field → fill schema default
                if child.type == VariableType.OBJECT:
                    merged[child.name] = LLMNode._schema_default(child.children) if child.children else {}
                elif child.type == VariableType.ARRAY_OBJECT:
                    merged[child.name] = []
                else:
                    merged[child.name] = None
        return merged

    @staticmethod
    def _fill_array_object_items(
            value: Any,
            children: list[JsonOutputFieldConfig],
    ) -> Any:
        """Ensure every object inside an array[object] has all defined sub-fields.

        For each object in the array, merge it with the schema defaults so
        missing child fields appear as null rather than being absent entirely.
        """
        if not isinstance(value, list):
            return value
        return [
            LLMNode._fill_object_defaults(item, children)
            if isinstance(item, dict)
            else item
            for item in value
        ]

    def _append_structured_output(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self._is_structured_output_requested(self.config):
            return result

        fields = self._json_output_fields()
        raw_output = str(result.get("output") or "")
        parsed = self._parse_json_object(raw_output)

        # Build the complete schema default — every field, including nested
        # children, appears (null / {} / []) even if the model omits it.
        schema_default = self._schema_default(fields)

        if parsed is None:
            logger.warning(
                f"节点 {self.node_id}: JSON 结构化输出解析失败。"
                f"raw_output 长度={len(raw_output)}, "
                f"前200字符={raw_output[:200]}"
            )
            # Return the full schema default when parsing fails
            result["structured_output"] = schema_default
            return result

        if fields:
            # Merge model response into schema defaults so every defined
            # field is present — model data fills in, missing keys stay null.
            result["structured_output"] = self._fill_object_defaults(parsed, fields)
            logger.info(
                f"节点 {self.node_id}: structured_output 解析成功。"
                f"parsed keys={list(parsed.keys())}, "
                f"requested fields=[{', '.join(f.name for f in fields)}]"
            )
        else:
            result["structured_output"] = parsed
        return result

    def _render_context(self, message: str, variable_pool: VariablePool):
        context = f"<context>{self._render_template(self.typed_config.context, variable_pool, strict=False)}</context>"
        return message.replace("{{context}}", context)

    def _extract_reasoning_content(self, content: str) -> tuple[str, str]:
        import re
        pattern = r'<think>(.*?)</think>'
        matches = re.findall(pattern, content, re.DOTALL)
        reasoning_content = '\n'.join(matches)
        cleaned_content = re.sub(pattern, '', content, flags=re.DOTALL).strip()
        return cleaned_content, reasoning_content

    def _is_inside_reasoning_block(self, text: str, pos: int) -> bool:
        import re
        think_ranges = [(m.start(), m.end()) for m in re.finditer(r' thinking(.*?) response', text, re.DOTALL)]
        for start, end in think_ranges:
            if start <= pos < end:
                return True
        last_open = text.rfind(' thinking')
        last_close = text.rfind(' response')
        if last_open != -1 and (last_close == -1 or last_open > last_close) and pos >= last_open:
            return True
        return False

    def _apply_stop_sequences(self, text: str) -> tuple[str, bool]:
        if not (self.typed_config.stop.enable and self.typed_config.stop.value):
            return text, False
        stop_sequences = self.typed_config.stop.value[:4]
        for seq in stop_sequences:
            idx = 0
            while True:
                pos = text.find(seq, idx)
                if pos == -1:
                    break
                if not self._is_inside_reasoning_block(text, pos):
                    return text[:pos], True
                idx = pos + len(seq)
        return text, False

    async def _prepare_llm(
            self,
            state: WorkflowState,
            variable_pool: VariablePool,
            stream: bool = False
    ) -> RedBearLLM:
        """准备 LLM 实例（公共逻辑）
        
        Args:
            variable_pool: 变量池
        
        Returns:
            (llm, messages_or_prompt): LLM 实例和消息列表或 prompt 字符串
        """
        self.typed_config = LLMNodeConfig(**self.config)

        model_id = self.typed_config.model_id
        if not model_id:
            raise ValueError(f"节点 {self.node_id} 缺少 model_id 配置")

        # 3. 在 with 块内完成所有数据库操作和数据提取
        with get_db_context() as db:
            config = ModelConfigService.get_model_by_id(db=db, model_id=model_id)

            if not config:
                raise BusinessException("配置的模型不存在", BizCode.NOT_FOUND)

            if not config.api_keys or len(config.api_keys) == 0:
                raise BusinessException("模型配置缺少 API Key", BizCode.INVALID_PARAMETER)

            # 在 Session 关闭前提取所有需要的数据
            api_config = self.model_balance(config)
            model_info = ModelInfo(
                model_name=api_config.model_name,
                model_type=ModelType(config.type),
                api_key=api_config.api_key,
                api_base=api_config.api_base,
                provider=api_config.provider,
                is_omni=api_config.is_omni,
                capability=api_config.capability
            )
            self.model_info = model_info

        param_warnings = validate_llm_param_constraints(
            config=self.typed_config,
            capability=model_info.capability or [],
            provider=model_info.provider or "",
            is_omni=model_info.is_omni,
        )
        if param_warnings:
            for w in param_warnings:
                logger.warning(f"节点 {self.node_id} 参数限制警告: {w} (模型={model_info.model_name}, 提供商={model_info.provider})")
            self._param_warnings.extend(param_warnings)

        # 4. 创建 LLM 实例（使用已提取的数据）
        # 注意：对于流式输出，需要在模型初始化时设置 streaming=True
        extra_params: dict[str, Any] = {"streaming": stream} if stream else {}
        
        if self.typed_config.temperature is not None:
            extra_params["temperature"] = self.typed_config.temperature
        if self.typed_config.max_tokens is not None:
            extra_params["max_tokens"] = self.typed_config.max_tokens
        
        if self.typed_config.top_p.enable and self.typed_config.top_p.value is not None:
            extra_params["top_p"] = self.typed_config.top_p.value
        if self.typed_config.top_k.enable and self.typed_config.top_k.value is not None:
            extra_params["top_k"] = self.typed_config.top_k.value
        if self.typed_config.seed.enable and self.typed_config.seed.value is not None:
            extra_params["seed"] = self.typed_config.seed.value
        if self.typed_config.repetition_penalty.enable and self.typed_config.repetition_penalty.value is not None:
            extra_params["repetition_penalty"] = self.typed_config.repetition_penalty.value
        if self.typed_config.frequency_penalty.enable and self.typed_config.frequency_penalty.value is not None:
            extra_params["frequency_penalty"] = self.typed_config.frequency_penalty.value
        if self.typed_config.presence_penalty.enable and self.typed_config.presence_penalty.value is not None:
            extra_params["presence_penalty"] = self.typed_config.presence_penalty.value
        if self.typed_config.stop.enable and self.typed_config.stop.value:
            extra_params["stop"] = self.typed_config.stop.value[:4]
        
        if self.typed_config.search:
            extra_params["enable_search"] = True

        deep_thinking = self.typed_config.thinking.enable
        thinking_budget_tokens = self.typed_config.thinking.budget.value if (
            self.typed_config.thinking.budget.enable and self.typed_config.thinking.budget.value is not None
        ) else None

        capability_set = set(model_info.capability or [])
        json_output = bool(self.typed_config.json_output)
        response_format_json = (
            self.typed_config.response_format.enable and
            self.typed_config.response_format.value == "json_object"
        )

        # response_format is an independent API option. It must not turn the
        # json_output switch on or off.
        if response_format_json and ModelCapability.JSON_OUTPUT in capability_set:
            extra_params["response_format"] = {"type": "json_object"}

        # If the model lacks JSON output capability, disable the json_output
        # switch. The warning is already produced by validation.
        supports_json_output = self._supports_json_output(capability_set)
        if json_output and not supports_json_output:
            json_output = False

        structured_output = self._is_structured_output_requested(self.config) and json_output
        inject_json_prompt = self._should_inject_json_prompt(
            json_output,
            response_format_json,
            capability_set,
        )
        logger.info(
            f"节点 {self.node_id}: json_output={json_output}, "
            f"structured_output={structured_output}, "
            f"response_format_json={response_format_json}, "
            f"inject_json_prompt={inject_json_prompt}, "
            f"typed_config.json_output={self.typed_config.json_output}, "
            f"typed_config.structured_output={self.typed_config.structured_output}, "
            f"capability={model_info.capability}, "
            f"has_json_output={ModelCapability.JSON_OUTPUT in capability_set}, "
            f"supports_json_output={supports_json_output}"
        )
        # 结构化输出（strict json_schema）由用户在 LLM 节点的"结构化输出"按钮控制；
        # 不再做 capability 校验——发送方与处理方由 fallback 链（prompt 注入 + _parse_json_object + _fill_object_defaults）兜底。
        if structured_output:
            response_format = self._build_structured_response_format()
            if response_format:
                extra_params["response_format"] = response_format

        if self.typed_config.extra_headers.enable and self.typed_config.extra_headers.value:
            try:
                extra_headers_dict = json.loads(self.typed_config.extra_headers.value)
                extra_params["default_headers"] = extra_headers_dict
            except json.JSONDecodeError as e:
                logger.warning(f"节点 {self.node_id}: 额外请求头 JSON 解析失败: {e}")

        # Strip provider-unsupported parameters so they never reach the API call
        extra_params, strip_warnings = strip_unsupported_llm_params(
            extra_params, model_info.provider or "", model_info.is_omni
        )
        if strip_warnings:
            for w in strip_warnings:
                logger.warning(f"节点 {self.node_id} 参数安全剥离: {w} (模型={model_info.model_name}, 提供商={model_info.provider})")
            self._param_warnings = (self._param_warnings or []) + strip_warnings

        # Vision: only enable for providers whose LLM class accepts
        # OpenAI-style multimodal content format ([{type: text, text: ...}]).
        # DashScope non-Omni (ChatTongyi) rejects this format.
        effective_vision = self.typed_config.vision
        if effective_vision:
            try:
                provider_enum = ModelProvider(model_info.provider.lower())
            except ValueError:
                provider_enum = None
            is_compatible = (
                provider_enum in _MULTIMODAL_COMPATIBLE_PROVIDERS
                or (provider_enum == ModelProvider.DASHSCOPE and model_info.is_omni)
            )
            if not is_compatible:
                effective_vision = False
                logger.warning(
                    f"节点 {self.node_id}: 模型提供商 {model_info.provider} 不支持 "
                    f"OpenAI 多模态内容格式，已自动关闭 vision")

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_info.model_name,
                provider=model_info.provider,
                api_key=model_info.api_key,
                base_url=model_info.api_base,
                is_omni=model_info.is_omni,
                capability=model_info.capability,
                deep_thinking=deep_thinking,
                thinking_budget_tokens=thinking_budget_tokens,
                json_output=json_output,
                extra_params=extra_params,
            ),
            type=model_info.model_type
        )

        logger.debug(
            f"创建 LLM 实例: provider={model_info.provider}, model={model_info.model_name}, streaming={stream}")

        messages_config = self.typed_config.messages
        if messages_config:
            # 使用 LangChain 消息格式
            messages = []
            for msg_config in messages_config:
                role = msg_config.role.lower()
                content_template = msg_config.content
                content_template = self._render_context(content_template, variable_pool)
                content = self._render_template(content_template, variable_pool, strict=False)
                # 根据角色创建对应的消息对象
                if role == "system":
                    messages.append({
                        "role": "system",
                        "content": await self.process_message(
                            model_info,
                            content,
                            effective_vision,
                        )
                    })
                elif role in ["user", "human"]:
                    messages.append({
                        "role": "user",
                        "content": await self.process_message(model_info, content, effective_vision)
                    })
                elif role in ["ai", "assistant"]:
                    messages.append({
                        "role": "assistant",
                        "content": await self.process_message(model_info, content, effective_vision)
                    })
                else:
                    logger.warning(f"未知的消息角色: {role}，默认使用 user")
                    messages.append({
                        "role": "user",
                        "content": await self.process_message(model_info, content, effective_vision)
                    })

            if self.typed_config.vision_input and effective_vision:
                file_content = []
                files = variable_pool.get_instance(self.typed_config.vision_input)
                for file in files.value:
                    content = await self.process_message(model_info, file.value, effective_vision)
                    if content:
                        file_content.extend(content)
                if messages and messages[-1]["role"] == 'user':
                    messages[-1]['content'] = messages[-1]["content"] + file_content
                else:
                    messages.append({"role": "user", "content": file_content})

            if self.typed_config.memory.enable:
                history_message = []
                history_messages = deepcopy(state["messages"][-self.typed_config.memory.window_size:])
                for message in history_messages:
                    if isinstance(message["content"], list):
                        file_content = []
                        for file in message["content"]:
                            content = await self.process_message(model_info, file, effective_vision)
                            if content:
                                file_content.extend(content)
                        history_message.append(
                            {"role": message["role"], "content": file_content}
                        )
                    else:
                        message["content"] = await self.process_message(
                            model_info,
                            message["content"],
                            effective_vision
                        )
                        history_message.append(message)
                messages = messages[:-1] + history_message + messages[-1:]
                self.history_messages = history_message
            self.messages = messages
        else:
            # 使用简单的 prompt 格式（向后兼容）——包装为标准消息列表以兼容所有 provider
            prompt_template = self.config.get("prompt", "")
            rendered = self._render_template(prompt_template, variable_pool, strict=False)
            self.messages = [{"role": "user", "content": rendered}]

        # 所有 provider 统一注入 JSON prompt 兜底，确保即使 API 层 response_format 未生效也能引导 JSON 输出
        if inject_json_prompt:
            json_prompt_suffix = self._build_json_prompt_suffix()
            system_msg = next((m for m in self.messages if m["role"] == "system"), None)
            if system_msg:
                if isinstance(system_msg["content"], list):
                    # Multimodal format: append suffix to the last text object
                    for item in reversed(system_msg["content"]):
                        if isinstance(item, dict) and item.get("type") == "text":
                            item["text"] += json_prompt_suffix
                            break
                    else:
                        system_msg["content"].append({"type": "text", "text": json_prompt_suffix})
                else:
                    system_msg["content"] += json_prompt_suffix
            else:
                self.messages.insert(0, {"role": "system", "content": json_prompt_suffix})

        return llm

    async def execute(self, state: WorkflowState, variable_pool: VariablePool):
        """非流式执行 LLM 调用
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Returns:
            dict: {"llm_result": AIMessage, "branch_signal": "SUCCESS"} on success,
                  {"llm_result": None, "branch_signal": "ERROR"} on branch error
        """
        import asyncio
        
        llm = await self._prepare_llm(state, variable_pool, False)
        max_attempts = self.typed_config.retry.max_attempts + 1 if self.typed_config.retry.enable else 1
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（非流式），尝试 {attempt + 1}/{max_attempts}")

                response = await llm.ainvoke(self.messages)
                
                if hasattr(response, 'content'):
                    content = self.process_model_output(response.content)
                else:
                    content = str(response)
                
                reasoning_content = ""
                if self.typed_config.enable_reasoning_content_extraction:
                    additional_kwargs = getattr(response, 'additional_kwargs', None) or {}
                    reasoning_content = additional_kwargs.get("reasoning_content") or additional_kwargs.get("reasoning", "")
                    if reasoning_content:
                        content, _ = self._extract_reasoning_content(content)
                    else:
                        content, reasoning_content = self._extract_reasoning_content(content)

                content, _ = self._apply_stop_sequences(content)

                logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(content)}")

                result = {
                    "llm_result": AIMessage(content=content, response_metadata={
                        **response.response_metadata,
                        "token_usage": getattr(response, 'usage_metadata', None) or response.response_metadata.get(
                            'token_usage'),
                        "reasoning_content": reasoning_content if reasoning_content else None
                    }),
                    "branch_signal": "SUCCESS",
                }
                
                result["reasoning_content"] = reasoning_content
                result["history"] = self.history_messages
                
                if hasattr(self, '_param_warnings') and self._param_warnings:
                    result["param_warnings"] = self._param_warnings
                
                return result
                
            except Exception as e:
                last_error = e
                logger.error(f"节点 {self.node_id} LLM 调用失败（尝试 {attempt + 1}/{max_attempts}）: {e}")
                
                if attempt < max_attempts - 1 and self.typed_config.retry.enable:
                    await asyncio.sleep(self.typed_config.retry.retry_interval / 1000)
                else:
                    break
        
        return self._handle_llm_error(last_error)

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """提取输入数据（用于记录）"""

        return {
            "prompt": self.messages if isinstance(self.messages, str) else None,
            "messages": [
                {"role": msg.get("role"), "content": msg.get("content", "")}
                for msg in self.messages
            ] if isinstance(self.messages, list) else None,
            "config": {
                "model_id": self.config.get("model_id"),
                "temperature": self.config.get("temperature"),
                "max_tokens": self.config.get("max_tokens")
            }
        }

    def _extract_extra_fields(self, business_result: Any) -> dict:
        llm_result = business_result.get("llm_result") if isinstance(business_result, dict) else business_result
        if isinstance(llm_result, AIMessage):
            meta = llm_result.response_metadata or {}
            return {"process": {
                "finish_reason": meta.get("finish_reason") or meta.get("stop_reason"),
                "model": self.model_info.model_name,
            }}
        return {}

    def _extract_output(self, business_result: Any) -> dict:
        """从业务结果中提取输出变量
        
        支持新旧两种格式：
        - 新格式：{"llm_result": AIMessage, "branch_signal": "SUCCESS", "reasoning_content": "..."}
        - 旧格式：AIMessage（向后兼容）
        """
        if isinstance(business_result, dict) and "branch_signal" in business_result:
            llm_result = business_result.get("llm_result")
            result = {}
            if isinstance(llm_result, AIMessage):
                result = {
                    "output": llm_result.content,
                    "branch_signal": business_result["branch_signal"],
                }
            else:
                result = {
                    "output": str(llm_result) if llm_result else "",
                    "branch_signal": business_result["branch_signal"],
                }
            result["reasoning_content"] = business_result.get("reasoning_content") or ""
            result["history"] = business_result.get("history") or []
            if business_result.get("param_warnings"):
                result["param_warnings"] = business_result["param_warnings"]
            token_usage = self._extract_token_usage(business_result)
            result["token_usage"] = token_usage or {}
            return self._append_structured_output(result)
        # 旧格式向后兼容
        if isinstance(business_result, AIMessage):
            result = {
                "output": business_result.content,
                "branch_signal": "SUCCESS",
                "reasoning_content": "",
                "token_usage": self._extract_token_usage(business_result) or {},
            }
            return self._append_structured_output(result)
        result = {"output": str(business_result), "branch_signal": "SUCCESS", "reasoning_content": "", "token_usage": {}}
        return self._append_structured_output(result)

    def _extract_token_usage(self, business_result: Any) -> dict[str, int] | None:
        """从业务结果中提取 token 使用情况"""
        llm_result = business_result
        if isinstance(business_result, dict):
            llm_result = business_result.get("llm_result", business_result)
        if isinstance(llm_result, AIMessage) and hasattr(llm_result, 'response_metadata'):
            usage = llm_result.response_metadata.get('token_usage')
            if usage:
                return {
                    "prompt_tokens": usage.get('input_tokens', 0),
                    "completion_tokens": usage.get('output_tokens', 0),
                    "total_tokens": usage.get('total_tokens', 0)
                }
        return None

    def _handle_llm_error(self, error: Exception) -> dict:
        """处理 LLM 调用异常，根据 error_handle 配置决定行为
        
        Args:
            error: LLM 调用中捕获的异常
        
        Returns:
            dict: {"llm_result": None, "branch_signal": "ERROR"} for branch mode,
                  or default output for default mode
        
        Raises:
            原异常（当 error_handle.method 为 NONE 时）
        """
        if self.typed_config is None:
            raise error

        match self.typed_config.error_handle.method:
            case HttpErrorHandle.NONE:
                raise error
            case HttpErrorHandle.DEFAULT:
                logger.warning(
                    f"节点 {self.node_id}: LLM 调用失败，返回默认输出"
                )
                default_output = self.typed_config.error_handle.output or ""
                return {
                    "llm_result": AIMessage(content=default_output, response_metadata={}),
                    "branch_signal": "SUCCESS",
                }
            case HttpErrorHandle.BRANCH:
                logger.warning(
                    f"节点 {self.node_id}: LLM 调用失败，切换到异常处理分支"
                )
                return {
                    "llm_result": None,
                    "branch_signal": "ERROR",
                }
        raise error

    async def execute_stream(self, state: WorkflowState, variable_pool: VariablePool):
        """流式执行 LLM 调用（支持失败重试）
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Yields:
            文本片段（chunk）或完成标记
        """
        import asyncio as _asyncio

        self.typed_config = LLMNodeConfig(**self.config)
        max_attempts = self.typed_config.retry.max_attempts + 1 if self.typed_config.retry.enable else 1
        last_error = None

        for attempt in range(max_attempts):
            try:
                llm = await self._prepare_llm(state, variable_pool, True)

                logger.info(f"节点 {self.node_id} 开始执行 LLM 调用（流式），尝试 {attempt + 1}/{max_attempts}")

                full_response = ""
                chunk_count = 0
                full_reasoning_content = ""
                stop_sequences = self.typed_config.stop.value[:4] if (self.typed_config.stop.enable and self.typed_config.stop.value) else None
                need_buffer = bool(stop_sequences)
                buffered_chunks = [] if need_buffer else None
                reasoning_done_sent = False

                last_meta_data = {}
                last_usage_metadata = {}
                async for chunk in llm.astream(self.messages):
                    if hasattr(chunk, 'content'):
                        content = self.process_model_output(chunk.content)
                    else:
                        content = str(chunk)
                    if hasattr(chunk, 'response_metadata') and chunk.response_metadata:
                        last_meta_data = chunk.response_metadata
                    if hasattr(chunk, 'usage_metadata') and chunk.usage_metadata:
                        last_usage_metadata = chunk.usage_metadata
                    reasoning_chunk = ""
                    if self.typed_config.enable_reasoning_content_extraction:
                        additional_kwargs = getattr(chunk, 'additional_kwargs', None) or {}
                        reasoning_chunk = additional_kwargs.get("reasoning_content") or additional_kwargs.get("reasoning", "")
                        if reasoning_chunk:
                            full_reasoning_content += reasoning_chunk
                            yield {"__final__": False, "chunk": reasoning_chunk, "field": "reasoning_content"}

                    if content:
                        # When reasoning_content has been received but current chunk
                        # has no new reasoning, reasoning is complete. Emit the done
                        # signal now so the stream-output cursor advances past the
                        # reasoning_content segment before output chunks arrive.
                        if self.typed_config.enable_reasoning_content_extraction \
                                and full_reasoning_content \
                                and not reasoning_done_sent \
                                and not reasoning_chunk:
                            reasoning_done_sent = True
                            yield {"__final__": False, "chunk": "", "done": True, "field": "reasoning_content"}

                        full_response += content

                        if stop_sequences:
                            truncated = False
                            truncated_pos = -1
                            for seq in stop_sequences:
                                idx = 0
                                while True:
                                    pos = full_response.find(seq, idx)
                                    if pos == -1:
                                        break
                                    if not self._is_inside_reasoning_block(full_response, pos):
                                        truncated_pos = pos
                                        truncated = True
                                        break
                                    idx = pos + len(seq)
                                if truncated:
                                    break
                            if truncated:
                                full_response = full_response[:truncated_pos]
                                chunk_count += 1
                                break

                        chunk_count += 1
                        if need_buffer:
                            buffered_chunks.append(content)
                        else:
                            yield {"__final__": False, "chunk": content, "field": "output"}

                # Emit reasoning_content done signal if it wasn't sent
                # during the loop (e.g. model had no reasoning, or every
                # chunk contained reasoning_content).
                if self.typed_config.enable_reasoning_content_extraction and not reasoning_done_sent:
                    yield {"__final__": False, "chunk": "", "done": True, "field": "reasoning_content"}

                if need_buffer:
                    for c in buffered_chunks:
                        yield {"__final__": False, "chunk": c, "field": "output"}

                yield {
                    "__final__": False,
                    "chunk": "",
                    "done": True,
                    "field": "output"
                }

                reasoning_content = ""
                if self.typed_config.enable_reasoning_content_extraction:
                    if full_reasoning_content:
                        reasoning_content = full_reasoning_content
                        full_response, _ = self._extract_reasoning_content(full_response)
                    else:
                        full_response, reasoning_content = self._extract_reasoning_content(full_response)

                full_response, _ = self._apply_stop_sequences(full_response)

                logger.info(f"节点 {self.node_id} LLM 调用完成，输出长度: {len(full_response)}, 总 chunks: {chunk_count}")

                final_message = AIMessage(
                    content=full_response,
                    response_metadata={
                        **last_meta_data,
                        "token_usage": last_usage_metadata or last_meta_data.get('token_usage'),
                        "reasoning_content": reasoning_content if reasoning_content else None
                    }
                )

                result = {"llm_result": final_message, "branch_signal": "SUCCESS"}
                result["reasoning_content"] = reasoning_content
                result["history"] = self.history_messages

                if hasattr(self, '_param_warnings') and self._param_warnings:
                    result["param_warnings"] = self._param_warnings

                yield {"__final__": True, "result": result}
                return

            except Exception as e:
                last_error = e
                logger.error(f"节点 {self.node_id} LLM 流式调用失败（尝试 {attempt + 1}/{max_attempts}）: {e}")

                if attempt < max_attempts - 1 and self.typed_config.retry.enable:
                    await _asyncio.sleep(self.typed_config.retry.retry_interval / 1000)
                else:
                    break

        logger.error(f"节点 {self.node_id} LLM 流式调用最终失败，已重试 {max_attempts} 次")
        error_result = self._handle_llm_error(last_error)
        yield {"__final__": True, "result": error_result}
