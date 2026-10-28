"""Resolve the workspace LLM and build a blocking chat model for it.

Mirrors ``memory-profile/src/modules/shared/model_config_service.py``: workspace
→ active ``memory_config`` → the slot's model id (default-config workspaces follow
the singleton preset) → ``model_configs`` tenant/active check. Only the ``llm``
slot is needed by the generation pipelines.

Resolution and model construction are separate because interest distribution
runs under a per-call time budget: it resolves the configuration once and then
builds one model per call with that call's timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.infrastructure.model_service import ModelServiceManager, SyncChatModel
from src.modules.shared.repositories.model_config import ModelConfigRepository

logger = logging.getLogger(__name__)


class ModelConfigurationError(RuntimeError):
    """Raised when no usable model configuration exists for the workspace."""


@dataclass(frozen=True, slots=True)
class ResolvedChatConfig:
    """A validated ``model_configs`` row plus the tenant it is called for."""

    model_config_id: UUID
    tenant_id: UUID


def _parse_uuid(value: object, label: str) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return UUID(value.strip())
        except ValueError:
            logger.warning("invalid %s model id: %r", label, value)
    return None


def resolve_chat_config(
    repository: ModelConfigRepository,
    workspace: dict[str, Any],
) -> ResolvedChatConfig:
    """Resolve the ``llm`` slot for an already-loaded workspace row."""

    label = "LLM"
    workspace_id = workspace["id"]
    config_id = workspace["memory_config"]
    if config_id is None:
        raise ModelConfigurationError(f"空间{workspace_id}无启用的记忆配置")
    row = repository.resolve_model(
        config_id=config_id,
        slot="llm",
        is_default_config=bool(workspace["is_default_config"]),
        workspace_model_id=_parse_uuid(workspace["llm"], "llm"),
    )
    if row is None or not row["config_exists"]:
        raise ModelConfigurationError(f"记忆配置 {config_id} 不可用")
    model_id = row["model_id"]
    if model_id is None:
        raise ModelConfigurationError(f"记忆配置 {config_id} 未配置 {label} 模型")
    if row["model_row_id"] is None:
        raise ModelConfigurationError(f"{label} 模型 {model_id} 不存在")
    if not row["is_active"]:
        raise ModelConfigurationError(f"{label} 模型 {model_id} 已停用")

    tenant_id = workspace["tenant_id"]
    if tenant_id is None:
        raise ModelConfigurationError(f"workspace {workspace_id} 缺少租户")
    if not row["is_public"] and row["tenant_id"] != tenant_id:
        raise ModelConfigurationError(f"{label} 模型 {model_id} 不属于当前租户")

    return ResolvedChatConfig(model_config_id=model_id, tenant_id=tenant_id)


def build_chat_model(
    model_service: ModelServiceManager,
    resolved: ResolvedChatConfig,
    *,
    timeout_seconds: float | None = None,
) -> SyncChatModel:
    """Build a model for a resolved configuration."""

    return model_service.chat_model(
        resolved.model_config_id,
        resolved.tenant_id,
        timeout_seconds=timeout_seconds,
    )


def resolve_chat_model(
    repository: ModelConfigRepository,
    model_service: ModelServiceManager,
    workspace: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
) -> SyncChatModel:
    """Resolve and build in one step (the insight/summary path)."""

    return build_chat_model(
        model_service,
        resolve_chat_config(repository, workspace),
        timeout_seconds=timeout_seconds,
    )
