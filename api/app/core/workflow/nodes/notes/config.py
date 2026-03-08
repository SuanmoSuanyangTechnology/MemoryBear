from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig


class NoteNodeConfig(BaseNodeConfig):
    author: str = Field(..., description="author")
    text: str = Field(..., description="note context")
