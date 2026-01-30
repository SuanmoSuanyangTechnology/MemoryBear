"""
Configuration utility functions

Shared utilities for configuration handling to avoid circular imports.
"""
from uuid import UUID
from sqlalchemy.orm import Session


def resolve_config_id(config_id: UUID | int|str, db: Session) -> UUID:
    """
    解析 config_id，如果是整数则通过 config_id_old 查找对应的 UUID
    
    Args:
        config_id: 配置ID（UUID 或整数）
        db: 数据库会话
        
    Returns:
        UUID: 解析后的配置ID
        
    Raises:
        ValueError: 当找不到对应的配置时
    """

    from app.models.memory_config_model import MemoryConfig
    if  isinstance(config_id, UUID):
        return config_id
    if isinstance(config_id, str) and len(config_id)<=6:
        memory_config = db.query(MemoryConfig).filter(
            MemoryConfig.config_id_old == int(config_id)
        ).first()
        print(memory_config)
        if not memory_config:
            raise ValueError(f"STR 未找到 config_id_old={config_id} 对应的配置")
        return memory_config.config_id
    if isinstance(config_id, int):
        memory_config = db.query(MemoryConfig).filter(
            MemoryConfig.config_id_old == config_id
        ).first()

        if not memory_config:
            raise ValueError(f"INT 未找到 config_id_old={config_id} 对应的配置")

        return memory_config.config_id

    return config_id
