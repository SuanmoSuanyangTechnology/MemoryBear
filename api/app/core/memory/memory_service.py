"""
MemoryService — 记忆模块统一入口（Facade）

所有外部调用方（controllers、Celery tasks、API service）只依赖此类。

职责：
- 接收已加载的 MemoryConfig，选择并调用对应的 Pipeline
- 暴露 write / read / pilot_write / forget / reflection 等实例方法
- create_long_term_memory_tool 创建长期记忆检索工具
"""

import logging
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.memory.enums import SearchStrategy, StorageType
from app.core.memory.models.message_models import DialogData
from app.core.memory.models.service_models import LongTermMemoryInput, MemoryContext, MemorySearchResult
from app.core.memory.pipelines.forgetting_pipeline import ForgettingPipeline
from app.core.memory.pipelines.memory_read import ReadPipeLine
from app.core.memory.pipelines.pilot_write_pipeline import PilotWriteResult
from app.core.memory.pipelines.write_pipeline import WriteResult
from app.db import get_db_read, get_async_db_context
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

    异步初始化：
    - 推荐使用 ``await MemoryService.create(...)`` 工厂方法，避免同步 DB 调用阻塞事件循环。
    - ``__init__`` 仍保留同步路径以兼容存量调用方。
    """

    def __init__(
            self,
            config_id: uuid.UUID | None,
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
            if config_id is not None:
                memory_config = config_service.load_memory_config(
                    config_id=config_id
                )
        if memory_config is None and storage_type.lower() == "neo4j":
            logger.warning(
                "MemoryService 初始化时未提供 memory config（config_id=None），"
                "write/read/pilot_write 方法将不可用"
            )
        self.ctx = MemoryContext(
            end_user_id=end_user_id,
            config_id=config_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
            conversation_id=conversation_id,
            draft=draft
        )

    @classmethod
    async def create(
            cls,
            config_id: uuid.UUID | None,
            end_user_id: str,
            workspace_id: str | None = None,
            storage_type: str = "neo4j",
            user_rag_memory_id: str | None = None,
            conversation_id: str | None = None,
            language: str = "zh",
            draft=False,
    ) -> "MemoryService":
        """Async factory — loads MemoryConfig with true async DB calls.

        Uses ``get_async_db_context()`` + ``load_memory_config_async`` so the
        event loop is never blocked on sync I/O during initialization.

        All parameters mirror ``__init__``.
        """
        instance = object.__new__(cls)
        async with get_async_db_context() as db:
            config_service = MemoryConfigService(db)
            memory_config = None
            if config_id is not None:
                memory_config = await config_service.load_memory_config_async(
                    config_id=config_id
                )

        if memory_config is None and storage_type.lower() == "neo4j":
            logger.warning(
                "MemoryService 初始化时未提供 memory config（config_id=None），"
                "write/read/pilot_write 方法将不可用"
            )

        instance.ctx = MemoryContext(
            end_user_id=end_user_id,
            config_id=config_id,
            memory_config=memory_config,
            storage_type=StorageType(storage_type),
            user_rag_memory_id=user_rag_memory_id,
            language=language,
            conversation_id=conversation_id,
            draft=draft,
        )
        return instance

    # ──────────────────────────────────────────────
    # 静态方法：摄入/派发（不需要实例化，不加载 config）
    # ──────────────────────────────────────────────

    @staticmethod
    async def refresh_user_card_tags(
            end_user_id: str,
            workspace_id: str,
    ) -> Dict[str, Any]:
        """通过统一的记忆服务入口刷新单个用户的名片 Tag 缓存。"""
        from app.core.memory.analytics.user_card_tags import refresh_user_card_tags

        return await refresh_user_card_tags(end_user_id, workspace_id)

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
    async def dispatch_mcp_write(
        message: str,
        end_user_id: str,
        config_id: uuid.UUID,
        workspace_id: str,
        storage_type: str = "neo4j",
        dialog_at: str = "",
    ) -> str:
        """MCP 单条消息写入入口。

        根据 storage_type 选择走 RAG 或 Neo4j 路径。

        Args:
            message: 用户消息内容
            end_user_id: 终端用户 ID
            config_id: 记忆配置 ID
            workspace_id: 工作空间 ID
            storage_type: 存储类型 ("neo4j" | "rag")
            dialog_at: 对话发生时间（ISO 8601）

        Returns:
            派发的任务 msg_id（RAG 路径返回空字符串）
        """
        from app.core.memory.pipelines.dispatcher import (
            dispatch_mcp_write,
            write_messages_to_rag,
        )

        if storage_type and storage_type.lower() == "rag":
            await write_messages_to_rag(
                messages=[{"role": "user", "content": message, "dialog_at": dialog_at}],
                end_user_id=end_user_id,
                user_rag_memory_id="",
            )
            return ""

        return await dispatch_mcp_write(
            message=message,
            end_user_id=end_user_id,
            config_id=config_id,
            workspace_id=workspace_id,
            dialog_at=dialog_at,
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
    async def dispatch_flush_conversation(conversation_id: str) -> int:
        """Flush 兜底任务派发。"""
        from app.core.memory.pipelines.dispatcher import dispatch_flush_conversation
        return await dispatch_flush_conversation(conversation_id)

    @staticmethod
    async def delete_node_by_element_id(
        element_id: str,
        end_user_id: str,
        operator: uuid.UUID,
    ) -> bool:
        """通过 elementId 删除 Neo4j 图节点（同时 DETACH DELETE 关联边）。"""
        from app.core.memory.models.service_models import MemoryContext
        from app.core.memory.pipelines.forgetting_pipeline import ForgettingPipeline

        pipeline = ForgettingPipeline(MemoryContext(end_user_id=end_user_id))
        return await pipeline.delete_node_by_element_id(
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
        from app.core.memory.models.service_models import MemoryContext
        from app.core.memory.pipelines.forgetting_pipeline import ForgettingPipeline

        pipeline = ForgettingPipeline(MemoryContext(end_user_id=end_user_id))
        return await pipeline.delete_all_nodes_by_end_user_id(end_user_id)

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
            source: str = "",
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
            source: 写入来源（agent/service_api/mcp/workflow），用于快照路径和节点 ID 生成
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
            source=source,
        )

    async def fast_write(
            self,
            target_message: dict,
            conversation_id: str = "",
            message_seq: int = 0,
            source: str = "",
            dispatch_at: str = "",
    ) -> dict:
        """快速写入记忆：清洗 → Embedding → 写入 :Dialogue 节点

        与 self.write() 并列，复用 __init__/create 已加载到 self.ctx 的 memory_config，
        构造并驱动 FastWritePipeline（不重复加载配置）。

        Args:
            target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
            conversation_id: 对话 ID（会话类入口非空，用于确定性 ID 生成）
            message_seq: 目标消息的 message_seq
            source: 写入来源（agent/service_api/mcp/workflow），用于节点 ID 生成
            dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳，用于 created_at 时间降级

        Returns:
            dict: {"status": "success"|"dropped", "dialog_id": str | None}

        Raises:
            RuntimeError: 当前实例未加载 memory_config
        """
        from app.core.memory.pipelines.fast_write_pipeline import FastWritePipeline

        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.fast_write() 需要 memory_config，但当前实例未加载配置")
        pipeline = FastWritePipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=self.ctx.language,
        )
        return await pipeline.run(
            target_message=target_message,
            conversation_id=conversation_id,
            message_seq=message_seq,
            source=source,
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
            includes: list | None = None,
            skip_summary: bool = False,
            enable_rerank: bool = False,
            record_display: bool = False,
    ) -> MemorySearchResult:
        """检索记忆。

        Args:
            record_display: 是否记录读取展示卡片。
        """
        if history is None:
            history = []
        if self.ctx.memory_config is None:
            raise RuntimeError("MemoryService.read() 需要 memory_config，但当前实例未加载配置")
        return await ReadPipeLine(self.ctx).run(
            query,
            search_switch,
            history,
            limit,
            includes=includes,
            skip_summary=skip_summary,
            enable_rerank=enable_rerank,
            record_display=record_display,
        )

    async def forget(self) -> dict:
        return await ForgettingPipeline(self.ctx).run()

    async def run_reflection_layer2(self, language: str = "zh") -> dict:
        """反思引擎 Layer 2 离线巡检

        由 Celery 定时任务调用（每 10 分钟），执行描述合并等子问题。
        """
        from app.core.memory.pipelines.reflection_pipeline import ReflectionPipeline

        pipeline = ReflectionPipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language=language,
        )
        return await pipeline.run_layer2(baseline=self.ctx.memory_config.reflexion_baseline or "HYBRID")

    async def run_dedup_full_scan(self) -> Dict[str, Any]:
        """反思引擎 Layer 2 — 去重方案B低频全量扫描去重（单用户入口）

        由 Celery 定时任务调用（每天）。
        """
        from app.core.memory.pipelines.reflection_pipeline import ReflectionPipeline

        pipeline = ReflectionPipeline(
            memory_config=self.ctx.memory_config,
            end_user_id=self.ctx.end_user_id,
            language="zh",
        )
        return await pipeline.run_dedup_full_scan(baseline=self.ctx.memory_config.reflexion_baseline or "HYBRID")

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
        workspace_id: uuid.UUID | None,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        memory_name: Optional[str] = None,
        config_id: uuid.UUID | None = None,
        db: Optional[Session] = None,
):
    """创建长期记忆检索工具。

    若未启用或缺少用户 ID 则返回 None。

    Args:
        memory_config: 记忆配置字典（来自 app_releases.config.memory）
        end_user_id: 用户 ID
        workspace_id: 工作空间 ID
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

    if config_id is None and workspace_id:
        try:
            with get_db_read() as read_db:
                config_id = MemoryConfigService(read_db).get_workspace_active_config_id(workspace_id)
        except Exception:
            logger.warning("按工作空间解析长期记忆配置失败", exc_info=True)

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

    logger.info(
        f"创建长期记忆工具，配置: end_user_id={end_user_id}, "
        f"workspace_id={workspace_id}, active_config_id={config_id}, storage_type={storage_type}"
    )

    @tool(args_schema=LongTermMemoryInput)
    async def long_term_memory(question: str, search_mode: str) -> str:
        """检索用户的历史记忆，用于了解其背景、偏好和过往对话。

        适用：用户询问历史/个人信息或偏好，或需基于历史上下文做个性化判断。
        不适用：寒暄、纯任务请求(写代码/翻译)、用户已给全信息、纯创作类任务。
        """
        logger.info(f" 长期记忆工具被调用！question={question}, user={end_user_id}")
        try:
            memory_service = await MemoryService.create(
                config_id,
                end_user_id,
                workspace_id=workspace_id,
                storage_type=storage_type or "neo4j",
                user_rag_memory_id=user_rag_memory_id,
            )
            search_result = await memory_service.read(
                question,
                SearchStrategy(search_mode),
                record_display=True,
            )
            return f"检索到以下历史记忆：\n\n{search_result.content}"
        except Exception as e:
            logger.error("长期记忆检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"记忆检索失败: {str(e)}"

    long_term_memory._tool_meta = {
        "tool_type": "long_term_memory",
        "sources": [{"id": str(config_id), "name": memory_name or str(config_id)}],
    }
    return long_term_memory
