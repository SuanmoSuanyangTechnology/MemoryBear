from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import AssignmentOperator


class AssignerNodeConfig(BaseNodeConfig):
    variable_selector: str | list[str] = Field(
        ...,
        description="Variables to be assigned",
    )

    operation: AssignmentOperator = Field(
        ...,
        description="Operator to assign",
    )

    value: str | list[str] = Field(
        ...,
        description="Values to assign",
    )
