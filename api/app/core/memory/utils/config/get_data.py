import json
import uuid
import logging

from typing import List, Dict, Any

from openai import BaseModel
import json
import sys
from pathlib import Path
from pydantic import model_validator, Field

from app.schemas.memory_storage_schema import SingleReflexionResultSchema
from app.schemas.memory_storage_schema import ReflexionResultSchema
from app.repositories.neo4j.neo4j_update import map_field_names
# 添加项目根目录到 Python 路径
sys.path.append(str(Path(__file__).parent))


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
    EXCLUDE_FIELDS = {
        "user_id",
        "group_id",
        "entity_type",
        "connect_strength",
        "relationship_type",
        "apply_id"
    }
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
                    filtered_item[key] = value
                elif key == 'entity2' and value is not None:
                    # 过滤entity2的name_embedding字段
                    entity2_filtered = {}
                    if hasattr(value, 'items'):
                        for e_key, e_value in value.items():
                            if e_key in EXCLUDE_FIELDS:
                                continue
                            if 'name_embedding' in e_key.lower():
                                continue
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

class ReflexionResultSchema(BaseModel):
    """Schema for the complete reflexion result data - a list of individual conflict resolutions."""
    results: List[SingleReflexionResultSchema] = Field(..., description="List of individual conflict resolution results, grouped by conflict type.")

    @model_validator(mode="before")
    def _normalize_resolved(cls, v):
        if isinstance(v, dict):
            conflict = v.get("conflict")
            if isinstance(conflict, dict) and conflict.get("conflict") is False:
                v["resolved"] = None
            else:
                resolved = v.get("resolved")
                if isinstance(resolved, dict):
                    orig = resolved.get("original_memory_id")
                    mem = resolved.get("resolved_memory")
                    if orig is None and (mem is None or mem == {}):
                        v["resolved"] = None
        return v
def extract_and_process_changes(DATA):
    """提取并处理 change 字段"""
    all_changes = []
    for i, item in enumerate(DATA):
        try:
            result = ReflexionResultSchema(**item)
            for j, res in enumerate(result.results):
                if res.resolved and res.resolved.change:
                    for k, change in enumerate(res.resolved.change):
                        change_data = {}
                        for field_item in change.field:
                            for key, value in field_item.items():
                                change_data[key] = value
                                if isinstance(value, list):
                                    print(f"  - {key}: {value[0]} -> {value[1]}")
                                else:
                                    print(f"  - {key}: {value}")

                        all_changes.append({
                            'data': change_data
                        })

                        # 测试字段映射
                        try:
                            mapped = map_field_names(change_data)
                            print(f"  映射结果: {mapped}")
                        except Exception as e:
                            print(f"  映射失败: {e}")

        except Exception as e:
            print(f"处理结果 {i + 1} 失败: {e}")

    return all_changes

if __name__ == "__main__":
    import asyncio

    # 从数据库中获取数据
    host_id = uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122")
    data = asyncio.run(get_data(host_id))
    print(type(data))
    print(data)
