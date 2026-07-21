import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

from app.core.config import settings
from app.core.rag.knowledge_graph.config import GraphPipeline
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.retrieval.models import (
    GraphRetrievalSnapshot,
    ModelRuntimeSnapshot,
    RetrievalTimings,
)

logger = logging.getLogger(__name__)


class GraphRetrievalBridge:
    _executor: ClassVar[ThreadPoolExecutor | None] = None
    _semaphore: ClassVar[asyncio.Semaphore | None] = None

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        if cls._executor is None:
            cls._executor = ThreadPoolExecutor(
                max_workers=settings.KNOWLEDGE_RETRIEVAL_GRAPH_MAX_CONCURRENCY,
                thread_name_prefix="knowledge-graph-retrieval",
            )
        return cls._executor

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        if cls._semaphore is None:
            cls._semaphore = asyncio.Semaphore(settings.KNOWLEDGE_RETRIEVAL_GRAPH_MAX_CONCURRENCY)
        return cls._semaphore

    @classmethod
    async def retrieve(
        cls,
        snapshot: GraphRetrievalSnapshot,
        timings: RetrievalTimings | None = None,
    ) -> DocumentChunk | None:
        if not isinstance(snapshot, GraphRetrievalSnapshot):
            raise TypeError("GraphRetrievalBridge requires a GraphRetrievalSnapshot")
        if snapshot.pipeline is not GraphPipeline.LEGACY:
            raise ValueError("legacy graph bridge requires the legacy pipeline")

        wait_started_at = time.perf_counter()
        semaphore = cls._get_semaphore()
        await semaphore.acquire()
        wait_ms = cls._elapsed_ms(wait_started_at)
        if timings is not None:
            timings.graph_wait_ms += wait_ms
        graph_started_at = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(cls._get_executor(), cls._retrieve_sync, snapshot)
        except BaseException:
            semaphore.release()
            raise

        future.add_done_callback(lambda _future: semaphore.release())
        try:
            document = await asyncio.shield(future)
        except asyncio.CancelledError:
            logger.info(
                "[Retrieval] graph_cancelled graph_async_mode=thread_bridge "
                "wait_ms=%s running_worker_not_stopped=true capacity_release=worker_done",
                wait_ms,
            )
            raise

        graph_ms = cls._elapsed_ms(graph_started_at)
        if timings is not None:
            timings.graph_ms += graph_ms
        logger.info(
            "[Retrieval] graph_done graph_async_mode=thread_bridge graph_wait_ms=%s graph_ms=%s",
            wait_ms,
            graph_ms,
        )
        return document

    @staticmethod
    def _retrieve_sync(snapshot: GraphRetrievalSnapshot) -> DocumentChunk | None:
        from app.core.rag.common.settings import kg_retriever

        document = kg_retriever.retrieval(
            question=snapshot.query,
            workspace_ids=list(snapshot.workspace_ids),
            kb_ids=list(snapshot.knowledge_ids),
            emb_mdl=GraphRetrievalBridge._build_embedding_model(snapshot.embedding),
            llm=GraphRetrievalBridge._build_chat_model(snapshot.llm),
        )
        if not document or not str(document.get("page_content", "")).strip():
            return None

        return DocumentChunk(
            page_content=document.get("page_content", ""),
            metadata=dict(document.get("metadata") or {}),
        )

    @staticmethod
    def _build_chat_model(snapshot: ModelRuntimeSnapshot) -> Base:
        return Base(
            key=snapshot.api_key,
            model_name=snapshot.model_name,
            base_url=snapshot.api_base,
        )

    @staticmethod
    def _build_embedding_model(snapshot: ModelRuntimeSnapshot) -> OpenAIEmbed:
        return OpenAIEmbed(
            key=snapshot.api_key,
            model_name=snapshot.model_name,
            base_url=snapshot.api_base,
        )

    @classmethod
    def shutdown(cls) -> None:
        if cls._executor is not None:
            cls._executor.shutdown(wait=False, cancel_futures=False)
            cls._executor = None
        cls._semaphore = None

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(0, int((time.perf_counter() - started_at) * 1000))
