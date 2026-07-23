import uuid

from app.core.memory.models.service_models import ForgetLog
from app.core.memory.pipelines.base_pipeline import BasePipeline
from app.core.memory.storage_services.forgetting_engine.forget_service import ForgetService
from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
from app.core.quota_manager import get_end_user_memory_limit
from app.core.utils.datetime_utils import utcnow
from app.db import get_db_context
from app.models.memory_forget_model import ForgetTrigger
from app.repositories.end_user_repository import get_tenant_id_by_end_user_id
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.neo4j.cypher_queries import DELETE_NODE_BY_ELEMENT_ID
from app.repositories.neo4j.neo4j_connector import Neo4jConnector


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
        return res

    @staticmethod
    async def delete_node_by_element_id(
        element_id: str,
        end_user_id: str,
        operator: uuid.UUID,
    ) -> bool:
        """通过 elementId 删除 Neo4j 图节点并同步审计日志。

        Args:
            element_id: Neo4j 内部元素 ID。
            end_user_id: 终端用户 ID。
            operator: 执行删除操作的用户 ID。
        """
        async with Neo4jConnector() as connector:
            result = await connector.execute_query(
                DELETE_NODE_BY_ELEMENT_ID,
                element_id=element_id,
                end_user_id=end_user_id,
            )

        if not result or result[0]["deleted"] == 0:
            return False

        row = result[0]
        labels = row.get("labels", [])
        node_type = next(
            (label for label in labels if label != "Memory"), "unknown",
        )

        content = (
            row.get("content") or row.get("statement") or row.get("text")
            or row.get("name") or ""
        )

        log = ForgetLog(
            node_id=row["node_id"],
            end_user_id=uuid.UUID(end_user_id),
            node_type=node_type,
            content=content,
            trigger=ForgetTrigger.Manual.value,
            reason="manual",
            recoverable=False,
            operator=operator,
            delete_at=utcnow(),
            is_recovered=False,
        )

        with get_db_context() as db:
            ForgetLogRepository.sync_logs(db, [log])
            db.commit()

        return True

    @staticmethod
    async def delete_all_nodes_by_end_user_id(end_user_id: str) -> int:
        """删除指定用户的所有 Neo4j 记忆节点和边。

        Returns:
            删除的节点总数
        """

        async with Neo4jConnector() as connector:
            return await connector.delete_group(end_user_id)
