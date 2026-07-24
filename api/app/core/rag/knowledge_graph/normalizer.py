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
    return _digest(
        "entity",
        str(kb_id),
        normalize_name(name),
        normalize_name(entity_type),
    )


def relation_key(
    kb_id: str,
    from_entity_key: str,
    predicate: str,
    to_entity_key: str,
    directed: bool,
) -> str:
    from_key = str(from_entity_key)
    to_key = str(to_entity_key)
    if not directed:
        from_key, to_key = sorted((from_key, to_key))
    return _digest(
        "relation",
        str(kb_id),
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
        str(kb_id),
        str(document_id),
        str(source_chunk_id),
        str(normalized_entity_key),
    )


def relation_evidence_id(
    kb_id: str,
    document_id: str,
    source_chunk_id: str,
    normalized_relation_key: str,
) -> str:
    return _digest(
        "relation_evidence",
        str(kb_id),
        str(document_id),
        str(source_chunk_id),
        str(normalized_relation_key),
    )


def projection_id(kb_id: str, projection_type: str, key: str) -> str:
    return _digest("projection", str(kb_id), str(projection_type), str(key))


def document_map_id(kb_id: str, document_id: str) -> str:
    return _digest("document_map", str(kb_id), str(document_id))
