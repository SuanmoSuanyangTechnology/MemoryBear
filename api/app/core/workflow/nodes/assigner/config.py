from pydantic import Field, BaseModel

from app.core.workflow.nodes.base_config import BaseNodeConfig
from app.core.workflow.nodes.enums import AssignmentOperator


class AssignmentItem(BaseModel):
    """
    Single assignment definition.
    """

    variable_selector: str | list[str] = Field(
        ...,
        description="Target variable name(s) to assign",
    )

    operation: AssignmentOperator = Field(
        ...,
        description="Assignment operator",
    )

    value: str | list[str] = Field(
        ...,
        description="Value(s) to assign to the variable(s)",
    )


class AssignerNodeConfig(BaseNodeConfig):
    assignments: list[AssignmentItem] = Field(
        ...,
        description="List of variable assignment definitions",
    )
