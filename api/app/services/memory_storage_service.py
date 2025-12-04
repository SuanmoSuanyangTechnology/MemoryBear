"""
Memory Storage Service

Handles business logic for memory storage operations.
"""

from typing import Dict, List, Optional, Any
import os
import json

from dotenv import load_dotenv

from app.core.logging_config import get_logger
from app.schemas.memory_storage_schema import (
    ConfigFilter,
    ConfigPilotRun,
    ConfigParamsCreate,
    ConfigParamsDelete,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ConfigUpdateForget,
    ConfigKey,
)
from app.repositories.data_config_repository import DataConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
# TODO 后续更新
# from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
# from app.core.memory.analytics.memory_insight import MemoryInsight
# from app.core.memory.analytics.recent_activity_stats import get_recent_activity_stats
# from app.core.memory.analytics.user_summary import generate_user_summary
from app.repositories.data_config_repository import DataConfigRepository

logger = get_logger(__name__)

# Load environment variables for Neo4j connector
load_dotenv()
_neo4j_connector = Neo4jConnector()


class MemoryStorageService:
    """Service for memory storage operations"""
    
    def __init__(self):
        logger.info("MemoryStorageService initialized")
    
    async def get_storage_info(self) -> dict:
        """
        Example wrapper method - retrieves storage information
        
        Args:
            
        Returns:
            Storage information dictionary
        """
        logger.info(f"Getting storage info ")
        
        # Empty wrapper - implement your logic here
        result = {
            "status": "active",
            "message": "This is an example wrapper"
        }
        
        return result

