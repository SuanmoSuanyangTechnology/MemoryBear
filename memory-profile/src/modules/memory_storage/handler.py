"""HTTP boundary for memory-storage operations."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import redis
from typing import TYPE_CHECKING

from src.common.errors import BizCode
from src.common.responses import ApiResponse, failure, success
from src.middleware.auth import MemoryPrincipal
from src.infrastructure.redis_lock import RedisFairLock

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime
from src.services.memory_storage import MemoryStorageService, TimelineSort
from src.services.graph import PREFERENCE_GROUP_NODE_ID_PREFIX


api_logger = logging.getLogger(__name__)


class MemoryStorageHandler:
    def __init__(self, runtime: ProfileRuntime):
        self.service = MemoryStorageService(runtime)

    async def timeline_memories(self, context: MemoryPrincipal, id: str, label: str, language="zh") -> ApiResponse:
        workspace_id = context.workspace_id
        workspace_models = await self.service.get_workspace_models_configs_async(workspace_id)

        if workspace_models:
            model_id = workspace_models.get("llm", None)
        else:
            model_id = None
        timeline_memories_result = await self.service.get_timeline_memories_server(
            id, label, model_id, language, workspace_id=workspace_id
        )

        return success(data=timeline_memories_result, msg="共同记忆时间线")

    async def entity_timeline(
        self, context: MemoryPrincipal, id=None, type="all", sort="time_desc",
        from_ts=None, to_ts=None, page=1, pagesize=10, end_user_id=None,
    ) -> ApiResponse:
        if (id is None) == (end_user_id is None):
            return failure(BizCode.INVALID_PARAMETER, "id 和 end_user_id 必须且只能提供一个")
        if id is not None and not id.strip():
            return failure(BizCode.INVALID_PARAMETER, "id 不能为空")
        if end_user_id is not None:
            try:
                end_user_id = str(UUID(end_user_id))
            except ValueError:
                return failure(BizCode.INVALID_USER_ID, "无效的终端用户ID格式", "invalid uuid")

        allowed = {"all", "key_node", "statement", "memory_summary"}
        if type not in allowed:
            return failure(BizCode.INVALID_PARAMETER, "type 取值非法", f"type={type}")

        if sort not in TimelineSort.values():
            return failure(BizCode.INVALID_PARAMETER, "sort 取值非法", f"sort={sort}")

        # 校验日期区间：from_ts 必须 <= to_ts，否则返回明确错误而非空结果
        if from_ts is not None and to_ts is not None and from_ts > to_ts:
            return failure(BizCode.INVALID_PARAMETER, "from_ts 不能大于 to_ts", f"from_ts={from_ts}, to_ts={to_ts}")

        workspace_id = context.workspace_id
        if workspace_id is None:
            return failure(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
        memory_entity = self.service
        owner_id = end_user_id
        if id is not None:
            owner_id = await memory_entity.get_end_user_id_by_element_id(id)
            if owner_id is None:
                return failure(BizCode.PERMISSION_DENIED, "无法验证该实体的用户归属", "entity owner not found")
        try:
            owner_id = UUID(str(owner_id))
        except ValueError:
            return failure(BizCode.PERMISSION_DENIED, "无法验证该实体的用户归属", "invalid entity owner")
        end_user = await self.service.get_end_user_by_id_async(owner_id)
        if end_user is None:
            return failure(BizCode.USER_NOT_FOUND, "终端用户不存在", "end_user not found")
        if str(end_user.workspace_id) != str(workspace_id):
            return failure(BizCode.PERMISSION_DENIED, "该终端用户不属于当前工作空间", "end_user workspace mismatch")
        if end_user_id is not None:
            end_user_id = str(end_user.id)

        result = await memory_entity.get_unified_timeline(
            id=id,
            end_user_id=end_user_id,
            source_type=type,
            page=page,
            pagesize=pagesize,
            sort=sort,
            from_ts=from_ts,
            to_ts=to_ts,
        )
        return success(data=result, msg="记忆时间线")

    async def relationship_evolution(self, context: MemoryPrincipal, id: str, label: str) -> ApiResponse:
        await self.service.require_memory_owner(id, context.workspace_id)
        try:
            api_logger.info(f"关系演变查询请求: id={id}, table={label}, user={context.actor_id}")

            # 获取情绪数据
            emotion_result = await self.service.get_emotion(id, label)

            # 获取交互数据
            interaction_result = await self.service.get_interaction_frequency(id, label)

            # Shared Neo4j connections are managed by runtime, not individual requests.

            result = {
                "emotion": emotion_result,
                "interaction": interaction_result
            }

            api_logger.info(f"关系演变查询成功: id={id}, table={label}")
            return success(data=result, msg="关系演变")

        except Exception as e:
            api_logger.error(f"关系演变查询失败: id={id}, table={label}, error={str(e)}", exc_info=True)
            return failure(BizCode.INTERNAL_ERROR, "关系演变查询失败", str(e))

    async def pilot_run(self, context, payload, language):
        # v0.3.13: 入参改为 QA 格式 messages（与 /write 接口对齐），不再接收 dialogue_text / custom_text
        total_files = sum(len(m.files) for m in payload.messages if m.files)
        logging.getLogger(__name__).info(
            f"Pilot run requested: config_id={payload.config_id}, "
            f"messages_count={len(payload.messages)}, files_count={total_files}"
        )
        # Resolve before starting SSE; the repository closes its short session here.
        # The controller does not hold a database session across the streaming response.
        payload.config_id = await self.service.resolve_trial_config_id(
            payload.config_id, context.workspace_id
        )
        return self.service.pilot_run_stream(
            payload,
            workspace_id=context.workspace_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            actor_kind=context.actor_kind,
            language=language,
        )

    async def chat(self, context, payload, language):
        payload.config_id = await self.service.resolve_trial_config_id(
            payload.config_id, context.workspace_id
        )
        logging.getLogger(__name__).info(
            "Trial-run chat requested: config_id=%s, history_count=%s, files_count=%s, workspace_id=%s",
            payload.config_id,
            len(payload.history),
            len(payload.files),
            context.workspace_id,
        )
        return self.service.trial_run_chat_stream(
            payload,
            workspace_id=context.workspace_id,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            actor_kind=context.actor_kind,
            language=language,
        )

    async def delete_end_user(self, context, end_user_id):
        """删除终端用户的全部记忆数据（Neo4j 节点 + PostgreSQL 记录）。

        1. DETACH DELETE 清除 Neo4j 中该用户所有节点和边
        2. memory_count 归零
        3. 软删除 end_user 记录（is_active=False）

        这是一个危险操作，不可撤销。
        """
        workspace_id = context.workspace_id
        if workspace_id is None:
            api_logger.warning(f"用户 {context.actor_id} 尝试删除终端用户但未选择工作空间")
            return failure(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

        end_user_id_str = str(end_user_id)

        api_logger.info(
            f"删除终端用户请求: end_user_id={end_user_id_str}, "
            f"user={context.actor_id}, workspace={workspace_id}"
        )

        redis_client = None
        try:
            while True:
                resolved_snapshot: tuple[UUID, str] | None = None
                end_user = await self.service.get_end_user_by_id_async(end_user_id)
                if end_user is not None:
                    resolved_snapshot = (
                        UUID(str(end_user.id)),
                        str(end_user.workspace_id),
                    )
                if resolved_snapshot is None:
                    api_logger.warning(
                        f"终端用户不存在或已删除: end_user_id={end_user_id_str}"
                    )
                    return failure(
                        BizCode.NOT_FOUND,
                        "终端用户不存在或已删除",
                        f"end_user_id={end_user_id_str}",
                    )

                resolved_end_user_id, resolved_workspace_id = resolved_snapshot
                if resolved_workspace_id != str(workspace_id):
                    api_logger.warning(
                        f"用户 {context.actor_id} 尝试删除不属于工作空间 "
                        f"{workspace_id} 的终端用户 {end_user_id_str}"
                    )
                    return failure(
                        BizCode.PERMISSION_DENIED,
                        "该终端用户不属于当前工作空间",
                        "end_user workspace mismatch",
                    )

                if resolved_end_user_id != end_user_id:
                    api_logger.warning(
                        "拒绝通过已合并源用户删除目标用户记忆: requested_id=%s, "
                        "resolved_id=%s",
                        end_user_id_str,
                        resolved_end_user_id,
                    )
                    return failure(
                        BizCode.NOT_FOUND,
                        "终端用户不存在或已删除",
                        f"end_user_id={end_user_id_str}",
                    )

                effective_end_user_id = resolved_end_user_id
                effective_end_user_id_str = str(effective_end_user_id)
                if redis_client is None:
                    redis_client = redis.Redis.from_url(
                        self.service.runtime.settings.resolved_redis_url, decode_responses=True
                    )
                write_lock = RedisFairLock(
                    key=f"memory_write:{effective_end_user_id_str}",
                    redis_client=redis_client,
                    expire=1200,
                    timeout=60,
                    auto_renewal=True,
                )
                if not await asyncio.to_thread(write_lock.acquire):
                    raise RuntimeError("memory write lock acquisition timed out")
                try:
                    # The requested active user may be merged between the first
                    # lookup and lock acquisition. Re-resolve under its old lock;
                    # retry preflight so the merged source is rejected before any
                    # target lock or destructive operation.
                    confirmed_snapshot: tuple[UUID, str] | None = None
                    confirmed = await self.service.get_end_user_by_id_async(end_user_id)
                    if confirmed is not None:
                        confirmed_snapshot = (
                            UUID(str(confirmed.id)),
                            str(confirmed.workspace_id),
                        )
                    if confirmed_snapshot is None:
                        return failure(
                            BizCode.NOT_FOUND,
                            "终端用户不存在或已删除",
                            f"end_user_id={end_user_id_str}",
                        )

                    confirmed_id, confirmed_workspace_id = confirmed_snapshot
                    if confirmed_workspace_id != str(workspace_id):
                        return failure(
                            BizCode.PERMISSION_DENIED,
                            "该终端用户不属于当前工作空间",
                            "end_user workspace mismatch",
                        )

                    if confirmed_id != effective_end_user_id:
                        api_logger.info(
                            "删除终端用户时 merge owner 已变化，释放旧锁并重跑预检: "
                            "%s -> %s",
                            effective_end_user_id,
                            confirmed_id,
                        )
                        continue

                    total_deleted = await self.service.delete_all_nodes_by_end_user_id(
                        effective_end_user_id_str
                    )

                    finalized = await self.service.finalize_memory_delete_async(
                        effective_end_user_id, workspace_id
                    )
                    if not finalized:
                        raise RuntimeError(
                            "effective end user disappeared during memory deletion"
                        )

                    api_logger.info(
                        "终端用户删除完成: requested_id=%s, effective_id=%s, "
                        "total_deleted=%s",
                        end_user_id_str,
                        effective_end_user_id_str,
                        total_deleted,
                    )
                    return success(
                        data={
                            "deleted": True,
                            "end_user_id": end_user_id_str,
                            "effective_end_user_id": effective_end_user_id_str,
                            "total_deleted": total_deleted,
                        },
                        msg=f"删除用户{end_user_id_str}记忆库成功",
                    )
                finally:
                    await asyncio.to_thread(write_lock.release)

        except Exception as e:
            api_logger.error(f"删除终端用户失败: end_user_id={end_user_id_str}, error={str(e)}")
            return failure(BizCode.INTERNAL_ERROR, "删除终端用户失败", str(e))

        finally:
            if redis_client is not None:
                try:
                    await asyncio.to_thread(redis_client.close)
                except Exception:
                    api_logger.warning("Failed to close memory deletion Redis client", exc_info=True)

    async def delete_node(self, context, payload) -> ApiResponse:
        """通过 elementId 删除 Neo4j 图节点（含关联边）。

        会自动校验节点归属的 end_user_id，仅删除属于指定用户的节点。
        """
        workspace_id = context.workspace_id
        if workspace_id is None:
            api_logger.warning(f"用户 {context.actor_id} 尝试删除节点但未选择工作空间")
            return failure(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

        element_id = payload.element_id
        end_user_id = payload.end_user_id

        if element_id.startswith(PREFERENCE_GROUP_NODE_ID_PREFIX):
            return failure(BizCode.INVALID_PARAMETER, "偏好汇总节点不可删除", "请先删除具体的偏好记忆节点")

        api_logger.info(
            f"节点删除请求: element_id={element_id}, end_user_id={end_user_id}, "
            f"user={context.actor_id}, workspace={workspace_id}"
        )

        await self.service.require_end_user_in_workspace(end_user_id, workspace_id)
        try:
            deleted = await self.service.delete_node_by_element_id(
                element_id=element_id,
                end_user_id=end_user_id,
                operator=context.actor_id,
            )

            if deleted:
                api_logger.info(f"节点删除成功: element_id={element_id}, end_user_id={end_user_id}")

                # 同步 memory_count 到 PostgreSQL
                try:
                    await self.service.sync_end_user_memory_count_from_neo4j(
                        end_user_id, context.tenant_id
                    )
                except Exception as sync_err:
                    api_logger.warning(f"同步 memory_count 失败（不影响删除结果）: {sync_err}")

                return success(data={"deleted": True, "element_id": element_id}, msg="节点删除成功")
            else:
                api_logger.warning(f"节点未找到或不属于该用户: element_id={element_id}, end_user_id={end_user_id}")
                return failure(BizCode.NOT_FOUND, "节点未找到或不属于该用户", f"element_id={element_id}")

        except Exception as e:
            api_logger.error(f"节点删除失败: element_id={element_id}, error={str(e)}")
            return failure(BizCode.INTERNAL_ERROR, "节点删除失败", str(e))
