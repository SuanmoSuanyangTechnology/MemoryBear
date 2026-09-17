"""model_channels 渠道登记表数据访问。

幂等合并键 (provider, tenant_id, api_base, credential_sha256) 由 DB 唯一约束兜底；
写原语均为单语句原子操作（union/unbind/delete_if_empty），避免 read-modify-write 竞态。
事务边界在调用方（服务层/迁移脚本），本仓库只 flush。
"""
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import utcnow_naive
from app.models.models_model import ModelChannel

# update_attributes 中 api_base 的哨兵：显式传 None 表示"改为 NULL"，传 _UNSET 表示"不改"
_UNSET = object()


class ModelChannelRepository:
    def __init__(self, db: Session):
        self.db = db

    # ---- 读 ----
    def get(self, channel_id: uuid.UUID) -> ModelChannel | None:
        return self.db.execute(
            select(ModelChannel).where(ModelChannel.id == channel_id)
        ).scalars().first()

    def get_by_credential(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_base: str | None,
        credential_sha256: str,
    ) -> ModelChannel | None:
        stmt = select(ModelChannel).where(
            ModelChannel.provider == provider,
            ModelChannel.tenant_id == tenant_id,
            ModelChannel.credential_sha256 == credential_sha256,
        )
        if api_base is None:
            stmt = stmt.where(ModelChannel.api_base.is_(None))
        else:
            stmt = stmt.where(ModelChannel.api_base == api_base)
        return self.db.execute(stmt).scalars().first()

    def list_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str | None = None,
        is_active: bool | None = None,
        source: str | None = None,
        model_name: str | None = None,
    ) -> list[ModelChannel]:
        stmt = select(ModelChannel).where(ModelChannel.tenant_id == tenant_id)
        if provider is not None:
            stmt = stmt.where(ModelChannel.provider == provider)
        if is_active is not None:
            stmt = stmt.where(ModelChannel.is_active.is_(is_active))
        if source is not None:
            stmt = stmt.where(ModelChannel.source == source)
        rows = list(self.db.execute(stmt).scalars().all())
        if model_name is None:
            return rows
        # covers 语义：[] = provider 级覆盖全部未点名模型；点名 = 仅含列出的模型名
        return [ch for ch in rows if not ch.model_names or model_name in ch.model_names]

    def exists_covering(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_name: str | None = None,
        source: str | None = None,
    ) -> bool:
        """是否存在覆盖目标（模型或 provider 级）的渠道，**不按 is_active 过滤**。

        启用前置检查用：区分"有渠道但全部停用"与"从未登记渠道"。
        """
        conditions = [
            ModelChannel.tenant_id == tenant_id,
            ModelChannel.provider == provider,
        ]
        if source is not None:
            conditions.append(ModelChannel.source == source)
        if model_name is not None:
            conditions.append(
                or_(
                    ModelChannel.model_names == text("'[]'::jsonb"),
                    ModelChannel.model_names.has_key(model_name),
                )
            )
        stmt = select(ModelChannel.id).where(*conditions).limit(1)
        return self.db.execute(stmt).first() is not None

    # ---- 写 ----
    def create(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_names: list[str],
        api_base: str | None,
        credential_encrypted: str,
        credential_sha256: str,
        credential_masked: str,
        source: str = "manual",
        remark: str | None = None,
        created_by: uuid.UUID | None = None,
        priority: int = 0,
        extra: dict | None = None,
    ) -> ModelChannel:
        row = ModelChannel(
            tenant_id=tenant_id,
            provider=provider,
            model_names=list(model_names),
            api_base=api_base,
            credential_encrypted=credential_encrypted,
            credential_sha256=credential_sha256,
            credential_masked=credential_masked,
            priority=priority,
            source=source,
            extra=extra or {},
            remark=remark,
            created_by=created_by,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def union_model_name(self, channel_id: uuid.UUID, model_name: str) -> bool:
        """点名渠道并集 ∪ {model_name}：jsonb ? 标量成员判断做守卫，无重复并入。"""
        result = self.db.execute(
            update(ModelChannel)
            .where(
                ModelChannel.id == channel_id,
                ~ModelChannel.model_names.has_key(model_name),
            )
            .values(
                model_names=ModelChannel.model_names.concat(
                    func.jsonb_build_array(model_name)
                ),
                updated_at=utcnow_naive(),
            )
        )
        return result.rowcount > 0

    def promote_to_provider_level(self, channel_id: uuid.UUID) -> bool:
        """点名渠道 → provider 级：model_names 置空（单语句整体替换，无读改写）。

        '[]' 守卫：已是 provider 级的行 no-op（防并发/重复升级）。
        """
        result = self.db.execute(
            update(ModelChannel)
            .where(
                ModelChannel.id == channel_id,
                ModelChannel.model_names != text("'[]'::jsonb"),
            )
            .values(model_names=[], updated_at=utcnow_naive())
        )
        return result.rowcount > 0

    def unbind_model_name(self, channel_id: uuid.UUID, model_name: str) -> bool:
        """移除点名渠道中的模型名（单语句重建去元素，无读改写）。

        jsonb ? 成员存在守卫保证：名字不在覆盖集时是 no-op，不会整列重写。
        """
        result = self.db.execute(
            text(
                """
                UPDATE model_channels
                SET model_names = COALESCE(
                        (SELECT jsonb_agg(elem)
                           FROM jsonb_array_elements_text(model_channels.model_names) AS e(elem)
                          WHERE elem <> :model_name),
                        '[]'::jsonb),
                    updated_at = :ts
                WHERE id = :channel_id
                  AND model_names ? :model_name
                """
            ),
            {"model_name": model_name, "ts": utcnow_naive(), "channel_id": channel_id},
        )
        if result.rowcount > 0:
            # text SQL 绕过 ORM 不同步 identity map，需过期已加载实例防 stale 读
            self.db.expire_all()
        return result.rowcount > 0

    def delete_if_empty(self, channel_id: uuid.UUID) -> bool:
        """点名渠道清空后删行；'[]' 守卫防并发并集（并集先提交则本语句 no-op）。"""
        result = self.db.execute(
            delete(ModelChannel).where(
                ModelChannel.id == channel_id,
                ModelChannel.model_names == text("'[]'::jsonb"),
            )
        )
        return result.rowcount > 0

    def delete(self, channel_id: uuid.UUID) -> None:
        row = self.db.get(ModelChannel, channel_id)
        if row is not None:
            self.db.delete(row)

    def update_attributes(
        self,
        channel_id: uuid.UUID,
        *,
        priority: int | None = _UNSET,
        remark: str | None = _UNSET,
        api_base: str | None = _UNSET,   # _UNSET=不改；None=清空为 NULL；str=更新端点
        is_active: bool | None = _UNSET,
        extra: dict | None = _UNSET,     # _UNSET=不改；dict=整体替换（企业语义元数据）
    ) -> ModelChannel:
        values: dict = {}
        if priority is not _UNSET:
            values["priority"] = priority
        if remark is not _UNSET:
            values["remark"] = remark
        if api_base is not _UNSET:
            values["api_base"] = api_base
        if is_active is not _UNSET:
            values["is_active"] = is_active
        if extra is not _UNSET:
            values["extra"] = extra if extra is not None else {}
        if values:
            values["updated_at"] = utcnow_naive()
            self.db.execute(
                update(ModelChannel).where(ModelChannel.id == channel_id).values(**values)
            )
        row = self.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        return row

    def update_credential(
        self,
        channel_id: uuid.UUID,
        *,
        credential_encrypted: str,
        credential_sha256: str,
        credential_masked: str,
    ) -> ModelChannel:
        self.db.execute(
            update(ModelChannel)
            .where(ModelChannel.id == channel_id)
            .values(
                credential_encrypted=credential_encrypted,
                credential_sha256=credential_sha256,
                credential_masked=credential_masked,
                updated_at=utcnow_naive(),
            )
        )
        row = self.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        return row


__all__ = ["ModelChannelRepository"]
