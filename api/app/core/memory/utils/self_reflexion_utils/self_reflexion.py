# -*- coding: utf-8 -*-
"""自我反思主执行模块

本模块提供自我反思引擎的主流程，包括：
- 获取反思数据
- 冲突判断
- 反思执行
- 记忆更新

从 app.core.memory.src.data_config_api.self_reflexion 迁移而来。
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List

#TODO: Fix this

# Default values (previously from definitions.py)
REFLEXION_ENABLED = os.getenv("REFLEXION_ENABLED", "false").lower() == "true"
REFLEXION_ITERATION_PERIOD = os.getenv("REFLEXION_ITERATION_PERIOD", "3")
REFLEXION_RANGE = os.getenv("REFLEXION_RANGE", "retrieval")
REFLEXION_BASELINE = os.getenv("REFLEXION_BASELINE", "TIME")

from app.core.memory.utils.config.get_data import get_data
from app.core.memory.utils.self_reflexion_utils.evaluate import conflict
from app.core.memory.utils.self_reflexion_utils.reflexion import reflexion
from app.db import get_db
from app.models.retrieval_info import RetrievalInfo
from app.repositories.neo4j.cypher_queries import UPDATE_STATEMENT_INVALID_AT
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from sqlalchemy.orm import Session

# 并发限制（可通过环境变量覆盖）
CONCURRENCY = int(os.getenv("REFLEXION_CONCURRENCY", "5"))

# 确保 INFO 级别日志输出到终端
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
else:
    _root_logger.setLevel(logging.INFO)


async def get_reflexion_data(host_id: uuid.UUID) -> List[Any]:
    """
    根据反思范围获取判断的记忆数据。

    Args:
        host_id: 主机ID
    Returns:
        符合反思范围的记忆数据列表。
    """
    if REFLEXION_RANGE == "retrieval":
        return await get_data(host_id)
    elif REFLEXION_RANGE == "database":
        return []
    else:
        raise ValueError(f"未知的反思范围: {REFLEXION_RANGE}")


async def run_conflict(conflict_data: List[Any]) -> List[Any]:
    """
    判断反思数据中是否存在冲突。

    Args:
        conflict_data: 冲突数据列表。
    Returns:
        如果存在冲突则返回冲突记忆列表，否则返回空列表。
    """
    if not conflict_data:
        return []

    conflict_data = await conflict(conflict_data)
    # 仅保留存在冲突的条目（conflict == True）
    try:
        return [c for c in conflict_data if isinstance(c, dict) and c.get("conflict") is True]
    except Exception:
        return []


async def run_reflexion(reflexion_data: List[Any]) -> Any:
    """
    执行反思，解决冲突。

    Args:
        reflexion_data: 反思数据列表。
    Returns:
        解决冲突后的反思结果（由 LLM 返回）。
    """
    if not reflexion_data:
        return []
    # 并行对每个冲突进行反思，整体缩短等待时间
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _reflex_one(item: Any) -> Dict[str, Any] | None:
        async with sem:
            try:
                result_list = await reflexion([item])
                if not result_list:
                    return None
                obj = result_list[0]
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                elif hasattr(obj, "dict"):
                    return obj.dict()
                elif isinstance(obj, dict):
                    return obj
            except Exception as e:
                logging.warning(f"反思失败，跳过一项: {e}")
            return None

    tasks = [_reflex_one(item) for item in reflexion_data]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    return [r for r in results if r]


async def update_memory(solved_data: List[Any], host_id: uuid.UUID) -> str:
    """
    更新记忆库，将解决冲突后的记忆更新到记忆库中。

    Args:
        solved_data: 解决冲突后的记忆（由 LLM 返回）。
        host_id: 主机ID
    Returns:
        更新结果（成功或失败）。
    """
    flag = False
    if not solved_data:
        return "数据缺失，更新失败"
    if not isinstance(solved_data, list):
        return "数据格式错误，更新失败"
    neo4j_connector = Neo4jConnector()
    try:
        print(f"====== 更新记忆开始 ======\n")

        sem = asyncio.Semaphore(CONCURRENCY)
        success_count = 0

        async def _update_one(item: Dict[str, Any]) -> bool:
            async with sem:
                try:
                    if not isinstance(item, dict):
                        return False
                    if not item:
                        return False
                    resolved = item.get("resolved")
                    if not isinstance(resolved, dict) or not resolved:
                        logging.warning(f"反思结果无可更新内容，跳过此项: {item}")
                        return False
                    resolved_mem = resolved.get("resolved_memory")
                    if not isinstance(resolved_mem, dict) or not resolved_mem:
                        logging.warning(f"反思结果缺少 resolved_memory，跳过此项: {item}")
                        return False
                    group_id = resolved_mem.get("group_id")
                    id = resolved_mem.get("id")
                    # 使用 invalid_at 字段作为新的失效时间
                    new_invalid_at = resolved_mem.get("invalid_at")
                    if not all([group_id, id, new_invalid_at]):
                        logging.warning(f"记忆更新参数缺失，跳过此项: {item}")
                        return False
                    await neo4j_connector.execute_query(
                        UPDATE_STATEMENT_INVALID_AT,
                        group_id=group_id,
                        id=id,
                        new_invalid_at=new_invalid_at,
                    )
                    return True
                except Exception as e:
                    logging.error(f"更新单条记忆失败: {e}")
                    return False

        tasks = [_update_one(item) for item in solved_data if isinstance(item, dict)]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        success_count = sum(1 for r in results if r)

        logging.info(f"成功更新 {success_count} 条记忆")
        flag = success_count > 0
        return "更新成功" if flag else "更新失败"
    except Exception as e:
        logging.error(f"更新记忆库失败: {e}")
        return "更新失败"
    finally:
        if flag:  # 删除数据库中的检索数据
            db: Session = next(get_db())
            try:
                db.query(RetrievalInfo).filter(RetrievalInfo.host_id == host_id).delete()
                db.commit()
                logging.info(f"成功删除 {success_count} 条检索数据")
            except Exception as e:
                logging.error(f"删除数据库中的检索数据失败: {e}")
            finally:
                db.close()
        


async def _append_json(label: str, data: Any) -> None:
    """记录冲突记忆（后台线程写入，避免阻塞事件循环）"""
    def _write():
        with open("reflexion_data.json", "a", encoding="utf-8") as f:
            f.write(f"### {label} ###\n")
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.write("\n\n")
    # 正确地在协程内等待后台线程执行，避免未等待的协程警告
    await asyncio.to_thread(_write)


async def self_reflexion(host_id: uuid.UUID) -> str:
    """
    自我反思引擎，执行反思流程。
    
    Args:
        host_id: 主机ID
        
    Returns:
        反思结果描述字符串
    """
    if not REFLEXION_ENABLED:
        return "未开启反思..."
    print(f"====== 自我反思流程开始 ======\n")
    reflexion_data = await get_reflexion_data(host_id)
    if not reflexion_data:
        print(f"====== 自我反思流程结束 ======\n")
        return "无反思数据，结束反思"
    print(f"反思数据获取成功，共 {len(reflexion_data)} 条")

    conflict_data = await run_conflict(reflexion_data)
    if not conflict_data:
        print(f"====== 自我反思流程结束 ======\n")
        return "无冲突，无需反思"
    print(f"冲突记忆类型: {type(conflict_data)}")
    await _append_json("conflict", conflict_data)

    solved_data = await run_reflexion(conflict_data)
    if not solved_data:
        print(f"====== 自我反思流程结束 ======\n")
        return "反思失败，未解决冲突"
    print(f"解决冲突后的记忆类型: {type(solved_data)}")
    await _append_json("solved_data", solved_data)

    result = await update_memory(solved_data, host_id)
    print(f"更新记忆库结果: {result}")
    print(f"====== 自我反思流程结束 ======\n")
    return result


if __name__ == "__main__":
    import asyncio
    # host_id = uuid.UUID("3f6ff1eb-50c7-4765-8e89-e4566be33333")
    host_id = uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122")
    asyncio.run(self_reflexion(host_id))
