from typing import Any

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.memory.config import MemoryReadNodeConfig, MemoryWriteNodeConfig
from app.db import get_db_read, get_db_context
from app.services.memory_agent_service import MemoryAgentService


class MemoryReadNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: MemoryReadNodeConfig | None = None

    async def execute(self, state: WorkflowState) -> Any:
        self.typed_config = MemoryReadNodeConfig(**self.config)
        with get_db_read() as db:
            workspace_id = self.get_variable('sys.workspace_id', state)
            end_user_id = self.get_variable("sys.user_id", state)

            if not workspace_id:
                raise RuntimeError("Workspace id is required")
            if not end_user_id:
                raise RuntimeError("End user id is required")

            return await MemoryAgentService().read_memory(
                group_id=end_user_id,
                message=self._render_template(self.typed_config.message, state),
                config_id=str(self.typed_config.config_id),
                search_switch=self.typed_config.search_switch,
                history=[],
                db=db,
                storage_type="neo4j",
                user_rag_memory_id=""
            )


class MemoryWriteNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config = MemoryWriteNodeConfig(**self.config)

    async def execute(self, state: WorkflowState) -> Any:
        with get_db_context() as db:
            workspace_id = self.get_variable('sys.workspace_id', state)
            end_user_id = self.get_variable("sys.user_id", state)

            if not workspace_id:
                raise RuntimeError("Workspace id is required")
            if not end_user_id:
                raise RuntimeError("End user id is required")

            return await MemoryAgentService().write_memory(
                group_id=end_user_id,
                message=self._render_template(self.typed_config.message, state),
                config_id=str(self.typed_config.config_id),
                db=db,
                storage_type="neo4j",
                user_rag_memory_id=""
            )
