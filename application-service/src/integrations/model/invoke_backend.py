"""Runtime-plane invoke backend: host shells -> service-side invoke for the embedding / rerank / asr families.

The host side keeps only "which config, on behalf of which tenant": ``RemoteInvokeRef``
carries just the config id and tenant; credential decryption, channel routing, and
failover all live service-side (callers never hold credentials). This layer translates
host contracts into the strict service-side inputs and maps results back onto host
indices:

- **Blank texts never go on the wire** (the service side rejects blank entries):
  embedding backfills ``None`` at the original indices, rerank skips them and maps hit
  indices back to the original list -- callers never need to align themselves;
- A ``top_n`` of ``None`` or ``<=0`` (langchain's "all" semantics) **omits** the field
  (the service side requires ``top_n >= 1``);
- Result count/indices that disagree with the submission are a protocol violation
  (fail loudly, never misalign silently);
- **Dual media channel**: a remote URL is passed through (``url_media_ref``) or the
  payload is inlined at <=1MB (``inline_media_ref``) -- over the limit with no URL
  available is a loud rejection (storing to object storage first is the caller's
  responsibility).

Transport shape per family (``stream=False`` uses the JSON envelope, result body in
``data``): embedding / rerank / asr are all non-streaming; asr task polling belongs to
the service side (zero protocol surface on the host).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redbear_model.contracts import ModelType
from redbear_model.runtime.remote import InvokeRequest

from ...trace import get_trace_id

from .contracts import MODEL_SOURCE_INTERNAL_API, ModelCallContext
from .errors import ModelServiceProtocolError
from .runtime import (
    get_model_invoke_client,
    get_model_invoke_sync_client,
    get_model_media_invoke_client,
    get_model_media_invoke_sync_client,
)

if TYPE_CHECKING:
    from ...rag.retrieval.models import ModelRuntimeSnapshot
    from ...schemas.model_schema import ModelInfo


@dataclass(frozen=True, slots=True)
class RemoteInvokeRef:
    """Non-decrypting model reference: the caller only names "which config, on behalf of which tenant".

    The business attribution facts (resource_type / resource_id / trace_id) are
    attached automatically at frame-send time from the host usage context and
    request context (see ``invoke_target``) and are not restated here.
    """

    config_id: UUID
    tenant_id: UUID
    workspace_id: UUID | None = None
    actor_id: UUID | None = None
    actor_name: str | None = None


def ref_from_snapshot(snapshot: "ModelRuntimeSnapshot") -> RemoteInvokeRef:
    """Host runtime snapshot -> non-decrypting reference: takes only config id and tenant.

    Plaintext api_key, channel failover plans, etc. in the snapshot do not take part
    in remote calls (credentials stay service-side).
    """

    return RemoteInvokeRef(
        config_id=UUID(str(snapshot.model_config_id)),
        tenant_id=UUID(str(snapshot.tenant_id)),
    )


def ref_from_model_info(info: "ModelInfo") -> RemoteInvokeRef:
    """Runtime model view (non-decrypting ``ModelInfo``) -> reference: same policy as :func:`ref_from_snapshot`.

    A view missing attribution (``model_config_id`` / ``tenant_id``) cannot be
    attributed and is rejected loudly -- sending it under a default tenant would
    pollute the usage ledger.
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
    """Reference -> identity/trace metadata for one call (both seams share one assembly, avoiding drift)."""

    return ModelCallContext(
        actor_id=ref.actor_id,
        actor_name=ref.actor_name,
        tenant_id=ref.tenant_id,
        workspace_id=ref.workspace_id,
        trace_id=get_trace_id(),
        source=MODEL_SOURCE_INTERNAL_API,
    )


def _non_blank(texts: Sequence[str]) -> tuple[list[str], list[int]]:
    """Drop blank entries and record their original indices (the service side rejects blanks; the host owns alignment)."""

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
    """Result body ``{"vectors": [[...]], "usage": {...}}``; a count mismatch is a protocol violation."""

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
    """Result body ``{"results": [{"index", "relevance_score"}]}``; indices are mapped back to the original list."""

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
    """Async batch embedding: returns a list as long as the input, with ``None`` at blank positions."""

    kept, positions = _non_blank(texts)
    if not kept:
        return [None] * len(texts)
    client = get_model_invoke_client()
    data = await client.call(_embedding_request(ref, kept), context_from_ref(ref))
    return _scatter(_vectors(data, expected=len(kept)), positions, size=len(texts))


