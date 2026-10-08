"""Read-only model config view reads for Knowledge worker/API code.

km 侧唯一的模型配置读面：ORM 行 → 非解密视图 ``ModelConfigSnapshot``（凭据与渠道
全在模型服务侧，设计与解析纪律见 invoke 接缝设计 §2.2）。租户可见性在本地过滤
（本租户 ∪ is_public 放行），与模型服务侧 invoke 时的可见性检查互为 backstop。
"""

from __future__ import annotations

import uuid

from redbear_model import (
    LoadBalanceStrategy,
    ModelConfigSnapshot,
    ModelProfile,
    ModelProvider,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, joinedload

from ..models.references import ModelConfig


def _config_snapshot(config: ModelConfig) -> ModelConfigSnapshot:
    return ModelConfigSnapshot(
        model_config_id=config.id,
        tenant_id=config.tenant_id,
        provider=ModelProvider(config.provider),
        name=config.name,
        is_active=config.is_active,
        is_public=config.is_public,
        is_deprecated=bool(config.model_base and config.model_base.is_deprecated),
        load_balance_strategy=LoadBalanceStrategy(
            config.load_balance_strategy or LoadBalanceStrategy.NONE
        ),
        profile=ModelProfile.from_stored_fields(
            model_id=config.id,
            tenant_id=config.tenant_id,
            type=config.type,
            provider=config.provider,
            input_modalities=config.input_modalities or (),
            output_modalities=config.output_modalities or (),
            features=config.features or (),
            capabilities=config.capability or (),
            is_omni=bool(config.is_omni),
        ),
        config=dict(config.config or {}),
    )


def _visible(config: ModelConfig, tenant_id: uuid.UUID) -> bool:
    """本租户 ∪ is_public 放行（原 resolver 访问校验在本地的等价物）。"""

    return bool(config.tenant_id == tenant_id or config.is_public)


class SyncSQLModelRegistry:
    """同步视图读：celery worker 任务内短会话使用。"""

    def __init__(self, db: Session):
        self.db = db

    def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        config = self.db.execute(
            select(ModelConfig)
            .options(joinedload(ModelConfig.model_base))
            .where(ModelConfig.id == model_config_id)
        ).scalar_one_or_none()
        if config is None or not _visible(config, tenant_id):
            return None
        return _config_snapshot(config)


class AsyncSQLModelRegistry:
    """异步视图读：API/检索准备面使用。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        config = (
            await self.db.execute(
                select(ModelConfig)
                .options(joinedload(ModelConfig.model_base))
                .where(ModelConfig.id == model_config_id)
            )
        ).scalar_one_or_none()
        if config is None or not _visible(config, tenant_id):
            return None
        return _config_snapshot(config)


__all__ = ["AsyncSQLModelRegistry", "SyncSQLModelRegistry"]
