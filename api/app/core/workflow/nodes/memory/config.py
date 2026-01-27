from uuid import UUID

from pydantic import Field

from app.core.workflow.nodes.base_config import BaseNodeConfig


class MemoryReadNodeConfig(BaseNodeConfig):
    message: str = Field(
        ...
    )

    config_id: UUID | int = Field(
        ...
    )

    search_switch: str = Field(
        "0",
        description="Search mode: 0=verify, 1=direct, 2=context"
    )


class MemoryWriteNodeConfig(BaseNodeConfig):
    message: str = Field(
        ...
    )

    config_id: UUID = Field(
        ...
    )
