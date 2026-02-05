import redis
import uuid
from app.core.config import settings
from typing import List, Dict, Any, Optional, Union

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




class RedisWriteStore:
    """Redis Write 类型存储类，用于管理 save_session_write 相关的数据"""
    
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

    def save_session_write(self, userid: str, messages: str) -> str:
        """
        写入一条会话数据，返回 session_id

        Args:
            userid: 用户ID
            messages: 用户消息

        Returns:
            str: 新生成的 session_id
        """
        try:
            messages = serialize_messages(messages)
            session_id = str(uuid.uuid4())
            key = generate_session_key(session_id, key_type="write")

            pipe = self.r.pipeline()
            pipe.hset(key, mapping={
                "id": self.uudi,
                "sessionid": userid,
                "messages": messages,
                "starttime": get_current_timestamp()
            })
            result = pipe.execute()

            print(f"[save_session_write] 保存结果: {result[0]}, session_id: {session_id}")
            return session_id
        except Exception as e:
            print(f"[save_session_write] 保存会话失败: {e}")
            raise e

    def get_session_by_userid(self, userid: str) -> Union[List[Dict[str, str]], bool]:
        """
        通过 save_session_write 的 userid 获取 sessionid 和 messages
        
        Args:
            userid: 用户ID (对应 sessionid 字段)
            
        Returns:
            List[Dict] 或 False: 如果找到数据返回 [{"sessionid": "...", "messages": "..."}, ...]，否则返回 False
        """
        try:
            # 只查询 write 类型的 key
            keys = self.r.keys('session:write:*')
            if not keys:
                return False

            # 批量获取数据
            pipe = self.r.pipeline()
            for key in keys:
                pipe.hgetall(key)
            all_data = pipe.execute()

            # 筛选符合 userid 的数据
            results = []
            for key, data in zip(keys, all_data):
                if not data:
                    continue
                
                # 从 write 类型读取，匹配 sessionid 字段
                if data.get('sessionid') == userid:
                    # 从 key 中提取 session_id: session:write:{session_id}
                    session_id = key.split(':')[-1]
                    results.append({
                        "sessionid": session_id,
                        "messages": fix_encoding(data.get('messages', ''))
                    })
            
            if not results:
                return False
            
            print(f"[get_session_by_userid] userid={userid}, 找到 {len(results)} 条数据")
            return results
        except Exception as e:
            print(f"[get_session_by_userid] 查询失败: {e}")
            return False
    
    def get_all_sessions_by_end_user_id(self, end_user_id: str) -> Union[List[Dict[str, Any]], bool]:
        """
        通过 end_user_id 获取所有 write 类型的会话数据
        
        Args:
            end_user_id: 终端用户ID (对应 sessionid 字段)
            
        Returns:
            List[Dict] 或 False: 如果找到数据返回完整的会话信息列表，否则返回 False
            
        返回格式:
        [
            {
                "session_id": "uuid",
                "id": "...",
                "sessionid": "end_user_id",
                "messages": "...",
                "starttime": "timestamp"
            },
            ...
        ]
        """
        try:
            # 只查询 write 类型的 key
            keys = self.r.keys('session:write:*')
            if not keys:
                print(f"[get_all_sessions_by_end_user_id] 没有找到任何 write 类型的会话")
                return False

            # 批量获取数据
            pipe = self.r.pipeline()
            for key in keys:
                pipe.hgetall(key)
            all_data = pipe.execute()

            # 筛选符合 end_user_id 的数据
            results = []
            for key, data in zip(keys, all_data):
                if not data:
                    continue
                
                # 从 write 类型读取，匹配 sessionid 字段
                if data.get('sessionid') == end_user_id:
                    # 从 key 中提取 session_id: session:write:{session_id}
                    session_id = key.split(':')[-1]
                    
                    # 构建完整的会话信息
                    session_info = {
                        "session_id": session_id,
                        "id": data.get('id', ''),
                        "sessionid": data.get('sessionid', ''),
                        "messages": fix_encoding(data.get('messages', '')),
                        "starttime": data.get('starttime', '')
                    }
                    results.append(session_info)
            
            if not results:
                print(f"[get_all_sessions_by_end_user_id] end_user_id={end_user_id}, 没有找到数据")
                return False
            
            # 按时间排序（最新的在前）
            results.sort(key=lambda x: x.get('starttime', ''), reverse=True)
            
            print(f"[get_all_sessions_by_end_user_id] end_user_id={end_user_id}, 找到 {len(results)} 条数据")
            return results
        except Exception as e:
            print(f"[get_all_sessions_by_end_user_id] 查询失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def find_user_recent_sessions(self, userid: str, 
                                  minutes: int = 5) -> List[Dict[str, str]]:
        """
        根据 userid 从 save_session_write 写入的数据中查询最近 N 分钟内的会话数据
        
        Args:
            userid: 用户ID (对应 sessionid 字段)
            minutes: 查询最近几分钟的数据，默认5分钟
            
        Returns:
            List[Dict]: 会话列表 [{"Query": "...", "Answer": "..."}, ...]
        """
        import time
        start_time = time.time()
        
        # 只查询 write 类型的 key
        keys = self.r.keys('session:write:*')
        if not keys:
            print(f"[find_user_recent_sessions] 查询耗时: {time.time() - start_time:.3f}秒, 结果数: 0")
            return []

        # 批量获取数据
        pipe = self.r.pipeline()
        for key in keys:
            pipe.hgetall(key)
        all_data = pipe.execute()

        # 筛选符合 userid 的数据
        matched_items = []
        for data in all_data:
            if not data:
                continue
            
            # 从 write 类型读取，匹配 sessionid 字段
            if data.get('sessionid') == userid and data.get('starttime'):
                # write 类型没有 aimessages，所以 Answer 为空
                matched_items.append({
                    "Query": fix_encoding(data.get('messages', '')),
                    "Answer": "",
                    "starttime": data.get('starttime', '')
                })
        
        # 根据时间范围过滤
        filtered_items = filter_by_time_range(matched_items, minutes)
        # 排序并移除时间字段
        result_items = sort_and_limit_results(filtered_items, limit=None)
        print(result_items)

        elapsed_time = time.time() - start_time
        print(f"[find_user_recent_sessions] userid={userid}, minutes={minutes}, "
              f"查询耗时: {elapsed_time:.3f}秒, 结果数: {len(result_items)}")

        return result_items

    def delete_all_write_sessions(self) -> int:
        """
        删除所有 write 类型的会话
        
        Returns:
            int: 删除的数量
        """
        keys = self.r.keys('session:write:*')
        if keys:
            return self.r.delete(*keys)
        return 0


