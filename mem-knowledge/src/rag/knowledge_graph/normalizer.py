"""Stable identifiers for Evidence Graph records."""

import hashlib
import json
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


def _digest(kind: str, *parts: str) -> str:
    payload = json.dumps(
        [kind, *(str(part) for part in parts)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entity_key(kb_id: str, name: str, entity_type: str) -> str:
    return _digest("entity", kb_id, normalize_name(name), normalize_name(entity_type))


def relation_key(
    kb_id: str,
    from_entity_key: str,
    predicate: str,
    to_entity_key: str,
    directed: bool,
) -> str:
    from_key, to_key = str(from_entity_key), str(to_entity_key)
    if not directed:
        from_key, to_key = sorted((from_key, to_key))
    return _digest(
        "relation",
        kb_id,
        from_key,
        normalize_name(predicate),
        to_key,
        "directed" if directed else "undirected",
    )


def entity_evidence_id(
    kb_id: str,
    document_id: str,
    source_chunk_id: str,
    normalized_entity_key: str,
) -> str:
    return _digest(
        "entity_evidence",
        kb_id,
        document_id,
        source_chunk_id,
        normalized_entity_key,
    )


def relation_evidence_id(
    kb_id: str,
    document_id: str,
    source_chunk_id: str,
    normalized_relation_key: str,
) -> str:
    return _digest(
        "relation_evidence",
        kb_id,
        document_id,
        source_chunk_id,
        normalized_relation_key,
    )


def projection_id(kb_id: str, projection_type: str, key: str) -> str:
    return _digest("projection", kb_id, projection_type, key)


def document_map_id(kb_id: str, document_id: str) -> str:
    return _digest("document_map", kb_id, document_id)


__all__ = [
    "document_map_id",
    "entity_evidence_id",
    "entity_key",
    "normalize_name",
    "projection_id",
    "relation_evidence_id",
    "relation_key",
]
