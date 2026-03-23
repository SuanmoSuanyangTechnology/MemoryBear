import re
from typing import Any

from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.memory.config import MemoryReadNodeConfig, MemoryWriteNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable.variable_objects import FileVariable, ArrayVariable
from app.db import get_db_read
from app.schemas import FileInput
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
                storage_type=state["memory_storage_type"],
                user_rag_memory_id=state["user_rag_memory_id"]
            )


class MemoryWriteNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any]):
        super().__init__(node_config, workflow_config)
        self.typed_config: MemoryWriteNodeConfig | None = None

    def _output_types(self) -> dict[str, VariableType]:
        return {"output": VariableType.STRING}

    @staticmethod
    def _extract_multimodal_memory_variables(content: str, variable_pool: VariablePool) -> tuple[list[str], str]:
        variable_pattern_string = r'\{\{\s*[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\s*\}\}'
        variable_pattern = re.compile(variable_pattern_string)
        variables = variable_pattern.findall(content)
        file_variables = []
        for variable in variables:
            if variable_pool.is_file_variable(variable):
                file_variables.append(variable)
        for var in file_variables:
            content = content.replace(var, "")
        return file_variables, content

    async def execute(self, state: WorkflowState, variable_pool: VariablePool) -> Any:
        self.typed_config = MemoryWriteNodeConfig(**self.config)
        end_user_id = self.get_variable("sys.user_id", variable_pool)

        if not end_user_id:
            raise RuntimeError("End user id is required")
        messages = []
        multimodal_memories = []
        if self.typed_config.message:
            messages.append({
                "role": "user",
                "content": self._render_template(self.typed_config.message, variable_pool)
            })

        for message in self.typed_config.messages:
            file_variables, content = self._extract_multimodal_memory_variables(
                message.content,
                variable_pool
            )
            file_info = []
            for var in file_variables:
                instence: FileVariable | ArrayVariable[FileVariable] = variable_pool.get_instance(var)
                if isinstance(instence, FileVariable):
                    file_info.append(FileInput(
                        type=instence.value.type,
                        transfer_method=instence.value.transfer_method,
                        upload_file_id=instence.value.file_id,
                        url=instence.value.url,
                        file_type=instence.value.origin_file_type
                    ).model_dump())
                elif isinstance(instence, ArrayVariable) and instence.child_type == FileVariable:
                    for file_instence in instence.value:
                        file_info.append(FileInput(
                            type=file_instence.value.type,
                            transfer_method=file_instence.value.transfer_method,
                            upload_file_id=file_instence.value.file_id,
                            url=file_instence.value.url,
                            file_type=file_instence.value.origin_file_type
                        ).model_dump())
            multimodal_memories.append({
                "role": message.role,
                "files": file_info
            })
            messages.append({
                "role": message.role,
                "content": self._render_template(content, variable_pool)
            })

        write_message_task.delay(
            end_user_id=end_user_id,
            message=messages,
            file_messages=multimodal_memories,
            config_id=str(self.typed_config.config_id),
            storage_type=state["memory_storage_type"],
            user_rag_memory_id=state["user_rag_memory_id"]
        )

        return "success"
