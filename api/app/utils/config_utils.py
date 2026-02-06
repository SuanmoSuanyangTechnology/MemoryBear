"""
Configuration utility functions

Shared utilities for configuration handling to avoid circular imports.
"""
from uuid import UUID
from sqlalchemy.orm import Session
import uuid as uuid_module


def resolve_config_id(config_id: UUID | int | str, db: Session) -> UUID:
    """
    解析 config_id，支持 UUID、UUID字符串、整数等多种格式

    Args:
        config_id: 配置ID（UUID、UUID字符串 或 整数）
        db: 数据库会话

    Returns:
        UUID: 解析后的配置ID

    Raises:
        ValueError: 当找不到对应的配置时或格式无效时
    """
    from app.models.memory_config_model import MemoryConfig
    
    # 1. 如果已经是 UUID 类型，直接返回
    if isinstance(config_id, UUID):
        return config_id
    
    # 2. 如果是字符串类型
    if isinstance(config_id, str):
        config_id_stripped = config_id.strip()
        
        # 2.1 尝试解析为 UUID（标准 UUID 字符串长度为 36）
        try:
            return uuid_module.UUID(config_id_stripped)
        except ValueError:
            pass
        
        # 2.2 尝试解析为整数（用于查询 config_id_old）
        try:
            old_id = int(config_id_stripped)
            if old_id > 0:
                memory_config = db.query(MemoryConfig).filter(
                    MemoryConfig.config_id_old == old_id
                ).first()
                if not memory_config:
                    raise ValueError(f"未找到 config_id_old={old_id} 对应的配置")
                return memory_config.config_id
        except ValueError:
            pass
        
        # 2.3 无法解析的字符串格式
        raise ValueError(f"无效的 config_id 格式: '{config_id}'（必须是 UUID 或正整数）")
    
    # 3. 如果是整数类型，通过 config_id_old 查找
    if isinstance(config_id, int):
        if config_id <= 0:
            raise ValueError(f"config_id 必须是正整数: {config_id}")
        
        memory_config = db.query(MemoryConfig).filter(
            MemoryConfig.config_id_old == config_id
        ).first()

        if not memory_config:
            raise ValueError(f"未找到 config_id_old={config_id} 对应的配置")

        return memory_config.config_id

    # 4. 不支持的类型
    raise ValueError(f"不支持的 config_id 类型: {type(config_id).__name__}")
