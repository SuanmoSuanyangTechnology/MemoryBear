"""Legacy profile fields kept readable during and after the metadata migration.

Ported from the API's ``legacy_metadata_merge``; only the merge hooks the graph
merge and reflection need are included.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.domain.metadata_merge import (
    LEGACY_METADATA_FIELDS,
    normalize_legacy_metadata_values,
)


def merge_legacy_user_properties(
    target: dict[str, Any], source: dict[str, Any]
) -> dict[str, list[str]]:
    """只返回有历史内容的属性，保持目标在前，不为新用户补空字段。"""

    merged: dict[str, list[str]] = {}
    for field in LEGACY_METADATA_FIELDS:
        values = normalize_legacy_metadata_values(target.get(field))
        values += normalize_legacy_metadata_values(source.get(field))
        if not values:
            continue
        texts = [
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            for value in values
        ]
        merged[field] = list(dict.fromkeys(texts))
    return merged


#: Cypher fragment that folds one entity's legacy fields into its keeper.
LEGACY_METADATA_MERGE_CLAUSES = "\n".join(
    f"""
WITH keeper, loser,
     CASE WHEN keeper.{field} IS NULL THEN []
          WHEN keeper.{field} IS :: LIST<ANY> THEN keeper.{field}
          ELSE [keeper.{field}] END
     + CASE WHEN loser.{field} IS NULL THEN []
            WHEN loser.{field} IS :: LIST<ANY> THEN loser.{field}
            ELSE [loser.{field}] END AS legacy_items
WITH keeper, loser,
     [value IN legacy_items
      WHERE value IS NOT NULL AND toString(value) <> '' | toString(value)] AS legacy_values
FOREACH (_ IN CASE WHEN size(legacy_values) > 0 THEN [1] ELSE [] END |
  SET keeper.{field} = reduce(values = [], value IN legacy_values |
      CASE WHEN value IN values THEN values ELSE values + value END)
)
"""
    for field in LEGACY_METADATA_FIELDS
)


__all__ = ["LEGACY_METADATA_MERGE_CLAUSES", "merge_legacy_user_properties"]
