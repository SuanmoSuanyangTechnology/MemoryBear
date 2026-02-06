from typing import Any

from app.core.workflow.nodes import WorkflowState
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.memory.config import MemoryReadNodeConfig, MemoryWriteNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable_pool import VariablePool
from app.db import get_db_read
from app.services.memory_agent_service import MemoryAgentService
from app.tasks import write_message_task


class MemoryReadNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: MemoryReadNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {
            "answer": VariableType.STRING,
            "intermediate_outputs": VariableType.ARRAY_OBJECT
        }

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = MemoryReadNodeConfig(**self.config)
        with get_db_read() as db:
            end_user_id = self.get_variable("sys.user_id", variable_pool)

            if not end_user_id:
                raise RuntimeError("End user id is required")

            return await MemoryAgentService().read_memory(
                end_user_id=end_user_id,
                message=self._render_template(self.typed_config.message, variable_pool),
                config_id=self.typed_config.config_id,
                search_switch=self.typed_config.search_switch,
                history=[],
                db=db,
                storage_type="neo4j",
                user_rag_memory_id=""
            )


class MemoryWriteNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: MemoryWriteNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {"output": VariableType.STRING}

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = MemoryWriteNodeConfig(**self.config)
        end_user_id = self.get_variable("sys.user_id", variable_pool)

        if not end_user_id:
            raise RuntimeError("End user id is required")

        write_message_task.delay(
            end_user_id,
            self._render_template(self.typed_config.message, variable_pool),
            str(self.typed_config.config_id),
            "neo4j",
            ""
        )

        return "success"
