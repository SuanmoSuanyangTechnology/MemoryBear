"""
Generate a concise "关于我" style user summary using data from Neo4j
and the existing LLM configuration (mirrors hot_memory_tags.py setup).

Usage:
    python -m analytics.user_summary --user_id <group_id>
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from typing import List, Tuple

# Ensure absolute imports work whether executed directly or via module
try:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    src_path = os.path.join(project_root, 'src')
    if src_path not in sys.path:
        sys.path.insert(0, src_path)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
except Exception:
    pass

from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.services.memory_config_service import MemoryConfigService

#TODO: Fix this

# Default values (previously from definitions.py)
DEFAULT_LLM_ID = os.getenv("SELECTED_LLM_ID", "openai/qwen-plus")
DEFAULT_GROUP_ID = os.getenv("SELECTED_GROUP_ID", "group_123")


@dataclass
class StatementRecord:
    statement: str
    created_at: str | None


class UserSummary:
    """Builds a textual user summary for a given user/group id."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.connector = Neo4jConnector()
        
        # Get config_id using get_end_user_connected_config
        with get_db_context() as db:
            try:
                from app.services.memory_agent_service import (
                    get_end_user_connected_config,
                )
                connected_config = get_end_user_connected_config(user_id, db)
                config_id = connected_config.get("memory_config_id")
                
                if config_id:
                    # Use the config_id to get the proper LLM client
                    config_service = MemoryConfigService(db)
                    memory_config = config_service.load_memory_config(config_id)
                    factory = MemoryClientFactory(db)
                    self.llm = factory.get_llm_client(memory_config.llm_model_id)
                else:
                    # TODO: Remove DEFAULT_LLM_ID fallback once all users have proper config
                    # Fallback to default LLM if no config found
                    factory = MemoryClientFactory(db)
                    self.llm = factory.get_llm_client(DEFAULT_LLM_ID)
            except Exception as e:
                print(f"Failed to get user connected config, using default LLM: {e}")
                # TODO: Remove DEFAULT_LLM_ID fallback once all users have proper config
                # Fallback to default LLM
                factory = MemoryClientFactory(db)
                self.llm = factory.get_llm_client(DEFAULT_LLM_ID)

    async def close(self):
        await self.connector.close()

    async def _get_recent_statements(self, limit: int = 80) -> List[StatementRecord]: # TODO Used by user_memory_service
        """Fetch recent statements authored by the user/group for context."""
        query = (
            "MATCH (s:Statement) "
            "WHERE s.group_id = $group_id AND s.statement IS NOT NULL "
            "RETURN s.statement AS statement, s.created_at AS created_at "
            "ORDER BY created_at DESC LIMIT $limit"
        )
        rows = await self.connector.execute_query(query, group_id=self.user_id, limit=limit)
        records: List[StatementRecord] = []
        for r in rows:
            try:
                records.append(StatementRecord(statement=r.get("statement", ""), created_at=r.get("created_at")))
            except Exception:
                continue
        return records

    async def _get_top_entities(self, limit: int = 30) -> List[Tuple[str, int]]:
        """Reuse hot tag logic to get meaningful entities and their frequencies."""
        # get_hot_memory_tags internally filters out non-meaningful nouns with LLM
        return await get_hot_memory_tags(self.user_id, limit=limit) # TODO Used by user_memory_service


async def generate_user_summary(user_id: str | None = None) -> str: # TODO useless
    """
    生成用户摘要的便捷函数
    
    Args:
        user_id: 可选的用户ID
        
    Returns:
        用户摘要字符串
    """
    # 导入服务层函数
    from app.services.user_memory_service import analytics_user_summary
    
    # 调用服务层函数
    result = await analytics_user_summary(user_id)
    return result.get("summary", "")


if __name__ == "__main__":
    print("开始生成用户摘要…")
    try:
        # 直接使用 runtime.json 中的 group_id
        summary = asyncio.run(generate_user_summary())
        print("\n— 用户摘要 —\n")
        print(summary)

        # 将结果写入统一的 User-Dashboard.json
        try:
            from app.core.config import settings
            settings.ensure_memory_output_dir()
            output_dir = settings.MEMORY_OUTPUT_DIR
            try:
                os.makedirs(output_dir, exist_ok=True)
            except Exception:
                pass
            dashboard_path = os.path.join(output_dir, "User-Dashboard.json")
            existing = {}
            if os.path.exists(dashboard_path):
                with open(dashboard_path, "r", encoding="utf-8") as rf:
                    existing = json.load(rf)
            existing["user_summary"] = {
                "group_id": DEFAULT_GROUP_ID,
                "summary": summary
            }
            with open(dashboard_path, "w", encoding="utf-8") as wf:
                json.dump(existing, wf, ensure_ascii=False, indent=2)
            print(f"已写入 {dashboard_path} -> user_summary")
        except Exception as e:
            print(f"写入 User-Dashboard.json 失败: {e}")
    except Exception as e:
        print(f"生成摘要失败: {e}")
        print("请检查: 1) Neo4j 是否可用；2) config.json 与 .env 的 LLM/Neo4j 配置是否正确；3) 数据是否包含该用户的内容。")
