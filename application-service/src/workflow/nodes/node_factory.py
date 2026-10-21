"""
Node factory.

Creates node instances based on node type.
"""

import logging
from typing import Any, Union

from .agent import AgentNode
from .assigner import AssignerNode
from .base_node import BaseNode
from .code import CodeNode
from .cycle_graph.node import CycleGraphNode
from .end import EndNode
from ...utils.workflow_node_types import NodeType
from .http_request import HttpRequestNode
from .human_intervention import HumanInterventionNode
from .if_else import IfElseNode
from .jinja_render import JinjaRenderNode
from .knowledge import KnowledgeRetrievalNode
from .llm import LLMNode
from .memory import MemoryReadNode, MemoryWriteNode
from .parameter_extractor import ParameterExtractorNode
from .start import StartNode
from .trigger import TriggerNode
from .variable_aggregator import VariableAggregatorNode
from .question_classifier import QuestionClassifierNode
from .breaker import BreakNode
from .tool import ToolNode
from .document_extractor import DocExtractorNode
from .list_operator import ListOperatorNode
from .output import OutputNode

logger = logging.getLogger(__name__)

WorkflowNode = Union[
    BaseNode,
    StartNode,
    TriggerNode,
    EndNode,
    LLMNode,
    IfElseNode,
    AgentNode,
    AssignerNode,
    HttpRequestNode,
    KnowledgeRetrievalNode,
    JinjaRenderNode,
    VariableAggregatorNode,
    ParameterExtractorNode,
    CycleGraphNode,
    BreakNode,
    ParameterExtractorNode,
    QuestionClassifierNode,
    ToolNode,
    MemoryReadNode,
    MemoryWriteNode,
    CodeNode,
    DocExtractorNode,
    ListOperatorNode,
    OutputNode,
    HumanInterventionNode,
]


class NodeFactory:
    """Node factory.

    Creates node instances via the factory pattern; easy to extend and maintain.
    """

    # Node type registry
    _node_types: dict[str, type[WorkflowNode]] = {
        NodeType.START: StartNode,
        NodeType.TRIGGER: TriggerNode,
        NodeType.END: EndNode,
        NodeType.LLM: LLMNode,
        NodeType.AGENT: AgentNode,
        NodeType.IF_ELSE: IfElseNode,
        NodeType.KNOWLEDGE_RETRIEVAL: KnowledgeRetrievalNode,
        NodeType.ASSIGNER: AssignerNode,
        NodeType.HTTP_REQUEST: HttpRequestNode,
        NodeType.JINJARENDER: JinjaRenderNode,
        NodeType.VAR_AGGREGATOR: VariableAggregatorNode,
        NodeType.PARAMETER_EXTRACTOR: ParameterExtractorNode,
        NodeType.QUESTION_CLASSIFIER: QuestionClassifierNode,
        NodeType.LOOP: CycleGraphNode,
        NodeType.ITERATION: CycleGraphNode,
        NodeType.BREAK: BreakNode,
        NodeType.CYCLE_START: StartNode,
        NodeType.TOOL: ToolNode,
        NodeType.MEMORY_READ: MemoryReadNode,
        NodeType.MEMORY_WRITE: MemoryWriteNode,
        NodeType.CODE: CodeNode,
        NodeType.DOCUMENT_EXTRACTOR: DocExtractorNode,
        NodeType.LIST_OPERATOR: ListOperatorNode,
        NodeType.OUTPUT: OutputNode,
        NodeType.HUMAN_INTERVENTION: HumanInterventionNode,
    }

    @classmethod
    def register_node_type(cls, node_type: str, node_class: type[WorkflowNode]):
        """Register a new node type.

        Args:
            node_type: Node type name.
            node_class: Node class.

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
            workflow_config: dict[str, Any],
            down_stream_nodes: list[str]
    ) -> WorkflowNode | None:
        """Create a node instance.

        Args:
            node_config: Node configuration.
            workflow_config: Workflow configuration.
            down_stream_nodes: Downstream nodes.

        Returns:
            The node instance, or None for unsupported node types.

        Raises:
            ValueError: Unsupported node type.
        """
        node_type = node_config.get("type")

        # Get the node class
        node_class = cls._node_types.get(node_type)
        if not node_class:
            raise ValueError(f"Unsupported node type: {node_type}")

        # Create the node instance
        logger.debug(f"create node instance: {node_config.get('id')} (type={node_type})")
        return node_class(node_config, workflow_config, down_stream_nodes)

    @classmethod
    def get_supported_types(cls) -> list[str]:
        """Get the list of supported node types.

        Returns:
            List of node types.
        """
        return list(cls._node_types.keys())
