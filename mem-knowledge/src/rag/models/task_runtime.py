"""Synchronous non-decrypting model view resolution for Knowledge worker tasks.

km 侧只剩「用哪个配置、代表哪个租户」：resolve 产出**非解密视图**
（``ModelConfigSnapshot``），凭据解密、渠道选路与 failover 全在模型服务侧
（设计 §2.2）。状态语义与旧 resolver 访问校验对齐：不存在/不可见 → NotFound、
下线 → Deprecated、停用 → Inactive（映射词表见 ``error_mapping``）。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from redbear_model import (
    ModelConfigDeprecatedError,
    ModelConfigInactiveError,
    ModelConfigNotFoundError,
    ModelConfigSnapshot,
)

from ...repositories.model_registry import SyncSQLModelRegistry

if TYPE_CHECKING:
    from ...runtime import ProcessRuntime


class TaskModelFactory:
    """Resolve model views in short sync sessions before shell construction."""

    def __init__(self, runtime: ProcessRuntime):
        self._runtime = runtime

    def resolve_view(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot:
        if model_config_id is None:
            raise ValueError("Model config ID is required")
        with self._runtime.database.sync_session() as session:
            config = SyncSQLModelRegistry(session).get_model_config(
                model_config_id, tenant_id
            )
        if config is None:
            raise ModelConfigNotFoundError(model_config_id)
        if config.is_deprecated:
            raise ModelConfigDeprecatedError(model_config_id)
        if not config.is_active:
            raise ModelConfigInactiveError(model_config_id)
        return config

    # 词表别名（存量站点调用名）：三族解析已是同一「行 → 视图」操作
    resolve_embedding = resolve_view
    resolve_chat = resolve_view
    resolve_image = resolve_view


__all__ = ["TaskModelFactory"]
