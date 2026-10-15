"""用户归并与反思实体合并的旧画像字段兼容；迁移完成后随旧字段统一删除。"""

import json
from typing import Any

from app.core.memory.models.metadata_models import (
    LEGACY_METADATA_FIELDS,
    normalize_legacy_metadata_values,
)


def merge_legacy_user_properties(
    target: dict[str, Any], source: dict[str, Any]
) -> dict[str, list[str]]:
    """仅返回有历史内容的属性，保持目标在前，不为新用户补空字段。

    历史单值整体保留；非字符串转为文本，使 Neo4j 属性始终为同质字符串列表，
    同时兼容图模型及 ES keyword 映射。正常字符串内容不改写、不截断。
    """
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


# keeper / loser 已由调用方匹配。历史单值包装为一个条目，toString 保证同质列表；
# 仅有内容时 SET，双方缺失时不重新生成空旧字段。
# 两处实体去重查询共用此片段，属性归并和 loser 删除仍在同一事务内。
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
