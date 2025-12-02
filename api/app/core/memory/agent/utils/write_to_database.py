import os
import uuid
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
import logging
import json

from app.db import get_db
from app.models.retrieval_info import RetrievalInfo

logger = logging.getLogger(__name__)

async def write_to_database(host_id: uuid.UUID, data: Any) -> str:
    """
    将数据写入数据库
    :param host_id: 宿主 ID
    :param data: 要写入的数据
    :return: 写入数据库的结果
    """
    # 从数据库会话中获取会话
    db: Session = next(get_db())
    try:
        if isinstance(data, (dict, list)):
            serialized = json.dumps(data, ensure_ascii=False)
        elif isinstance(data, str):
            serialized = data
        else:
            serialized = str(data)

        new_retrieval_info = RetrievalInfo(
            # host_id=host_id,
            host_id=uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122"),
            retrieve_info=serialized,
            created_at=datetime.now()
        )
        db.add(new_retrieval_info)
        db.commit()
        logger.info(f"success to write data to database, host_id: {host_id}, retrieve_info: {serialized}")
        return "success to write data to database"
    except Exception as e:
        db.rollback()
        logger.error(f"failed to write data to database, host_id: {host_id}, retrieve_info: {data}, error: {e}")
        raise e
    finally:
        try:
            db.close()
        except Exception:
            pass
