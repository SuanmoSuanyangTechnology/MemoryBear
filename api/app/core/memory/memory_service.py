"""
MemoryService — 记忆模块统一入口（Facade）

所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

职责：
- 接收已加载的 MemoryConfig，选择并调用对应的 Pipeline
- 暴露 write / read / pilot_write / forget / reflection 等实例方法
- create_long_term_memory_tool 创建长期记忆检索工具
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.message_models import DialogData
from app.core.memory.models.service_models import LongTermMemoryInput, MemoryContext, MemorySearchResult
from app.core.memory.pipelines.memory_read import ReadPipeLine
from app.core.memory.pipelines.pilot_write_pipeline import PilotWriteResult
from app.core.memory.pipelines.write_pipeline import WriteResult
from app.db import get_db_context, get_db_read
from app.services.memory_config_service import MemoryConfigService

logger = logging.getLogger(__name__)



class MemoryService:
    """记忆模块统一入口

    所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

    设计决策：
    - __init__ 接收已加载的 MemoryConfig（而非 config_id），
      配置加载的职责留在调用方（MemoryAgentService），
      因为调用方需要 config 做其他事情（如感知记忆处理）。
    - 未实现的方法抛出 NotImplementedError，明确标记待实现状态。
    """

    def __init__(
            self,
            config_id: str | None,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            conversation_id: str | None = None,
            language: str = "zh",
            draft=False
    ):
        with get_db_read() as db:
            config_service = MemoryConfigService(db)
            memory_config = None
            if config_id is not None and config_id != "":
                memory_config = config_service.load_memory_config(
                    config_id=config_id,
                    workspace_id=workspace_id,
                    service_name="MemoryService",
                )
        if memory_config is None and storage_type.lower() == "neo4j":
            logger.warning(
                "MemoryService 初始化时未提供 memory config（config_id=None），"
                "write/read/pilot_write 方法将不可用"
            )
        self.ctx = MemoryContext(
            end_user_id=end_user_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
            conversation_id=conversation_id,
            draft=draft
        )

    # ──────────────────────────────────────────────
    # 静态方法：摄入/派发（不需要实例化，不加载 config）
    # ──────────────────────────────────────────────

    @staticmethod
    async def ingest_agent_message(
        conversation_id: str,
        message: Any,
        app_id: str,
        config_id: str = "",
        workspace_id: str = "",
        end_user_id: str = "",
        should_memorize: bool = True,
        language: str = "zh",
    ) -> bool:
        """Agent 消息摄入：写入 memory_messages 表 + 触发滑动窗口派发。"""
        from app.core.memory.pipelines.dispatcher import ingest_agent_message
        return await ingest_agent_message(
            conversation_id=conversation_id,
            message=message,
            app_id=app_id,
            config_id=config_id,
            workspace_id=workspace_id,
            end_user_id=end_user_id,
            should_memorize=should_memorize,
            language=language,
        )

    @staticmethod
    async def ingest_workflow_messages(
        messages: List[dict],
        conversation_id: str,
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        language: str = "zh",
    ) -> None:
        """Workflow 消息摄入：批量写入 memory_messages 表 + 触发滑动窗口派发。"""
        from app.core.memory.pipelines.dispatcher import ingest_workflow_messages
        await ingest_workflow_messages(
            messages=messages,
            conversation_id=conversation_id,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=workspace_id,
            language=language,
        )

    @staticmethod
    async def dispatch_api_service_async(
        messages: List[dict],
        end_user_id: str,
        config_id: str,
        workspace_id: str,
        language: str = "zh",
    ) -> List[str]:
        """API Service 异步写入入口。"""
        from app.core.memory.pipelines.dispatcher import dispatch_api_service_async
        return await dispatch_api_service_async(
            messages=messages,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=workspace_id,
            language=language,
        )

    @staticmethod
    async def write_messages_to_rag(
        messages: List[dict],
        end_user_id: str,
        user_rag_memory_id: str,
    ) -> None:
        """将 messages 写入 RAG 存储。"""
        from app.core.memory.pipelines.dispatcher import write_messages_to_rag
        await write_messages_to_rag(
            messages=messages,
            end_user_id=end_user_id,
            user_rag_memory_id=user_rag_memory_id,
        )

    @staticmethod
    def dispatch_flush_conversation(conversation_id: str) -> int:
        """Flush 兜底任务派发。"""
        from app.core.memory.pipelines.dispatcher import dispatch_flush_conversation
        return dispatch_flush_conversation(conversation_id)

    @staticmethod # 同步写入 下一个版本移除
    def get_or_create_service_api_conversation(workspace_id: str, end_user_id: str) -> str:
        """获取或创建 Service API 虚拟会话。"""
        from app.core.memory.pipelines.dispatcher import get_or_create_service_api_conversation
        return get_or_create_service_api_conversation(workspace_id, end_user_id)

    @staticmethod # 同步写入 下一个版本移除
    async def ensure_conversation_exists(conversation_id: str, workspace_id: str = "") -> None:
        """确保 conversations 表中存在该记录。"""
        from app.core.memory.pipelines.dispatcher import ensure_conversation_exists
        await ensure_conversation_exists(conversation_id, workspace_id)

    @staticmethod # 同步写入 下一个版本移除
    def verify_unmark_safe(conversation_id: str) -> bool:
        """验证对话是否可以安全 unmark。"""
        from app.core.memory.pipelines.dispatcher import verify_unmark_safe
        return verify_unmark_safe(conversation_id)

    @staticmethod # 同步写入 下一个版本移除
    def unmark_conversation_pending(conversation_id: str) -> None:
        """将对话从 pending set 中移除。"""
        from app.core.memory.pipelines.dispatcher import unmark_conversation_pending
        unmark_conversation_pending(conversation_id)

    # ──────────────────────────────────────────────
    # 实例方法：写入执行（由 write_message_task 调用）
    # ──────────────────────────────────────────────

    async def write(
            self,
            target_message: dict,
            context_before: List[dict] = None,
            context_after: List[dict] = None,
            conversation_id: str = "",
            message_seq: int = 0,
            language: str = "zh",
            ref_id: str = "",
            is_pilot_run: bool = False,
            skip_cursor_advance: bool = False,
            dispatch_at: str = "",
            progress_callback: Optional[
                Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
            ] = None,
    ) -> "WriteResult":
        """写入记忆：对话 → 萃取 → 存储 → 聚类 → 摘要

        Args:
            target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
            context_before: 上文消息列表（按 message_seq 升序）
            context_after: 下文消息列表（按 message_seq 升序）
            conversation_id: 对话 ID
            message_seq: 目标消息的 message_seq
            language: 语言 ("zh" | "en")
            ref_id: 引用 ID，为空则自动生成
            is_pilot_run: 试运行模式（只萃取不写入）
            skip_cursor_advance: 跳过 write_cursor 推进（直接写入路径）
            dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳
            progress_callback: 可选的进度回调

        Returns:
            WriteResult 包含状态和统计信息
        """
        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.write() 需要 memory_config，但当前实例未加载配置")
        from app.core.memory.pipelines.write_pipeline import WritePipeline

        pipeline = WritePipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
            progress_callback=progress_callback,
        )
        return await pipeline.run(
            target_message=target_message,
            context_before=context_before,
            context_after=context_after,
            conversation_id=conversation_id,
            message_seq=message_seq,
            ref_id=ref_id,
            is_pilot_run=is_pilot_run,
            skip_cursor_advance=skip_cursor_advance,
            dispatch_at=dispatch_at,
        )

    async def pilot_write(
            self,
            chunked_dialogs: List["DialogData"],
            language: str = "zh",
            progress_callback: Optional[
                Callable[[str, str, Optional[Dict[str, Any]]], Awaitable[None]]
            ] = None,
    ) -> "PilotWriteResult":
        """试运行写入：只执行萃取链路，不写入 Neo4j

        Args:
            chunked_dialogs: 预处理 + 分块后的 DialogData 列表
            language: 语言 ("zh" | "en")
            progress_callback: 可选的进度回调

        Returns:
            PilotWriteResult 包含萃取结果、图构建结果和去重结果
        """
        from app.core.memory.pipelines.pilot_write_pipeline import PilotWritePipeline

        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.pilot_write() 需要 memory_config，但当前实例未加载配置")
        pipeline = PilotWritePipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
            progress_callback=progress_callback,
        )
        return await pipeline.run(chunked_dialogs)

    async def read(
            self,
            query: str,
            search_switch: SearchStrategy,
            history: list | None = None,
            limit: int = 10,
    ) -> MemorySearchResult:
        if history is None:
            history = []
        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.read() 需要 memory_config，但当前实例未加载配置")
        return await ReadPipeLine(self.ctx).run(query, search_switch, history, limit)

    async def forget(
            self, max_batch: int = 100, min_days: int = 30
    ) -> dict:
        """遗忘：识别低激活节点并融合"""
        raise NotImplementedError("ForgettingPipeline 尚未实现")

    async def run_reflection_layer2(self, baseline: str = "HYBRID", language: str = "zh") -> dict:
        """反思引擎 Layer 2 离线巡检

        由 Celery 定时任务调用（每 10 分钟），执行描述合并等子问题。
        """
        from app.core.memory.pipelines.reflection_pipeline import ReflectionPipeline

        pipeline = ReflectionPipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
        )
        return await pipeline.run_layer2(baseline=baseline)

    async def run_dedup_full_scan(self, baseline: str = "HYBRID") -> Dict[str, Any]:
        """反思引擎 Layer 2 — 去重方案B低频全量扫描去重（单用户入口）

        由 Celery 定时任务调用（每天）。
        """
        from app.core.memory.pipelines.reflection_pipeline import ReflectionPipeline

        pipeline = ReflectionPipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language="zh",
        )
        return await pipeline.run_dedup_full_scan(baseline=baseline)

    async def run_reflection_layer3(self) -> dict:
        """反思引擎 Layer 3 知识综合

        由 Celery 定时任务调用（每天一次）。
        TODO: Observation 合成、Opinion 演化、模式反馈
        """
        raise NotImplementedError("Layer 3 尚未实现")

    # async def cluster(self, new_entity_ids: list[str] = None) -> None:
    #     """聚类：全量初始化或增量更新社区"""
    #     raise NotImplementedError("ClusteringPipeline 尚未实现")


def create_long_term_memory_tool(
        memory_config: Dict[str, Any],
        end_user_id: str,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        memory_name: Optional[str] = None,
        db: Optional[Session] = None,
):
    """创建长期记忆检索工具。

    若未启用或缺少用户 ID 则返回 None。

    Args:
        memory_config: 记忆配置字典（来自 app_releases.config.memory）
        end_user_id: 用户 ID
        storage_type: 存储类型（可选）
        user_rag_memory_id: 用户 RAG 记忆 ID（可选）
        memory_name: 记忆配置名称（可选，若提供 db 则自动查询）
        db: 数据库会话（可选，用于自动查询 memory_name）

    Returns:
        长期记忆工具，或 None
    """
    if not memory_config or not memory_config.get("enabled") or not end_user_id:
        return None

    from langchain.tools import tool

    config_id = memory_config.get("memory_config_id") or memory_config.get("memory_content", None)

    # 若未显式传入 memory_name 但提供了 db，则自动查询
    if not memory_name and config_id and db is not None:
        try:
            from app.models import MemoryConfig
            mc = db.query(MemoryConfig.config_name).filter(
                MemoryConfig.config_id == config_id
            ).first()
            memory_name = mc.config_name if mc else None
        except Exception:
            pass

    logger.info(f"创建长期记忆工具，配置: end_user_id={end_user_id}, config_id={config_id}, storage_type={storage_type}")

    @tool(args_schema=LongTermMemoryInput)
    async def long_term_memory(question: str, search_mode: str) -> str:
        """检索用户的历史记忆，用于了解其背景、偏好和过往对话。

        适用：用户询问历史/个人信息或偏好，或需基于历史上下文做个性化判断。
        不适用：寒暄、纯任务请求(写代码/翻译)、用户已给全信息、纯创作类任务。
        """
        logger.info(f" 长期记忆工具被调用！question={question}, user={end_user_id}")
        try:
            memory_service = MemoryService(config_id, end_user_id)
            search_result = await memory_service.read(question, SearchStrategy(search_mode))
            return f"检索到以下历史记忆：\n\n{search_result.content}"
        except Exception as e:
            logger.error("长期记忆检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"记忆检索失败: {str(e)}"

    long_term_memory._tool_meta = {
        "tool_type": "long_term_memory",
        "sources": [{"id": config_id, "name": memory_name or config_id}],
    }
    return long_term_memory
