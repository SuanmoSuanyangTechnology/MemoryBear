"""km 运行面调用后端（C2 接缝）：视图 → 服务侧 invoke 的族 helper（镜像宿主 invoke_backend）。

km 侧只剩「用哪个配置、代表哪个租户」：``RemoteInvokeRef`` 只带配置 id 与租户，凭据解密、
渠道选路、failover 全在服务侧（设计 §2.2）。本层把 km 契约翻成服务侧严格入参（§2.3），
结果按 km 下标回填：

- **空白文本不上线**（服务侧拒收空白条目）：embedding 按原下标回填 ``None``，rerank 跳过并把
  命中项的下标映回原列表——调用方无需自行对齐；
- ``top_n`` 为 ``None`` 或 ``<=0``（langchain 的「全部」语义）一律**省略**该字段（服务侧
  须 ``>=1``）；
- 结果条数/下标与上送不符即协议违规（响亮失败，不静默错位）；
- 结构化 embedding 走包 contract（``EmbeddingRequest`` → wire contents+purpose），
  结果重建包 ``EmbeddingResult``（dimension 由包 contract 强校验 2048）；
- 多模态 rerank 的 wire ``index`` 是 ``views[].chunk_index``（调用方候选序号），
  本层按 chunk_index→位置字典映回，并构造包 ``RerankScore``。

各族传输形态（``stream=False`` 走 JSON 信封，结果体在 ``data`` 里）：embedding / rerank / asr
均非流式；asr 的任务轮询归服务侧（km 零协议面，单次同步调用阻塞至完成）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from redbear_model.contracts import (
    EmbeddingRequest,
    EmbeddingResult,
    ImageEmbeddingContent,
    ModelType,
    RerankCandidateView,
    RerankScore,
    TextEmbeddingContent,
)
from redbear_model.runtime.remote import InvokeRequest

from ...trace import get_trace_id
from .errors import ModelInvokeProtocolError
from .invoke import MODEL_SOURCE_MEM_KNOWLEDGE, ModelCallContext
from .runtime import ModelInvokeRuntime

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RemoteInvokeRef:
    """非解密模型引用：调用方只声明「哪个配置、代表哪个租户」。

    ``tenant_id`` 语义 = **调用方租户**（不是配置 owner）：服务侧可见性检查按此租户过滤
    （本租户 ∪ is_public），与 km 本地过滤互为 backstop；用量归因也落调用方租户。
    """

    config_id: UUID
    tenant_id: UUID
    workspace_id: UUID | None = None
    actor_id: UUID | None = None
    actor_name: str | None = None


def ref_from_view(
    view: Any,
    tenant_id: UUID,
    *,
    workspace_id: UUID | None = None,
    actor_id: UUID | None = None,
    actor_name: str | None = None,
) -> RemoteInvokeRef:
    """非解密视图（``ModelConfigSnapshot``）→ 引用：只取配置 id，租户取调用方。

    视图里的渠道/密钥材料不参与远端调用（凭据留在服务侧）。
    """

    return RemoteInvokeRef(
        config_id=UUID(str(view.model_config_id)),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        actor_name=actor_name,
    )


def context_from_ref(ref: RemoteInvokeRef) -> ModelCallContext:
    """引用 → 单次调用的身份/追踪元数据（各族 helper 共用同一拼装，避免漂移）。"""

    return ModelCallContext(
        actor_id=ref.actor_id,
        actor_name=ref.actor_name,
        tenant_id=ref.tenant_id,
        workspace_id=ref.workspace_id,
        trace_id=get_trace_id(),
        source=MODEL_SOURCE_MEM_KNOWLEDGE,
    )


def _non_blank(texts: Sequence[str]) -> tuple[list[str], list[int]]:
    """剔除空白条目并记下原下标（服务侧拒收空白，km 负责对齐）。"""

    kept: list[str] = []
    positions: list[int] = []
    for position, text in enumerate(texts):
        if isinstance(text, str) and text.strip():
            kept.append(text)
            positions.append(position)
    return kept, positions


def _scatter(
    vectors: Sequence[list[float]], positions: Sequence[int], *, size: int
) -> list[list[float] | None]:
    aligned: list[list[float] | None] = [None] * size
    for position, vector in zip(positions, vectors, strict=True):
        aligned[position] = vector
    return aligned


def _vectors(data: Any, *, expected: int) -> list[list[float]]:
    """结果体 ``{"vectors": [[...]], "usage": {...}}``；条数不符即协议违规。"""

    vectors = data.get("vectors") if isinstance(data, dict) else None
    count = len(vectors) if isinstance(vectors, list) else -1
    if count != expected:
        raise ModelInvokeProtocolError(
            f"embedding result shape mismatch: expected {expected} vectors, got {count}"
        )
    return [list(vector) for vector in vectors]


def _embedding_request(ref: RemoteInvokeRef, texts: Sequence[str]) -> InvokeRequest:
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.EMBEDDING,
        params={"input": list(texts)},
        stream=False,
    )


async def acall_embedding(
    pool: ModelInvokeRuntime, ref: RemoteInvokeRef, texts: Sequence[str]
) -> list[list[float] | None]:
    """异步批量向量化：返回与入参等长的列表，空白位为 ``None``。"""

    kept, positions = _non_blank(texts)
    if not kept:
        return [None] * len(texts)
    client = pool.invoke_client
    data = await client.call(_embedding_request(ref, kept), context_from_ref(ref))
    return _scatter(_vectors(data, expected=len(kept)), positions, size=len(texts))


def call_embedding_sync(
    pool: ModelInvokeRuntime, ref: RemoteInvokeRef, texts: Sequence[str]
) -> list[list[float] | None]:
    """同步孪生（ES 向量链路的写/检索链路本身即同步）。"""

    kept, positions = _non_blank(texts)
    if not kept:
        return [None] * len(texts)
    client = pool.invoke_sync_client
    data = client.call(_embedding_request(ref, kept), context_from_ref(ref))
    return _scatter(_vectors(data, expected=len(kept)), positions, size=len(texts))


# ---------------- 结构化 embedding（G4b：EmbeddingRequest 单向量融合） ----------------


def _content_block(block: TextEmbeddingContent | ImageEmbeddingContent) -> dict[str, Any]:
    """包 contract 内容块 → wire（字段镜像，仅删包内缺省字段）。"""

    if isinstance(block, TextEmbeddingContent):
        return {"type": "text", "text": block.text}
    return {
        "type": "image",
        "media_type": block.media_type,
        "data_uri": block.data_uri,
        "decoded_bytes": block.decoded_bytes,
    }


def _contents_request(ref: RemoteInvokeRef, request: EmbeddingRequest) -> InvokeRequest:
    # dimension/fusion 是包内缺省约束，wire 不含（服务侧按其契约选路）
    params = {
        "purpose": request.purpose.value,
        "contents": [_content_block(block) for block in request.contents],
    }
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.EMBEDDING,
        params=params,
        stream=False,
    )


def _embedding_result(data: Any) -> EmbeddingResult:
    """结果体 ``{"vector", "dimension", "usage"}`` → 包 ``EmbeddingResult``。

    dimension 由包 contract 强校验（``Literal[2048]``）：服务侧返回他值即协议违规，
    响亮失败而不是带着错误维度继续入库。
    """

    if not isinstance(data, dict):
        raise ModelInvokeProtocolError("structured embedding result is not an object")
    try:
        return EmbeddingResult(
            vector=tuple(data.get("vector") or ()),
            dimension=data.get("dimension"),
            usage=dict(data.get("usage") or {}),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise ModelInvokeProtocolError(
            f"structured embedding result violates the package contract: {exc}"
        ) from exc


async def acall_embedding_contents(
    pool: ModelInvokeRuntime, ref: RemoteInvokeRef, request: EmbeddingRequest
) -> EmbeddingResult:
    """异步结构化向量化（qwen3-vl 单向量融合；内容块上限由包 contract 保证）。"""

    client = pool.invoke_client
    data = await client.call(_contents_request(ref, request), context_from_ref(ref))
    return _embedding_result(data)


def call_embedding_contents_sync(
    pool: ModelInvokeRuntime, ref: RemoteInvokeRef, request: EmbeddingRequest
) -> EmbeddingResult:
    """同步孪生（ES 向量库链路 ``_embed_units``）。"""

    client = pool.invoke_sync_client
    data = client.call(_contents_request(ref, request), context_from_ref(ref))
    return _embedding_result(data)


# ---------------- rerank（文本 / 多模态两模式） ----------------


def _rerank_request(
    ref: RemoteInvokeRef, query: str, documents: Sequence[str], top_n: int | None
) -> InvokeRequest:
    params: dict[str, Any] = {"query": query, "documents": list(documents)}
    if top_n is not None and top_n > 0:
        params["top_n"] = top_n
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.RERANK,
        params=params,
        stream=False,
    )


def _ranked(data: Any, positions: Sequence[int]) -> list[dict[str, Any]]:
    """结果体 ``{"results": [{"index", "relevance_score"}]}``，下标映回原列表。"""

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ModelInvokeProtocolError("rerank result is missing 'results'")
    ranked: list[dict[str, Any]] = []
    for item in results:
        index = item.get("index") if isinstance(item, dict) else None
        score = item.get("relevance_score") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(positions):
            raise ModelInvokeProtocolError(f"rerank result index out of range: {index!r}")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ModelInvokeProtocolError(f"rerank result has non-numeric score: {score!r}")
        ranked.append({"index": positions[index], "relevance_score": float(score)})
    return ranked


async def acall_rerank(
    pool: ModelInvokeRuntime,
    ref: RemoteInvokeRef,
    *,
    query: str,
    documents: Sequence[str],
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """异步重排：返回 ``[{"index", "relevance_score"}]``，``index`` 为原文稿下标。"""

    kept, positions = _non_blank(documents)
    if not kept:
        return []
    client = pool.invoke_client
    data = await client.call(_rerank_request(ref, query, kept, top_n), context_from_ref(ref))
    return _ranked(data, positions)


def call_rerank_sync(
    pool: ModelInvokeRuntime,
    ref: RemoteInvokeRef,
    *,
    query: str,
    documents: Sequence[str],
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """同步孪生（``RedBearRerank.compress_documents`` 同步面）。"""

    kept, positions = _non_blank(documents)
    if not kept:
        return []
    client = pool.invoke_sync_client
    data = client.call(_rerank_request(ref, query, kept, top_n), context_from_ref(ref))
    return _ranked(data, positions)


def _view_payload(view: RerankCandidateView) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chunk_index": view.chunk_index,
        "kind": view.kind,
        "content": view.content,
    }
    if view.image_index is not None:
        payload["image_index"] = view.image_index
    return payload


def _multimodal_rerank_request(
    ref: RemoteInvokeRef,
    query_content: TextEmbeddingContent | ImageEmbeddingContent,
    views: Sequence[RerankCandidateView],
    top_n: int | None,
) -> InvokeRequest:
    params: dict[str, Any] = {
        "query_content": _content_block(query_content),
        "views": [_view_payload(view) for view in views],
    }
    if top_n is not None and top_n > 0:
        params["top_n"] = top_n
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.RERANK,
        params=params,
        stream=False,
    )


def _multimodal_scores(data: Any, views: Sequence[RerankCandidateView]) -> list[RerankScore]:
    """结果体 ``{"results": [{"index": chunk_index, "relevance_score"}]}``。

    wire ``index`` 是 ``views[].chunk_index``（调用方候选序号，服务侧按此回指）——
    本层按 chunk_index→位置字典映回数组位置，再构造包 ``RerankScore``。
    """

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ModelInvokeProtocolError("rerank result is missing 'results'")
    positions = {view.chunk_index: position for position, view in enumerate(views)}
    scores: list[RerankScore] = []
    for item in results:
        index = item.get("index") if isinstance(item, dict) else None
        score = item.get("relevance_score") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or index not in positions:
            raise ModelInvokeProtocolError(f"rerank result index out of range: {index!r}")
        try:
            scores.append(
                RerankScore(input_index=positions[index], relevance_score=score)
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise ModelInvokeProtocolError(
                f"rerank result violates the package contract: {exc}"
            ) from exc
    return scores


async def acall_rerank_multimodal(
    pool: ModelInvokeRuntime,
    ref: RemoteInvokeRef,
    *,
    query_content: TextEmbeddingContent | ImageEmbeddingContent,
    views: Sequence[RerankCandidateView],
    top_n: int | None = None,
) -> list[RerankScore]:
    """异步多模态重排（qwen3-vl rerank 原生多模态）。

    ``RerankScore.input_index`` 为 ``views`` 数组位置；``top_n`` 缺省 = 全部视图。
    """

    client = pool.invoke_client
    data = await client.call(
        _multimodal_rerank_request(ref, query_content, views, top_n),
        context_from_ref(ref),
    )
    return _multimodal_scores(data, views)


# ---------------- 媒体族（G4a：asr；音频经服务侧阻塞轮询） ----------------


def _asr_request(ref: RemoteInvokeRef, *, file_url: str) -> InvokeRequest:
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.ASR,
        params={"audio": {"kind": "url", "url": file_url}},
        stream=False,
    )


def _transcript_text(data: Any) -> str:
    """结果体 ``{"text": ..., "tracks": [...], "usage": {...}}``；缺 ``text`` 即协议违规。"""

    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str):
        raise ModelInvokeProtocolError("asr result is missing 'text'")
    return text


def call_asr_sync(pool: ModelInvokeRuntime, ref: RemoteInvokeRef, *, file_url: str) -> str:
    """同步音频转写：服务侧轮询至任务完成，返回转录文本（分轨细节留在服务侧结果体）。

    只收公网 URL（dashscope 文件转写不吃内联）；本地文件先落存储换 URL。
    """

    client = pool.media_invoke_sync_client
    data = client.call(_asr_request(ref, file_url=file_url), context_from_ref(ref))
    return _transcript_text(data)


__all__ = [
    "RemoteInvokeRef",
    "acall_embedding",
    "acall_embedding_contents",
    "acall_rerank",
    "acall_rerank_multimodal",
    "call_asr_sync",
    "call_embedding_contents_sync",
    "call_embedding_sync",
    "call_rerank_sync",
    "context_from_ref",
    "ref_from_view",
]
