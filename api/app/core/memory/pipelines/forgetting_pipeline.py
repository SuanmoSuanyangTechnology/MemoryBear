import logging
import uuid

from app.core.memory.pipelines.base_pipeline import BasePipeline
from app.core.memory.storage.custom.manual_node_delete import (
    delete_manual_node_by_element_id as delete_manual_node,
)
from app.core.memory.storage_services.forgetting_engine.forget_service import ForgetService
from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
from app.core.quota_manager import get_end_user_memory_limit
from app.db import get_db_context
from app.repositories.end_user_repository import get_tenant_id_by_end_user_id
from app.repositories.neo4j.neo4j_connector import Neo4jConnector

logger = logging.getLogger(__name__)


class ForgettingPipeline(BasePipeline):
    async def run(self):
        with get_db_context() as db:
            tenant_id = get_tenant_id_by_end_user_id(db, uuid.UUID(self.ctx.end_user_id))
            if tenant_id is None:
                raise Exception(f"End user not found. - User ID:{self.ctx.end_user_id}")
            memory_limit = get_end_user_memory_limit(db, tenant_id)
            if memory_limit is None:
                raise Exception(f"Memory limit not found. - Tenant ID:{tenant_id}")
        if memory_limit <= 0:
            return {"skipped": True, "reason": "memory_limit <= 0"}
        res = await ForgetService(self.ctx, memory_limit).run()

        async with Neo4jConnector() as connector:
            await sync_end_user_memory_count_from_neo4j(self.ctx.end_user_id, connector)

        # 引擎动态展示投影：只有本轮实际软删除数 > 0 才落一条 FORGETTING 卡片事件。
        # 尽力写入，PG 失败不影响 Neo4j 软删除结果和定时任务返回值。
        try:
            from app.services.memory_engine_display_service import MemoryEngineDisplayService
            await MemoryEngineDisplayService.save_forgetting_event(
                end_user_id=self.ctx.end_user_id,
                forget_summary=res,
            )
        except Exception as e:
            logger.warning(
                f"[EngineDisplay] 遗忘引擎展示写入异常（不影响主流程）: {e}",
                exc_info=True,
            )

        return res

    @staticmethod
    async def delete_node_by_element_id(
        element_id: str,
        end_user_id: str,
        operator: uuid.UUID,
    ) -> bool:
        """Delegate manual deletion to the storage custom operation."""
        return await delete_manual_node(
            element_id=element_id,
            end_user_id=end_user_id,
            operator=operator,
        )

    @staticmethod
    async def delete_all_nodes_by_end_user_id(end_user_id: str) -> int:
        """删除指定用户的所有 Neo4j 记忆节点和边。

        Returns:
            删除的节点总数
        """

        async with Neo4jConnector() as connector:
            return await connector.delete_group(end_user_id)
