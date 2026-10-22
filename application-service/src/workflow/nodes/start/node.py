"""
Start node implementation

The entry node of a workflow: declares input variables and outputs system parameters.
"""

import json
import logging
from typing import Any

from ...engine.state_manager import WorkflowState
from ...engine.variable_pool import VariablePool
from ..base_node import BaseNode
from .config import StartNodeConfig
from ...variable.base_variable import VariableType, DEFAULT_VALUE

logger = logging.getLogger(__name__)


class StartNode(BaseNode):
    """Start node
    
    The entry node of a workflow. Responsibilities:
    1. Declare the workflow input variables (via configuration)
    2. Output system variables (sys.*)
    3. Output conversation variables (conv.*)
    
    Note: variable validation and default handling are performed by the Executor at initialization time.
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)

        # Parse and validate configuration
        self.typed_config: StartNodeConfig | None = None
        self.output_var_types = {}

    def _output_types(self) -> dict[str, VariableType]:
        return self.output_var_types | {
            "message": VariableType.STRING,
            "execution_id": VariableType.STRING,
            "conversation_id": VariableType.STRING,
            "workspace_id": VariableType.STRING,
            "user_id": VariableType.STRING,
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """Execute the start node business logic
        
        The start node outputs system variables, conversation variables and custom variables.
        
        Args:
            state: Workflow state
            variable_pool: Variable pool
        
        Returns:
            Dictionary containing system parameters, conversation variables and custom variables
        """
        self.typed_config = StartNodeConfig(**self.config)

        # Process custom variables (pool passed in to avoid re-creating it)
        custom_vars = self._process_custom_variables(variable_pool)
        user_message = variable_pool.get_value("sys.message", default=None, strict=False)

        # Return business data (including custom variables)
        result = {
            "execution_id": variable_pool.get_value("sys.execution_id"),
            "conversation_id": variable_pool.get_value("sys.conversation_id"),
            "workspace_id": variable_pool.get_value("sys.workspace_id"),
            "user_id": variable_pool.get_value("sys.user_id"),
            **custom_vars  # Custom variables as part of the node output
        }
        if user_message is not None:
            result["message"] = user_message

        logger.debug(
            f"Node {self.node_id} (Start) execution completed, "
            f"outputting {len(custom_vars)} custom variables"
        )

        return result

    def _process_custom_variables(self, pool: VariablePool) -> dict[str, Any]:
        """Process custom variables
        
        Extract custom variables from the input data, applying defaults and validation.
        
        Args:
            pool: Variable pool instance
        
        Returns:
            Dictionary of processed custom variables
        
        Raises:
            ValueError: A required variable is missing
        """
        # Read custom variables from the input data
        input_variables = pool.get_value("sys.input_variables", default={}, strict=False)

        processed = {}

        # Iterate over the configured variable definitions
        for var_def in self.typed_config.variables:
            var_name = var_def.name
            var_type = var_def.type
            ui_type = var_def.ui_type

            # Boolean (boolean/checkbox) types: the frontend submits `false` (not `None`)
            # when unchecked, while API callers may omit the field or pass `null`.
            # `false` is a valid required boolean value, so a missing/null field must fall
            # back to the default (usually `False`) instead of raising a
            # "missing required variable" error, matching frontend behavior.
            provided = var_name in input_variables
            value = input_variables.get(var_name) if provided else None
            if var_type == VariableType.BOOLEAN and value is None:
                value = var_def.default if var_def.default is not None else DEFAULT_VALUE(VariableType.BOOLEAN)
                provided = True

            # An explicit `null` is valid input but every non-FILE variable type
            # rejects None. Fall back to the default value (same as a missing field)
            # instead of letting the downstream typed variable raise TypeError.
            if provided and value is None and var_type != VariableType.FILE:
                value = var_def.default if var_def.default is not None else DEFAULT_VALUE(var_type)

            # Check whether the variable was provided
            if provided:
                # select: the value must be in the options list
                if ui_type == "select" and var_def.options:
                    if value not in var_def.options:
                        raise ValueError(
                            f"变量 '{var_name}' 的值 '{value}' 不在可选范围内: {var_def.options}"
                        )

                # json-editor: validate the JSON format
                if var_type == VariableType.OBJECT and ui_type == "json-editor":
                    if not isinstance(value, dict):
                        try:
                            value = json.loads(value) if isinstance(value, str) else value
                        except (json.JSONDecodeError, TypeError):
                            raise ValueError(f"变量 '{var_name}' 不是有效的 JSON 对象")

                if var_type == VariableType.STRING and ui_type != "select":
                    max_len = var_def.max_length
                    if isinstance(value, str) and len(value) > max_len:
                        raise ValueError(
                            f"变量 '{var_name}' 超过最大长度限制 ({max_len})"
                        )

                # file: file type validation
                if var_type == VariableType.FILE and var_def.allowed_file_types:
                    if isinstance(value, dict):
                        file_type = value.get("type", value.get("origin_file_type", ""))
                        if file_type and file_type not in var_def.allowed_file_types:
                            raise ValueError(
                                f"变量 '{var_name}' 的文件类型 '{file_type}' 不在允许范围内: "
                                f"{var_def.allowed_file_types}"
                            )

                # array[file]: count and type validation
                if var_type == VariableType.ARRAY_FILE and isinstance(value, list):
                    if var_def.max_file_count and len(value) > var_def.max_file_count:
                        raise ValueError(
                            f"变量 '{var_name}' 的文件数量 {len(value)} 超过限制 {var_def.max_file_count}"
                        )
                    if var_def.allowed_file_types:
                        for f in value:
                            file_type = f.get("type", f.get("origin_file_type", "")) if isinstance(f, dict) else ""
                            if file_type and file_type not in var_def.allowed_file_types:
                                raise ValueError(
                                    f"变量 '{var_name}' 中包含不允许的文件类型 '{file_type}'"
                                )

            elif var_def.required and var_def.default is None:
                raise ValueError(
                    f"缺少必需的输入变量: {var_name}"
                    + (f" ({var_def.description})" if var_def.description else "")
                )

            else:
                value = var_def.default if var_def.default is not None else DEFAULT_VALUE(var_type)

            processed[var_name] = value
            self.output_var_types[var_name] = var_type

        return processed

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """Extract input data (for recording)
        
        Args:
            state: Workflow state
        
        Returns:
            Input data dictionary
        """
        input_variables = variable_pool.get_value("sys.input_variables", default={}, strict=False)
        result = {
            "execution_id": variable_pool.get_value("sys.execution_id"),
            "conversation_id": variable_pool.get_value("sys.conversation_id"),
            "conversation_vars": variable_pool.get_all_conversation_vars(),
            "input_variables": input_variables,
        }
        user_message = variable_pool.get_value("sys.message", default=None, strict=False)
        if user_message is not None:
            result["message"] = user_message
        return result