class RedisCountStore:
    """Redis Count 类型存储类，用于管理访问次数统计相关的数据"""
    
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

    def save_sessions_count(self, end_user_id: str, count: int, messages: Any) -> str:
        """
        保存用户访问次数统计
        
        Args:
            end_user_id: 终端用户ID
            count: 访问次数
            messages: 消息内容
            
        Returns:
            str: 新生成的 session_id
        """
        session_id = str(uuid.uuid4())
        key = generate_session_key(session_id, key_type="count")
        index_key = f'session:count:index:{end_user_id}'  # 索引键
        
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={
            "id": self.uudi,
            "end_user_id": end_user_id,
            "count": int(count),
            "messages": serialize_messages(messages),
            "starttime": get_current_timestamp()
        })
        pipe.expire(key, 30 * 24 * 60 * 60)  # 30天过期
        
        # 创建索引：end_user_id -> session_id 映射
        pipe.set(index_key, session_id, ex=30 * 24 * 60 * 60)
        
        result = pipe.execute()
        
        print(f"[save_sessions_count] 保存结果: {result}, session_id: {session_id}")
        return session_id

    def get_sessions_count(self, end_user_id: str) -> Union[List[Any], bool]:
        """
        通过 end_user_id 查询访问次数统计
        
        Args:
            end_user_id: 终端用户ID
            
        Returns:
            list 或 False: 如果找到返回 [count, messages]，否则返回 False
        """
        try:
            # 使用索引键快速查找
            index_key = f'session:count:index:{end_user_id}'
            
            # 检查索引键类型，避免 WRONGTYPE 错误
            try:
                key_type = self.r.type(index_key)
                if key_type != 'string' and key_type != 'none':
                    # 索引键类型错误，删除并返回 False
                    print(f"[get_sessions_count] 索引键类型错误: {key_type}，删除索引")
                    self.r.delete(index_key)
                    return False
            except Exception as type_error:
                print(f"[get_sessions_count] 检查键类型失败: {type_error}")
            
            session_id = self.r.get(index_key)
            
            if not session_id:
                return False
            
            # 直接获取数据
            key = generate_session_key(session_id, key_type="count")
            data = self.r.hgetall(key)
            
            if not data:
                # 索引存在但数据不存在，清理索引
                self.r.delete(index_key)
                return False
            
            count = data.get('count')
            messages_str = data.get('messages')
            
            if count is not None:
                messages = deserialize_messages(messages_str)
                return [int(count), messages]
            
            return False
        except Exception as e:
            print(f"[get_sessions_count] 查询失败: {e}")
            return False
    def update_sessions_count(self, end_user_id: str, new_count: int, 
                             messages: Any) -> bool:
        """
        通过 end_user_id 修改访问次数统计（优化版：使用索引）
        
        Args:
            end_user_id: 终端用户ID
            new_count: 新的 count 值
            messages: 消息内容
            
        Returns:
            bool: 更新成功返回 True，未找到记录返回 False
        """
        try:
            # 使用索引键快速查找
            index_key = f'session:count:index:{end_user_id}'
            
            # 检查索引键类型，避免 WRONGTYPE 错误
            try:
                key_type = self.r.type(index_key)
                if key_type != 'string' and key_type != 'none':
                    # 索引键类型错误，删除并返回 False
                    print(f"[update_sessions_count] 索引键类型错误: {key_type}，删除索引")
                    self.r.delete(index_key)
                    print(f"[update_sessions_count] 未找到记录: end_user_id={end_user_id}")
                    return False
            except Exception as type_error:
                print(f"[update_sessions_count] 检查键类型失败: {type_error}")
            
            session_id = self.r.get(index_key)
            
            if not session_id:
                print(f"[update_sessions_count] 未找到记录: end_user_id={end_user_id}")
                return False
            
            # 直接更新数据
            key = generate_session_key(session_id, key_type="count")
            messages_str = serialize_messages(messages)
            
            pipe = self.r.pipeline()
            pipe.hset(key, 'count', int(new_count))
            pipe.hset(key, 'messages', messages_str)
            result = pipe.execute()
            
            print(f"[update_sessions_count] 更新成功: end_user_id={end_user_id}, new_count={new_count}, key={key}")
            return True
            
        except Exception as e:
            print(f"[update_sessions_count] 更新失败: {e}")
            return False

    def delete_all_count_sessions(self) -> int:
        """
        删除所有 count 类型的会话
        
        Returns:
            int: 删除的数量
        """
        keys = self.r.keys('session:count:*')
        if keys:
            return self.r.delete(*keys)
        return 0


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

            print(f"[save_session] 保存结果: {result[0]}, session_id: {session_id}")
            return session_id
        except Exception as e:
            print(f"[save_session] 保存会话失败: {e}")
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
            print(f"[find_user_apply_group] 查询耗时: {time.time() - start_time:.3f}秒, 结果数: 0")
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
        print(f"[find_user_apply_group] 查询耗时: {elapsed_time:.3f}秒, 结果数: {len(result_items)}")

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
            print("[delete_duplicate_sessions] 没有会话数据")
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
        print(f"[delete_duplicate_sessions] 删除重复会话数量: {deleted_count}, 耗时: {elapsed_time:.3f}秒")
        return deleted_count


# 全局实例
store = RedisSessionStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)

write_store = RedisWriteStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)

count_store = RedisCountStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)
