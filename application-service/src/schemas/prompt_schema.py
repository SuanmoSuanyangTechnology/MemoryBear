from jinja2 import Environment, Template, meta
from typing import Any, Dict
from enum import Enum
from pydantic import BaseModel, Field
from abc import ABC
from typing import Union, List


class PromptMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

class TextPromptMessageContent(BaseModel):
    type: str = Field(default="text")
    data: str
PromptMessageContentUnionTypes = TextPromptMessageContent
class PromptMessage(ABC, BaseModel):
    role: PromptMessageRole
    content: Union[str, List[PromptMessageContentUnionTypes], None] = None
    name: Union[str, None] = None

    model_config = {"arbitrary_types_allowed": True}

    def is_empty(self) -> bool:
        return not self.content

    def get_text_content(self) -> str:
        if isinstance(self.content, str):
            return self.content
        elif isinstance(self.content, list):
            return "".join([item.data for item in self.content if isinstance(item, TextPromptMessageContent)])
        return ""


def render_prompt_message(template_str: str, role: PromptMessageRole, params: Dict[str, Any]) -> PromptMessage:
    """
    Generic helper: auto-parse template variables and render a PromptMessage.
    - template_str: Jinja2 template string
    - role: PromptMessageRole
    - params: dict supplying the template variables
    """
    env = Environment()
    parsed_content = env.parse(template_str)
    variables = meta.find_undeclared_variables(parsed_content)

    # Fill in missing parameters with the default ''
    for var in variables:
        if var not in params:
            params[var] = ""

    # Render the template
    jinja_template = Template(template_str)
    rendered_text = jinja_template.render(**params)

    return PromptMessage(
        role=role,
        content=[TextPromptMessageContent(data=rendered_text)]
    )


