"""Wire-level helpers shared by the HTTP and gRPC knowledge transports.

The payload and the success/error envelope have exactly one definition here so
the two transports cannot drift; only carriage differs.
"""

from __future__ import annotations

import json
from typing import Any

from ...schemas.knowledge_retrieval_schema import (
    KnowledgeRetrievalRequest,
    KnowledgeRetrievalResult,
)

from .contracts import KnowledgeCallContext
from .errors import KnowledgeProtocolError, KnowledgeServiceError

_REQUEST_RERANK_WIRE_FIELDS = ("rerank_id", "rerank_mode", "rerank_weights")
_KNOWLEDGE_BASE_RERANK_WIRE_FIELDS = ("rerank_mode", "rerank_weights")


def retrieval_wire_payload(
    request: KnowledgeRetrievalRequest,
    context: KnowledgeCallContext,
) -> dict[str, Any]:
    """Build the JSON body shared by the HTTP and gRPC retrieval calls."""

    normalized = request.model_copy(update={"source": context.source})
    payload = normalized.model_dump(mode="json")
    for field_name in _REQUEST_RERANK_WIRE_FIELDS:
        if getattr(normalized, field_name) is None:
            payload.pop(field_name, None)
    knowledge_bases = []
    for config in normalized.knowledge_bases:
        config_payload = config.model_dump(
            mode="json",
            include=config.model_fields_set | {"kb_id"},
        )
        for field_name in _KNOWLEDGE_BASE_RERANK_WIRE_FIELDS:
            if getattr(config, field_name) is None:
                config_payload.pop(field_name, None)
        knowledge_bases.append(config_payload)
    payload["knowledge_bases"] = knowledge_bases
    return payload


def parse_envelope(raw: bytes, *, status_code: int, trace_id: str) -> dict[str, Any]:
    """Validate the legacy envelope and raise the transport-neutral failure."""

    try:
        envelope = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise KnowledgeProtocolError("Knowledge service returned invalid JSON") from exc
    if not isinstance(envelope, dict):
        raise KnowledgeProtocolError("Knowledge service envelope must be an object")
    code = envelope.get("code")
    if not isinstance(code, int):
        raise KnowledgeProtocolError(
            "Knowledge service envelope code must be an integer"
        )
    if not 200 <= status_code < 300 or code != 0:
        message = str(envelope.get("error") or envelope.get("msg") or "Knowledge error")
        raise KnowledgeServiceError(status_code, code, message, trace_id)
    return envelope


def retrieval_result_from_data(data: Any) -> KnowledgeRetrievalResult:
    try:
        if isinstance(data, list):
            return KnowledgeRetrievalResult(chunks=data)
        if isinstance(data, dict):
            return KnowledgeRetrievalResult.model_validate(data)
        raise KnowledgeProtocolError(
            "Knowledge retrieval data must be a list or object"
        )
    except ValueError as exc:
        raise KnowledgeProtocolError(
            "Knowledge retrieval data is incompatible"
        ) from exc


def retrieval_policy_from_data(data: Any) -> dict[str, frozenset[str]]:
    if not isinstance(data, dict):
        raise KnowledgeProtocolError("Knowledge retrieval policy data is incompatible")
    policy: dict[str, frozenset[str]] = {}
    for retrieve_type, modalities in data.items():
        if isinstance(retrieve_type, str) and isinstance(modalities, list):
            policy[retrieve_type] = frozenset(
                modality for modality in modalities if isinstance(modality, str)
            )
    return policy


__all__ = [
    "parse_envelope",
    "retrieval_policy_from_data",
    "retrieval_result_from_data",
    "retrieval_wire_payload",
]