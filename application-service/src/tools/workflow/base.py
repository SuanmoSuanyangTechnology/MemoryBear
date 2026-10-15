import json
import time
import uuid
from typing import Any, Dict, List, Optional

from langchain_core.runnables.config import RunnableConfig, var_child_runnable_config
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..base import BaseTool
from ...errors import BizCode, BusinessException
from ...models import App, AppRelease
from ...models.tool_model import ToolType
from ...schemas.app_schema import DraftRunRequest
from ...schemas.tool_schema import ParameterType, ToolParameter, ToolResult


class WorkflowAsTool(BaseTool):
    """Wrap a pure workflow as an executable tool."""

    def __init__(
        self,
        db: Session,
        tool_id: str,
        workflow_app_id: uuid.UUID,
        release_id: Optional[uuid.UUID],
        tool_name: str,
        tool_description: str,
        input_parameters: List[Dict[str, Any]],
        output_schema: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
    ):
        config = {
            "workflow_app_id": str(workflow_app_id),
            "version": "1.0.0",
            "tags": ["workflow"],
            "timeout": timeout,
        }
        super().__init__(tool_id=tool_id, config=config)

        self.db = db
        self.workflow_app_id = workflow_app_id
        self.release_id = release_id
        self._name = tool_name
        self._description = tool_description
        self._input_parameters = input_parameters
        self._output_schema = output_schema
        self.workflow_service = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def tool_type(self) -> ToolType:
        return ToolType.WORKFLOW

    @property
    def parameters(self) -> List[ToolParameter]:
        params = []
        for p in self._input_parameters:
            type_str = p.get("type", "string")
            param_type = ParameterType.STRING
            if type_str == "integer":
                param_type = ParameterType.INTEGER
            elif type_str == "number":
                param_type = ParameterType.NUMBER
            elif type_str == "boolean":
                param_type = ParameterType.BOOLEAN
            elif type_str == "array":
                param_type = ParameterType.ARRAY
            elif type_str == "object":
                param_type = ParameterType.OBJECT

            params.append(
                ToolParameter(
                    name=p.get("name"),
                    type=param_type,
                    description=p.get("description", ""),
                    required=p.get("required", False),
                )
            )
        return params

    def _normalize_output(self, result: Dict[str, Any]) -> Any:
        """Prefer the structured output, tolerating workflow_service.run's return format."""
        structured_output = result.get("output_data")
        if structured_output:
            return structured_output

        structured_output = result.get("output")
        if isinstance(structured_output, str):
            text = structured_output.strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return structured_output
        return structured_output

    async def execute(self, **kwargs) -> ToolResult:
        start_time = time.time()
        try:
            if self.workflow_service is None:
                from ...services.workflow_service import WorkflowService
                # WorkflowService.run manages its own sessions and is not bound to
                # the caller's session (the caller may be an AsyncSession, or may
                # already be closed before the tool executes)
                self.workflow_service = WorkflowService()

            user_id = self.get_runtime_context("user_id")
            workspace_id, workflow_config = await self._load_runtime_config()

            payload = DraftRunRequest(
                user_id=str(user_id) if user_id else None,
                variables=kwargs,
                stream=False,
            )

            # Isolate the LangChain run config: workflow-internal nodes (LLM/Agent)
            # use llm.astream() even for non-streaming execution; if parent
            # callbacks were inherited, their on_chat_model_stream events would be
            # surfaced by the outer Agent's astream_events as answer content,
            # mixing the workflow's raw output into the answer.
            config_token = var_child_runnable_config.set(
                RunnableConfig(callbacks=[], tags=[], metadata={})
            )
            try:
                # A workflow invoked as a tool is a pure function execution: no
                # conversation is created and no user/assistant messages are
                # persisted; only the internal technical execution record with
                # source="tool" is kept, which app-level workflow logs exclude.
                result = await self.workflow_service.run(
                    app_id=self.workflow_app_id,
                    payload=payload,
                    config=workflow_config,
                    workspace_id=workspace_id,
                    release_id=self.release_id,
                    source="tool",
                    skip_save=True,
                )
            finally:
                var_child_runnable_config.reset(config_token)

            execution_time = time.time() - start_time
            structured_output = self._normalize_output(result)
            final_output = {}
            if self._output_schema and "properties" in self._output_schema:
                if isinstance(structured_output, dict):
                    for key in self._output_schema["properties"]:
                        if key in structured_output:
                            final_output[key] = structured_output[key]
                elif structured_output is not None:
                    output_keys = list(self._output_schema["properties"].keys())
                    if len(output_keys) == 1:
                        final_output[output_keys[0]] = structured_output
            else:
                final_output = structured_output if structured_output is not None else {}

            return ToolResult.success_result(
                data=final_output,
                execution_time=execution_time,
                token_usage=result.get("token_usage"),
            )
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="WORKFLOW_EXECUTION_ERROR",
                execution_time=execution_time,
            )

    def _workspace_id_from_context(self) -> Optional[uuid.UUID]:
        """Read workspace_id from the runtime context."""
        workspace_id = self.get_runtime_context("workspace_id")
        if isinstance(workspace_id, uuid.UUID):
            return workspace_id
        if isinstance(workspace_id, str) and workspace_id:
            return uuid.UUID(workspace_id)
        return None

    async def _load_runtime_config(self) -> tuple[uuid.UUID, Any]:
        """Resolve workspace_id and the release-snapshot config.

        Read in a dedicated async session so nothing depends on the caller
        session's type (Session/AsyncSession) or lifetime (the tool instance may
        execute after the caller's session has closed).
        """
        workspace_id = self._workspace_id_from_context()
        if workspace_id is not None and not self.release_id:
            return workspace_id, None

        from ...db import get_async_db_context

        async with get_async_db_context() as db:
            if workspace_id is None:
                app = await db.get(App, self.workflow_app_id)
                workspace_id = getattr(app, "workspace_id", None) if app else None
                if workspace_id is None:
                    raise ValueError("workflow tool execution requires workspace_id")

            workflow_config = None
            if self.release_id:
                from ...repositories.workflow_repository import WorkflowConfigRepository
                from ...services.workflow_service import WorkflowService

                # Monolith AppService.get_release_by_id_async equivalent: the
                # release must belong to this app and still be active
                release = await db.scalar(
                    select(AppRelease).where(
                        AppRelease.app_id == self.workflow_app_id,
                        AppRelease.id == self.release_id,
                        AppRelease.is_active.is_(True),
                    ).limit(1)
                )
                if not release:
                    raise BusinessException(
                        f"版本 {self.release_id} 不存在或已下线",
                        BizCode.RELEASE_NOT_FOUND,
                    )
                real_config = await WorkflowConfigRepository(db).get_by_app_id_async(
                    self.workflow_app_id
                )
                workflow_config = WorkflowService._build_runtime_workflow_config_from_release(
                    release,
                    real_config_id=real_config.id if real_config else None,
                )

            return workspace_id, workflow_config
