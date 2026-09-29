"""运行面调用后端（C2 接缝）：宿主壳 → 服务侧 invoke 的 embedding / rerank 两族。

宿主侧只剩「用哪个配置、代表哪个租户」：``RemoteInvokeRef`` 只带配置 id 与租户，凭据解密、
渠道选路、failover 全在服务侧（设计 §2.2 调用方不持有凭据）。本层把宿主契约翻成服务侧严格
入参（§2.3），结果按宿主下标回填：

- **空白文本不上线**（服务侧拒收空白条目）：embedding 按原下标回填 ``None``，rerank 跳过并把
  命中项的下标映回原列表——调用方无需自行对齐；
- ``top_n`` 为 ``None`` 或 ``<=0``（langchain 的「全部」语义）一律**省略**该字段（服务侧
  ``top_n`` 必须 ``>=1``）；
- 结果条数/下标与上送不符即协议违规（响亮失败，不静默错位）。

G1 的 embedding / rerank 均非流式：``stream=False`` 走 JSON 信封，结果体在 ``data`` 里。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redbear_model.contracts import ModelType
from redbear_model.runtime.remote import InvokeRequest

from app.core.trace import get_trace_id

from .contracts import MODEL_SOURCE_INTERNAL_API, ModelCallContext
from .errors import ModelServiceProtocolError
from .runtime import get_model_invoke_client, get_model_invoke_sync_client

if TYPE_CHECKING:
    from app.core.rag.retrieval.models import ModelRuntimeSnapshot
    from app.schemas.model_schema import ModelInfo


@dataclass(frozen=True, slots=True)
class RemoteInvokeRef:
    """非解密模型引用：调用方只声明「哪个配置、代表哪个租户」。

    归因的业务事实（resource_type / resource_id / trace_id）由宿主用量上下文与请求上下文在
    发帧时自动带上（见 ``invoke_target``），不在此重复声明。
    """

    config_id: UUID
    tenant_id: UUID
    workspace_id: UUID | None = None
    actor_id: UUID | None = None
    actor_name: str | None = None


def ref_from_snapshot(snapshot: "ModelRuntimeSnapshot") -> RemoteInvokeRef:
    """宿主运行期快照 → 非解密引用：只取配置 id 与租户。

    快照里的明文 api_key、渠道换线计划等不参与远端调用（凭据留在服务侧）。
    """

    return RemoteInvokeRef(
        config_id=UUID(str(snapshot.model_config_id)),
        tenant_id=UUID(str(snapshot.tenant_id)),
    )


def ref_from_model_info(info: "ModelInfo") -> RemoteInvokeRef:
    """运行期模型视图（非解密 ``ModelInfo``）→ 引用：口径同 :func:`ref_from_snapshot`。

    视图缺归属（``model_config_id`` / ``tenant_id``）即无法归因，响亮拒止——按缺省租户
    发出去会污染用量账。
    """

    config_id = getattr(info, "model_config_id", None)
    tenant_id = getattr(info, "tenant_id", None)
    if not config_id or not tenant_id:
        raise ModelServiceProtocolError(
            "runtime model view is missing model_config_id/tenant_id for invoke attribution"
        )
    return RemoteInvokeRef(
        config_id=UUID(str(config_id)),
        tenant_id=UUID(str(tenant_id)),
    )


def context_from_ref(ref: RemoteInvokeRef) -> ModelCallContext:
    """引用 → 单次调用的身份/追踪元数据（两个接缝共用同一拼装，避免漂移）。"""

    return ModelCallContext(
        actor_id=ref.actor_id,
        actor_name=ref.actor_name,
        tenant_id=ref.tenant_id,
        workspace_id=ref.workspace_id,
        trace_id=get_trace_id(),
        source=MODEL_SOURCE_INTERNAL_API,
    )


def _non_blank(texts: Sequence[str]) -> tuple[list[str], list[int]]:
    """剔除空白条目并记下原下标（服务侧拒收空白，宿主负责对齐）。"""

    kept: list[str] = []
    positions: list[int] = []
    for position, text in enumerate(texts):
        if isinstance(text, str) and text.strip():
            kept.append(text)
            positions.append(position)
    return kept, positions


def _embedding_request(ref: RemoteInvokeRef, texts: Sequence[str]) -> InvokeRequest:
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.EMBEDDING,
        params={"input": list(texts)},
        stream=False,
    )


def _vectors(data: Any, *, expected: int) -> list[list[float]]:
    """结果体 ``{"vectors": [[...]], "usage": {...}}``；条数不符即协议违规。"""

    vectors = data.get("vectors") if isinstance(data, dict) else None
    count = len(vectors) if isinstance(vectors, list) else -1
    if count != expected:
        raise ModelServiceProtocolError(
            f"embedding result shape mismatch: expected {expected} vectors, got {count}"
        )
    return [list(vector) for vector in vectors]


def _scatter(
    vectors: Sequence[list[float]], positions: Sequence[int], *, size: int
) -> list[list[float] | None]:
    aligned: list[list[float] | None] = [None] * size
    for position, vector in zip(positions, vectors, strict=True):
        aligned[position] = vector
    return aligned


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
        raise ModelServiceProtocolError("rerank result is missing 'results'")
    ranked: list[dict[str, Any]] = []
    for item in results:
        index = item.get("index") if isinstance(item, dict) else None
        score = item.get("relevance_score") if isinstance(item, dict) else None
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(positions):
            raise ModelServiceProtocolError(f"rerank result index out of range: {index!r}")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ModelServiceProtocolError(f"rerank result has non-numeric score: {score!r}")
        ranked.append({"index": positions[index], "relevance_score": float(score)})
    return ranked


async def acall_embedding(ref: RemoteInvokeRef, texts: Sequence[str]) -> list[list[float] | None]:
    """异步批量向量化：返回与入参等长的列表，空白位为 ``None``。"""

    kept, positions = _non_blank(texts)
    if not kept:
        return [None] * len(texts)
    client = get_model_invoke_client()
    data = await client.call(_embedding_request(ref, kept), context_from_ref(ref))
    return _scatter(_vectors(data, expected=len(kept)), positions, size=len(texts))


def call_embedding_sync(ref: RemoteInvokeRef, texts: Sequence[str]) -> list[list[float] | None]:
    """同步孪生（ES 向量链路的写/检索链路本身即同步）。"""

    kept, positions = _non_blank(texts)
    if not kept:
        return [None] * len(texts)
    client = get_model_invoke_sync_client()
    data = client.call(_embedding_request(ref, kept), context_from_ref(ref))
    return _scatter(_vectors(data, expected=len(kept)), positions, size=len(texts))


async def acall_rerank(
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
    client = get_model_invoke_client()
    data = await client.call(_rerank_request(ref, query, kept, top_n), context_from_ref(ref))
    return _ranked(data, positions)


def call_rerank_sync(
    ref: RemoteInvokeRef,
    *,
    query: str,
    documents: Sequence[str],
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """同步孪生（``RedBearRerank.rerank`` 与 dashscope 直连路径的替代）。"""

    kept, positions = _non_blank(documents)
    if not kept:
        return []
    client = get_model_invoke_sync_client()
    data = client.call(_rerank_request(ref, query, kept, top_n), context_from_ref(ref))
    return _ranked(data, positions)


__all__ = [
    "RemoteInvokeRef",
    "acall_embedding",
    "acall_rerank",
    "call_embedding_sync",
    "call_rerank_sync",
    "context_from_ref",
    "ref_from_model_info",
    "ref_from_snapshot",
]
