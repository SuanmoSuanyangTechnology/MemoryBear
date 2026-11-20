"""Use existing chat-model factory and validate structured model responses."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from src.application.ports.chat_model import ChatModel
from src.application.ports.llm import ChatModelFactory
from src.domain.shared.ids import ModelConfigId, TenantId


def model_for_context(factory: ChatModelFactory, context: dict[str, Any]) -> ChatModel:
    if not context.get("model_id"):
        raise ValueError("工作空间缺少可用的租户 LLM 配置")
    return factory.chat_model(
        ModelConfigId(UUID(context["model_id"])),
        TenantId(UUID(context["tenant_id"])),
        timeout_seconds=180,
    )


def structured_data(value: Any, schema: type[BaseModel]) -> dict[str, Any]:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return schema.model_validate(raw).model_dump(mode="json")
