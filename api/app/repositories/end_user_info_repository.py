"""
终端用户信息仓储层
"""
import uuid
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.end_user_info_model import EndUserInfo
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class EndUserInfoRepository:
    """终端用户信息仓储类"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def create(self, end_user_id: uuid.UUID, other_name: str, aliases: List[str] = None, meta_data: dict = None) -> EndUserInfo:
        """创建终端用户信息"""
        end_user_info = EndUserInfo(
            end_user_id=end_user_id,
            other_name=other_name,
            aliases=aliases or [],
            meta_data=meta_data
        )
        self.db.add(end_user_info)
        self.db.commit()
        self.db.refresh(end_user_info)
        logger.info(f"创建终端用户信息: end_user_id={end_user_id}, aliases={aliases}")
        return end_user_info
    
    def get_by_id(self, info_id: uuid.UUID) -> Optional[EndUserInfo]:
        """根据ID获取用户信息"""
        return self.db.query(EndUserInfo).filter(EndUserInfo.id == info_id).first()
    

    def get_by_end_user_id(self, end_user_id: uuid.UUID) -> Optional[EndUserInfo]:
        """获取用户的信息记录"""
        return self.db.query(EndUserInfo).filter(EndUserInfo.end_user_id == end_user_id).first()
    
    def update(self, info_id: uuid.UUID, aliases: List[str] = None, meta_data: dict = None) -> Optional[EndUserInfo]:
        """更新用户信息"""
        end_user_info = self.get_by_id(info_id)
        if end_user_info:
            if aliases is not None:
                end_user_info.aliases = aliases
            if meta_data is not None:
                end_user_info.meta_data = meta_data
            self.db.commit()
            self.db.refresh(end_user_info)
            logger.info(f"更新终端用户信息: info_id={info_id}")
        return end_user_info
    
    def delete(self, info_id: uuid.UUID) -> bool:
        """删除用户信息"""
        end_user_info = self.get_by_id(info_id)
        if end_user_info:
            self.db.delete(end_user_info)
            self.db.commit()
            logger.info(f"删除终端用户信息: info_id={info_id}")
            return True
        return False
    
    def delete_by_end_user_id(self, end_user_id: uuid.UUID) -> int:
        """删除用户的所有信息记录"""
        count = self.db.query(EndUserInfo).filter(EndUserInfo.end_user_id == end_user_id).delete()
        self.db.commit()
        logger.info(f"删除用户所有信息记录: end_user_id={end_user_id}, count={count}")
        return count

    def replace_metadata_fields(
        self,
        end_user_id: uuid.UUID,
        metadata: Dict[str, List[str]],
    ) -> Optional["EndUserInfo"]:
        """以 Neo4j 为权威源，覆盖 ``meta_data`` 中指定字段。

        语义：
            - 只覆盖 ``metadata`` 中显式提供的 key；其他 key 原样保留
              （避免误伤未管理字段或别的链路写入的内容）
            - ``aliases`` 与 ``other_name`` 不在本方法管辖范围内，原值保留
            - 入参 dict 的 value 必须是 list；非 list 类型会被忽略

        Args:
            end_user_id: 终端用户 ID
            metadata: 要覆盖的字段字典，例如
                {"core_facts": [...], "traits": [...], ...}

        Returns:
            更新后的 EndUserInfo；记录不存在时返回 None
        """
        if not metadata:
            return self.get_by_end_user_id(end_user_id)

        end_user_info = self.get_by_end_user_id(end_user_id)
        if not end_user_info:
            logger.warning(
                f"[EndUserInfo] 记录不存在，跳过 metadata 覆盖: end_user_id={end_user_id}"
            )
            return None

        existing_meta = dict(end_user_info.meta_data or {})
        changed = False
        for field, values in metadata.items():
            if not isinstance(values, list):
                logger.warning(
                    f"[EndUserInfo] meta_data.{field} 期望 list，实际为 "
                    f"{type(values).__name__}，已跳过该字段: end_user_id={end_user_id}"
                )
                continue
            existing_meta[field] = list(values)
            changed = True

        if not changed:
            return end_user_info

        end_user_info.meta_data = existing_meta
        self.db.commit()
        self.db.refresh(end_user_info)
        logger.info(
            f"[EndUserInfo] meta_data 字段覆盖完成: end_user_id={end_user_id}, "
            f"fields={list(metadata.keys())}"
        )
        return end_user_info

    def remove_aliases( # NOTE：刘淼 别名移除
        self,
        end_user_id: uuid.UUID,
        aliases_to_remove: List[str],
    ) -> Optional["EndUserInfo"]:
        """从用户别名列表中移除指定别名（忽略大小写）。

        Args:
            end_user_id: 终端用户 ID
            aliases_to_remove: 需要移除的别名列表

        Returns:
            更新后的 EndUserInfo，若记录不存在则返回 None
        """
        if not aliases_to_remove:
            return self.get_by_end_user_id(end_user_id)

        end_user_info = self.get_by_end_user_id(end_user_id)
        if not end_user_info:
            logger.warning(f"[EndUserInfo] 记录不存在，跳过别名移除: end_user_id={end_user_id}")
            return None

        remove_lower = {a.strip().lower() for a in aliases_to_remove if a.strip()}
        existing = list(end_user_info.aliases or [])
        new_aliases = [a for a in existing if a.lower() not in remove_lower]

        if len(new_aliases) == len(existing):
            return end_user_info

        end_user_info.aliases = new_aliases
        self.db.commit()
        self.db.refresh(end_user_info)
        logger.info(
            f"[EndUserInfo] 别名移除完成: end_user_id={end_user_id}, "
            f"removed={aliases_to_remove}, remaining={new_aliases}"
        )
        return end_user_info
