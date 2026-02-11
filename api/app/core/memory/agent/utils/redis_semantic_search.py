"""
Redis 语义搜索集成模块

将 Redis 向量搜索与 Embedding 模型集成，提供端到端的语义搜索功能。

注意：此模块需要 RediSearch 支持。如果 RediSearch 不可用，
向量搜索功能将被禁用，但不会影响其他功能。
"""


from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.core.logging_config import get_logger

logger = get_logger(__name__)

# 延迟导入，避免在模块加载时就失败
vector_search = None
REDISEARCH_AVAILABLE = False

from app.core.memory.agent.utils import redis_vector_search as rvs_module
from app.core.memory.agent.utils.redis_keyword_search import keyword_search
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
vector_search = rvs_module.vector_search
REDISEARCH_AVAILABLE = rvs_module.REDISEARCH_AVAILABLE
logger.info(f"向量搜索模块导入成功，RediSearch 可用: {REDISEARCH_AVAILABLE}")

class RedisSemanticSearch:
    """Redis 语义搜索类"""
    
    def __init__(self, db: Session, embedding_model_id: str = None):
        """
        初始化语义搜索
        
        Args:
            db: 数据库会话
            embedding_model_id: Embedding 模型ID（可选）
        """
        if not REDISEARCH_AVAILABLE:
            logger.warning("RediSearch 不可用，向量搜索功能将被禁用")
        
        if MemoryClientFactory is None:
            raise ImportError("MemoryClientFactory 不可用，无法初始化语义搜索")
        
        self.db = db
        self.embedding_model_id = embedding_model_id
        self._embedder_client = None
        
        if embedding_model_id:
            self._init_embedder(embedding_model_id)
    
    def _init_embedder(self, embedding_model_id: str):
        """初始化 Embedder 客户端"""
        try:
            factory = MemoryClientFactory(self.db)
            self._embedder_client = factory.get_embedder_client(embedding_model_id)
            logger.info(f"Embedder 客户端初始化成功: {embedding_model_id}")
        except Exception as e:
            logger.error(f"Embedder 客户端初始化失败: {e}")
            raise
    
    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        获取文本的 embedding 向量
        
        Args:
            text: 输入文本
            
        Returns:
            List[float]: 向量
        """
        if not self._embedder_client:
            raise ValueError("Embedder 客户端未初始化")
        
        try:
            embeddings = await self._embedder_client.response([text])
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
            return None
        except Exception as e:
            logger.error(f"获取 embedding 失败: {e}")
            return None
    
    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量获取文本的 embedding 向量
        
        Args:
            texts: 文本列表
            
        Returns:
            List[List[float]]: 向量列表
        """
        if not self._embedder_client:
            raise ValueError("Embedder 客户端未初始化")
        
        try:
            embeddings = await self._embedder_client.response(texts)
            return embeddings
        except Exception as e:
            logger.error(f"批量获取 embedding 失败: {e}")
            return []
    
    # ==================== 索引管理 ====================
    
    def create_index(self, session_type: str = "read") -> bool:
        """
        创建向量索引
        
        Args:
            session_type: 会话类型
            
        Returns:
            bool: 是否成功
        """
        if not REDISEARCH_AVAILABLE or vector_search is None:
            logger.warning("RediSearch 不可用，无法创建索引")
            return False
        return vector_search.create_vector_index(session_type)
    
    def drop_index(self, session_type: str = "read") -> bool:
        """
        删除向量索引
        
        Args:
            session_type: 会话类型
            
        Returns:
            bool: 是否成功
        """
        if not REDISEARCH_AVAILABLE or vector_search is None:
            logger.warning("RediSearch 不可用，无法删除索引")
            return False
        return vector_search.drop_vector_index(session_type)
    
    # ==================== 添加会话 ====================
    
    async def add_session_with_vector(self,
                                     end_user_id: str,
                                     messages: str,
                                     aimessages: str = "",
                                     session_type: str = "read",
                                     metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        添加会话并自动生成向量
        
        Args:
            end_user_id: 用户ID
            messages: 用户消息
            aimessages: AI回复
            session_type: 会话类型
            metadata: 元数据
            
        Returns:
            Dict: 包含 session_id 和 success 状态
        """
        try:
            # 检查 vector_search 是否可用
            if not REDISEARCH_AVAILABLE or vector_search is None:
                logger.error("RediSearch 不可用，无法添加向量")
                return {"success": False, "session_id": None, "error": "RediSearch 不可用"}
            
            # 1. 自动生成 session_id
            import uuid
            session_id = str(uuid.uuid4())
            
            # 2. 生成向量（使用消息和回复的组合）
            text = f"{messages} {aimessages}".strip()
            vector = await self.get_embedding(text)

            
            if not vector:
                logger.error("生成向量失败")
                return {"success": False, "session_id": session_id, "error": "生成向量失败"}
            
            # 3. 自动调整 vector_search 的维度（如果需要）
            if len(vector) != vector_search.VECTOR_DIM:
                logger.info(f"自动调整向量维度: {vector_search.VECTOR_DIM} -> {len(vector)}")
                vector_search.VECTOR_DIM = len(vector)
            
            # 4. 添加到 Redis
            result = vector_search.add_vector(
                session_id=session_id,
                vector=vector,
                end_user_id=end_user_id,
                messages=messages,
                aimessages=aimessages,
                session_type=session_type,
                metadata=metadata
            )
            
            # 5. 同时建立关键词索引
            if keyword_search:
                keywords = keyword_search.extract_keywords(text)
                if keywords:
                    keyword_search.add_keyword_index(session_id, keywords, session_type)
            
            logger.info(f"成功添加会话向量: {session_id}")
            return {"success": True, "session_id": session_id}
            
        except Exception as e:
            logger.error(f"添加会话向量失败: {e}")
            return {"success": False, "session_id": None, "error": str(e)}
    
    async def batch_add_sessions_with_vectors(self,
                                             sessions: List[Dict[str, Any]],
                                             session_type: str = "read",
                                             batch_size: int = 10) -> Dict[str, Any]:
        """
        批量添加会话并生成向量
        
        Args:
            sessions: 会话列表，每个包含:
                - end_user_id: 用户ID
                - messages: 用户消息
                - aimessages: AI回复（可选）
                - metadata: 元数据（可选）
            session_type: 会话类型
            batch_size: 批处理大小
            
        Returns:
            Dict: 包含成功数量和生成的 session_ids
        """
        success_count = 0
        session_ids = []
        
        # 分批处理
        for i in range(0, len(sessions), batch_size):
            batch = sessions[i:i + batch_size]
            
            # 1. 批量生成向量
            texts = [f"{s['messages']} {s.get('aimessages', '')}".strip() for s in batch]
            vectors = await self.get_embeddings_batch(texts)
            
            if len(vectors) != len(batch):
                logger.warning(f"向量数量不匹配: {len(vectors)} vs {len(batch)}")
                continue
            
            # 2. 批量添加
            for session, vector in zip(batch, vectors):
                try:
                    # 自动生成 session_id
                    import uuid
                    session_id = str(uuid.uuid4())
                    
                    result = vector_search.add_vector(
                        session_id=session_id,
                        vector=vector,
                        end_user_id=session['end_user_id'],
                        messages=session['messages'],
                        aimessages=session.get('aimessages', ''),
                        session_type=session_type,
                        metadata=session.get('metadata')
                    )
                    
                    if result:
                        success_count += 1
                        session_ids.append(session_id)
                        
                        # 建立关键词索引
                        text = f"{session['messages']} {session.get('aimessages', '')}".strip()
                        keywords = keyword_search.extract_keywords(text)
                        if keywords:
                            keyword_search.add_keyword_index(
                                session_id,
                                keywords,
                                session_type
                            )
                    
                except Exception as e:
                    logger.error(f"添加会话失败: {e}")
                    continue
        
        logger.info(f"批量添加完成: {success_count}/{len(sessions)}")
        return {
            "success_count": success_count,
            "total": len(sessions),
            "session_ids": session_ids
        }
    
    # ==================== 语义搜索 ====================
    
    async def semantic_search(self,
                             query: str,
                             session_type: str = "read",
                             top_k: int = 10,
                             end_user_id: Optional[str] = None,
                             score_threshold: float = 0.7) -> List[Dict[str, Any]]:
        """
        语义搜索
        
        Args:
            query: 查询文本
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            score_threshold: 相似度阈值
            
        Returns:
            List[Dict]: 搜索结果
        """
        try:
            # 检查 vector_search 是否可用
            if not REDISEARCH_AVAILABLE or vector_search is None:
                logger.warning("RediSearch 不可用，无法进行语义搜索")
                return []
            
            # 1. 生成查询向量
            query_vector = await self.get_embedding(query)
            
            if not query_vector:
                logger.error("生成查询向量失败")
                return []
            
            # 2. 向量搜索
            results = vector_search.vector_search(
                query_vector=query_vector,
                session_type=session_type,
                top_k=top_k,
                end_user_id=end_user_id,
                score_threshold=1 - score_threshold  # 转换为距离阈值
            )
            
            logger.info(f"语义搜索找到 {len(results)} 个结果")
            return results
            
        except Exception as e:
            logger.error(f"语义搜索失败: {e}")
            return []
    
    async def hybrid_semantic_search(self,
                                    query: str,
                                    session_type: str = "read",
                                    top_k: int = 10,
                                    end_user_id: Optional[str] = None,
                                    vector_weight: float = 0.7,
                                    keyword_weight: float = 0.3,
                                    score_threshold: float = 0.3) -> List[Dict[str, Any]]:
        """
        混合语义搜索（向量 + 关键词）
        
        Args:
            query: 查询文本
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            vector_weight: 向量权重
            keyword_weight: 关键词权重
            score_threshold: 相似度阈值
            
        Returns:
            List[Dict]: 搜索结果
        """
        try:
            # 1. 生成查询向量
            query_vector = await self.get_embedding(query)
            
            if not query_vector:
                logger.error("生成查询向量失败")
                return []
            
            # 2. 提取关键词
            keywords = keyword_search.extract_keywords(query)
            
            # 3. 混合搜索
            results = vector_search.hybrid_search(
                query_vector=query_vector,
                keywords=keywords,
                session_type=session_type,
                top_k=top_k,
                end_user_id=end_user_id,
                vector_weight=vector_weight,
                keyword_weight=keyword_weight
            )

            # 4. 过滤低分结果
            filtered_results = [
                r for r in results
                if r.get('hybrid_score', 0) >= score_threshold
            ]
            
            logger.info(f"混合语义搜索找到 {len(filtered_results)} 个结果")
            return filtered_results
            
        except Exception as e:
            logger.error(f"混合语义搜索失败: {e}")
            return []
    
    # ==================== 相似会话推荐 ====================
    
    async def find_similar_sessions(self,
                                   session_id: str,
                                   session_type: str = "read",
                                   top_k: int = 5,
                                   end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        查找相似会话
        
        Args:
            session_id: 参考会话ID
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            
        Returns:
            List[Dict]: 相似会话列表
        """
        try:
            # 1. 获取参考会话的向量
            session_data = vector_search.get_vector(session_id, session_type)
            
            if not session_data or not session_data.get('vector'):
                logger.error(f"会话 {session_id} 没有向量数据")
                return []
            
            # 2. 使用该向量进行搜索
            results = vector_search.vector_search(
                query_vector=session_data['vector'],
                session_type=session_type,
                top_k=top_k + 1,  # +1 因为会包含自己
                end_user_id=end_user_id
            )
            
            # 3. 过滤掉自己
            similar_sessions = [
                r for r in results
                if r['session_id'] != session_id
            ][:top_k]
            logger.info(f"找到 {len(similar_sessions)} 个相似会话")
            return similar_sessions
            
        except Exception as e:
            logger.error(f"查找相似会话失败: {e}")
            return []
    
    # ==================== 批量索引现有数据 ====================
    
    async def index_existing_sessions(self,
                                     session_type: str = "read",
                                     batch_size: int = 50,
                                     limit: int = None) -> int:
        """
        为现有会话批量建立向量索引
        
        Args:
            session_type: 会话类型
            batch_size: 批处理大小
            limit: 最大处理数量
            
        Returns:
            int: 索引的会话数量
        """
        try:
            from app.core.memory.agent.utils.redis_tool import store
            
            # 1. 获取所有会话
            if session_type == "read":
                pattern = "session:*"
            elif session_type == "write":
                pattern = "session:write:*"
            elif session_type == "count":
                pattern = "session:count:*"
            else:
                return 0
            
            # 2. 扫描所有会话
            sessions_to_index = []
            cursor = 0
            indexed_count = 0
            
            while True:
                cursor, keys = store.r.scan(cursor, match=pattern, count=100)
                
                if not keys:
                    if cursor == 0:
                        break
                    continue
                
                # 排除索引 key 和向量 key
                session_keys = [
                    k for k in keys
                    if ':index:' not in k and ':reverse:' not in k and not k.startswith('vector:')
                ]
                
                # 批量获取数据
                pipe = store.r.pipeline()
                for key in session_keys:
                    pipe.hgetall(key)
                
                all_data = pipe.execute()
                
                # 准备索引数据
                for key, data in zip(session_keys, all_data):
                    if not data:
                        continue
                    
                    session_id = key.split(':')[-1]
                    
                    # 检查是否已有向量
                    existing_vector = vector_search.get_vector(session_id, session_type)
                    if existing_vector:
                        continue
                    
                    sessions_to_index.append({
                        'session_id': session_id,
                        'end_user_id': data.get('end_user_id', data.get('sessionid', '')),
                        'messages': data.get('messages', ''),
                        'aimessages': data.get('aimessages', ''),
                        'metadata': {}
                    })
                    
                    # 达到批处理大小，开始索引
                    if len(sessions_to_index) >= batch_size:
                        count = await self.batch_add_sessions_with_vectors(
                            sessions_to_index,
                            session_type,
                            batch_size
                        )
                        indexed_count += count
                        sessions_to_index = []
                        
                        logger.info(f"已索引 {indexed_count} 个会话")
                        
                        # 检查是否达到限制
                        if limit and indexed_count >= limit:
                            break
                
                if cursor == 0 or (limit and indexed_count >= limit):
                    break
            
            # 处理剩余的会话
            if sessions_to_index:
                count = await self.batch_add_sessions_with_vectors(
                    sessions_to_index,
                    session_type,
                    batch_size
                )
                indexed_count += count
            
            logger.info(f"批量索引完成，共索引 {indexed_count} 个会话")
            return indexed_count
            
        except Exception as e:
            logger.error(f"批量索引失败: {e}")
            return 0


def create_semantic_search(db: Session, embedding_model_id: str) -> RedisSemanticSearch:
    """
    创建语义搜索实例的工厂函数
    
    Args:
        db: 数据库会话
        embedding_model_id: Embedding 模型ID
        
    Returns:
        RedisSemanticSearch: 语义搜索实例
    """
    return RedisSemanticSearch(db, embedding_model_id)
