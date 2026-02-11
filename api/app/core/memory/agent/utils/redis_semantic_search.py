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
        添加会话并自动生成向量（带去重检查）
        
        Args:
            end_user_id: 用户ID
            messages: 用户消息
            aimessages: AI回复
            session_type: 会话类型
            metadata: 元数据
            
        Returns:
            Dict: 包含 session_id、success 状态和 is_duplicate 标志
        """
        try:
            # 检查 vector_search 是否可用
            if not REDISEARCH_AVAILABLE or vector_search is None:
                logger.error("RediSearch 不可用，无法添加向量")
                return {"success": False, "session_id": None, "error": "RediSearch 不可用", "is_duplicate": False}
            
            # 1. 检查是否已存在相同的会话（去重）
            existing_session = await self._find_duplicate_session(
                end_user_id=end_user_id,
                messages=messages,
                aimessages=aimessages,
                session_type=session_type
            )
            
            if existing_session:
                logger.info(f"会话已存在，跳过添加: {existing_session['session_id']}")
                return {
                    "success": True,
                    "session_id": existing_session['session_id'],
                    "is_duplicate": True,
                    "message": "会话已存在，未重复添加"
                }
            
            # 2. 自动生成 session_id
            import uuid
            session_id = str(uuid.uuid4())
            
            # 3. 生成向量（使用消息和回复的组合）
            text = f"{messages} {aimessages}".strip()
            vector = await self.get_embedding(text)

            
            if not vector:
                logger.error("生成向量失败")
                return {"success": False, "session_id": session_id, "error": "生成向量失败", "is_duplicate": False}
            
            # 4. 自动调整 vector_search 的维度（如果需要）
            if len(vector) != vector_search.VECTOR_DIM:
                logger.info(f"自动调整向量维度: {vector_search.VECTOR_DIM} -> {len(vector)}")
                vector_search.VECTOR_DIM = len(vector)
            
            # 5. 添加到 Redis
            result = vector_search.add_vector(
                session_id=session_id,
                vector=vector,
                end_user_id=end_user_id,
                messages=messages,
                aimessages=aimessages,
                session_type=session_type,
                metadata=metadata
            )
            
            # 6. 同时建立关键词索引
            if keyword_search:
                keywords = keyword_search.extract_keywords(text)
                if keywords:
                    keyword_search.add_keyword_index(session_id, keywords, session_type)
            
            logger.info(f"成功添加会话向量: {session_id}")
            return {"success": True, "session_id": session_id, "is_duplicate": False}
            
        except Exception as e:
            logger.error(f"添加会话向量失败: {e}")
            return {"success": False, "session_id": None, "error": str(e), "is_duplicate": False}
    
    async def _find_duplicate_session(self,
                                     end_user_id: str,
                                     messages: str,
                                     aimessages: str,
                                     session_type: str = "read") -> Optional[Dict[str, Any]]:
        """
        查找是否存在重复的会话
        
        Args:
            end_user_id: 用户ID
            messages: 用户消息
            aimessages: AI回复
            session_type: 会话类型
            
        Returns:
            Dict: 如果找到重复会话，返回会话数据；否则返回 None
        """
        try:
            # 确定索引名称和前缀
            if session_type == "read":
                index_name = vector_search.INDEX_NAME_READ
                prefix = "vector:read:"
            elif session_type == "write":
                index_name = vector_search.INDEX_NAME_WRITE
                prefix = "vector:write:"
            elif session_type == "count":
                index_name = vector_search.INDEX_NAME_COUNT
                prefix = "vector:count:"
            else:
                return None
            
            # 检查索引是否存在
            try:
                vector_search.r.ft(index_name).info()
            except:
                # 索引不存在，说明没有任何数据
                return None
            
            # 使用 Redis 搜索查找完全匹配的会话
            # 转义特殊字符
            def escape_redis_query(text: str) -> str:
                """转义 Redis 查询中的特殊字符"""
                special_chars = ['@', '-', '(', ')', '{', '}', '[', ']', '"', '~', '*', ':', '\\', '|', '!', '^']
                for char in special_chars:
                    text = text.replace(char, f'\\{char}')
                return text
            
            escaped_end_user_id = escape_redis_query(end_user_id)
            escaped_messages = escape_redis_query(messages)
            escaped_aimessages = escape_redis_query(aimessages) if aimessages else ""
            
            # 构建查询
            if aimessages:
                query_str = f"@end_user_id:{escaped_end_user_id} @messages:{escaped_messages} @aimessages:{escaped_aimessages}"
            else:
                query_str = f"@end_user_id:{escaped_end_user_id} @messages:{escaped_messages}"
            
            from redis.commands.search.query import Query
            query = Query(query_str).return_fields("end_user_id", "messages", "aimessages", "timestamp", "created_at")
            
            # 执行搜索
            results = vector_search.r.ft(index_name).search(query)
            
            # 检查结果是否完全匹配
            for doc in results.docs:
                doc_end_user_id = doc.end_user_id if hasattr(doc, 'end_user_id') else ''
                doc_messages = doc.messages if hasattr(doc, 'messages') else ''
                doc_aimessages = doc.aimessages if hasattr(doc, 'aimessages') else ''
                
                # 完全匹配检查
                if (doc_end_user_id == end_user_id and 
                    doc_messages == messages and 
                    doc_aimessages == aimessages):
                    
                    # 从 doc.id 中提取 session_id
                    doc_id = doc.id if hasattr(doc, 'id') else ''
                    session_id = doc_id.split(':')[-1] if ':' in doc_id else doc_id
                    
                    logger.info(f"找到重复会话: {session_id}")
                    return {
                        'session_id': session_id,
                        'end_user_id': doc_end_user_id,
                        'messages': doc_messages,
                        'aimessages': doc_aimessages,
                        'timestamp': int(doc.timestamp) if hasattr(doc, 'timestamp') else 0,
                        'created_at': doc.created_at if hasattr(doc, 'created_at') else ''
                    }
            
            return None
            
        except Exception as e:
            logger.warning(f"查找重复会话时出错: {e}")
            # 出错时返回 None，允许继续添加
            return None
    
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
                                    vector_weight: float = 0.6,
                                    keyword_weight: float = 0.4,
                                    score_threshold: float = 0.3,
                                    candidate_multiplier: int = 3) -> List[Dict[str, Any]]:
        """
        混合语义搜索（向量搜索 + 关键词模糊搜索）
        
        结合向量语义搜索和关键词模糊搜索的优势：
        - 向量搜索：捕捉语义相似性
        - 关键词搜索：确保关键词匹配
        
        Args:
            query: 查询文本
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            vector_weight: 向量搜索权重（默认 0.6）
            keyword_weight: 关键词搜索权重（默认 0.4）
            score_threshold: 混合分数阈值（0-1）
            candidate_multiplier: 候选结果倍数（获取 top_k * multiplier 个候选）
            
        Returns:
            List[Dict]: 搜索结果，按混合分数降序排序
        """
        try:
            logger.info(f"开始混合语义搜索: query='{query}', top_k={top_k}")
            
            # 计算候选数量
            candidate_count = top_k * candidate_multiplier
            
            # 1. 向量语义搜索
            vector_results = []
            if self._embedder_client:
                try:
                    query_vector = await self.get_embedding(query)
                    
                    if query_vector and REDISEARCH_AVAILABLE and vector_search:
                        vector_results = vector_search.vector_search(
                            query_vector=query_vector,
                            session_type=session_type,
                            top_k=candidate_count,
                            end_user_id=end_user_id,
                            score_threshold=0.0  # 不在这里过滤，后面统一过滤
                        )
                        logger.info(f"向量搜索找到 {len(vector_results)} 个结果")
                    else:
                        logger.warning("向量搜索不可用")
                except Exception as e:
                    logger.warning(f"向量搜索失败: {e}")
            else:
                logger.warning("Embedder 客户端未初始化，跳过向量搜索")
            
            # 2. 关键词模糊搜索
            keyword_results = []
            try:
                keyword_results = self.keyword_fuzzy_search(
                    query_text=query,
                    session_type=session_type,
                    top_k=candidate_count,
                    end_user_id=end_user_id
                )
                logger.info(f"关键词搜索找到 {len(keyword_results)} 个结果")
            except Exception as e:
                logger.warning(f"关键词搜索失败: {e}")
            
            # 3. 合并结果并计算混合分数
            session_scores = {}
            print(keyword_results)
            # 处理向量搜索结果
            for result in vector_results:
                session_id = result['session_id']
                # 向量搜索返回的 similarity 是相似度（0-1，越大越好）
                vector_score = result.get('similarity', 0.0)
                
                session_scores[session_id] = {
                    'vector_score': vector_score,
                    'keyword_score': 0.0,
                    'data': result
                }
            
            # 处理关键词搜索结果
            for result in keyword_results:
                session_id = result['session_id']
                # 关键词搜索返回的 match_score 是匹配分数（0-1，越大越好）
                keyword_score = result.get('match_score', 0.0)
                
                if session_id in session_scores:
                    # 已存在，更新关键词分数
                    session_scores[session_id]['keyword_score'] = keyword_score
                    # 合并 matched_keywords 信息
                    session_scores[session_id]['data']['matched_keywords'] = result.get('matched_keywords', [])
                    session_scores[session_id]['data']['total_keywords'] = result.get('total_keywords', 0)
                else:
                    # 新结果，添加
                    session_scores[session_id] = {
                        'vector_score': 0.0,
                        'keyword_score': keyword_score,
                        'data': result
                    }
            
            # 4. 计算混合分数并构建最终结果
            hybrid_results = []
            for session_id, scores in session_scores.items():
                # 计算加权混合分数
                hybrid_score = (
                    scores['vector_score'] * vector_weight +
                    scores['keyword_score'] * keyword_weight
                )
                
                # 应用阈值过滤
                if hybrid_score < score_threshold:
                    continue
                
                # 构建结果
                result = scores['data'].copy()
                result['hybrid_score'] = hybrid_score
                result['vector_score'] = scores['vector_score']
                result['keyword_score'] = scores['keyword_score']
                
                # 确保包含所有必要字段
                if 'matched_keywords' not in result:
                    result['matched_keywords'] = []
                if 'total_keywords' not in result:
                    result['total_keywords'] = 0
                
                hybrid_results.append(result)
            
            # 5. 按混合分数排序
            hybrid_results.sort(key=lambda x: x['hybrid_score'], reverse=True)
            
            # 6. 返回前 top_k 个结果
            final_results = hybrid_results[:top_k]
            
            logger.info(f"混合语义搜索完成，返回 {len(final_results)} 个结果")
            
            # 记录详细信息（调试用）
            if final_results:
                logger.debug(f"Top 3 结果:")
                for i, r in enumerate(final_results[:3], 1):
                    logger.debug(
                        f"  {i}. session_id={r['session_id']}, "
                        f"hybrid={r['hybrid_score']:.3f}, "
                        f"vector={r['vector_score']:.3f}, "
                        f"keyword={r['keyword_score']:.3f}, "
                        f"matched_kw={r.get('matched_keywords', [])}"
                    )
            
            return final_results
            
        except Exception as e:
            logger.error(f"混合语义搜索失败: {e}")
            import traceback
            traceback.print_exc()
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
    
    # ==================== 关键词模糊搜索 ====================
    
    def keyword_fuzzy_search(self,
                            query_text: str,
                            session_type: str = "read",
                            top_k: int = 10,
                            end_user_id: Optional[str] = None,
                            search_fields: List[str] = None) -> List[Dict[str, Any]]:
        """
        基于关键词的模糊搜索
        
        先提取查询文本的关键词，然后对每个关键词在 messages 和 aimessages 字段中进行模糊匹配
        
        Args:
            query_text: 查询文本
            session_type: 会话类型 ("read", "write", "count")
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤（可选）
            search_fields: 要搜索的字段列表，默认为 ["messages", "aimessages"]
            
        Returns:
            List[Dict]: 搜索结果列表
        """
        try:
            # 检查 vector_search 是否可用
            if not REDISEARCH_AVAILABLE or vector_search is None:
                logger.warning("RediSearch 不可用，无法进行关键词搜索")
                return []
            
            # 确定索引名称
            if session_type == "read":
                index_name = vector_search.INDEX_NAME_READ
            elif session_type == "write":
                index_name = vector_search.INDEX_NAME_WRITE
            elif session_type == "count":
                index_name = vector_search.INDEX_NAME_COUNT
            else:
                logger.error(f"不支持的会话类型: {session_type}")
                return []
            
            # 检查索引是否存在
            try:
                vector_search.r.ft(index_name).info()
            except Exception as e:
                logger.error(f"索引 {index_name} 不存在: {e}")
                return []
            
            # 1. 提取关键词
            if keyword_search:
                keywords = keyword_search.extract_keywords(query_text)
            else:
                # 如果关键词提取器不可用，使用简单的分词
                keywords = query_text.split()
            
            if not keywords:
                logger.warning("未提取到关键词")
                return []
            
            logger.info(f"提取到关键词: {keywords}")
            
            # 2. 确定搜索字段
            if search_fields is None:
                search_fields = ["messages", "aimessages"]
            
            # 3. 构建查询字符串（对每个关键词进行模糊匹配）
            query_parts = []
            
            for keyword in keywords:
                # 转义特殊字符
                escaped_keyword = self._escape_redis_query(keyword)
                
                # 为每个字段构建模糊查询
                field_queries = []
                for field in search_fields:
                    # 使用通配符进行模糊匹配
                    field_queries.append(f"@{field}:*{escaped_keyword}*")
                
                # 组合字段查询（OR 关系）
                if field_queries:
                    query_parts.append(f"({' | '.join(field_queries)})")
            
            # 组合所有关键词查询（OR 关系，匹配任意关键词）
            if not query_parts:
                logger.warning("未生成有效的查询")
                return []
            
            query_str = " | ".join(query_parts)
            
            # 如果指定了用户ID，添加过滤条件
            if end_user_id:
                escaped_user_id = self._escape_redis_query(end_user_id)
                query_str = f"(@end_user_id:{escaped_user_id}) ({query_str})"
            
            logger.info(f"查询字符串: {query_str}")
            
            # 4. 执行搜索
            from redis.commands.search.query import Query
            
            query = (
                Query(query_str)
                .paging(0, top_k)
                .return_fields("end_user_id", "messages", "aimessages", "timestamp", "created_time", "created_at")
            )
            
            results = vector_search.r.ft(index_name).search(query)
            
            # 5. 解析结果
            parsed_results = []
            for doc in results.docs:
                # 从 doc.id 中提取 session_id
                doc_id = doc.id if hasattr(doc, 'id') else ''
                session_id = doc_id.split(':')[-1] if ':' in doc_id else doc_id
                
                # 计算匹配的关键词数量（用于排序）
                messages = doc.messages if hasattr(doc, 'messages') else ''
                aimessages = doc.aimessages if hasattr(doc, 'aimessages') else ''
                combined_text = f"{messages} {aimessages}".lower()
                
                matched_keywords = [kw for kw in keywords if kw.lower() in combined_text]
                match_score = len(matched_keywords) / len(keywords) if keywords else 0
                
                parsed_results.append({
                    'session_id': session_id,
                    'end_user_id': doc.end_user_id if hasattr(doc, 'end_user_id') else '',
                    'messages': messages,
                    'aimessages': aimessages,
                    'timestamp': int(doc.timestamp) if hasattr(doc, 'timestamp') else 0,
                    'created_time': int(doc.created_time) if hasattr(doc, 'created_time') else 0,
                    'created_at': doc.created_at if hasattr(doc, 'created_at') else '',
                    'matched_keywords': matched_keywords,
                    'match_score': match_score,
                    'total_keywords': len(keywords)
                })
            
            # 6. 按匹配分数排序
            parsed_results.sort(key=lambda x: x['match_score'], reverse=True)
            
            logger.info(f"关键词模糊搜索找到 {len(parsed_results)} 个结果")
            return parsed_results
            
        except Exception as e:
            logger.error(f"关键词模糊搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def _escape_redis_query(self, text: str) -> str:
        """
        转义 Redis 查询中的特殊字符
        
        Args:
            text: 要转义的文本
            
        Returns:
            str: 转义后的文本
        """
        # Redis 查询中的特殊字符
        special_chars = ['@', '-', '(', ')', '{', '}', '[', ']', '"', '~', '*', ':', '\\', '|', '!', '^', '.', ',', '<', '>', '&', '%', '$', '#']
        
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        
        return text
    
    def simple_text_search(self,
                          query_text: str,
                          session_type: str = "read",
                          top_k: int = 10,
                          end_user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        简单的全文搜索（不提取关键词，直接搜索原文）
        
        Args:
            query_text: 查询文本
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            
        Returns:
            List[Dict]: 搜索结果列表
        """
        try:
            # 检查 vector_search 是否可用
            if not REDISEARCH_AVAILABLE or vector_search is None:
                logger.warning("RediSearch 不可用，无法进行文本搜索")
                return []
            
            # 确定索引名称
            if session_type == "read":
                index_name = vector_search.INDEX_NAME_READ
            elif session_type == "write":
                index_name = vector_search.INDEX_NAME_WRITE
            elif session_type == "count":
                index_name = vector_search.INDEX_NAME_COUNT
            else:
                logger.error(f"不支持的会话类型: {session_type}")
                return []
            
            # 检查索引是否存在
            try:
                vector_search.r.ft(index_name).info()
            except Exception as e:
                logger.error(f"索引 {index_name} 不存在: {e}")
                return []
            
            # 构建查询
            escaped_text = self._escape_redis_query(query_text)
            query_str = f"(@messages:*{escaped_text}*) | (@aimessages:*{escaped_text}*)"
            
            # 如果指定了用户ID，添加过滤条件
            if end_user_id:
                escaped_user_id = self._escape_redis_query(end_user_id)
                query_str = f"(@end_user_id:{escaped_user_id}) ({query_str})"
            
            logger.info(f"查询字符串: {query_str}")
            
            # 执行搜索
            from redis.commands.search.query import Query
            
            query = (
                Query(query_str)
                .paging(0, top_k)
                .return_fields("end_user_id", "messages", "aimessages", "timestamp", "created_time", "created_at")
            )
            
            results = vector_search.r.ft(index_name).search(query)
            
            # 解析结果
            parsed_results = []
            for doc in results.docs:
                doc_id = doc.id if hasattr(doc, 'id') else ''
                session_id = doc_id.split(':')[-1] if ':' in doc_id else doc_id
                
                parsed_results.append({
                    'session_id': session_id,
                    'end_user_id': doc.end_user_id if hasattr(doc, 'end_user_id') else '',
                    'messages': doc.messages if hasattr(doc, 'messages') else '',
                    'aimessages': doc.aimessages if hasattr(doc, 'aimessages') else '',
                    'timestamp': int(doc.timestamp) if hasattr(doc, 'timestamp') else 0,
                    'created_time': int(doc.created_time) if hasattr(doc, 'created_time') else 0,
                    'created_at': doc.created_at if hasattr(doc, 'created_at') else ''
                })
            
            logger.info(f"简单文本搜索找到 {len(parsed_results)} 个结果")
            return parsed_results
            
        except Exception as e:
            logger.error(f"简单文本搜索失败: {e}")
            import traceback
            traceback.print_exc()
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
