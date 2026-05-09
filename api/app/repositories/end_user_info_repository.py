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

    def update_aliases_and_metadata(
        self,
        end_user_id: uuid.UUID,
        new_aliases: Optional[List[str]] = None,
        new_metadata: Optional[dict] = None,
    ) -> Optional[EndUserInfo]:
        """增量更新用户别名列表和元数据。

        - aliases：将 new_aliases 合并到现有列表（去重，忽略大小写），不覆盖
        - meta_data：将 new_metadata 的各字段列表合并到现有 meta_data（去重），不覆盖
        - other_name：若当前为空且 aliases 非空，则取 aliases[0] 作为 other_name

        Args:
            end_user_id: 终端用户 ID
            new_aliases: 本次新增的别名列表
            new_metadata: 本次提取的 extracted_metadata 字典

        Returns:
            更新后的 EndUserInfo，若记录不存在则返回 None
        """
        end_user_info = self.get_by_end_user_id(end_user_id)
        if not end_user_info:
            logger.warning(f"[EndUserInfo] 记录不存在，跳过更新: end_user_id={end_user_id}")
            return None

        changed = False

        # ── 合并 aliases（去重，忽略大小写）──
        if new_aliases:
            existing = list(end_user_info.aliases or [])
            existing_lower = {a.lower() for a in existing}
            for alias in new_aliases:
                alias = alias.strip()
                if alias and alias.lower() not in existing_lower:
                    existing.append(alias)
                    existing_lower.add(alias.lower())
            end_user_info.aliases = existing
            changed = True

        # ── 同步 other_name：取 aliases[0]（若当前为空）──
        if end_user_info.aliases and not (end_user_info.other_name or "").strip():
            end_user_info.other_name = end_user_info.aliases[0]
            changed = True

        # ── 合并 meta_data（各字段列表去重追加）──
        if new_metadata:
            existing_meta = dict(end_user_info.meta_data or {})
            for field, values in new_metadata.items():
                if not isinstance(values, list):
                    continue
                existing_list = list(existing_meta.get(field) or [])
                existing_set = {str(v).lower() for v in existing_list}
                for v in values:
                    if str(v).lower() not in existing_set:
                        existing_list.append(v)
                        existing_set.add(str(v).lower())
                existing_meta[field] = existing_list
            end_user_info.meta_data = existing_meta
            changed = True

        if changed:
            self.db.commit()
            self.db.refresh(end_user_info)
            logger.info(
                f"[EndUserInfo] 更新完成: end_user_id={end_user_id}, "
                f"aliases_count={len(end_user_info.aliases or [])}"
            )
        return end_user_info

    def remove_aliases(
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
