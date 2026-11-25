"""
终端用户信息仓储层
"""
import uuid
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.models.end_user_model import EndUser
from app.models.workspace_model import Workspace
from app.plugins import get_plugin

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.end_user_info_model import EndUserInfo
from app.core.logging_config import get_logger

logger = get_logger(__name__)


def _profile_statement(end_user_ids: list[str]):
    return (
        select(EndUserInfo.end_user_id, EndUserInfo.meta_data, Workspace.tenant_id)
        .join(EndUser, EndUser.id == EndUserInfo.end_user_id)
        .join(Workspace, Workspace.id == EndUser.workspace_id)
        .where(EndUserInfo.end_user_id.in_([UUID(str(value)) for value in end_user_ids]))
    )


def _scene_ids(rows) -> list[UUID]:
    ids = set()
    for row in rows:
        metadata = row.meta_data if isinstance(row.meta_data, dict) else {}
        domains = metadata.get("domain_specific_info")
        if not isinstance(domains, dict):
            continue
        for key in domains:
            try:
                ids.add(UUID(key))
            except (ValueError, TypeError, AttributeError):
                continue
    return list(ids)


_SCENE_QUERY = text("""
    SELECT scene_type_id, scene_name, tenant_id
    FROM ontology_scene_type
    WHERE scene_type_id IN :scene_ids
      AND tenant_id IN :tenant_ids
""").bindparams(
    bindparam("scene_ids", expanding=True, type_=PGUUID(as_uuid=True)),
    bindparam("tenant_ids", expanding=True, type_=PGUUID(as_uuid=True)),
)


def _profile_data(rows, scenes) -> list[tuple[str, Any, dict[str, str]]]:
    names = {}
    for scene in scenes:
        name = scene.scene_name
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            UUID(name)
        except ValueError:
            names.setdefault(str(scene.tenant_id), {})[str(scene.scene_type_id)] = name
    return [
        (str(row.end_user_id), row.meta_data, names.get(str(row.tenant_id), {}))
        for row in rows
    ]


class EndUserInfoRepository:
    """终端用户信息仓储类"""
    
    def __init__(self, db: Session | AsyncSession):
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

    async def get_end_user_info_async(self, end_user_id: uuid.UUID) -> Optional[EndUserInfo]:
        """获取用户的信息记录（异步版本）"""
        from sqlalchemy import select
        result = await self.db.execute(
            select(EndUserInfo).where(EndUserInfo.end_user_id == end_user_id).limit(1)
        )
        return result.scalars().first()

    def get_meta_data_sync(self, end_user_id: uuid.UUID) -> dict:
        """读取用户的 meta_data（同步版本）；记录不存在或字段为空时返回 {}。"""
        from sqlalchemy import select
        result = self.db.execute(
            select(EndUserInfo.meta_data).where(EndUserInfo.end_user_id == end_user_id).limit(1)
        )
        meta_data = result.scalars().first()
        return meta_data if isinstance(meta_data, dict) else {}
    
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
    ) -> Optional["EndUserInfo"]:
        """增量更新用户别名列表和元数据。

        - aliases：将 ``new_aliases`` 合并到现有列表（去重，忽略大小写），不覆盖
        - meta_data：将 ``new_metadata`` 的各字段列表合并到现有 ``meta_data``
          （去重），不覆盖
        - other_name：若当前为空且 aliases 非空，则取 ``aliases[0]`` 作为
          ``other_name``

        Args:
            end_user_id: 终端用户 ID
            new_aliases: 本次新增的别名列表
            new_metadata: 本次提取的 extracted_metadata 字典

        Returns:
            更新后的 EndUserInfo；若记录不存在则返回 None
        """
        end_user_info = self.get_by_end_user_id(end_user_id)
        if not end_user_info:
            logger.warning(
                f"[EndUserInfo] 记录不存在，跳过更新: end_user_id={end_user_id}"
            )
            return None

        changed = False

        # 合并 aliases（去重，忽略大小写）
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

        # 同步 other_name：取 aliases[0]（仅当当前为空）
        if end_user_info.aliases and not (end_user_info.other_name or "").strip():
            end_user_info.other_name = end_user_info.aliases[0]
            changed = True

        # 合并 meta_data（各字段列表去重追加）
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

    def profiles_for_users_sync(
        self, end_user_ids: list[str]
    ) -> list[tuple[str, Any, dict[str, str]]]:
        rows = self.db.execute(_profile_statement(end_user_ids)).all()
        scene_ids = _scene_ids(rows)
        scenes = []
        if scene_ids and get_plugin("memory.ontology_query") is not None:
            scenes = self.db.execute(_SCENE_QUERY, {
                "scene_ids": scene_ids,
                "tenant_ids": list({row.tenant_id for row in rows}),
            }).all()
        return _profile_data(rows, scenes)

    async def profiles_for_users(
        self, end_user_ids: list[str]
    ) -> list[tuple[str, Any, dict[str, str]]]:
        rows = (await self.db.execute(_profile_statement(end_user_ids))).all()
        scene_ids = _scene_ids(rows)
        scenes = []
        if scene_ids and get_plugin("memory.ontology_query") is not None:
            scenes = (await self.db.execute(_SCENE_QUERY, {
                "scene_ids": scene_ids,
                "tenant_ids": list({row.tenant_id for row in rows}),
            })).all()
        return _profile_data(rows, scenes)