class DataConfigService: # 数据配置服务类（PostgreSQL）
    """Service layer for config params CRUD.

    The DB connection is optional; when absent, methods return a failure
    response containing an SQL preview to aid integration.
    """

    def __init__(self, db_conn: Optional[Any] = None) -> None:
        self.db_conn = db_conn
    
    # --- Driver compatibility helpers ---
    @staticmethod
    def _is_pgsql_conn(conn: Any) -> bool:  # 判断是否为 PostgreSQL 连接
        mod = type(conn).__module__
        return ("psycopg2" in mod) or ("psycopg" in mod)
    
    @staticmethod
    def _convert_timestamps_to_format(data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 created_at 和 updated_at 字段从 datetime 对象转换为 YYYYMMDDHHmmss 格式"""
        from datetime import datetime
        
        for item in data_list:
            for field in ['created_at', 'updated_at']:
                if field in item and item[field] is not None:
                    value = item[field]
                    dt = None
                    
                    # 如果是 datetime 对象，直接使用
                    if isinstance(value, datetime):
                        dt = value
                    # 如果是字符串，先解析
                    elif isinstance(value, str):
                        try:
                            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                        except Exception:
                            pass  # 保持原值
                    
                    # 转换为 YYYYMMDDHHmmss 格式
                    if dt:
                        item[field] = dt.strftime('%Y%m%d%H%M%S')
        
        return data_list

    # --- Create ---
    def create(self, params: ConfigParamsCreate) -> Dict[str, Any]: # 创建配置参数（仅名称与描述）
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")
        
        # 如果workspace_id存在且模型字段未全部指定，则自动获取
        if params.workspace_id and not all([params.llm_id, params.embedding_id, params.rerank_id]):
            configs = self._get_workspace_configs(params.workspace_id)
            if configs is None:
                raise ValueError(f"工作空间不存在: workspace_id={params.workspace_id}")
            
            # 只在未指定时填充（允许手动覆盖）
            if not params.llm_id:
                params.llm_id = configs.get('llm')
            if not params.embedding_id:
                params.embedding_id = configs.get('embedding')
            if not params.rerank_id:
                params.rerank_id = configs.get('rerank')
        
        query, qparams = DataConfigRepository.build_insert(params)
        cur = self.db_conn.cursor()
        # PostgreSQL 使用 psycopg2 的命名参数格式
        cur.execute(query, qparams)
        self.db_conn.commit()
        return {"affected": getattr(cur, "rowcount", None)}
    
    def _get_workspace_configs(self, workspace_id) -> Optional[Dict[str, Any]]:
        """获取工作空间模型配置（内部方法，便于测试）"""
        from app.db import SessionLocal
        from app.repositories.workspace_repository import get_workspace_models_configs
        
        db_session = SessionLocal()
        try:
            return get_workspace_models_configs(db_session, workspace_id)
        finally:
            db_session.close()

    # --- Delete ---
    def delete(self, key: ConfigParamsDelete) -> Dict[str, Any]: # 删除配置参数（按配置名称）
        query, qparams = DataConfigRepository.build_delete(key)
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        affected = getattr(cur, "rowcount", None)
        self.db_conn.commit()
        # 如果没有任何行被删除，抛出异常
        if not affected:
            raise ValueError("未找到配置")
        return {"affected": affected}

    # --- Update ---
    def update(self, update: ConfigUpdate) -> Dict[str, Any]: # 部分更新配置参数
        query, qparams = DataConfigRepository.build_update(update)
        
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        affected = getattr(cur, "rowcount", None)
        self.db_conn.commit()
        if not affected:
            raise ValueError("未找到配置")
        return {"affected": affected}



    def update_extracted(self, update: ConfigUpdateExtracted) -> Dict[str, Any]: # 更新记忆萃取引擎配置参数
        query, qparams = DataConfigRepository.build_update_extracted(update)

        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        affected = getattr(cur, "rowcount", None)
        self.db_conn.commit()
        if not affected:
            raise ValueError("未找到配置")
        return {"affected": affected}

   
    # --- Forget config params ---
    def update_forget(self, update: ConfigUpdateForget) -> Dict[str, Any]: # 保存遗忘引擎的配置
        query, qparams = DataConfigRepository.build_update_forget(update)

        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        affected = getattr(cur, "rowcount", None)
        self.db_conn.commit()
        if not affected:
            raise ValueError("未找到配置")
        return {"affected": affected}
    
    # --- Read ---
    def get_extracted(self, key: ConfigKey) -> Dict[str, Any]: # 获取配置参数
        query, qparams = DataConfigRepository.build_select_extracted(key)
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        row = cur.fetchone()
        if not row:
            raise ValueError("未找到配置")
        # Map row to dict (DB-API cursor description available for many drivers)
        columns = [desc[0] for desc in cur.description]
        raw = {columns[i]: row[i] for i in range(len(columns))}
        # 将 created_at 和 updated_at 转换为 YYYYMMDDHHmmss 格式
        data_list = self._convert_timestamps_to_format([raw])
        return data_list[0] if data_list else raw

    def get_forget(self, key: ConfigKey) -> Dict[str, Any]: # 获取配置参数
        query, qparams = DataConfigRepository.build_select_forget(key)
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        row = cur.fetchone()
        if not row:
            raise ValueError("未找到配置")
        # Map row to dict (DB-API cursor description available for many drivers)
        columns = [desc[0] for desc in cur.description]
        raw = {columns[i]: row[i] for i in range(len(columns))}
        # 将 created_at 和 updated_at 转换为 YYYYMMDDHHmmss 格式
        data_list = self._convert_timestamps_to_format([raw])
        return data_list[0] if data_list else raw

    # --- Read All ---
    def get_all(self, workspace_id = None) -> List[Dict[str, Any]]: # 获取所有配置参数
        query, qparams = DataConfigRepository.build_select_all(workspace_id)
        if self.db_conn is None:
            raise ConnectionError("数据库连接未配置")

        cur = self.db_conn.cursor()
        cur.execute(query, qparams)
        rows = cur.fetchall()
        # 如果没有查询到任何配置，返回空列表（这是正常情况，不应抛出异常）
        if not rows:
            return []
        # Map rows to list of dicts
        columns = [desc[0] for desc in cur.description]
        data_list = [dict(zip(columns, row)) for row in rows]
        # 将 UUID 转换为字符串，将 created_at 和 updated_at 转换为 YYYYMMDDHHmmss 格式
        for item in data_list:
            if 'workspace_id' in item and item['workspace_id'] is not None:
                item['workspace_id'] = str(item['workspace_id'])
        return self._convert_timestamps_to_format(data_list)


    async def pilot_run(self, payload: ConfigPilotRun) -> Dict[str, Any]:
        """
        选择策略与内存覆写与同步版保持一致：优先 payload.config_id，其次 dbrun.json；两者皆无时报错。
        支持 dialogue_text 参数用于试运行模式。
        """
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        dbrun_path = os.path.join(project_root, "app", "core", "memory", "dbrun.json")

        payload_cid = str(getattr(payload, "config_id", "") or "").strip()
        cid: Optional[str] = payload_cid if payload_cid else None

        if not cid and os.path.isfile(dbrun_path):
            try:
                with open(dbrun_path, "r", encoding="utf-8") as f:
                    dbrun = json.load(f)
                if isinstance(dbrun, dict):
                    sel = dbrun.get("selections", {})
                    if isinstance(sel, dict):
                        fallback_cid = str(sel.get("config_id") or "").strip()
                        cid = fallback_cid or None
            except Exception:
                cid = None

        if not cid:
            raise ValueError("未提供 payload.config_id，且 dbrun.json 未设置 selections.config_id，禁止启动试运行")

        # 验证 dialogue_text 必须提供
        dialogue_text = payload.dialogue_text.strip() if payload.dialogue_text else ""
        logger.info(f"[PILOT_RUN] Received dialogue_text length: {len(dialogue_text)}, preview: {dialogue_text[:100]}")
        if not dialogue_text:
            raise ValueError("试运行模式必须提供 dialogue_text 参数")

        # 应用内存覆写并刷新常量（在导入主管线前）
        # 注意：仅在内存中覆写配置，不修改 runtime.json 文件
        from app.core.memory.utils.config.definitions import reload_configuration_from_database
        
        ok_override = reload_configuration_from_database(cid)
        if not ok_override:
            raise RuntimeError("运行时覆写失败，config_id 无效或刷新常量失败")

        # 导入并 await 主管线（使用当前 ASGI 事件循环）
        from app.core.memory.main import main as pipeline_main
        from app.core.memory.utils.self_reflexion_utils import reflexion

        logger.info(f"[PILOT_RUN] Calling pipeline_main with dialogue_text length: {len(dialogue_text)}, is_pilot_run=True")
        await pipeline_main(dialogue_text=dialogue_text, is_pilot_run=True)
        logger.info("[PILOT_RUN] pipeline_main completed")
        
        # 调用自我反思
        # data = [
        #     {
        #         "data": {
        #             "id": "1",
        #             "statement": "张明现在在谷歌工作。",
        #             "group_id": "1",
        #             "chunk_id": "10",
        #             "created_at": "2023-01-01",
        #             "expired_at": "2023-01-02",
        #             "valid_at": "2023-01-01",
        #             "invalid_at": "2023-01-02",
        #             "entity_ids": []
        #         },
        #         "conflict": True,
        #         "conflict_memory": {
        #             "id": "1",
        #             "statement": "张明现在在清华大学当讲师。",
        #             "group_id": "1",
        #             "chunk_id": "1",
        #             "created_at": "2019-12-01T19:15:05.213210",
        #             "expired_at": None,
        #             "valid_at": None,
        #             "invalid_at": None,
        #             "entity_ids": []
        #         }
        #     }
        # ]
        from app.core.memory.utils.config.get_example_data import get_example_data
        data = get_example_data()
        reflexion_result = await reflexion(data)

        # 读取输出，使用全局配置路径
        from app.core.config import settings
        result_path = settings.get_memory_output_path("extracted_result.json")
        if not os.path.isfile(result_path):
            raise FileNotFoundError(f"试运行完成，但未找到提取结果文件: {result_path}")
        
        with open(result_path, "r", encoding="utf-8") as rf:
            extracted_result = json.load(rf)
            
        extracted_result["self_reflexion"] = reflexion_result if reflexion_result else None
        return {
            "config_id": cid,
            "time_log": os.path.join(project_root, "time.log"),
            "extracted_result": extracted_result,
        }


# -------------------- Neo4j Search & Analytics (fused from data_search_service.py) --------------------
# Ensure env for connector (e.g., NEO4J_PASSWORD)
load_dotenv()
_neo4j_connector = Neo4jConnector()


async def search_dialogue(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_DIALOGUE,
        group_id=end_user_id,
    )
    data = {"search_for": "dialogue", "num": result[0]["num"]}
    return data


async def search_chunk(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_CHUNK,
        group_id=end_user_id,
    )
    data = {"search_for": "chunk", "num": result[0]["num"]}
    return data


async def search_statement(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_STATEMENT,
        group_id=end_user_id,
    )
    data = {"search_for": "statement", "num": result[0]["num"]}
    return data


async def search_entity(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_ENTITY,
        group_id=end_user_id,
    )
    data = {"search_for": "entity", "num": result[0]["num"]}
    return data


async def search_all(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_ALL,
        group_id=end_user_id,
    )
    
    # 检查结果是否为空或长度不足
    if not result or len(result) < 4:
        data = {
            "total": 0,
            "counts": {
                "dialogue": 0,
                "chunk": 0,
                "statement": 0,
                "entity": 0,
            },
        }
        return data
    
    data = {
        "total": result[-1]["Count"],
        "counts": {
            "dialogue": result[0]["Count"],
            "chunk": result[1]["Count"],
            "statement": result[2]["Count"],
            "entity": result[3]["Count"],
        },
    }
    return data


async def kb_type_distribution(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """统一知识库类型分布接口。

    聚合 dialogue/chunk/statement/entity 四类计数，返回统一的分布结构，便于前端一次性消费。
    """
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_ALL,
        group_id=end_user_id,
    )

    # 检查结果是否为空或长度不足
    if not result or len(result) < 4:
        data = {
            "total": 0, 
            "distribution": [
                {"type": "dialogue", "count": 0},
                {"type": "chunk", "count": 0},
                {"type": "statement", "count": 0},
                {"type": "entity", "count": 0},
            ]
        }
        return data

    total = result[-1]["Count"]
    distribution = [
        {"type": "dialogue", "count": result[0]["Count"]},
        {"type": "chunk", "count": result[1]["Count"]},
        {"type": "statement", "count": result[2]["Count"]},
        {"type": "entity", "count": result[3]["Count"]},
    ]

    data = {"total": total, "distribution": distribution}
    return data


async def search_detials(end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_DETIALS,
        group_id=end_user_id,
    )
    return result


async def search_edges(end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_EDGES,
        group_id=end_user_id,
    )
    return result


async def search_entity_graph(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    """搜索所有实体之间的关系网络（group 维度）。"""
    result = await _neo4j_connector.execute_query(
        DataConfigRepository.SEARCH_FOR_ENTITY_GRAPH,
        group_id=end_user_id,
    )
    # 对source_node 和 target_node 的 fact_summary进行截取，只截取前三条的内容（需要提取前三条“来源”）
    for item in result:
        source_fact = item["sourceNode"]["fact_summary"]
        target_fact = item["targetNode"]["fact_summary"]
        # 截取前三条“来源”
        item["sourceNode"]["fact_summary"] = source_fact.split("\n")[:4] if source_fact else []
        item["targetNode"]["fact_summary"] = target_fact.split("\n")[:4] if target_fact else []
    # 与现有返回风格保持一致，携带搜索类型、数量与详情
    data = {
        "search_for": "entity_graph",
        "num": len(result),
        "detials": result,
    }
    return data


async def analytics_hot_memory_tags(end_user_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    获取热门记忆标签，按数量排序并返回前N个
    """
    # 获取更多标签供LLM筛选（获取limit*4个标签）
    raw_limit = limit * 4
    tags = await get_hot_memory_tags(end_user_id, limit=raw_limit)
    
    # 按频率降序排序（虽然数据库已经排序，但为了确保正确性再次排序）
    sorted_tags = sorted(tags, key=lambda x: x[1], reverse=True)
    
    # 只返回前limit个
    top_tags = sorted_tags[:limit]
    
    return [{"name": t, "frequency": f} for t, f in top_tags]


async def analytics_memory_insight_report(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    insight = MemoryInsight(end_user_id)
    report = await insight.generate_insight_report()
    await insight.close()
    data = {"report": report}
    return data


async def analytics_recent_activity_stats() -> Dict[str, Any]:
    stats, _msg = get_recent_activity_stats()
    total = (
        stats.get("chunk_count", 0)
        + stats.get("statements_count", 0)
        + stats.get("triplet_entities_count", 0)
        + stats.get("triplet_relations_count", 0)
        + stats.get("temporal_count", 0)
    )
    # 精简：仅提供“最新一次活动多久前”
    latest_relative = None
    try:
        info = stats.get("log_path", "")
        idx = info.rfind("最新：")
        if idx != -1:
            latest_path = info[idx + 3 :].strip()
            if latest_path and os.path.exists(latest_path):
                import time
                diff = max(0.0, time.time() - os.path.getmtime(latest_path))
                m = int(diff // 60)
                if m < 1:
                    latest_relative = "刚刚"
                elif m < 60:
                    latest_relative = f"{m}分钟前"
                else:
                    h = int(m // 60)
                    latest_relative = f"{h}小时前" if h < 24 else f"{int(h // 24)}天前"
    except Exception:
        pass

    data = {"total": total, "stats": stats, "latest_relative": latest_relative}
    return data


async def analytics_user_summary(end_user_id: Optional[str] = None) -> Dict[str, Any]:
    summary = await generate_user_summary(end_user_id)
    data = {"summary": summary}
    return data