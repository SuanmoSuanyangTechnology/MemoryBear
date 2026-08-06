"""
ReflectionPipeline — 反思引擎流水线（离线部分）

编排反思引擎中定时执行的部分：Layer 2（离线巡检）和 Layer 3（知识综合）。
两层频率不同，由不同的 Celery Beat 定时任务通过 MemoryService 分别调用。

设计原则：
- Pipeline 不直接操作数据库，通过 Inspector / Repository 完成
- Pipeline 不包含 LLM 调用逻辑，通过 Layer2Inspector 内部的 synthesizer 完成
- Pipeline 负责资源生命周期管理（客户端初始化 / 连接关闭）
- Pipeline 负责错误边界划分（哪些错误中断流程，哪些吞掉继续）

依赖方向：Task → MemoryService → ReflectionPipeline → Layer2Inspector → Engine → Repository
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.memory_config_schema import MemoryConfig

logger = logging.getLogger(__name__)


def _create_log_repo():
    """创建自动 commit + close 的日志仓库，避免 session 泄漏"""
    from app.repositories.reflection_log_repository import ReflectionLogRepository
    from app.db import get_db_context

    class _AutoCommitLogRepo:
        """包装 ReflectionLogRepository，create 后自动 commit 并关闭 session"""
        def create(self, **kwargs):
            with get_db_context() as db:
                repo = ReflectionLogRepository(db)
                result = repo.create(**kwargs)
                db.commit()
                return result

    return _AutoCommitLogRepo()


class ReflectionPipeline:
    """反思引擎流水线

    提供 run_layer2 / run_layer3 两个方法，由不同频率的定时任务分别调用。
    内部管理资源生命周期（LLM client、Neo4j connector、log_repo）。
    """

    def __init__(
        self,
        memory_config: "MemoryConfig",
        end_user_id: str,
        language: str = "zh",
    ):
        """
        Args:
            memory_config: 不可变的记忆配置对象（从数据库加载）
            end_user_id: 终端用户 ID
            language: 语言 ("zh" | "en")
        """
        self.memory_config = memory_config
        self.end_user_id = end_user_id
        self.language = language

        # 延迟初始化的客户端
        self._llm_client = None

    async def _lazy_init(self):
        """延迟初始化依赖，避免循环导入和不必要的连接创建"""
        if self._llm_client is None:
            from app.core.memory.pipelines.base_pipeline import ModelClientMixin
            from app.db import get_async_db_context

            llm_id = (
                getattr(self.memory_config, 'reflection_model_id', None)
                or getattr(self.memory_config, 'llm_model_id', None)
                or getattr(self.memory_config, 'llm_id', None)
            )

            if llm_id:
                async with get_async_db_context() as db:
                    self._llm_client = await ModelClientMixin.get_llm_client_async(
                        db,
                        llm_id,
                        getattr(self.memory_config, "tenant_id", None),
                    )

        # 构建 embedding_client（用于更名后重新生成 name_embedding）
        if not hasattr(self, '_embedding_client'):
            self._embedding_client = None
            embedding_id = getattr(self.memory_config, 'embedding_model_id', None)
            if embedding_id:
                try:
                    from app.core.memory.pipelines.base_pipeline import ModelClientMixin
                    from app.db import get_async_db_context
                    async with get_async_db_context() as db:
                        self._embedding_client = await ModelClientMixin.get_embedding_client_async(
                            db,
                            embedding_id,
                            tenant_id=getattr(self.memory_config, "tenant_id", None),
                        )
                except Exception as e:
                    logger.warning(f"构建 embedding_client 失败: {e}")

    async def run_layer2(self, baseline: str = "HYBRID") -> Dict[str, Any]:
        """Layer 2 离线巡检 — 由高频定时任务调用（如每 10 分钟）

        执行顺序：子问题 1→2→5→3→6→4（当前只实现子问题 3 和 6）
        """
        await self._lazy_init()

        if not self._llm_client:
            return {"status": "skipped", "reason": "no llm_id configured"}

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.reflection_engine.layer2_inspector import Layer2Inspector

        connector = Neo4jConnector()
        inspector = Layer2Inspector(
            neo4j_connector=connector,
            llm_client=self._llm_client,
            log_repo_factory=_create_log_repo,
            embedding_client=self._embedding_client,
        )

        try:
            result = await inspector.run(
                end_user_id=self.end_user_id,
                baseline=baseline,
                language=self.language,
            )
        finally:
            await connector.close()

        await self._save_reflection_display_event(result, "layer2_frequent")
        return result

    async def run_dedup_full_scan(self, baseline: str = "HYBRID") -> Dict[str, Any]:
        """方案B：低频全量扫描去重 — 由每天一次的定时任务调用"""
        await self._lazy_init()

        if not self._llm_client:
            return {"status": "skipped", "reason": "no llm_id configured"}

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.reflection_engine.layer2_inspector import Layer2Inspector

        connector = Neo4jConnector()
        inspector = Layer2Inspector(
            neo4j_connector=connector,
            llm_client=self._llm_client,
            log_repo_factory=_create_log_repo,
            embedding_client=self._embedding_client,
        )

        try:
            result = await inspector.run_dedup_full_scan(self.end_user_id, baseline=baseline)
        finally:
            await connector.close()

        await self._save_reflection_display_event(result, "dedup_full_scan")
        return result

    async def _save_reflection_display_event(
        self,
        result: Dict[str, Any],
        scan_type: str,
    ) -> None:
        """引擎动态展示投影：五类成果合计 > 0 时落一条 REFLECTION 卡片事件。

        只用内存里的汇总结果，不需要 Neo4j 连接，因此放在 connector 关闭之后，
        不延长连接持有时间。inspector 抛异常时调用方直接跳过写入。
        尽力写入，PG 失败不影响归并结果和定时任务返回值。
        """
        try:
            from app.services.memory_engine_display_service import MemoryEngineDisplayService
            await MemoryEngineDisplayService.save_reflection_event(
                end_user_id=self.end_user_id,
                layer2_result=result,
                scan_type=scan_type,
            )
        except Exception as e:
            logger.warning(
                f"[EngineDisplay] 反思引擎展示写入异常（不影响主流程）: {e}",
                exc_info=True,
            )

    async def run_layer3(self) -> Dict[str, Any]:
        """Layer 3 知识综合 — 由低频定时任务调用（如每天一次）

        TODO: Observation 合成、Opinion 演化、模式反馈
        """
        return {"status": "not_implemented"}