def call_embedding_sync(ref: RemoteInvokeRef, texts: Sequence[str]) -> list[list[float] | None]:
    """Sync twin (the write/retrieval paths of the ES vector chain are inherently synchronous)."""

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
    """Async rerank: returns ``[{"index", "relevance_score"}]`` where ``index`` is the original document index."""

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
    """Sync twin (replacement for the ``RedBearRerank.rerank`` and direct dashscope paths)."""

    kept, positions = _non_blank(documents)
    if not kept:
        return []
    client = get_model_invoke_sync_client()
    data = client.call(_rerank_request(ref, query, kept, top_n), context_from_ref(ref))
    return _ranked(data, positions)


# ---------------- Media family (asr; the image/video generation families have no live host callers) ----------------

#: Inline media limit: same value as the runtime-plane contract ``invoke_schema.MAX_INLINE_MEDIA_BYTES`` (422 service-side when exceeded)
MAX_INLINE_MEDIA_BYTES = 1024 * 1024


def url_media_ref(url: str, *, mime: str | None = None) -> dict[str, Any]:
    """Publicly fetchable media -> URL reference (passed straight through to the upstream by the service side)."""

    ref: dict[str, Any] = {"kind": "url", "url": url}
    if mime:
        ref["mime"] = mime
    return ref


def inline_media_ref(data: bytes, *, mime: str) -> dict[str, Any]:
    """Local bytes -> inline reference (contract limit 1MB).

    Over the limit is a loud rejection: the caller must store the data first and
    swap in a URL (``url_media_ref``); silent truncation or dropping would change
    call semantics.
    """

    if len(data) > MAX_INLINE_MEDIA_BYTES:
        raise ModelServiceProtocolError(
            f"inline media exceeds {MAX_INLINE_MEDIA_BYTES} bytes ({len(data)}); "
            "store it and pass an http(s) URL instead"
        )
    return {
        "kind": "inline",
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
    }


def _asr_request(ref: RemoteInvokeRef, *, file_url: str) -> InvokeRequest:
    return InvokeRequest(
        config_id=ref.config_id,
        type=ModelType.ASR,
        params={"audio": url_media_ref(file_url)},
        stream=False,
    )


def _transcript_text(data: Any) -> str:
    """Result body ``{"text": ..., "tracks": [...], "usage": {...}}``; a missing ``text`` is a protocol violation."""

    text = data.get("text") if isinstance(data, dict) else None
    if not isinstance(text, str):
        raise ModelServiceProtocolError("asr result is missing 'text'")
    return text


async def acall_asr(ref: RemoteInvokeRef, *, file_url: str) -> str:
    """Async audio transcription: the service side polls until the task completes and returns the transcript text (per-track details stay in the service-side result body).

    Only public URLs are accepted (dashscope file transcription rejects inline
    payloads); local files must be stored first and swapped for a URL.
    """

    client = get_model_media_invoke_client()
    data = await client.call(_asr_request(ref, file_url=file_url), context_from_ref(ref))
    return _transcript_text(data)


def call_asr_sync(ref: RemoteInvokeRef, *, file_url: str) -> str:
    """Sync twin (the sync celery chain: media transcription in ``tasks.py``)."""

    client = get_model_media_invoke_sync_client()
    data = client.call(_asr_request(ref, file_url=file_url), context_from_ref(ref))
    return _transcript_text(data)


__all__ = [
    "MAX_INLINE_MEDIA_BYTES",
    "RemoteInvokeRef",
    "acall_asr",
    "acall_embedding",
    "acall_rerank",
    "call_asr_sync",
    "call_embedding_sync",
    "call_rerank_sync",
    "context_from_ref",
    "inline_media_ref",
    "ref_from_model_info",
    "ref_from_snapshot",
    "url_media_ref",
]
