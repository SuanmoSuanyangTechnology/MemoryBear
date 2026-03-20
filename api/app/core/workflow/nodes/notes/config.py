from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig


class NoteNodeConfig(BaseNodeConfig):
    author: str = Field(default="", description="author")
    text: str = Field(default="", description="note content")
    width: int = Field(default=80)
    height: int = Field(default=80)
    theme: str = Field(default="blue")
    show_author: bool = Field(default=True)
