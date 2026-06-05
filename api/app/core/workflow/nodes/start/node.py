"""
Start 节点实现

工作流的起始节点，定义输入变量并输出系统参数。
"""

import json
import logging
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.start.config import StartNodeConfig
from app.core.workflow.variable.base_variable import VariableType, DEFAULT_VALUE

logger = logging.getLogger(__name__)


class StartNode(BaseNode):
    """Start 节点
    
    工作流的起始节点，负责：
    1. 定义工作流的输入变量（通过配置）
    2. 输出系统变量（sys.*）
    3. 输出会话变量（conv.*）
    
    注意：变量的验证和默认值处理由 Executor 在初始化时完成。
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)

        # 解析并验证配置
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
        """执行 start 节点业务逻辑
        
        Start 节点输出系统变量、会话变量和自定义变量。
        
        Args:
            state: 工作流状态
            variable_pool: 变量池
        
        Returns:
            包含系统参数、会话变量和自定义变量的字典
        """
        self.typed_config = StartNodeConfig(**self.config)

        # 处理自定义变量（传入 pool 避免重复创建）
        custom_vars = self._process_custom_variables(variable_pool)
        user_message = variable_pool.get_value("sys.message", default=None, strict=False)

        # 返回业务数据（包含自定义变量）
        result = {
            "execution_id": variable_pool.get_value("sys.execution_id"),
            "conversation_id": variable_pool.get_value("sys.conversation_id"),
            "workspace_id": variable_pool.get_value("sys.workspace_id"),
            "user_id": variable_pool.get_value("sys.user_id"),
            **custom_vars  # 自定义变量作为节点输出的一部分
        }
        if user_message is not None:
            result["message"] = user_message

        logger.debug(
            f"Node {self.node_id} (Start) execution completed, "
            f"outputting {len(custom_vars)} custom variables"
        )

        return result

    def _process_custom_variables(self, pool: VariablePool) -> dict[str, Any]:
        """处理自定义变量
        
        从输入数据中提取自定义变量，应用默认值和验证。
        
        Args:
            pool: 变量池实例
        
        Returns:
            处理后的自定义变量字典
        
        Raises:
            ValueError: 缺少必需变量
        """
        # 获取输入数据中的自定义变量
        input_variables = pool.get_value("sys.input_variables", default={}, strict=False)

        processed = {}

        # 遍历配置的变量定义
        for var_def in self.typed_config.variables:
            var_name = var_def.name
            var_type = var_def.type
            ui_type = var_def.ui_type

            # 检查变量是否存在
            if var_name in input_variables:
                value = input_variables[var_name]

                # select: 值必须在 options 列表中
                if ui_type == "select" and var_def.options:
                    if value not in var_def.options:
                        raise ValueError(
                            f"变量 '{var_name}' 的值 '{value}' 不在可选范围内: {var_def.options}"
                        )

                # json-editor: 校验 JSON 格式
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

                # file: 文件类型校验
                if var_type == VariableType.FILE and var_def.allowed_file_types:
                    if isinstance(value, dict):
                        file_type = value.get("type", value.get("origin_file_type", ""))
                        if file_type and file_type not in var_def.allowed_file_types:
                            raise ValueError(
                                f"变量 '{var_name}' 的文件类型 '{file_type}' 不在允许范围内: "
                                f"{var_def.allowed_file_types}"
                            )

                # array[file]: 数量与类型校验
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

                processed[var_name] = value

            elif var_def.required:
                # 必需变量缺失
                raise ValueError(
                    f"缺少必需的输入变量: {var_name}"
                    + (f" ({var_def.description})" if var_def.description else "")
                )

            elif var_def.default is not None:
                # 使用默认值
                processed[var_name] = var_def.default
                logger.debug(f"变量 '{var_name}' 使用默认值: {var_def.default}")
            else:
                processed[var_name] = DEFAULT_VALUE(var_type)
            self.output_var_types[var_name] = var_type

        return processed

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        """提取输入数据（用于记录）
        
        Args:
            state: 工作流状态
        
        Returns:
            输入数据字典
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
