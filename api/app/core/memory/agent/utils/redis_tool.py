import redis
import uuid
from app.core.config import settings
from typing import List, Dict, Any, Optional, Union

from app.core.logging_config import get_logger
from app.core.memory.agent.utils.redis_base import (
    serialize_messages,
    deserialize_messages,
    fix_encoding,
    format_session_data,
    filter_by_time_range,
    sort_and_limit_results,
    generate_session_key,
    get_current_timestamp
)

logger = get_logger(__name__)


class RedisSessionStore:
    """Redis 会话存储类，用于管理会话数据"""

    def __init__(self, host='localhost', port=6379, db=0, password=None, session_id=''):
        """
        初始化 Redis 连接
        
        Args:
            host: Redis 主机地址
            port: Redis 端口
            db: Redis 数据库编号
            password: Redis 密码
            session_id: 会话ID
        """
        self.r = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            encoding='utf-8'
        )
        self.uudi = session_id

    # ==================== 写入操作 ====================

    def save_session(self, userid: str, messages: str, aimessages: str,
                     apply_id: str, end_user_id: str) -> str:
        """
        写入一条会话数据，返回 session_id
        
        Args:
            userid: 用户ID
            messages: 用户消息
            aimessages: AI回复消息
            apply_id: 应用ID
            end_user_id: 终端用户ID
            
        Returns:
            str: 新生成的 session_id
        """
        try:
            session_id = str(uuid.uuid4())
            key = generate_session_key(session_id, key_type="read")

            pipe = self.r.pipeline()
            pipe.hset(key, mapping={
                "id": self.uudi,
                "sessionid": userid,
                "apply_id": apply_id,
                "end_user_id": end_user_id,
                "messages": messages,
                "aimessages": aimessages,
                "starttime": get_current_timestamp()
            })
            result = pipe.execute()

            logger.debug(f"[save_session] 保存结果: {result[0]}, session_id: {session_id}")
            return session_id
        except Exception as e:
            logger.error(f"[save_session] 保存会话失败: {e}")
            raise e

    # ==================== 读取操作 ====================

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        读取一条会话数据
        
        Args:
            session_id: 会话ID
            
        Returns:
            Dict 或 None: 会话数据
        """
        key = generate_session_key(session_id)
        data = self.r.hgetall(key)
        return data if data else None

    def get_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有会话数据（不包括 count 和 write 类型）
        
        Returns:
            Dict: 所有会话数据，key 为 session_id
        """
        sessions = {}
        for key in self.r.keys('session:*'):
            # 排除 count 和 write 类型的 key
            if ':count:' not in key and ':write:' not in key:
                sid = key.split(':')[1]
                sessions[sid] = self.get_session(sid)
        return sessions

    def find_user_apply_group(self, sessionid: str, apply_id: str,
                              end_user_id: str) -> List[Dict[str, str]]:
        """
        根据 sessionid、apply_id 和 end_user_id 查询会话数据，返回最新的6条
        
        Args:
            sessionid: 会话ID（支持模糊匹配）
            apply_id: 应用ID
            end_user_id: 终端用户ID
            
        Returns:
            List[Dict]: 会话列表 [{"Query": "...", "Answer": "..."}, ...]
        """
        import time
        start_time = time.time()

        keys = self.r.keys('session:*')
        if not keys:
            logger.debug(f"[find_user_apply_group] 查询耗时: {time.time() - start_time:.3f}秒, 结果数: 0")
            return []

        # 批量获取数据
        pipe = self.r.pipeline()
        for key in keys:
            # 排除 count 和 write 类型
            if ':count:' not in key and ':write:' not in key:
                pipe.hgetall(key)
        all_data = pipe.execute()

        # 筛选符合条件的数据
        matched_items = []
        for data in all_data:
            if not data:
                continue

            if (data.get('apply_id') == apply_id and
                    data.get('end_user_id') == end_user_id):
                # 支持模糊匹配或完全匹配 sessionid
                if sessionid in data.get('sessionid', '') or data.get('sessionid') == sessionid:
                    matched_items.append(format_session_data(data, include_time=True))

        # 排序、限制数量并移除时间字段
        result_items = sort_and_limit_results(matched_items, limit=6)

        elapsed_time = time.time() - start_time
        logger.debug(f"[find_user_apply_group] 查询耗时: {elapsed_time:.3f}秒, 结果数: {len(result_items)}")

        return result_items

    # ==================== 更新操作 ====================

    def update_session(self, session_id: str, field: str, value: Any) -> bool:
        """
        更新单个字段
        
        Args:
            session_id: 会话ID
            field: 字段名
            value: 字段值
            
        Returns:
            bool: 是否更新成功
        """
        key = generate_session_key(session_id)
        pipe = self.r.pipeline()
        pipe.exists(key)
        pipe.hset(key, field, value)
        results = pipe.execute()
        return bool(results[0])

    # ==================== 删除操作 ====================

    def delete_session(self, session_id: str) -> int:
        """
        删除单条会话
        
        Args:
            session_id: 会话ID
            
        Returns:
            int: 删除的数量
        """
        key = generate_session_key(session_id)
        return self.r.delete(key)

    def delete_all_sessions(self) -> int:
        """
        删除所有会话（不包括 count 和 write 类型）
        
        Returns:
            int: 删除的数量
        """
        keys = self.r.keys('session:*')
        # 过滤掉 count 和 write 类型
        keys_to_delete = [k for k in keys if ':count:' not in k and ':write:' not in k]
        if keys_to_delete:
            return self.r.delete(*keys_to_delete)
        return 0

    def delete_duplicate_sessions(self) -> int:
        """
        删除重复会话数据（不包括 count 和 write 类型）
        条件：sessionid、user_id、end_user_id、messages、aimessages 五个字段都相同的只保留一个
        
        Returns:
            int: 删除的数量
        """
        import time
        start_time = time.time()

        keys = self.r.keys('session:*')
        if not keys:
            logger.debug("[delete_duplicate_sessions] 没有会话数据")
            return 0

        # 批量获取所有数据
        pipe = self.r.pipeline()
        for key in keys:
            # 排除 count 和 write 类型
            if ':count:' not in key and ':write:' not in key:
                pipe.hgetall(key)
        all_data = pipe.execute()

        # 识别重复数据
        seen = {}
        keys_to_delete = []

        for key, data in zip([k for k in keys if ':count:' not in k and ':write:' not in k], all_data, strict=False):
            if not data:
                continue

            # 用五元组作为唯一标识
            identifier = (
                data.get('sessionid', ''),
                data.get('id', ''),
                data.get('end_user_id', ''),
                data.get('messages', ''),
                data.get('aimessages', '')
            )

            if identifier in seen:
                keys_to_delete.append(key)
            else:
                seen[identifier] = key

        # 批量删除重复的 key
        deleted_count = 0
        if keys_to_delete:
            batch_size = 1000
            for i in range(0, len(keys_to_delete), batch_size):
                batch = keys_to_delete[i:i + batch_size]
                pipe = self.r.pipeline()
                for key in batch:
                    pipe.delete(key)
                pipe.execute()
                deleted_count += len(batch)

        elapsed_time = time.time() - start_time
        logger.debug(f"[delete_duplicate_sessions] 删除重复会话数量: {deleted_count}, 耗时: {elapsed_time:.3f}秒")
        return deleted_count


# 全局实例
store = RedisSessionStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)
