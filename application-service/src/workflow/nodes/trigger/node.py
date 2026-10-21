"""
Trigger node implementation.

The trigger node serves as the workflow entry point, structuring the raw
webhook/schedule event directly into node outputs that downstream nodes can
access via `trigger_id.xxx`.
"""

from __future__ import annotations

from typing import Any

from ...engine.state_manager import WorkflowState
from ...engine.variable_pool import VariablePool
from ..base_node import BaseNode
from ...variable.base_variable import VariableType
from ....utils.workflow_triggers import (
    build_schedule_trigger_output,
    build_webhook_trigger_output,
    get_trigger_type,
)


class TriggerNode(BaseNode):
    """Workflow trigger entry node."""

    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.trigger_type = get_trigger_type(node_config)

    def _output_types(self) -> dict[str, VariableType]:
        if self.trigger_type == "webhook":
            return {
                "query_params": VariableType.OBJECT,
                "header_params": VariableType.OBJECT,
                "req_body_params": VariableType.OBJECT,
                "webhook_raw": VariableType.OBJECT,
            }
        if self.trigger_type == "schedule":
            return {
                "schedule": VariableType.OBJECT,
            }
        return {
            "payload": VariableType.OBJECT,
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        trigger_payload = variable_pool.get_value("sys.trigger_payload", default={}, strict=False) or {}
        trigger_info = variable_pool.get_value("sys.trigger", default={}, strict=False) or {}

        configured_trigger_id = trigger_info.get("id")
        if configured_trigger_id and configured_trigger_id != self.node_id:
            raise ValueError(f"触发器节点不匹配: expected={configured_trigger_id}, actual={self.node_id}")

        if self.trigger_type == "webhook":
            return build_webhook_trigger_output(self.node_config, trigger_payload)

        if self.trigger_type == "schedule":
            return build_schedule_trigger_output(self.node_config, trigger_payload)

        return {"payload": trigger_payload}

    def _extract_input(self, state: WorkflowState, variable_pool: VariablePool) -> dict[str, Any]:
        return {
            "trigger": variable_pool.get_value("sys.trigger", default={}, strict=False) or {},
            "trigger_payload": variable_pool.get_value("sys.trigger_payload", default={}, strict=False) or {},
        }
