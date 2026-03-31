# -*- coding: UTF-8 -*-
# Author: Eternity
# @Email: 1533512157@qq.com
# @Time : 2026/2/10 13:33
from typing import Annotated, Any

from app.core.workflow.engine.runtime_schema import ExecutionContext
from app.core.workflow.nodes.enums import NodeType


def merge_activate_state(x, y):
    merged = dict(x)
    for k, v in y.items():
        merged[k] = merged.get(k, False) or v
    return merged


def merge_looping_state(x, y):
    return y if y > x else x


class WorkflowState(dict):
    """Workflow state

    The state object passed between nodes in a workflow, containing messages, variables, node outputs, etc.
    """
    __required_keys__ = frozenset({
        "messages",
        "cycle_nodes",
        "looping",
        "node_outputs",
        "execution_id",
        "workspace_id",
        "user_id",
        "activate",
        "memory_storage_type",
        "user_rag_memory_id"
    })
    __optional_keys__ = frozenset({
        "error",
        "error_node",
    })

    # List of messages (append mode)
    messages: Annotated[list[dict[str, str]], lambda x, y: y]

    # Set of loop node IDs, used for assigning values in loop nodes
    cycle_nodes: list
    looping: Annotated[int, merge_looping_state]

    # Node outputs (stores execution results of each node for variable references)
    # Uses a custom merge function to combine new node outputs into the existing dictionary
    node_outputs: Annotated[dict[str, Any], lambda x, y: {**x, **y}]

    # Execution context
    execution_id: str
    workspace_id: str
    user_id: str

    # Error information (for error edges)
    error: str | None
    error_node: str | None

    # node activate status
    activate: Annotated[dict[str, bool], merge_activate_state]

    memory_storage_type: str
    user_rag_memory_id: str


class WorkflowStateManager:
    def create_initial_state(
            self,
            workflow_config: dict,
            input_data: dict,
            execution_context: ExecutionContext,
            start_node_id: str
    ) -> WorkflowState:
        conversation_messages = input_data.get("conv_messages", [])

        return WorkflowState(
            messages=conversation_messages,
            node_outputs={},
            execution_id=execution_context.execution_id,
            workspace_id=execution_context.workspace_id,
            user_id=execution_context.user_id,
            error=None,
            error_node=None,
            cycle_nodes=self._identify_cycle_nodes(workflow_config),
            looping=0,
            activate={
                start_node_id: True
            },
            memory_storage_type=execution_context.memory_storage_type,
            user_rag_memory_id=execution_context.user_rag_memory_id
        )

    @staticmethod
    def _identify_cycle_nodes(
            workflow_config: dict
    ):
        return [
            node.get("id")
            for node in workflow_config.get("nodes")
            if node.get("type") in [NodeType.LOOP, NodeType.ITERATION]
        ]
