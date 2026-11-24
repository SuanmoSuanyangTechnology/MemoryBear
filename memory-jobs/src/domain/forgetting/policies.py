"""Pair selection preserves the legacy limit-before-dedup rule."""

from typing import Any


def unique_pairs(pairs: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    statements, entities, result = set(), set(), []
    for pair in pairs[:maximum]:
        if pair["statement_id"] in statements or pair["entity_id"] in entities:
            continue
        statements.add(pair["statement_id"])
        entities.add(pair["entity_id"])
        result.append(pair)
    return result
