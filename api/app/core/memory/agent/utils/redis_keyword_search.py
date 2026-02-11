"""
Redis 关键词匹配模块

提供基于 Redis 的关键词搜索和匹配功能，支持：
1. 全文搜索（使用 Redis SCAN + 模糊匹配）
2. 关键词索引（使用 Redis Set 存储关键词到会话的映射）
3. 多关键词组合搜索（AND/OR 逻辑）
4. 中文分词支持（可选）
"""

import re
from typing import List, Dict, Any

import jieba
import redis
from app.core.config import settings
from app.core.memory.agent.utils.redis_base import (
    fix_encoding,
    generate_session_key
)


class RedisKeywordSearch:
    """Redis 关键词搜索类"""
    
    def __init__(self, host='localhost', port=6379, db=0, password=None):
        """
        初始化 Redis 连接
        
        Args:
            host: Redis 主机地址
            port: Redis 端口
            db: Redis 数据库编号
            password: Redis 密码
        """
        self.r = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            encoding='utf-8'
        )
    
    # ==================== 关键词索引管理 ====================
    
    def add_keyword_index(self, session_id: str, keywords: List[str], 
                         session_type: str = "read") -> bool:
        """
        为会话添加关键词索引
        
        Args:
            session_id: 会话ID
            keywords: 关键词列表
            session_type: 会话类型 ("read", "write", "count")
            
        Returns:
            bool: 是否添加成功
        """
        try:
            pipe = self.r.pipeline()
            
            for keyword in keywords:
                if not keyword or not keyword.strip():
                    continue
                
                # 标准化关键词（转小写、去空格）
                normalized_keyword = keyword.strip().lower()
                
                # 关键词索引 key: keyword:index:{keyword} -> Set[session_id]
                index_key = f"keyword:index:{session_type}:{normalized_keyword}"
                pipe.sadd(index_key, session_id)
                
                # 设置过期时间（30天）
                pipe.expire(index_key, 30 * 24 * 60 * 60)
            
            # 反向索引：session_id -> Set[keywords]
            reverse_index_key = f"keyword:reverse:{session_type}:{session_id}"
            normalized_keywords = [k.strip().lower() for k in keywords if k and k.strip()]
            if normalized_keywords:
                pipe.sadd(reverse_index_key, *normalized_keywords)
                pipe.expire(reverse_index_key, 30 * 24 * 60 * 60)
            
            pipe.execute()
            print(f"[add_keyword_index] 为会话 {session_id} 添加了 {len(keywords)} 个关键词索引")
            return True
        except Exception as e:
            print(f"[add_keyword_index] 添加关键词索引失败: {e}")
            return False
    
    def remove_keyword_index(self, session_id: str, session_type: str = "read") -> bool:
        """
        删除会话的关键词索引
        
        Args:
            session_id: 会话ID
            session_type: 会话类型
            
        Returns:
            bool: 是否删除成功
        """
        try:
            # 获取该会话的所有关键词
            reverse_index_key = f"keyword:reverse:{session_type}:{session_id}"
            keywords = self.r.smembers(reverse_index_key)
            
            if not keywords:
                return True
            
            pipe = self.r.pipeline()
            
            # 从每个关键词的索引中删除该会话
            for keyword in keywords:
                index_key = f"keyword:index:{session_type}:{keyword}"
                pipe.srem(index_key, session_id)
            
            # 删除反向索引
            pipe.delete(reverse_index_key)
            
            pipe.execute()
            print(f"[remove_keyword_index] 删除会话 {session_id} 的关键词索引")
            return True
        except Exception as e:
            print(f"[remove_keyword_index] 删除关键词索引失败: {e}")
            return False
    
    # ==================== 关键词搜索 ====================
    
    def search_by_keyword(self, keyword: str, session_type: str = "read", 
                         limit: int = 100) -> List[str]:
        """
        通过单个关键词搜索会话ID
        
        Args:
            keyword: 关键词
            session_type: 会话类型
            limit: 最大返回数量
            
        Returns:
            List[str]: 会话ID列表
        """
        try:
            normalized_keyword = keyword.strip().lower()
            index_key = f"keyword:index:{session_type}:{normalized_keyword}"
            
            # 获取所有匹配的会话ID
            session_ids = list(self.r.smembers(index_key))
            
            print(f"[search_by_keyword] 关键词 '{keyword}' 找到 {len(session_ids)} 个会话")
            return session_ids[:limit]
        except Exception as e:
            print(f"[search_by_keyword] 搜索失败: {e}")
            return []
    
    def search_by_keywords_and(self, keywords: List[str], session_type: str = "read",
                              limit: int = 100) -> List[str]:
        """
        通过多个关键词搜索（AND 逻辑：必须包含所有关键词）
        
        Args:
            keywords: 关键词列表
            session_type: 会话类型
            limit: 最大返回数量
            
        Returns:
            List[str]: 会话ID列表
        """
        try:
            if not keywords:
                return []
            
            # 获取第一个关键词的结果集
            normalized_keywords = [k.strip().lower() for k in keywords if k and k.strip()]
            if not normalized_keywords:
                return []
            
            # 使用 Redis SINTER 求交集
            index_keys = [f"keyword:index:{session_type}:{k}" for k in normalized_keywords]
            session_ids = list(self.r.sinter(*index_keys))
            
            print(f"[search_by_keywords_and] 关键词 {keywords} (AND) 找到 {len(session_ids)} 个会话")
            return session_ids[:limit]
        except Exception as e:
            print(f"[search_by_keywords_and] 搜索失败: {e}")
            return []
    
    def search_by_keywords_or(self, keywords: List[str], session_type: str = "read",
                             limit: int = 100) -> List[str]:
        """
        通过多个关键词搜索（OR 逻辑：包含任一关键词即可）
        
        Args:
            keywords: 关键词列表
            session_type: 会话类型
            limit: 最大返回数量
            
        Returns:
            List[str]: 会话ID列表
        """
        try:
            if not keywords:
                return []
            
            normalized_keywords = [k.strip().lower() for k in keywords if k and k.strip()]
            if not normalized_keywords:
                return []
            
            # 使用 Redis SUNION 求并集
            index_keys = [f"keyword:index:{session_type}:{k}" for k in normalized_keywords]
            session_ids = list(self.r.sunion(*index_keys))
            
            print(f"[search_by_keywords_or] 关键词 {keywords} (OR) 找到 {len(session_ids)} 个会话")
            return session_ids[:limit]
        except Exception as e:
            print(f"[search_by_keywords_or] 搜索失败: {e}")
            return []
    
    # ==================== 模糊搜索 ====================
    
    def fuzzy_search_in_content(self, keyword: str, session_type: str = "read",
                               search_fields: List[str] = None,
                               limit: int = 100) -> List[Dict[str, Any]]:
        """
        在会话内容中进行模糊搜索（全文搜索）
        
        Args:
            keyword: 搜索关键词
            session_type: 会话类型
            search_fields: 要搜索的字段列表，默认 ["messages", "aimessages"]
            limit: 最大返回数量
            
        Returns:
            List[Dict]: 匹配的会话数据列表
        """
        try:
            if search_fields is None:
                search_fields = ["messages", "aimessages"]
            
            # 构建 key 模式
            if session_type == "read":
                pattern = "session:*"
            elif session_type == "write":
                pattern = "session:write:*"
            elif session_type == "count":
                pattern = "session:count:*"
            else:
                pattern = "session:*"
            
            matched_sessions = []
            cursor = 0
            
            # 使用 SCAN 遍历所有 key
            while True:
                cursor, keys = self.r.scan(cursor, match=pattern, count=100)
                
                if not keys:
                    if cursor == 0:
                        break
                    continue
                
                # 批量获取数据
                pipe = self.r.pipeline()
                for key in keys:
                    # 排除索引 key
                    if ':index:' in key or ':reverse:' in key:
                        continue
                    pipe.hgetall(key)
                
                all_data = pipe.execute()
                
                # 检查每个会话是否包含关键词
                for key, data in zip(keys, all_data):
                    if not data:
                        continue
                    
                    # 检查指定字段是否包含关键词
                    matched = False
                    for field in search_fields:
                        content = data.get(field, '')
                        if content and keyword.lower() in content.lower():
                            matched = True
                            break
                    
                    if matched:
                        # 提取 session_id
                        session_id = key.split(':')[-1]
                        matched_sessions.append({
                            'session_id': session_id,
                            'data': data,
                            'matched_fields': [f for f in search_fields 
                                             if keyword.lower() in data.get(f, '').lower()]
                        })
                        
                        if len(matched_sessions) >= limit:
                            break
                
                if cursor == 0 or len(matched_sessions) >= limit:
                    break
            
            print(f"[fuzzy_search_in_content] 关键词 '{keyword}' 在内容中找到 {len(matched_sessions)} 个会话")
            return matched_sessions[:limit]
        except Exception as e:
            print(f"[fuzzy_search_in_content] 模糊搜索失败: {e}")
            return []
    
    # ==================== 高级搜索 ====================
    
    def advanced_search(self, 
                       keywords: List[str] = None,
                       logic: str = "AND",
                       session_type: str = "read",
                       user_id: str = None,
                       time_range: tuple = None,
                       limit: int = 100) -> List[Dict[str, Any]]:
        """
        高级搜索：支持多条件组合
        
        Args:
            keywords: 关键词列表
            logic: 逻辑关系 ("AND" 或 "OR")
            session_type: 会话类型
            user_id: 用户ID过滤
            time_range: 时间范围 (start_time, end_time)，格式 "YYYY-MM-DD HH:MM:SS"
            limit: 最大返回数量
            
        Returns:
            List[Dict]: 匹配的会话完整数据列表
        """
        try:
            # 1. 通过关键词获取候选会话ID
            if keywords:
                if logic.upper() == "AND":
                    session_ids = self.search_by_keywords_and(keywords, session_type, limit * 2)
                else:
                    session_ids = self.search_by_keywords_or(keywords, session_type, limit * 2)
            else:
                # 如果没有关键词，获取所有会话
                if session_type == "read":
                    pattern = "session:*"
                elif session_type == "write":
                    pattern = "session:write:*"
                elif session_type == "count":
                    pattern = "session:count:*"
                else:
                    pattern = "session:*"
                
                all_keys = []
                cursor = 0
                while True:
                    cursor, keys = self.r.scan(cursor, match=pattern, count=100)
                    # 排除索引 key
                    all_keys.extend([k for k in keys if ':index:' not in k and ':reverse:' not in k])
                    if cursor == 0:
                        break
                
                session_ids = [k.split(':')[-1] for k in all_keys]
            
            if not session_ids:
                return []
            
            # 2. 批量获取会话数据
            pipe = self.r.pipeline()
            for sid in session_ids:
                key = generate_session_key(sid, key_type=session_type)
                pipe.hgetall(key)
            
            all_data = pipe.execute()
            
            # 3. 应用额外的过滤条件
            matched_sessions = []
            for sid, data in zip(session_ids, all_data):
                if not data:
                    continue
                
                # 用户ID过滤
                if user_id:
                    session_user_id = data.get('end_user_id') or data.get('sessionid')
                    if session_user_id != user_id:
                        continue
                
                # 时间范围过滤
                if time_range:
                    start_time, end_time = time_range
                    session_time = data.get('starttime', '')
                    if session_time:
                        if start_time and session_time < start_time:
                            continue
                        if end_time and session_time > end_time:
                            continue
                
                matched_sessions.append({
                    'session_id': sid,
                    'session_type': session_type,
                    'user_id': data.get('end_user_id') or data.get('sessionid'),
                    'messages': fix_encoding(data.get('messages', '')),
                    'aimessages': fix_encoding(data.get('aimessages', '')),
                    'starttime': data.get('starttime', ''),
                    'raw_data': data
                })
                
                if len(matched_sessions) >= limit:
                    break
            
            print(f"[advanced_search] 高级搜索找到 {len(matched_sessions)} 个会话")
            return matched_sessions
        except Exception as e:
            print(f"[advanced_search] 高级搜索失败: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    # ==================== 关键词提取 ====================
    
    def extract_keywords(self, text: str, min_length: int = 2) -> List[str]:
        """
        从文本中提取关键词（简单实现：基于分词和停用词过滤）
        
        Args:
            text: 输入文本
            min_length: 最小关键词长度
            
        Returns:
            List[str]: 关键词列表
        """
        if not text:
            return []
        words = jieba.cut(text)
        
        # 停用词列表（简化版）
        stopwords = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个',
            '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好',
            '自己', '这', '那', '里', '就是', '什么', '这个', '那个', '可以', '知道',
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
            'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be'
        }
        
        # 过滤停用词和短词
        keywords = [
            w for w in words 
            if len(w) >= min_length and w.lower() not in stopwords
        ]
        
        # 去重并保持顺序
        seen = set()
        unique_keywords = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique_keywords.append(kw)
        
        return unique_keywords
    
    # ==================== 批量操作 ====================
    
    def batch_index_sessions(self, session_type: str = "read", 
                            batch_size: int = 100) -> int:
        """
        批量为现有会话建立关键词索引
        
        Args:
            session_type: 会话类型
            batch_size: 批处理大小
            
        Returns:
            int: 索引的会话数量
        """
        try:
            # 构建 key 模式
            if session_type == "read":
                pattern = "session:*"
            elif session_type == "write":
                pattern = "session:write:*"
            elif session_type == "count":
                pattern = "session:count:*"
            else:
                pattern = "session:*"
            
            indexed_count = 0
            cursor = 0
            
            while True:
                cursor, keys = self.r.scan(cursor, match=pattern, count=batch_size)
                
                if not keys:
                    if cursor == 0:
                        break
                    continue
                
                # 排除索引 key
                session_keys = [k for k in keys if ':index:' not in k and ':reverse:' not in k]
                
                # 批量获取数据
                pipe = self.r.pipeline()
                for key in session_keys:
                    pipe.hgetall(key)
                
                all_data = pipe.execute()
                
                # 为每个会话建立索引
                for key, data in zip(session_keys, all_data):
                    if not data:
                        continue
                    
                    session_id = key.split(':')[-1]
                    
                    # 从 messages 和 aimessages 中提取关键词
                    text = data.get('messages', '') + ' ' + data.get('aimessages', '')
                    keywords = self.extract_keywords(text)
                    
                    if keywords:
                        self.add_keyword_index(session_id, keywords, session_type)
                        indexed_count += 1
                
                if cursor == 0:
                    break
            
            print(f"[batch_index_sessions] 批量索引完成，共索引 {indexed_count} 个会话")
            return indexed_count
        except Exception as e:
            print(f"[batch_index_sessions] 批量索引失败: {e}")
            return 0
    
    def get_keyword_statistics(self, session_type: str = "read", 
                              top_n: int = 20) -> List[Dict[str, Any]]:
        """
        获取关键词统计信息（热门关键词）
        
        Args:
            session_type: 会话类型
            top_n: 返回前 N 个热门关键词
            
        Returns:
            List[Dict]: 关键词统计列表 [{"keyword": "...", "count": 10}, ...]
        """
        try:
            pattern = f"keyword:index:{session_type}:*"
            keyword_counts = []
            
            cursor = 0
            while True:
                cursor, keys = self.r.scan(cursor, match=pattern, count=100)
                
                if not keys:
                    if cursor == 0:
                        break
                    continue
                
                # 获取每个关键词的会话数量
                pipe = self.r.pipeline()
                for key in keys:
                    pipe.scard(key)
                
                counts = pipe.execute()
                
                for key, count in zip(keys, counts):
                    # 提取关键词
                    keyword = key.split(':')[-1]
                    keyword_counts.append({
                        'keyword': keyword,
                        'count': count
                    })
                
                if cursor == 0:
                    break
            
            # 按数量排序
            keyword_counts.sort(key=lambda x: x['count'], reverse=True)
            
            print(f"[get_keyword_statistics] 找到 {len(keyword_counts)} 个关键词")
            return keyword_counts[:top_n]
        except Exception as e:
            print(f"[get_keyword_statistics] 获取统计失败: {e}")
            return []


# 全局实例
keyword_search = RedisKeywordSearch(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD
)
# 1. 添加索引
keyword_search.add_keyword_index(
    session_id="uuid-123",
    keywords=["杭州", "旅游", "美食"],
    session_type="read"
)

# 2. 搜索
results = keyword_search.search_by_keywords_and(
    keywords=["杭州", "美食"],
    session_type="read"
)
#
# 3. 高级搜索
results = keyword_search.advanced_search(
    keywords=["杭州"],
    user_id="user_123",
    time_range=("2026-02-01 00:00:00", "2026-02-09 23:59:59")
)