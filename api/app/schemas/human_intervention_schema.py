from typing import Any

from pydantic import BaseModel, Field


class HumanInterventionSubmitRequest(BaseModel):
    node_id: str = Field(..., description="人工介入节点 ID")
    action_id: str = Field(..., description="用户触发的操作 ID")
    form_data: dict[str, Any] | None = Field(default=None, description="用户填写的表单数据")
