"""
Start 节点实现

工作流的起始节点，定义输入变量并输出系统参数。
"""

import logging
from typing import Any

from app.core.workflow.nodes.base_node import BaseNode, WorkflowState
from app.core.workflow.nodes.start.config import StartNodeConfig

logger = logging.getLogger(__name__)


class StartNode(BaseNode):
    """Start 节点
    
    工作流的起始节点，负责：
    1. 定义工作流的输入变量（通过配置）
    2. 输出系统变量（sys.*）
    3. 输出会话变量（conv.*）
    
    注意：变量的验证和默认值处理由 Executor 在初始化时完成。
    """

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        """初始化 Start 节点
        
        Args:
            node_config: 节点配置
            workflow_config: 工作流配置
        """
        super().__init__(node_config, workflow_config)

        # 解析并验证配置
        self.typed_config = StartNodeConfig(**self.config)

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        """执行 start 节点业务逻辑
        
        Start 节点输出系统变量、会话变量和自定义变量。
        
        Args:
            state: 工作流状态
        
        Returns:
            包含系统参数、会话变量和自定义变量的字典
        """
        logger.info(f"节点 {self.node_id} (Start) 开始执行")

        # 创建变量池实例（在方法内复用）
        pool = self.get_variable_pool(state)

        # 处理自定义变量（传入 pool 避免重复创建）
        custom_vars = self._process_custom_variables(pool)

        # 返回业务数据（包含自定义变量）
        result = {
            "message": pool.get("sys.message"),
            "execution_id": pool.get("sys.execution_id"),
            "conversation_id": pool.get("sys.conversation_id"),
            "workspace_id": pool.get("sys.workspace_id"),
            "user_id": pool.get("sys.user_id"),
            **custom_vars  # 自定义变量作为节点输出的一部分
        }

        logger.info(
            f"节点 {self.node_id} (Start) 执行完成，"
            f"输出了 {len(custom_vars)} 个自定义变量"
        )

        return result

    def _process_custom_variables(self, pool) -> dict[str, Any]:
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
        input_variables = pool.get("sys.input_variables", default={})

        processed = {}

        # 遍历配置的变量定义
        for var_def in self.typed_config.variables:
            var_name = var_def.name

            # 检查变量是否存在
            if var_name in input_variables:
                # 使用用户提供的值
                processed[var_name] = input_variables[var_name]

            elif var_def.required:
                # 必需变量缺失
                raise ValueError(
                    f"缺少必需的输入变量: {var_name}"
                    + (f" ({var_def.description})" if var_def.description else "")
                )

            elif var_def.default is not None:
                # 使用默认值
                processed[var_name] = var_def.default
                logger.debug(
                    f"变量 '{var_name}' 使用默认值: {var_def.default}"
                )

        return processed

    def _extract_input(self, state: WorkflowState) -> dict[str, Any]:
        """提取输入数据（用于记录）
        
        Args:
            state: 工作流状态
        
        Returns:
            输入数据字典
        """
        pool = self.get_variable_pool(state)

        return {
            "execution_id": pool.get("sys.execution_id"),
            "conversation_id": pool.get("sys.conversation_id"),
            "message": pool.get("sys.message"),
            "conversation_vars": pool.get_all_conversation_vars()
        }
