"""
Memory Storage Service

Handles business logic for memory storage operations.
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.logging_config import get_config_logger, get_logger
from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.analytics.recent_activity_stats import get_recent_activity_stats
from app.models.user_model import User
from app.repositories.data_config_repository import DataConfigRepository
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_config_schema import ConfigurationError
from app.schemas.memory_storage_schema import (
    ConfigKey,
    ConfigParamsCreate,
    ConfigParamsDelete,
    ConfigPilotRun,
    ConfigUpdate,
    ConfigUpdateExtracted,
    ConfigUpdateForget,
)
from app.services.memory_config_service import MemoryConfigService
from app.utils.sse_utils import format_sse_message
from dotenv import load_dotenv
from sqlalchemy.orm import Session

logger = get_logger(__name__)
config_logger = get_config_logger()

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
        logger.info("Getting storage info ")
        
        # Empty wrapper - implement your logic here
        result = {
            "status": "active",
            "message": "This is an example wrapper"
        }
        
        return result
    

class DataConfigService: # 数据配置服务类（PostgreSQL）
    """Service layer for config params CRUD.

    使用 SQLAlchemy ORM 进行数据库操作。
    """

    def __init__(self, db: Session) -> None:
        """初始化服务

        Args:
            db: SQLAlchemy 数据库会话
        """
        self.db = db

    @staticmethod
    def _convert_timestamps_to_format(data_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 created_at 和 updated_at 字段从 datetime 对象转换为 YYYYMMDDHHmmss 格式"""

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

        config = DataConfigRepository.create(self.db, params)
        self.db.commit()
        return {"affected": 1, "config_id": config.config_id}

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
    def delete(self, key: ConfigParamsDelete) -> Dict[str, Any]: # 删除配置参数（按配置ID）
        success = DataConfigRepository.delete(self.db, key.config_id)
        if not success:
            raise ValueError("未找到配置")
        return {"affected": 1}

    # --- Update ---
    def update(self, update: ConfigUpdate) -> Dict[str, Any]: # 部分更新配置参数
        config = DataConfigRepository.update(self.db, update)
        if not config:
            raise ValueError("未找到配置")
        return {"affected": 1}

    def update_extracted(self, update: ConfigUpdateExtracted) -> Dict[str, Any]: # 更新记忆萃取引擎配置参数
        config = DataConfigRepository.update_extracted(self.db, update)
        if not config:
            raise ValueError("未找到配置")
        return {"affected": 1}

    # --- Forget config params ---
    def update_forget(self, update: ConfigUpdateForget) -> Dict[str, Any]: # 保存遗忘引擎的配置
        config = DataConfigRepository.update_forget(self.db, update)
        if not config:
            raise ValueError("未找到配置")
        return {"affected": 1}

    # --- Read ---
    def get_extracted(self, key: ConfigKey) -> Dict[str, Any]: # 获取萃取配置参数
        result = DataConfigRepository.get_extracted_config(self.db, key.config_id)
        if not result:
            raise ValueError("未找到配置")
        return result

    def get_forget(self, key: ConfigKey) -> Dict[str, Any]: # 获取遗忘配置参数
        result = DataConfigRepository.get_forget_config(self.db, key.config_id)
        if not result:
            raise ValueError("未找到配置")
        return result

    # --- Read All ---
    def get_all(self, workspace_id = None) -> List[Dict[str, Any]]: # 获取所有配置参数
        configs = DataConfigRepository.get_all(self.db, workspace_id)

        # 将 ORM 对象转换为字典列表
        data_list = []
        for config in configs:
            config_dict = {
                "config_id": config.config_id,
                "config_name": config.config_name,
                "config_desc": config.config_desc,
                "workspace_id": str(config.workspace_id) if config.workspace_id else None,
                "group_id": config.group_id,
                "user_id": config.user_id,
                "apply_id": config.apply_id,
                "llm_id": config.llm_id,
                "embedding_id": config.embedding_id,
                "rerank_id": config.rerank_id,
                "llm": config.llm,
                "enable_llm_dedup_blockwise": config.enable_llm_dedup_blockwise,
                "enable_llm_disambiguation": config.enable_llm_disambiguation,
                "deep_retrieval": config.deep_retrieval,
                "t_type_strict": config.t_type_strict,
                "t_name_strict": config.t_name_strict,
                "t_overall": config.t_overall,
                "state": config.state,
                "chunker_strategy": config.chunker_strategy,
                "pruning_enabled": config.pruning_enabled,
                "pruning_scene": config.pruning_scene,
                "pruning_threshold": config.pruning_threshold,
                "enable_self_reflexion": config.enable_self_reflexion,
                "iteration_period": config.iteration_period,
                "reflexion_range": config.reflexion_range,
                "baseline": config.baseline,
                "statement_granularity": config.statement_granularity,
                "include_dialogue_context": config.include_dialogue_context,
                "max_context": config.max_context,
                "lambda_time": config.lambda_time,
                "lambda_mem": config.lambda_mem,
                "offset": config.offset,
                "created_at": config.created_at,
                "updated_at": config.updated_at,
            }
            data_list.append(config_dict)

        # 将 created_at 和 updated_at 转换为 YYYYMMDDHHmmss 格式
        return self._convert_timestamps_to_format(data_list)


    async def pilot_run_stream(self, payload: ConfigPilotRun) -> AsyncGenerator[str, None]:
        """
        流式执行试运行，产生 SSE 格式的进度事件
        
        Args:
            payload: 试运行配置和对话文本
            
        Yields:
            SSE 格式的字符串，包含以下事件类型：
            - 各种阶段名称: 进度更新 (如 starting, knowledge_extraction_complete 等)
            - result: 最终结果
            - error: 错误信息
            - done: 完成标记
            
        Raises:
            ValueError: 当配置无效或参数缺失时
            RuntimeError: 当管线执行失败时
        """
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        try:
            # 发出初始进度事件
            yield format_sse_message("starting", {
                "message": "开始试运行...",
                "time": int(time.time() * 1000)
            })
            
            # 步骤 1: 配置加载和验证（数据库优先）
            payload_cid = str(getattr(payload, "config_id", "") or "").strip()
            cid: Optional[str] = payload_cid if payload_cid else None

            if not cid:
                raise ValueError("未提供 payload.config_id，禁止启动试运行")

            # 验证 dialogue_text 必须提供
            dialogue_text = payload.dialogue_text.strip() if payload.dialogue_text else ""
            logger.info(f"[PILOT_RUN_STREAM] Received dialogue_text length: {len(dialogue_text)}, preview: {dialogue_text[:100]}")
            if not dialogue_text:
                raise ValueError("试运行模式必须提供 dialogue_text 参数")

            # Load configuration from database only using centralized manager
            try:
                config_service = MemoryConfigService(self.db)
                memory_config = config_service.load_memory_config(
                    config_id=int(cid),
                    service_name="MemoryStorageService.pilot_run_stream"
                )
                logger.info(f"Configuration loaded successfully: {memory_config.config_name}")
            except ConfigurationError as e:
                raise RuntimeError(f"Configuration loading failed: {e}")

            # 步骤 2: 创建进度回调函数捕获管线进度
            # 使用队列在回调和生成器之间传递进度事件
            progress_queue: asyncio.Queue = asyncio.Queue()
            
            async def progress_callback(stage: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
                """
                进度回调函数，将进度事件放入队列
                
                Args:
                    stage: 阶段标识
                    message: 进度消息
                    data: 可选的结果数据（用于传递节点执行结果）
                """
                await progress_queue.put((stage, message, data))
            
            # 步骤 3: 在后台任务中执行管线
            async def run_pipeline():
                """在后台执行管线并捕获异常"""
                try:
                    from app.services.pilot_run_service import run_pilot_extraction
                    
                    logger.info(f"[PILOT_RUN_STREAM] Calling run_pilot_extraction with dialogue_text length: {len(dialogue_text)}")
                    await run_pilot_extraction(
                        memory_config=memory_config,
                        dialogue_text=dialogue_text,
                        db=self.db,
                        progress_callback=progress_callback,
                    )
                    logger.info("[PILOT_RUN_STREAM] pipeline_main completed")
                    
                    # 标记管线完成
                    await progress_queue.put(("__PIPELINE_COMPLETE__", "", None))
                except Exception as e:
                    # 将异常放入队列
                    await progress_queue.put(("__PIPELINE_ERROR__", str(e), None))
            
            # 启动后台任务
            pipeline_task = asyncio.create_task(run_pipeline())
            
            # 步骤 4: 从队列中读取进度事件并发出
            while True:
                try:
                    # 等待进度事件，设置超时以检测客户端断开
                    stage, message, data = await asyncio.wait_for(
                        progress_queue.get(), 
                        timeout=0.5
                    )
                    
                    # 检查特殊标记
                    if stage == "__PIPELINE_COMPLETE__":
                        break
                    elif stage == "__PIPELINE_ERROR__":
                        raise RuntimeError(message)
                    
                    # 构建进度事件数据
                    progress_data = {
                        "message": message,
                        "time": int(time.time() * 1000)
                    }
                    
                    # 如果有结果数据，添加到事件中
                    if data:
                        progress_data["data"] = data
                    
                    # 发出进度事件，使用 stage 作为事件类型
                    yield format_sse_message(stage, progress_data)
                    
                except TimeoutError:
                    # 超时，继续等待（这允许检测客户端断开）
                    continue
            
            # 等待管线任务完成
            await pipeline_task
            
            # 步骤 5: 读取提取结果
            from app.core.config import settings
            result_path = settings.get_memory_output_path("extracted_result.json")
            if not os.path.isfile(result_path):
                raise FileNotFoundError(f"试运行完成，但未找到提取结果文件: {result_path}")
            
            with open(result_path, "r", encoding="utf-8") as rf:
                extracted_result = json.load(rf)
            
            # 步骤 6: 发出结果事件
            result_data = {
                "config_id": cid,
                "time_log": os.path.join(project_root, "logs", "time.log"),
                "extracted_result": extracted_result,
            }
            yield format_sse_message("result", result_data)
            
            # 步骤 7: 发出完成事件
            yield format_sse_message("done", {
                "message": "试运行完成",
                "time": int(time.time() * 1000)
            })
            
        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info("[PILOT_RUN_STREAM] Client disconnected during streaming")
            raise
        except Exception as e:
            # 发出错误事件
            logger.error(f"[PILOT_RUN_STREAM] Error during streaming: {e}", exc_info=True)
            yield format_sse_message("error", {
                "code": 5000,
                "message": "试运行失败",
                "error": str(e),
                "time": int(time.time() * 1000)
            })


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


async def analytics_hot_memory_tags(
    db: Session, 
    current_user: User,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    获取热门记忆标签，按数量排序并返回前N个
    """
    workspace_id = current_user.current_workspace_id
    # 获取更多标签供LLM筛选（获取limit*4个标签）
    raw_limit = limit * 4
    from app.services.memory_dashboard_service import get_workspace_end_users
    end_users = get_workspace_end_users(db, workspace_id, current_user)
    
    tags = []
    for end_user in end_users:
        tag = await get_hot_memory_tags(str(end_user.id), limit=raw_limit)
        if tag:
            # 将每个用户的标签列表展平到总列表中
            tags.extend(tag)

    # 按频率降序排序（虽然数据库已经排序，但为了确保正确性再次排序）
    sorted_tags = sorted(tags, key=lambda x: x[1], reverse=True)
    
    # 只返回前limit个
    top_tags = sorted_tags[:limit]
    
    return [{"name": t, "frequency": f} for t, f in top_tags]


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

