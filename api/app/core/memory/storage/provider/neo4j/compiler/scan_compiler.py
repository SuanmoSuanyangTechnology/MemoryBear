"""Paged label scans: cursor protocol plus preflight and page Cypher.

Bulk workloads (vector rebuilds, migrations) cannot use
:func:`compile_neo4j_filter` on its own: they must walk a whole label in bounded
memory. This module compiles the two statements that make that possible, and
owns the cursor format that carries the id-comparison mode between pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.memory.storage.enums import MemoryNodeLabel
from app.core.memory.storage.models import NodeFilter, NodeProjection
from app.core.memory.storage.provider.neo4j.compiler.filter_compiler import (
    compile_neo4j_filter,
)
from app.core.memory.storage.provider.neo4j.compiler.projection_compiler import (
    compile_neo4j_projection,
)

# Every memory label is addressed by its ``id`` property: node merges, the
# outbox projector and the Elasticsearch document key all agree on it.
# ``Community`` also carries ``community_id``, but its ``id`` mirrors it.
_ID_PROPERTY = "id"

# Cursors are ``"<mode>:<value>"``. The mode records how the first page compared
# ids so every later page of one scan agrees with it, even when the scan resumes
# in another process.
_NATIVE_MODE = "n"
_STRING_MODE = "s"
_MODES = frozenset({_NATIVE_MODE, _STRING_MODE})

# First-page probe: the scope's size, how many of its ids are string typed, and
# how many distinct ids it holds. Mirrors the migration script's preflight so
# paging behaves identically on legacy data.
_PREFLIGHT_QUERY = """
MATCH (n:`{label}`)
WHERE {predicate} AND n.{id_property} IS NOT NULL
RETURN count(n) AS total,
       count(CASE
           WHEN n.{id_property} = toString(n.{id_property}) THEN 1
       END) AS string_ids,
       count(DISTINCT toString(n.{id_property})) AS distinct_ids
"""

# One page of a label. ``n.id > $cursor`` uses the id index when every id is
# string typed; the ``toString`` form keeps legacy mixed-type data correct at
# the cost of not using that index.
_PAGE_QUERY = """
MATCH (n:`{label}`)
WHERE {predicate} AND n.{id_property} IS NOT NULL
{cursor_clause}
WITH n, {cursor_expression} AS cursor
ORDER BY cursor
LIMIT $limit
RETURN {return_expression}, cursor
"""


@dataclass(frozen=True, slots=True)
class ScanCursor:
    """Decoded continuation token of one scan.

    :param native: whether ids are compared directly (index-backed) or through
        ``toString`` (legacy mixed-type ids).
    :param value: last id of the previous page. ``None`` starts a new scan, and
        ``native`` is then decided by the preflight row.
    """

    native: bool
    value: str | None = None


def decode_scan_cursor(cursor: str | None) -> ScanCursor:
    """Parse a token produced by :func:`encode_scan_cursor`.

    :raises ValueError: when the token is not well formed.
    """
    if cursor is None:
        return ScanCursor(native=False)
    mode, separator, value = cursor.partition(":")
    if mode not in _MODES or not separator or not value:
        raise ValueError(f"invalid scan cursor: {cursor!r}")
    return ScanCursor(native=mode == _NATIVE_MODE, value=value)


def encode_scan_cursor(native: bool, value: str) -> str:
    """Build the token that continues a page ending at ``value``."""
    mode = _NATIVE_MODE if native else _STRING_MODE
    return f"{mode}:{value}"


def resolve_scan_native_cursor(
    label: MemoryNodeLabel,
    row: dict[str, Any],
) -> bool:
    """Decide how this scan compares ids, from its preflight row.

    :return: ``True`` when every id in scope is string typed, so ``n.id >
        $cursor`` compares like types and stays index-backed.
    :raises RuntimeError: when the scope holds duplicate ids. A cursor on a
        duplicated id either skips or repeats the rest of the page, so refusing
        the scan beats silently dropping nodes.
    """
    total = int(row.get("total", 0) or 0)
    string_ids = int(row.get("string_ids", 0) or 0)
    distinct_ids = int(row.get("distinct_ids", 0) or 0)
    if distinct_ids != total:
        raise RuntimeError(
            f"{label.value} scan scope has duplicate {_ID_PROPERTY} values: "
            f"total={total} distinct={distinct_ids}"
        )
    return string_ids == total


def compile_neo4j_scan_preflight(
    label: MemoryNodeLabel,
    node_filter: NodeFilter,
) -> tuple[str, dict[str, Any]]:
    """Compile the statement that sizes a scan scope and types its ids."""
    predicate, parameters = compile_neo4j_filter(node_filter)
    return (
        _PREFLIGHT_QUERY.format(
            label=label.value,
            predicate=predicate,
            id_property=_ID_PROPERTY,
        ),
        parameters,
    )


def compile_neo4j_scan_page(
    label: MemoryNodeLabel,
    node_filter: NodeFilter,
    *,
    cursor: ScanCursor,
    limit: int,
    projection: NodeProjection | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compile one page ordered by id, starting after ``cursor``."""
    predicate, parameters = compile_neo4j_filter(node_filter)
    return_expression, projection_parameters = compile_neo4j_projection(
        projection
    )
    parameters.update(projection_parameters)
    parameters["limit"] = limit
    if cursor.value is not None:
        parameters["cursor"] = cursor.value

    comparison = (
        f"n.{_ID_PROPERTY}"
        if cursor.native
        else f"toString(n.{_ID_PROPERTY})"
    )
    return (
        _PAGE_QUERY.format(
            label=label.value,
            predicate=predicate,
            id_property=_ID_PROPERTY,
            cursor_clause=(
                f"  AND {comparison} > $cursor"
                if cursor.value is not None
                else ""
            ),
            cursor_expression=comparison,
            return_expression=return_expression,
        ),
        parameters,
    )


__all__ = [
    "ScanCursor",
    "compile_neo4j_scan_page",
    "compile_neo4j_scan_preflight",
    "decode_scan_cursor",
    "encode_scan_cursor",
    "resolve_scan_native_cursor",
]
