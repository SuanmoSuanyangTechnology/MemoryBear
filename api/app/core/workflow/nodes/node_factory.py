"""
节点工厂

根据节点类型创建相应的节点实例。
"""

import logging
from typing import Any, Union

from app.core.workflow.nodes.agent import AgentNode
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.end import EndNode
from app.core.workflow.nodes.enums import NodeType
from app.core.workflow.nodes.if_else import IfElseNode
from app.core.workflow.nodes.llm import LLMNode
from app.core.workflow.nodes.start import StartNode
from app.core.workflow.nodes.transform import TransformNode

logger = logging.getLogger(__name__)

WorkflowNode = Union[
    BaseNode,
    StartNode,
    EndNode,
    LLMNode,
    IfElseNode,
    AgentNode,
    TransformNode,
]


class NodeFactory:
    """节点工厂

    使用工厂模式创建节点实例，便于扩展和维护。
    """

    # 节点类型注册表
    _node_types: dict[str, type[WorkflowNode]] = {
        NodeType.START: StartNode,
        NodeType.END: EndNode,
        NodeType.LLM: LLMNode,
        NodeType.AGENT: AgentNode,
        NodeType.TRANSFORM: TransformNode,
        NodeType.IF_ELSE: IfElseNode
    }

    @classmethod
    def register_node_type(cls, node_type: str, node_class: type[WorkflowNode]):
        """注册新的节点类型

        Args:
            node_type: 节点类型名称
            node_class: 节点类

        Examples:
            >>> class CustomNode(BaseNode):
            ...     async def execute(self, state):
            ...         return {"node_outputs": {self.node_id: {"output": "custom"}}}
            >>> NodeFactory.register_node_type("custom", CustomNode)
        """
        cls._node_types[node_type] = node_class
        logger.info(f"注册节点类型: {node_type} -> {node_class.__name__}")

    @classmethod
    def create_node(
            cls,
            node_config: dict[str, Any],
            workflow_config: dict[str, Any]
    ) -> WorkflowNode | None:
        """创建节点实例

        Args:
            node_config: 节点配置
            workflow_config: 工作流配置

        Returns:
            节点实例或 None（对于不支持的节点类型）

        Raises:
            ValueError: 不支持的节点类型
        """
        node_type = node_config.get("type")

        # 跳过条件节点（由 LangGraph 处理）
        if node_type == "condition":
            return None

        # 获取节点类
        node_class = cls._node_types.get(node_type)
        if not node_class:
            raise ValueError(f"不支持的节点类型: {node_type}")

        # 创建节点实例
        logger.debug(f"创建节点: {node_config.get('id')} (type={node_type})")
        return node_class(node_config, workflow_config)

    @classmethod
    def get_supported_types(cls) -> list[str]:
        """获取支持的节点类型列表

        Returns:
            节点类型列表
        """
        return list(cls._node_types.keys())
