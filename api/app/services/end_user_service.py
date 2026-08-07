import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BusinessException
from app.core.logging_config import get_memory_logger
from app.models import EndUserInfo
from app.models.end_user_model import EndUser
from app.repositories.end_user_info_repository import EndUserInfoRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.neo4j.end_user_merge_repository import EndUserMergeNeo4jRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = get_memory_logger()


class EndUserService:
    def __init__(self, db: AsyncSession):
        self.user_repo = EndUserRepository(db)
        self.info_repo = EndUserInfoRepository(db)
        self.db = db

    async def merge_end_users(self, source: set[uuid.UUID], target: uuid.UUID):
        """将 source 中的用户合并到 target 用户。

        合并操作涵盖：
        1. PG EndUserInfo：aliases / meta_data 合并到 target
        2. Neo4j：所有节点的 end_user_id 改为 target；User 实体节点合并全部属性
        3. Neo4j：关系属性 end_user_id 改为 target（保留原始关系类型）
        4. PG 引用表：conversations / memory_messages / memory_short_term /
           memory_long_term / memory_forget_log / memory_perceptual /
           memory_reflection_log / forgetting_cycle_history /
           memory_display_record / memory_engine_display_event
           的 end_user_id 从 source 迁移到 target
        5. PG EndUser：source 用户 is_active = False（软删除）
        6. PG end_user_merge：记录每条合并映射（并摊平历史合并链）
        7. 同步 target 的 memory_count
        8. 触发 target 的 Layer2 反思（实体去重 + 描述合并，from_retry=True 跳过频率检查）
        9. 后续通过 EndUserMerge 表 + is_active 过滤，API 请求自动路由到 target
        """
        # ── 0. 提前收集 source EndUser 行（软删除前需要 other_id） ──
        source_users: dict[uuid.UUID, EndUser] = {}
        for end_user_id in source:
            eu = await self._get_end_user_any_async(end_user_id)
            if eu:
                source_users[end_user_id] = eu

        # ── 1. 收集 source 和 target 的 EndUserInfo ──
        end_user_infos: list[EndUserInfo] = []
        for end_user_id in source:
            end_user_info = await self.info_repo.get_end_user_info_async(end_user_id)
            if end_user_info:
                end_user_infos.append(end_user_info)

        target_user_info = await self.info_repo.get_end_user_info_async(target)
        if not target_user_info:
            raise BusinessException(message=f"Target user not found.")

        # ── 2. 合并 aliases 与 meta_data 到 target EndUserInfo ──
        final_aliases: list[str] = list(target_user_info.aliases or [])
        seen_alias_lower = {a.lower() for a in final_aliases}

        merged_meta: dict = dict(target_user_info.meta_data or {})

        for info in end_user_infos:
            for alias in (info.aliases or []):
                alias = alias.strip()
                if alias and alias.lower() not in seen_alias_lower:
                    final_aliases.append(alias)
                    seen_alias_lower.add(alias.lower())

            if info.meta_data:
                for key, values in info.meta_data.items():
                    if not isinstance(values, list):
                        continue
                    existing = list(merged_meta.get(key) or [])
                    existing_set = {str(v).lower() for v in existing}
                    for v in values:
                        if str(v).lower() not in existing_set:
                            existing.append(v)
                            existing_set.add(str(v).lower())
                    merged_meta[key] = existing

        target_user_info.aliases = final_aliases
        target_user_info.meta_data = merged_meta
        await self.db.commit()
        await self.db.refresh(target_user_info)
        logger.info(
            f"[merge_end_users] PG EndUserInfo 合并完成: "
            f"target={target}, aliases_count={len(final_aliases)}, "
            f"meta_keys={list(merged_meta.keys())}"
        )

        # ── 3. Neo4j 操作（委托给 EndUserMergeNeo4jRepository） ──
        connector = Neo4jConnector(shared_driver=True)
        try:
            neo4j_repo = EndUserMergeNeo4jRepository(connector)
            source_strs = [str(sid) for sid in source]
            stats = await neo4j_repo.reassign_all_to_target(
                source_strs, str(target)
            )
            logger.info(
                f"[merge_end_users] Neo4j 合并完成: "
                f"nodes={stats['nodes']}, edges={stats['edges']}"
            )

            # 同步 target 用户的 memory_count（合并后节点数已变化）
            from app.core.memory.utils.memory_count_utils import (
                sync_end_user_memory_count_from_neo4j,
            )
            new_count = await sync_end_user_memory_count_from_neo4j(
                str(target), connector
            )
            logger.info(
                f"[merge_end_users] target memory_count 同步完成: "
                f"target={target}, count={new_count}"
            )
        finally:
            await connector.close()

        # ── 3.5 PG 引用表迁移：将 source 的 end_user_id 改为 target ──
        pg_stats: dict[str, int] = {}
        for src_id in source:
            stats = await self.user_repo.migrate_reference_end_user_id_async(
                src_id, target
            )
            for table, count in stats.items():
                pg_stats[table] = pg_stats.get(table, 0) + count
        if pg_stats:
            logger.info(
                f"[merge_end_users] PG 引用表迁移完成: {pg_stats}"
            )

        # ── 4. 软删除 source 用户 (PG is_active = False) ──
        for src_id in source:
            await self.user_repo.soft_delete_by_end_user_id_async(src_id)
        logger.info(
            f"[merge_end_users] 软删除完成: source_ids={source}"
        )

        # ── 5. 收集 workspace_id + 摊平历史合并链 + 写入新 EndUserMerge 记录 ──
        # 所有 source 用户必须在同一 workspace（取第一个 source 的 workspace_id）
        first_src_user = next((u for u in source_users.values() if u), None)
        if not first_src_user:
            raise BusinessException(message="No valid source user found.")
        workspace_id = first_src_user.workspace_id

        await self.user_repo.flatten_merge_chain_async(source, target, workspace_id)
        if source:
            logger.info(
                f"[merge_end_users] 历史合并链已摊平: "
                f"source={source} → target={target}"
            )

        for src_id in source:
            src_user = source_users.get(src_id)
            origin_other_id = src_user.other_id if src_user else str(src_id)
            self.user_repo.create_merge_record(
                origin_id=src_id,
                origin_other_id=origin_other_id,
                target_id=target,
                workspace_id=workspace_id,
            )

        await self.db.commit()
        logger.info(
            f"[merge_end_users] 全部完成: source={source}, target={target}"
        )

        try:
            from app.services.memory_reflection_service import WorkspaceAppService
            await WorkspaceAppService(self.db).update_end_user_write_time_async(str(target))
        except Exception as e:
            logger.warning(
                f"[merge_end_users] 刷新 target write_time 失败（不影响合并结果）: {e}",
                exc_info=True,
            )

        # ── 6. 触发 target 反思（合并后数据已变化，立刻做一轮去重 + 摘要） ──
        try:
            from app.services.memory_config_service import MemoryConfigService
            config_svc = MemoryConfigService(self.db)
            reflection_config_id = await config_svc.get_config_id_by_end_user_async(target)
            if reflection_config_id:
                from app.tasks import do_layer2_reflection
                do_layer2_reflection.apply_async(
                    kwargs={
                        "end_user_id": str(target),
                        "config_id": str(reflection_config_id),
                        "workspace_id": str(workspace_id),
                        "from_retry": True,  # 跳过频率/活跃检查，合并后必定反思
                    },
                    queue="reflection_tasks",
                )
                logger.info(
                    f"[merge_end_users] 已派发 target 反思任务: target={target}, "
                    f"config_id={reflection_config_id}"
                )
            else:
                logger.warning(
                    f"[merge_end_users] target 无可用的 memory_config，"
                    f"跳过反思触发: target={target}"
                )
        except Exception as e:
            logger.warning(
                f"[merge_end_users] 派发反思任务失败（不影响合并结果）: {e}",
                exc_info=True,
            )

    # ── helpers ────────────────────────────────────────────────

    async def _get_end_user_any_async(
        self, end_user_id: uuid.UUID
    ) -> EndUser | None:
        """获取 EndUser（不过滤 is_active），用于软删除前收集信息。"""
        result = await self.db.execute(
            select(EndUser).where(EndUser.id == end_user_id)
        )
        return result.scalars().first()
