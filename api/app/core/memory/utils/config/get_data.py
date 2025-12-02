import json
import os
import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.retrieval_info import RetrievalInfo
from app.schemas.memory_storage_schema import BaseDataSchema

import logging
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


async def get_data(host_id: uuid.UUID) -> List[Dict]:
    """
    从数据库中获取数据
    """
    # 从数据库会话中获取会话
    db: Session = next(get_db())
    try:
        data = db.query(RetrievalInfo.retrieve_info).filter(RetrievalInfo.host_id == host_id).all()

        # print(f"data:\n{data}")
        # 解析，提取为字典的列表
        results = await _load_(data)
        return results
    except Exception as e:
        logger.error(f"failed to get data from database, host_id: {host_id}, error: {e}")
        raise e
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    import asyncio

    # 从数据库中获取数据
    host_id = uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122")
    data = asyncio.run(get_data(host_id))
    print(type(data))
    print(data)
