import json
import uuid
import logging

from typing import List, Dict, Any
logger = logging.getLogger(__name__)

async def _load_(data: List[Any]) -> List[Dict]:
    target_keys = [
        "id",
        "statement",
        "group_id",
        "chunk_id",
        "created_at",
        "expired_at",
        "valid_at",
        "invalid_at",
    ]
    results = []
    for row in data or []:
        s = None
        if isinstance(row, (tuple, list)) and row:
            s = row[0]
        elif hasattr(row, "retrieve_info"):
            s = getattr(row, "retrieve_info")
        elif isinstance(row, dict) and "retrieve_info" in row:
            s = row.get("retrieve_info")
        elif hasattr(row, "_mapping") and "retrieve_info" in getattr(row, "_mapping"):
            s = row._mapping["retrieve_info"]
        else:
            s = row
        if s is None:
            continue
        if isinstance(s, bytes):
            try:
                s = s.decode("utf-8")
            except Exception:
                try:
                    s = s.decode()
                except Exception:
                    continue
        s = str(s).strip()
        if not s or s == "[]":
            continue
        try:
            parsed = json.loads(s)
        except Exception:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if "statement" not in item and "statements" in item:
                item["statement"] = item.get("statements") or ""
            normalized = {k: item.get(k, "") for k in target_keys}
            results.append(normalized)
    return results


async def get_data(result):
    """
    从数据库中获取数据
    """
    neo4j_databasets=[]
    for item in result:
        filtered_item = {}
        for key, value in item.items():
            if 'name_embedding' not in key.lower():
                if key == 'relationship' and value is not None:
                    # 只保留relationship的指定字段
                    rel_filtered = {}
                    if hasattr(value, 'get'):
                        rel_filtered['run_id'] = value.get('run_id')
                        rel_filtered['statement'] = value.get('statement')
                        rel_filtered['statement_id'] = value.get('statement_id')
                        rel_filtered['expired_at'] = value.get('expired_at')
                        rel_filtered['created_at'] = value.get('created_at')
                    filtered_item[key] = rel_filtered
                elif key == 'entity2' and value is not None:
                    # 过滤entity2的name_embedding字段
                    entity2_filtered = {}
                    if hasattr(value, 'items'):
                        for e_key, e_value in value.items():
                            if 'name_embedding' not in e_key.lower():
                                entity2_filtered[e_key] = e_value
                    filtered_item[key] = entity2_filtered
                else:
                    filtered_item[key] = value

        # 直接将字典添加到列表中
        neo4j_databasets.append(filtered_item)
    return neo4j_databasets
async def get_data_statement( result):
    neo4j_databasets=[]
    for i in result:
        neo4j_databasets.append(i)
    return neo4j_databasets




if __name__ == "__main__":
    import asyncio

    # 从数据库中获取数据
    host_id = uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122")
    data = asyncio.run(get_data(host_id))
    print(type(data))
    print(data)
