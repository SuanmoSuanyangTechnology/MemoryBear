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

        合并操作涵盖（顺序即执行顺序，其中第 2 步是「归并意图先行落库」）：
        1. 校验：收集 source EndUser 行与双方 EndUserInfo，target 无 info 记录则中止
        2. PG EndUser + end_user_merge：source 软删（is_active = False）并写入合并
           映射，**同一次提交**。此后老 end_user_id 即经映射路由到 target，迁移
           期间的新写入直接落到 target，不会产生孤儿数据
        3. PG EndUserInfo：aliases / meta_data 合并到 target
        4. Neo4j：所有节点的 end_user_id 改为 target；User 实体节点合并全部属性
        5. Neo4j：关系属性 end_user_id 改为 target（保留原始关系类型）
        6. PG 引用表：conversations / memory_messages / memory_short_term /
           memory_long_term / memory_forget_log / memory_perceptual /
           memory_reflection_log / forgetting_cycle_history /
           memory_display_record / memory_engine_display_event
           的 end_user_id 从 source 迁移到 target
        7. 同步 target 的 memory_count
        8. 触发 target 的 Layer2 反思（实体去重 + 描述合并，from_retry=True 跳过频率检查）

        失败语义：第 2 步之后若中断（Neo4j 不可用、迁移报错等），source 已软删且
        映射已在，老 ID 仍能正确路由到 target，只是 source 名下尚未迁移的数据暂时
        不可见。Neo4j reassign 与引用表迁移均幂等，重跑本方法或补偿任务可补齐。
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
            raise BusinessException(message="Target user not found.")

        # ── 1.4 工作空间一致性校验：跨空间归并会串号，须在任何迁移前拦截 ──
        target_user = await self._get_end_user_any_async(target)
        if not target_user:
            raise BusinessException(message="Target user not found.")
        mismatched = {
            source_id
            for source_id, source_user in source_users.items()
            if source_user.workspace_id != target_user.workspace_id
        }
        if mismatched:
            raise BusinessException(
                message=f"Cross-workspace merge is not allowed: {mismatched} not in workspace {target_user.workspace_id}."
            )

        # ── 1.5 归并意图先行落库：软删 source + 写 end_user_merge 映射，同一次提交 ──
        # 位置刻意放在「所有前置校验之后、任何数据迁移之前」：
        # ① 必须晚于校验：上面的 target_user_info 校验若失败，source 不能已被软删；
        # ② 必须早于迁移：迁移期间落到 source 的新写入会经 get_end_user_by_id_async
        #    路由到 target，不会变成挂在死账号上的孤儿数据（引用表迁移与 Neo4j
        #    reassign 都是一次性的，迁移窗口之后写到 source 的数据永远迁不过去）；
        # ③ 必须原子：软删与映射缺一都会留下坏状态，详见
        #    EndUserRepository.soft_delete_many_pending_async 的 docstring。
        # 副作用：source 立刻从活跃集合消失，配额、Dashboard 列表、反思/遗忘/聚类
        # 扫描（均按 is_active == True 过滤）同步生效，这是归并后的正确语义提前生效。
        first_src_user = next((u for u in source_users.values() if u), None)
        if not first_src_user:
            raise BusinessException(message="No valid source user found.")
        workspace_id = first_src_user.workspace_id

        await self.user_repo.soft_delete_many_pending_async(source)
        await self.user_repo.flatten_merge_chain_async(source, target, workspace_id)
        for src_id in source:
            src_user = source_users.get(src_id)
            # EndUserMerge.origin_other_id 是 NOT NULL，而 EndUser.other_id 允许为空
            # （agent chat 等入口可不传 user_id）。这里必须兜底为 id 字符串，
            # 否则归并会因非空约束直接失败。
            origin_other_id = (src_user.other_id if src_user else None) or str(src_id)
            self.user_repo.create_merge_record(
                origin_id=src_id,
                origin_other_id=origin_other_id,
                target_id=target,
                workspace_id=workspace_id,
            )
        await self.db.commit()
        logger.info(
            f"[merge_end_users] 归并意图已落库（软删 + 映射原子生效）: "
            f"source={source} → target={target}"
        )

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

        # ── 4. 提交引用表迁移（软删与映射已在 1.5 提交） ──
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

    async def confirm_identity(
        self,
        workspace_id: uuid.UUID,
        current_end_user: EndUser,
        identity_features: str | None = None,
    ) -> tuple[uuid.UUID, str, bool]:
        """根据是否携带身份标识，确定临时/长时身份并执行跨渠道归并。

        - 带标识 → confirmed（长时）；不带 → temporary（临时）。
        - 存在相同 identity_features 的活跃用户时，把当前用户合并到该用户。
        返回 (最终 end_user_id, identity_status, 是否发生合并)。

        并发安全：带标识时先按 (workspace_id, identity_features) 取事务级排他锁，
        使「查找 → 判定 → 落标识」整体串行化。否则并发相同标识会各自判定无匹配，
        双双置 confirmed，产生两条同标识活跃记录，令归并永久失效。
        """
        has_id = bool(identity_features and identity_features.strip())
        clean_features = identity_features.strip() if has_id else None
        identity_status = "confirmed" if has_id else "temporary"

        if has_id:
            await self.user_repo.acquire_identity_lock_async(
                workspace_id, clean_features
            )
            existing = await self.user_repo.find_active_by_identity_features(
                workspace_id, clean_features
            )
            if existing and existing.id != current_end_user.id:
                # 先落标识再归并：source 行被软删后仍带 identity_features，
                # 便于事后排查「这条记录当时按哪个标识被并走」。
                current_end_user.identity_features = clean_features
                current_end_user.identity_status = identity_status
                await self.db.flush()
                await self.merge_end_users(
                    source={current_end_user.id}, target=existing.id
                )
                logger.info(
                    f"[confirm_identity] 跨渠道归并: source={current_end_user.id} "
                    f"→ target={existing.id}, identity_features={clean_features}"
                )
                return existing.id, "confirmed", True

        current_end_user.identity_features = clean_features
        current_end_user.identity_status = identity_status
        await self.db.commit()
        await self.db.refresh(current_end_user)
        logger.info(
            f"[confirm_identity] 身份确认: end_user={current_end_user.id}, "
            f"status={identity_status}, identity_features={clean_features}"
        )
        return current_end_user.id, identity_status, False

    # ── helpers ────────────────────────────────────────────────

    async def _get_end_user_any_async(
        self, end_user_id: uuid.UUID
    ) -> EndUser | None:
        """获取 EndUser（不过滤 is_active），用于软删除前收集信息。"""
        result = await self.db.execute(
            select(EndUser).where(EndUser.id == end_user_id)
        )
        return result.scalars().first()
