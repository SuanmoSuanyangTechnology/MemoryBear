"""
Redis 向量检索模块

提供基于 Redis 的语义向量搜索功能，支持：
1. 向量索引管理（使用 Redis Vector Similarity Search）
2. 语义相似度搜索
3. 混合搜索（关键词 + 向量）
4. 向量存储和检索
"""

import json
import uuid
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

import redis
from redis.commands.search.field import TextField, NumericField, TagField, VectorField

from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from app.core.config import settings
from app.core.memory.agent.utils.redis_base import (
    fix_encoding,
    get_current_timestamp,
)

# 全局变量：标记 RediSearch 是否可用
REDISEARCH_AVAILABLE = False


class RedisVectorSearch:
    """Redis 向量搜索类"""
    
    # 索引名称
    INDEX_NAME_READ = "idx:vector:read"
    INDEX_NAME_WRITE = "idx:vector:write"
    INDEX_NAME_COUNT = "idx:vector:count"
    
    # 向量维度（根据实际使用的 embedding 模型调整）
    VECTOR_DIM = 1536  # OpenAI text-embedding-ada-002 的维度
    
    def __init__(self, host='localhost', port=6379, db=0, password=None, vector_dim: int = None):
        """
        初始化 Redis 连接
        
        Args:
            host: Redis 主机地址
            port: Redis 端口
            db: Redis 数据库编号
            password: Redis 密码
            vector_dim: 向量维度（默认 1536）
        """
        self.r = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=False  # 向量数据需要二进制模式
        )
        
        if vector_dim:
            self.VECTOR_DIM = vector_dim
        
        # 检查 Redis 是否支持 RediSearch
        self._check_redisearch_support()
    
    def _check_redisearch_support(self) -> bool:
        """检查 Redis 是否支持 RediSearch 模块"""
        global REDISEARCH_AVAILABLE
        
        try:
            modules = self.r.module_list()
            # 检查是否有 search 或 vectorset 模块
            has_search = any(m[b'name'] in [b'search', b'vectorset'] for m in modules)
            
            if not has_search:
                print("[WARNING] RediSearch 或 VectorSet 模块未安装在 Redis 服务器上")
                print("请安装 RediSearch: https://redis.io/docs/stack/search/")
                REDISEARCH_AVAILABLE = False
                return False
            
            # 显示找到的模块
            for module in modules:
                module_name = module[b'name'].decode('utf-8')
                if module_name in ['search', 'vectorset']:
                    print(f"[INFO] 找到 Redis 模块: {module_name}")
            
            REDISEARCH_AVAILABLE = True
            return True
        except Exception as e:
            print(f"[WARNING] 无法检查 RediSearch 支持: {e}")
            REDISEARCH_AVAILABLE = False
            return False
    
    # ==================== 索引管理 ====================
    
    def create_vector_index(self, session_type: str = "read", 
                           distance_metric: str = "COSINE") -> bool:
        """
        创建向量索引
        
        Args:
            session_type: 会话类型 ("read", "write", "count")
            distance_metric: 距离度量 ("COSINE", "L2", "IP")
            
        Returns:
            bool: 是否创建成功
        """
        try:
            # 确定索引名称和前缀
            if session_type == "read":
                index_name = self.INDEX_NAME_READ
                prefix = "vector:read:"
            elif session_type == "write":
                index_name = self.INDEX_NAME_WRITE
                prefix = "vector:write:"
            elif session_type == "count":
                index_name = self.INDEX_NAME_COUNT
                prefix = "vector:count:"
            else:
                raise ValueError(f"不支持的会话类型: {session_type}")
            
            # 检查索引是否已存在
            try:
                self.r.ft(index_name).info()
                print(f"[create_vector_index] 索引 {index_name} 已存在")
                return True
            except:
                pass  # 索引不存在，继续创建
            
            # 定义索引字段
            schema = (
                TextField("end_user_id"),
                TextField("messages"),
                TextField("aimessages"),
                NumericField("timestamp"),
                NumericField("created_time"),
                TextField("created_at"),  # 可读的时间字符串
                TagField("session_type"),
                VectorField(
                    "vector",
                    "HNSW",  # 或 "HNSW" 用于大规模数据
                    {
                        "TYPE": "FLOAT32",
                        "DIM": self.VECTOR_DIM,
                        "DISTANCE_METRIC": distance_metric,
                    }
                )
            )
            
            # 创建索引
            definition = IndexDefinition(
                prefix=[prefix],
                index_type=IndexType.HASH
            )
            
            self.r.ft(index_name).create_index(
                fields=schema,
                definition=definition
            )
            
            print(f"[create_vector_index] 成功创建索引: {index_name}")
            return True
            
        except Exception as e:
            print(f"[create_vector_index] 创建索引失败: {e}")
            return False
    
    def drop_vector_index(self, session_type: str = "read") -> bool:
        """
        删除向量索引
        
        Args:
            session_type: 会话类型
            
        Returns:
            bool: 是否删除成功
        """
        try:
            if session_type == "read":
                index_name = self.INDEX_NAME_READ
            elif session_type == "write":
                index_name = self.INDEX_NAME_WRITE
            elif session_type == "count":
                index_name = self.INDEX_NAME_COUNT
            else:
                raise ValueError(f"不支持的会话类型: {session_type}")
            
            self.r.ft(index_name).dropindex(delete_documents=False)
            print(f"[drop_vector_index] 成功删除索引: {index_name}")
            return True
            
        except Exception as e:
            print(f"[drop_vector_index] 删除索引失败: {e}")
            return False
    
    def get_index_info(self, session_type: str = "read") -> Optional[Dict]:
        """
        获取索引信息
        
        Args:
            session_type: 会话类型
            
        Returns:
            Dict: 索引信息
        """
        try:
            if session_type == "read":
                index_name = self.INDEX_NAME_READ
            elif session_type == "write":
                index_name = self.INDEX_NAME_WRITE
            elif session_type == "count":
                index_name = self.INDEX_NAME_COUNT
            else:
                return None
            
            info = self.r.ft(index_name).info()
            return info
            
        except Exception as e:
            print(f"[get_index_info] 获取索引信息失败: {e}")
            return None
    
    # ==================== 向量存储 ====================
    
    def add_vector(self, 
                   session_id: str,
                   vector: List[float],
                   end_user_id: str,
                   messages: str,
                   aimessages: str = "",
                   session_type: str = "read",
                   metadata: Dict[str, Any] = None) -> bool:
        """
        添加向量到 Redis
        
        Args:
            session_id: 会话ID
            vector: 向量数据
            end_user_id: 用户ID
            messages: 用户消息
            aimessages: AI回复
            session_type: 会话类型
            metadata: 额外的元数据
            
        Returns:
            bool: 是否添加成功
        """
        try:
            # 自动调整向量维度（如果是第一次添加向量）
            actual_dim = len(vector)
            if actual_dim != self.VECTOR_DIM:
                print(f"[add_vector] 检测到向量维度: {actual_dim}, 当前配置: {self.VECTOR_DIM}")
                print(f"[add_vector] 自动调整向量维度为: {actual_dim}")
                self.VECTOR_DIM = actual_dim
            
            # 确定索引名称和 key 前缀
            if session_type == "read":
                index_name = self.INDEX_NAME_READ
                prefix = "vector:read:"
            elif session_type == "write":
                index_name = self.INDEX_NAME_WRITE
                prefix = "vector:write:"
            elif session_type == "count":
                index_name = self.INDEX_NAME_COUNT
                prefix = "vector:count:"
            else:
                raise ValueError(f"不支持的会话类型: {session_type}")
            
            # 检查索引是否存在，并验证维度
            index_exists = False
            index_dim_matches = False
            try:
                index_info = self.r.ft(index_name).info()
                index_exists = True
                
                # 检查索引的向量维度
                attributes = index_info.get('attributes', [])
                for attr in attributes:
                    if isinstance(attr, list) and len(attr) > 0:
                        # 查找 vector 字段
                        if attr[1] == b'vector':
                            # 查找 dim 参数
                            for i, item in enumerate(attr):
                                if item == b'dim' and i + 1 < len(attr):
                                    index_dim = int(attr[i + 1])
                                    if index_dim == actual_dim:
                                        index_dim_matches = True
                                    else:
                                        print(f"[add_vector] 索引维度 ({index_dim}) 与向量维度 ({actual_dim}) 不匹配")
                                    break
                            break
            except:
                pass
            
            # 如果索引不存在或维度不匹配，重新创建索引
            if not index_exists or not index_dim_matches:
                if index_exists:
                    print(f"[add_vector] 删除旧索引（维度不匹配）...")
                    try:
                        self.r.ft(index_name).dropindex(delete_documents=False)
                    except:
                        pass
                
                print(f"[add_vector] 创建新索引（维度: {actual_dim}）...")
                if not self.create_vector_index(session_type=session_type):
                    print(f"[add_vector] 创建索引失败")
                    return False
            
            key = f"{prefix}{session_id}"
            
            # 将向量转换为字节
            vector_bytes = np.array(vector, dtype=np.float32).tobytes()
            
            # 获取当前时间
            current_time = int(datetime.now().timestamp())
            current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 准备数据
            data = {
                "end_user_id": end_user_id,
                "messages": messages,
                "aimessages": aimessages,
                "timestamp": current_time,
                "created_time": current_time,
                "created_at": current_time_str,  # 可读的时间字符串
                "session_type": session_type,
                "vector": vector_bytes
            }
            
            # 添加元数据
            if metadata:
                data["metadata"] = json.dumps(metadata, ensure_ascii=False)
            
            # 存储到 Redis
            self.r.hset(key, mapping=data)
            
            # 设置过期时间（30天）
            self.r.expire(key, 30 * 24 * 60 * 60)
            
            print(f"[add_vector] 成功添加向量: {session_id}")
            return True
            
        except Exception as e:
            print(f"[add_vector] 添加向量失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_vector(self, session_id: str, session_type: str = "read") -> Optional[Dict[str, Any]]:
        """
        获取向量数据
        
        Args:
            session_id: 会话ID
            session_type: 会话类型
            
        Returns:
            Dict: 向量数据
        """
        try:
            if session_type == "read":
                prefix = "vector:read:"
            elif session_type == "write":
                prefix = "vector:write:"
            elif session_type == "count":
                prefix = "vector:count:"
            else:
                return None
            
            key = f"{prefix}{session_id}"
            data = self.r.hgetall(key)
            
            if not data:
                return None
            
            # 解析向量
            vector_bytes = data.get(b'vector')
            if vector_bytes:
                vector = np.frombuffer(vector_bytes, dtype=np.float32).tolist()
            else:
                vector = None
            
            # 解析元数据
            metadata_str = data.get(b'metadata')
            if metadata_str:
                metadata = json.loads(metadata_str.decode('utf-8'))
            else:
                metadata = {}
            
            return {
                'session_id': session_id,  # 从 key 中提取
                'end_user_id': data.get(b'end_user_id', b'').decode('utf-8'),
                'messages': data.get(b'messages', b'').decode('utf-8'),
                'aimessages': data.get(b'aimessages', b'').decode('utf-8'),
                'timestamp': int(data.get(b'timestamp', 0)),
                'created_time': int(data.get(b'created_time', 0)),
                'created_at': data.get(b'created_at', b'').decode('utf-8'),
                'session_type': data.get(b'session_type', b'').decode('utf-8'),
                'vector': vector,
                'metadata': metadata
            }
            
        except Exception as e:
            print(f"[get_vector] 获取向量失败: {e}")
            return None
    
    def delete_vector(self, session_id: str, session_type: str = "read") -> bool:
        """
        删除向量
        
        Args:
            session_id: 会话ID
            session_type: 会话类型
            
        Returns:
            bool: 是否删除成功
        """
        try:
            if session_type == "read":
                prefix = "vector:read:"
            elif session_type == "write":
                prefix = "vector:write:"
            elif session_type == "count":
                prefix = "vector:count:"
            else:
                return False
            
            key = f"{prefix}{session_id}"
            result = self.r.delete(key)
            
            print(f"[delete_vector] 删除向量: {session_id}, 结果: {result}")
            return result > 0
            
        except Exception as e:
            print(f"[delete_vector] 删除向量失败: {e}")
            return False
    
    # ==================== 向量搜索 ====================
    
    def vector_search(self,
                     query_vector: List[float],
                     session_type: str = "read",
                     top_k: int = 10,
                     end_user_id: Optional[str] = None,
                     score_threshold: float = 0.0) -> List[Dict[str, Any]]:
        """
        向量相似度搜索
        
        Args:
            query_vector: 查询向量
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤（可选）
            score_threshold: 相似度阈值（0-1）
            
        Returns:
            List[Dict]: 搜索结果列表
        """
        try:
            # 自动调整向量维度
            actual_dim = len(query_vector)
            if actual_dim != self.VECTOR_DIM:
                print(f"[vector_search] 检测到查询向量维度: {actual_dim}, 当前配置: {self.VECTOR_DIM}")
                print(f"[vector_search] 自动调整向量维度为: {actual_dim}")
                self.VECTOR_DIM = actual_dim
            
            # 确定索引名称
            if session_type == "read":
                index_name = self.INDEX_NAME_READ
            elif session_type == "write":
                index_name = self.INDEX_NAME_WRITE
            elif session_type == "count":
                index_name = self.INDEX_NAME_COUNT
            else:
                raise ValueError(f"不支持的会话类型: {session_type}")
            
            # 检查索引是否存在，并验证维度
            index_exists = False
            index_dim_matches = False

            try:
                index_info = self.r.ft(index_name).info()
                index_exists = True
                
                # 检查索引的向量维度
                attributes = index_info.get('attributes', [])
                for attr in attributes:
                    if isinstance(attr, list) and len(attr) > 0:
                        # 查找 vector 字段
                        if attr[1] == b'vector':
                            # 查找 dim 参数
                            for i, item in enumerate(attr):
                                if item == b'dim' and i + 1 < len(attr):
                                    index_dim = int(attr[i + 1])
                                    if index_dim == actual_dim:
                                        index_dim_matches = True
                                    else:
                                        print(f"[vector_search] 警告：索引维度 ({index_dim}) 与查询向量维度 ({actual_dim}) 不匹配")
                                        print(f"[vector_search] 需要重新创建索引或使用正确维度的向量")
                                    break
                            break
            except:
                pass
            
            # 如果索引不存在，创建它
            if not index_exists:
                print(f"[vector_search] 索引 {index_name} 不存在，正在创建...")
                if not self.create_vector_index(session_type=session_type):
                    print(f"[vector_search] 创建索引失败，返回空结果")
                    return []
            elif not index_dim_matches:
                print(f"[vector_search] 索引维度不匹配，无法搜索。请重新创建索引或使用正确维度的向量。")
                return []
            
            # 将查询向量转换为字节
            query_vector_bytes = np.array(query_vector, dtype=np.float32).tobytes()
            
            # 构建查询
            base_query = "*"
            if end_user_id:
                base_query = f"@end_user_id:{end_user_id}"
            
            # KNN 查询
            query = (
                Query(f"({base_query})=>[KNN {top_k} @vector $vec AS score]")
                .sort_by("score")
                .return_fields("session_id", "end_user_id", "messages", "aimessages", "timestamp", "created_time", "created_at", "score")
                .dialect(2)
            )
            
            # 执行搜索
            results = self.r.ft(index_name).search(
                query,
                query_params={"vec": query_vector_bytes}
            )
            
            # 解析结果
            parsed_results = []
            for doc in results.docs:
                score = float(doc.score) if hasattr(doc, 'score') else 0.0
                
                # 应用相似度阈值
                if score < score_threshold:
                    continue
                
                # 从 doc.id 中提取 session_id (格式: vector:read:session_id)
                doc_id = doc.id if hasattr(doc, 'id') else ''
                session_id = doc_id.split(':')[-1] if ':' in doc_id else doc_id
                
                parsed_results.append({
                    'session_id': session_id,
                    'end_user_id': doc.end_user_id,
                    'messages': doc.messages,
                    'aimessages': doc.aimessages,
                    'timestamp': int(doc.timestamp) if hasattr(doc, 'timestamp') else 0,
                    'created_time': int(doc.created_time) if hasattr(doc, 'created_time') else 0,
                    'created_at': doc.created_at if hasattr(doc, 'created_at') else '',
                    'score': score,
                    'similarity': 1 - score  # COSINE 距离转相似度
                })
            
            print(f"[vector_search] 找到 {len(parsed_results)} 个相似结果")
            return parsed_results
            
        except Exception as e:
            print(f"[vector_search] 向量搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def hybrid_search(self,
                     query_vector: List[float],
                     keywords: List[str],
                     session_type: str = "read",
                     top_k: int = 10,
                     end_user_id: Optional[str] = None,
                     vector_weight: float = 0.7,
                     keyword_weight: float = 0.3) -> List[Dict[str, Any]]:
        """
        混合搜索（向量 + 关键词）
        
        Args:
            query_vector: 查询向量
            keywords: 关键词列表
            session_type: 会话类型
            top_k: 返回前 K 个结果
            end_user_id: 用户ID过滤
            vector_weight: 向量搜索权重
            keyword_weight: 关键词搜索权重
            
        Returns:
            List[Dict]: 搜索结果列表
        """
        try:
            # 1. 向量搜索
            vector_results = self.vector_search(
                query_vector=query_vector,
                session_type=session_type,
                top_k=top_k * 2,  # 获取更多候选
                end_user_id=end_user_id
            )
            
            # 2. 关键词匹配
            from app.core.memory.agent.utils.redis_keyword_search import keyword_search
            
            if keywords:
                keyword_session_ids = keyword_search.search_by_keywords_or(
                    keywords=keywords,
                    session_type=session_type,
                    limit=top_k * 2
                )
            else:
                keyword_session_ids = []
            
            # 3. 合并结果并计算混合分数
            session_scores = {}
            
            # 向量搜索结果
            for result in vector_results:
                session_id = result['session_id']
                vector_score = result['similarity']
                session_scores[session_id] = {
                    'vector_score': vector_score,
                    'keyword_score': 0.0,
                    'data': result
                }
            
            # 关键词匹配结果
            for session_id in keyword_session_ids:
                if session_id in session_scores:
                    session_scores[session_id]['keyword_score'] = 1.0
                else:
                    # 获取会话数据
                    from app.core.memory.agent.utils.redis_tool import store
                    from app.core.memory.agent.utils.redis_base import generate_session_key
                    
                    key = generate_session_key(session_id, key_type=session_type)
                    data = store.r.hgetall(key)
                    
                    if data:
                        session_scores[session_id] = {
                            'vector_score': 0.0,
                            'keyword_score': 1.0,
                            'data': {
                                'session_id': session_id,
                                'end_user_id': data.get('end_user_id', data.get('sessionid', '')),
                                'messages': fix_encoding(data.get('messages', '')),
                                'aimessages': fix_encoding(data.get('aimessages', '')),
                                'timestamp': int(data.get('timestamp', 0)),
                                'created_time': int(data.get('created_time', 0)),
                                'created_at': data.get('created_at', ''),
                                'score': 0.0,
                                'similarity': 0.0
                            }
                        }
            
            # 4. 计算混合分数
            hybrid_results = []
            for session_id, scores in session_scores.items():
                hybrid_score = (
                    scores['vector_score'] * vector_weight +
                    scores['keyword_score'] * keyword_weight
                )
                
                result = scores['data'].copy()
                result['hybrid_score'] = hybrid_score
                result['vector_score'] = scores['vector_score']
                result['keyword_score'] = scores['keyword_score']
                
                hybrid_results.append(result)
            
            # 5. 按混合分数排序
            hybrid_results.sort(key=lambda x: x['hybrid_score'], reverse=True)
            
            print(f"[hybrid_search] 混合搜索找到 {len(hybrid_results)} 个结果")
            return hybrid_results[:top_k]
            
        except Exception as e:
            print(f"[hybrid_search] 混合搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    # ==================== 批量操作 ====================
    
    def batch_add_vectors(self,
                         vectors_data: List[Dict[str, Any]],
                         session_type: str = "read") -> int:
        """
        批量添加向量
        
        Args:
            vectors_data: 向量数据列表，每个元素包含:
                - session_id: 会话ID
                - vector: 向量
                - end_user_id: 用户ID
                - messages: 消息
                - aimessages: 回复
                - metadata: 元数据（可选）
            session_type: 会话类型
            
        Returns:
            int: 成功添加的数量
        """
        success_count = 0
        
        for data in vectors_data:
            try:
                result = self.add_vector(
                    session_id=data['session_id'],
                    vector=data['vector'],
                    end_user_id=data['end_user_id'],
                    messages=data['messages'],
                    aimessages=data.get('aimessages', ''),
                    session_type=session_type,
                    metadata=data.get('metadata')
                )
                
                if result:
                    success_count += 1
                    
            except Exception as e:
                print(f"[batch_add_vectors] 添加向量失败: {e}")
                continue
        
        print(f"[batch_add_vectors] 批量添加完成: {success_count}/{len(vectors_data)}")
        return success_count



try:
    print(f"[INFO] 尝试连接 Redis Stack: {settings.REDIS_HOST}:{settings.REDIS_PORT_STACK}")
    vector_search = RedisVectorSearch(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT_STACK,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD_STACK
    )
    print(f"[INFO] RedisVectorSearch 初始化成功，RediSearch 可用: {REDISEARCH_AVAILABLE}")
except Exception as e:
    print(f"[WARNING] RedisVectorSearch 初始化失败: {e}")
    import traceback
    traceback.print_exc()
    vector_search = None
    REDISEARCH_AVAILABLE = False

