import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..base import BaseTool
from ...models.tool_model import ToolType
from ...schemas.tool_schema import ParameterType, ToolParameter


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
