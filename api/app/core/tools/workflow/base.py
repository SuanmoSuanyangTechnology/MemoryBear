import json
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.tools.base import BaseTool
from app.models.app_model import App
from app.models.tool_model import ToolType
from app.schemas.app_schema import DraftRunRequest
from app.schemas.tool_schema import ParameterType, ToolParameter, ToolResult


class WorkflowAsTool(BaseTool):
    """将纯工作流包装为可执行工具。"""

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
        """优先提取结构化输出，兼容 workflow_service.run 的返回格式。"""
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
                from app.services.workflow_service import WorkflowService
                self.workflow_service = WorkflowService(self.db)

            workspace_id = self._resolve_workspace_id()
            user_id = self.get_runtime_context("user_id")
            workflow_config = None
            if self.release_id:
                from app.services.app_service import AppService
                app_service = AppService(self.db)
                release = app_service.get_release_by_id(self.workflow_app_id, self.release_id)
                workflow_config = app_service._workflow_config_from_release(release)

            payload = DraftRunRequest(
                user_id=str(user_id) if user_id else None,
                variables=kwargs,
                stream=False,
            )

            result = await self.workflow_service.run(
                app_id=self.workflow_app_id,
                payload=payload,
                config=workflow_config,
                workspace_id=workspace_id,
                release_id=self.release_id,
                source="tool",
            )

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

    def _resolve_workspace_id(self) -> uuid.UUID:
        """优先使用运行时上下文中的 workspace_id，缺失时回退到工作流所属应用。"""
        workspace_id = self.get_runtime_context("workspace_id")
        if isinstance(workspace_id, uuid.UUID):
            return workspace_id
        if isinstance(workspace_id, str) and workspace_id:
            return uuid.UUID(workspace_id)

        app = self.db.get(App, self.workflow_app_id)
        if app and getattr(app, "workspace_id", None):
            return app.workspace_id

        raise ValueError("workflow tool execution requires workspace_id")
