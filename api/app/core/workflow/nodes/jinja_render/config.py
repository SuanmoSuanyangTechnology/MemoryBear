from pydantic import Field, BaseModel
from app.core.workflow.nodes.base_config import BaseNodeConfig


class VariablesMappingConfig(BaseModel):
    name: str = Field(
        ...,
        description="The variable name to be rendered"
    )
    value: str = Field(
        ...,
        description="The corresponding value from the workflow"
    )


class JinjaRenderNodeConfig(BaseNodeConfig):
    template: str = Field(
        ...,
        description="The Jinja2 template string to be rendered"
    )
    mapping: list[VariablesMappingConfig] = Field(
        ...,
        description="Mapping configuration for variables used in the template"
    )
