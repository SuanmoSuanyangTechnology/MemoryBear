"""Resolve a workspace's LLM configuration.

The rules and their error messages are carried over verbatim from the service
that used to own them: a default-config workspace follows the singleton preset,
the model must exist, be active and belong to the workspace's tenant (public
models are usable by every tenant).
"""

from __future__ import annotations

from src.domain.model_config.model import ModelConfigRow, ResolvedChatModel
from src.domain.shared.errors import ModelConfigurationError
from src.domain.workspace.model import Workspace


class ModelResolutionService:
    """Stateless domain service: workspace + config row -> callable model target."""

    def resolve(
        self, workspace: Workspace, row: ModelConfigRow | None
    ) -> ResolvedChatModel:
        workspace.ensure_model_resolution_allowed()
        config_id = workspace.memory_config
        if config_id is None:  # pragma: no cover - guarded by the aggregate
            raise ModelConfigurationError(f"空间{workspace.id}无启用的记忆配置")

        if row is None or not row.config_exists:
            raise ModelConfigurationError(f"记忆配置 {config_id} 不可用")
        model_id = row.model_id
        if model_id is None:
            raise ModelConfigurationError(f"记忆配置 {config_id} 未配置 LLM 模型")
        if row.model_row_id is None:
            raise ModelConfigurationError(f"LLM 模型 {model_id} 不存在")
        if not row.is_active:
            raise ModelConfigurationError(f"LLM 模型 {model_id} 已停用")
        if workspace.tenant_id is None:
            raise ModelConfigurationError(f"workspace {workspace.id} 缺少租户")
        if not row.is_public and row.tenant_id != workspace.tenant_id:
            raise ModelConfigurationError(f"LLM 模型 {model_id} 不属于当前租户")

        return ResolvedChatModel(config_id=model_id, tenant_id=workspace.tenant_id)
