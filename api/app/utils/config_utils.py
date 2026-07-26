"""
Configuration utility functions

Shared utilities for configuration handling to avoid circular imports.
"""
from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
import uuid as uuid_module


def resolve_config_id(config_id: UUID | int | str, db: Optional[Session] = None) -> UUID:
    """
    解析 config_id，支持 UUID、UUID字符串、整数等多种格式。

    当 config_id 已经是 UUID 或 UUID 字符串时，无需 db 即可解析。
    当 config_id 是整数（需要查 config_id_old）时，若未传 db，则自动开一个短 session。

    Args:
        config_id: 配置ID（UUID、UUID字符串 或 整数）
        db: 可选的数据库会话。不传时内部自行开短 session（推荐用于流式端点）

    Returns:
        UUID: 解析后的配置ID

    Raises:
        ValueError: 当找不到对应的配置时或格式无效时
    """
    from app.models.memory_config_model import MemoryConfig

    # 1. 如果已经是 UUID 类型，直接返回（无需 db）
    if isinstance(config_id, UUID):
        return config_id

    # 2. 如果是字符串，先尝试解析为 UUID（无需 db）
    if isinstance(config_id, str):
        config_id_stripped = config_id.strip()

        # 2.1 先尝试解析为整数（用于查询 config_id_old）
        try:
            old_id = int(config_id_stripped)
            if old_id > 0:
                return _lookup_by_old_id(old_id, db)
        except ValueError:
            pass

        # 2.2 尝试解析为 UUID（无需 db）
        try:
            return uuid_module.UUID(config_id_stripped)
        except ValueError:
            pass

        raise ValueError(f"无效的 config_id 格式: '{config_id}'（必须是 UUID 或正整数）")

    # 3. 如果是整数类型，通过 config_id_old 查找
    if isinstance(config_id, int):
        if config_id <= 0:
            raise ValueError(f"config_id 必须是正整数: {config_id}")
        return _lookup_by_old_id(config_id, db)

    # 4. 不支持的类型
    raise ValueError(f"不支持的 config_id 类型: {type(config_id).__name__}")


def _lookup_by_old_id(old_id: int, db: Optional[Session]) -> UUID:
    """通过 config_id_old 查找 UUID，支持传入已有 session 或自动开短 session。"""
    from app.models.memory_config_model import MemoryConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    def _query(session) -> Optional[MemoryConfig]:
        return session.query(MemoryConfig).filter(
            MemoryConfig.config_id_old == old_id
        ).first()

    # AsyncSession 不支持 .query()，当传入 AsyncSession 时回退到开一个只读短 sync session
    # 注意：必须在 session 内访问 .config_id，否则 detached object 报错
    if db is None or isinstance(db, AsyncSession):
        from app.db import get_db_read
        with get_db_read() as short_db:
            memory_config = _query(short_db)
            if not memory_config:
                raise ValueError(f"未找到 config_id_old={old_id} 对应的配置")
            return memory_config.config_id
    else:
        memory_config = _query(db)
        if not memory_config:
            raise ValueError(f"未找到 config_id_old={old_id} 对应的配置")
        return memory_config.config_id
