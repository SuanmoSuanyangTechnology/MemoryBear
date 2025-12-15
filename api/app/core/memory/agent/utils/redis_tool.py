import redis
import uuid
from datetime import datetime
from app.core.config import settings


class RedisSessionStore:
    def __init__(self, host='localhost', port=6379, db=0, password=None, session_id=''):
        self.r = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            encoding='utf-8'
        )
        self.uudi = session_id

    def _fix_encoding(self, text):
        """修复错误编码的文本"""
        if not text or not isinstance(text, str):
            return text
        try:
            # 尝试修复 Latin-1 误编码为 UTF-8 的情况
            return text.encode('latin-1').decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            # 如果修复失败，返回原文本
            return text

    # 修改后的 save_session 方法
    def save_session(self, userid, messages, aimessages, apply_id, group_id):
        """
        写入一条会话数据，返回 session_id
        优化版本：确保写入时间不超过1秒
        """
        try:
            session_id = str(uuid.uuid4())  # 为每次会话生成新的 ID
            starttime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            key = f"session:{session_id}"  # 使用新生成的 session_id 作为 key

            # 使用 pipeline 批量写入，减少网络往返
            pipe = self.r.pipeline()

            # 直接写入数据，decode_responses=True 已经处理了编码
            pipe.hset(key, mapping={
                "id": self.uudi,
                "sessionid": userid,
                "apply_id": apply_id,
                "group_id": group_id,
                "messages": messages,
                "aimessages": aimessages,
                "starttime": starttime
            })

            # 可选：设置过期时间（例如30天），避免数据无限增长
            # pipe.expire(key, 30 * 24 * 60 * 60)

            # 执行批量操作
            result = pipe.execute()

            print(f"保存结果: {result[0]}, session_id: {session_id}")
            return session_id  # 返回新生成的 session_id
        except Exception as e:
            print(f"保存会话失败: {e}")
            raise e

    def save_sessions_batch(self, sessions_data):
        """
        批量写入多条会话数据，返回 session_id 列表
        sessions_data: list of dict, 每个 dict 包含 userid, messages, aimessages, apply_id, group_id
        优化版本：批量操作，大幅提升性能
        """
        try:
            session_ids = []
            pipe = self.r.pipeline()

            for session in sessions_data:
                session_id = str(uuid.uuid4())
                starttime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                key = f"session:{session_id}"

                pipe.hset(key, mapping={
                    "id": self.uudi,
                    "sessionid": session.get('userid'),
                    "apply_id": session.get('apply_id'),
                    "group_id": session.get('group_id'),
                    "messages": session.get('messages'),
                    "aimessages": session.get('aimessages'),
                    "starttime": starttime
                })

                session_ids.append(session_id)

            # 一次性执行所有写入操作
            results = pipe.execute()
            print(f"批量保存完成: {len(session_ids)} 条记录")
            return session_ids
        except Exception as e:
            print(f"批量保存会话失败: {e}")
            raise e

    # ---------------- 读取 ----------------
    def get_session(self, session_id):
        """
        读取一条会话数据
        """
        key = f"session:{session_id}"
        data = self.r.hgetall(key)
        return data if data else None

    def get_session_apply_group(self, sessionid, apply_id, group_id):
        """
        根据 sessionid、apply_id 和 group_id 三个条件查询会话数据
        """
        result_items = []

        # 遍历所有会话数据
        for key in self.r.keys('session:*'):
            data = self.r.hgetall(key)

            if not data:
                continue

            # 检查三个条件是否都匹配
            if (data.get('sessionid') == sessionid and
                    data.get('apply_id') == apply_id and
                    data.get('group_id') == group_id):
                result_items.append(data)

        return result_items

    def get_all_sessions(self):
        """
        获取所有会话数据
        """
        sessions = {}
        for key in self.r.keys('session:*'):
            sid = key.split(':')[1]
            sessions[sid] = self.get_session(sid)
        return sessions

    # ---------------- 更新 ----------------
    def update_session(self, session_id, field, value):
        """
        更新单个字段
        优化版本：使用 pipeline 减少网络往返
        """
        key = f"session:{session_id}"
        pipe = self.r.pipeline()
        pipe.exists(key)
        pipe.hset(key, field, value)
        results = pipe.execute()
        return bool(results[0])  # 返回 key 是否存在

    # ---------------- 删除 ----------------
    def delete_session(self, session_id):
        """
        删除单条会话
        """
        key = f"session:{session_id}"
        return self.r.delete(key)

    def delete_all_sessions(self):
        """
        删除所有会话
        """
        keys = self.r.keys('session:*')
        if keys:
            return self.r.delete(*keys)
        return 0

    def delete_duplicate_sessions(self):
        """
        删除重复会话数据，条件：
        "sessionid"、"user_id"、"group_id"、"messages"、"aimessages" 五个字段都相同的只保留一个，其他删除
        优化版本：使用 pipeline 批量操作，确保在1秒内完成
        """
        import time
        start_time = time.time()

        # 第一步：使用 pipeline 批量获取所有 key
        keys = self.r.keys('session:*')

        if not keys:
            print("[delete_duplicate_sessions] 没有会话数据")
            return 0

        # 第二步：使用 pipeline 批量获取所有数据
        pipe = self.r.pipeline()
        for key in keys:
            pipe.hgetall(key)
        all_data = pipe.execute()

        # 第三步：在内存中识别重复数据
        seen = {}  # 用字典记录：identifier -> key（保留第一个出现的 key）
        keys_to_delete = []  # 需要删除的 key 列表

        for key, data in zip(keys, all_data, strict=False):
            if not data:
                continue

            # 获取五个字段的值
            sessionid = data.get('sessionid', '')
            user_id = data.get('id', '')
            group_id = data.get('group_id', '')
            messages = data.get('messages', '')
            aimessages = data.get('aimessages', '')

            # 用五元组作为唯一标识
            identifier = (sessionid, user_id, group_id, messages, aimessages)

            if identifier in seen:
                # 重复，标记为待删除
                keys_to_delete.append(key)
            else:
                # 第一次出现，记录
                seen[identifier] = key

        # 第四步：使用 pipeline 批量删除重复的 key
        deleted_count = 0
        if keys_to_delete:
            # 分批删除，避免单次操作过大
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

    def find_user_session(self, sessionid):
        user_id = sessionid

        result_items = []
        for key, values in store.get_all_sessions().items():
            history = {}
            if user_id == str(values['sessionid']):
                history["Query"] = values['messages']
                history["Answer"] = values['aimessages']
                result_items.append(history)

        if len(result_items) <= 1:
            result_items = []
        return (result_items)

    def find_user_apply_group(self, sessionid, apply_id, group_id):
        """
        根据 sessionid、apply_id 和 group_id 三个条件查询会话数据，返回最新的6条
        """
        import time
        start_time = time.time()
        # 使用 pipeline 批量获取数据，提高性能
        keys = self.r.keys('session:*')

        if not keys:
            print(f"查询耗时: {time.time() - start_time:.3f}秒, 结果数: 0")
            return []

        # 使用 pipeline 批量获取所有 hash 数据
        pipe = self.r.pipeline()
        for key in keys:
            pipe.hgetall(key)
        all_data = pipe.execute()

        # 解析并筛选符合条件的数据
        matched_items = []
        for data in all_data:
            if not data:
                continue

            # 检查是否符合三个条件

            if (data.get('apply_id') == apply_id and
                    data.get('group_id') == group_id):
                # 支持模糊匹配 sessionid 或者完全匹配
                if sessionid in data.get('sessionid', '') or data.get('sessionid') == sessionid:
                    matched_items.append({
                        "Query": self._fix_encoding(data.get('messages')),
                        "Answer": self._fix_encoding(data.get('aimessages')),
                        "starttime": data.get('starttime', '')
                    })
        # 按时间降序排序（最新的在前）
        matched_items.sort(key=lambda x: x.get('starttime', ''), reverse=True)
        # 只保留最新的6条
        result_items = matched_items[:6]
        # # 移除 starttime 字段
        for item in result_items:
            item.pop('starttime', None)

        # 如果结果少于等于1条，返回空列表
        if len(result_items) <= 1:
            result_items = []

        elapsed_time = time.time() - start_time
        print(f"查询耗时: {elapsed_time:.3f}秒, 结果数: {len(result_items)}")

        return result_items


store = RedisSessionStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)