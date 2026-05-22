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
    from app.db import SessionLocal

    class _AutoCommitLogRepo:
        """包装 ReflectionLogRepository，create 后自动 commit 并关闭 session"""
        def __init__(self):
            self._db = SessionLocal()
            self._repo = ReflectionLogRepository(self._db)

        def create(self, **kwargs):
            try:
                result = self._repo.create(**kwargs)
                self._db.commit()
                return result
            except Exception:
                self._db.rollback()
                raise
            finally:
                self._db.close()

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

    def _lazy_init(self):
        """延迟初始化依赖，避免循环导入和不必要的连接创建"""
        if self._llm_client is None:
            from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
            from app.db import get_db_context

            llm_id = (
                getattr(self.memory_config, 'reflection_model_id', None)
                or getattr(self.memory_config, 'llm_model_id', None)
                or getattr(self.memory_config, 'llm_id', None)
            )

            with get_db_context() as db:
                if llm_id:
                    factory = MemoryClientFactory(db)
                    self._llm_client = factory.get_llm_client(llm_id)

    async def run_layer2(self, baseline: str = "HYBRID") -> Dict[str, Any]:
        """Layer 2 离线巡检 — 由高频定时任务调用（如每 10 分钟）

        执行顺序：子问题 1→2→5→3→6→4（当前只实现子问题 3 和 6）
        """
        self._lazy_init()

        if not self._llm_client:
            return {"status": "skipped", "reason": "no llm_id configured"}

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.reflection_engine.layer2_inspector import Layer2Inspector

        connector = Neo4jConnector()
        inspector = Layer2Inspector(
            neo4j_connector=connector,
            llm_client=self._llm_client,
            log_repo_factory=_create_log_repo,
        )

        try:
            return await inspector.run(
                end_user_id=self.end_user_id,
                baseline=baseline,
                language=self.language,
            )
        finally:
            await connector.close()

    async def run_dedup_full_scan(self) -> Dict[str, Any]:
        """方案B：低频全量扫描去重 — 由每天一次的定时任务调用"""
        self._lazy_init()

        if not self._llm_client:
            return {"status": "skipped", "reason": "no llm_id configured"}

        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.reflection_engine.layer2_inspector import Layer2Inspector

        connector = Neo4jConnector()
        inspector = Layer2Inspector(
            neo4j_connector=connector,
            llm_client=self._llm_client,
            log_repo_factory=_create_log_repo,
        )

        try:
            return await inspector._run_dedup_full_scan(self.end_user_id)
        finally:
            await connector.close()

    async def run_layer3(self) -> Dict[str, Any]:
        """Layer 3 知识综合 — 由低频定时任务调用（如每天一次）

        TODO: Observation 合成、Opinion 演化、模式反馈
        """
        return {"status": "not_implemented"}