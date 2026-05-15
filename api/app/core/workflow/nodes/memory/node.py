import re
import logging
from typing import Any

from app.core.memory.enums import SearchStrategy
from app.core.memory.memory_service import MemoryService
from app.core.workflow.engine.state_manager import WorkflowState
from app.core.workflow.engine.variable_pool import VariablePool
from app.core.workflow.nodes.base_node import BaseNode
from app.core.workflow.nodes.memory.config import MemoryReadNodeConfig, MemoryWriteNodeConfig
from app.core.workflow.variable.base_variable import VariableType
from app.core.workflow.variable.variable_objects import FileVariable, ArrayVariable
from app.db import get_db_context, get_db_read
from app.schemas import FileInput

logger = logging.getLogger(__name__)


class MemoryReadNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: MemoryReadNodeConfig | None = None
        self._process: dict = {}

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": self._process}

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

            memory_service = MemoryService(
                db=db,
                storage_type=state["memory_storage_type"],
                config_id=str(self.typed_config.config_id),
                end_user_id=end_user_id,
                user_rag_memory_id=state["user_rag_memory_id"],
            )
            query = self._render_template(self.typed_config.message, variable_pool)
            self._process = {"query": query, "config_id": str(self.typed_config.config_id)}
            # TODO: Historical Messages -> Used to refer to coreference resolution
            search_result = await memory_service.read(
                query,
                search_switch=SearchStrategy(self.typed_config.search_switch)
            )
            self._process["memories_count"] = len(search_result.memories)
            return {
                "answer": search_result.content,
                "intermediate_outputs": [_.model_dump() for _ in search_result.memories]
            }

            # return await MemoryAgentService().read_memory(
            #     end_user_id=end_user_id,
            #     message=self._render_template(self.typed_config.message, variable_pool),
            #     config_id=self.typed_config.config_id,
            #     search_switch=self.typed_config.search_switch,
            #     history=[],
            #     db=db,
            #     storage_type=state["memory_storage_type"],
            #     user_rag_memory_id=state["user_rag_memory_id"]
            # )


class MemoryWriteNode(BaseNode):
    def __init__(self, node_config: dict[str, Any], workflow_config: dict[str, Any], down_stream_nodes: list[str]):
        super().__init__(node_config, workflow_config, down_stream_nodes)
        self.typed_config: MemoryWriteNodeConfig | None = None
        self._process: dict = {}

    def _extract_extra_fields(self, business_result: Any) -> dict:
        return {"process": self._process}

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
        conversation_id = state.get("conversation_id", "")

        if not end_user_id:
            raise RuntimeError("End user id is required")

        if not conversation_id:
            raise RuntimeError("conversation_id is required for MemoryWriteNode")

        try:
            # 1. 收集需要写入的消息
            messages_to_write = []

            # 处理单条 message
            if self.typed_config.message:
                rendered = self._render_template(self.typed_config.message, variable_pool)
                messages_to_write.append({"role": "user", "content": rendered})

            # 处理 messages 列表
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
                        ).model_dump(mode="json"))
                    elif isinstance(instence, ArrayVariable) and instence.child_type == FileVariable:
                        for file_instence in instence.value:
                            file_info.append(FileInput(
                                type=file_instence.value.type,
                                transfer_method=file_instence.value.transfer_method,
                                upload_file_id=file_instence.value.file_id,
                                url=file_instence.value.url,
                                file_type=file_instence.value.origin_file_type
                            ).model_dump(mode="json"))

                rendered_content = self._render_template(content, variable_pool)
                messages_to_write.append({"role": message.role, "content": rendered_content})

            # 2. 通过 MemoryService 统一门户写入 memory_messages 并触发调度
            # MemoryService 内部负责：写入 memory_messages 表 + 分派 SlidingWindowScheduler
            with get_db_context() as db:
                memory_service = MemoryService(
                    db=db,
                    config_id=str(self.typed_config.config_id),
                    end_user_id=end_user_id,
                    workspace_id=state.get("workspace_id", ""),
                    storage_type=state.get("memory_storage_type", "neo4j"),
                    language=state.get("language", "zh"),
                )

            await memory_service.write_workflow_messages(
                conversation_id=str(conversation_id),
                messages=messages_to_write,
                config_id=str(self.typed_config.config_id),
                end_user_id=end_user_id,
                workspace_id=state.get("workspace_id", ""),
                language=state.get("language", "zh"),
            )

        except Exception as e:
            logger.error(
                f"[MemoryWriteNode] 执行异常: conversation_id={conversation_id}, "
                f"node_id={self.node_id}, err={e}",
                exc_info=True,
            )
            raise

        return {"output": "success"}
