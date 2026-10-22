"""End-user request/response contracts (frozen port of the monolith schemas:
``end_user_schema`` plus the ``CreateEndUser`` pair from ``memory_api_schema``)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..api.schemas.response_schema import PageMeta


class EndUserIdentityUpdate(BaseModel):
    """Identity-features update request.

    ``end_user_id`` stays a plain string: a malformed value must surface as the
    legacy HTTP 200 + code 9601 body from the handler, not as a 422 raised by
    Pydantic.
    """

    end_user_id: str = Field(description="要修改的终端用户 ID（UUID 字符串）")
    identity_features: str | None = Field(
        None,
        description="身份标识。传值=确认为长时身份(confirmed)并尝试归并；"
        "传 null/空串/纯空白=清空标识并降级为临时身份(temporary)。",
    )


class EndUserMappingItem(BaseModel):
    """Terminal-user id mapping row."""

    end_user_id: str = Field(description="终端用户ID（UUID）")
    other_id: str = Field(description="外部用户标识")
    other_name: str = Field(description="用户名称")


class EndUserMappingResponse(BaseModel):
    """Paginated mapping response."""

    items: list[EndUserMappingItem] = Field(description="用户映射列表", default_factory=list)
    page: PageMeta = Field(description="分页信息")


class CreateEndUserRequest(BaseModel):
    """Request schema for creating an end user."""

    other_id: str = Field(..., description="External user identifier (required)")
    other_name: str | None = Field("", description="Display name")
    memory_config_id: str | None = Field(
        None,
        description="Memory config ID. Falls back to workspace default if not provided.",
    )
    app_id: str | None = Field(None, description="App ID to bind the end user to")

    @field_validator("other_id")
    @classmethod
    def validate_other_id(cls, v: str) -> str:
        """Reject empty identifiers exactly like the host schema."""

        if not v or not v.strip():
            raise ValueError("other_id is required and cannot be empty")
        return v.strip()


class CreateEndUserResponse(BaseModel):
    """Response schema for end user creation."""

    id: str = Field(..., description="End user UUID")
    other_id: str = Field(..., description="External user identifier")
    other_name: str = Field("", description="Display name")
    workspace_id: str = Field(..., description="Workspace ID")
    memory_config_id: str | None = Field(None, description="Connected memory config ID")


__all__ = [
    "CreateEndUserRequest",
    "CreateEndUserResponse",
    "EndUserIdentityUpdate",
    "EndUserMappingItem",
    "EndUserMappingResponse",
]
