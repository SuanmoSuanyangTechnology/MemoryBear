import redis
import uuid
from datetime import datetime
from app.core.config import settings
class RedisSessionStore:
    def __init__(self, host='localhost', port=6379, db=0, password=None,session_id=''):
        self.r = redis.Redis(host=host, port=port, db=db, password=password)
        self.uudi=session_id


    # 修改后的 save_session 方法
    def save_session(self, userid, messages, aimessages, apply_id, group_id):
        """
        写入一条会话数据，返回 session_id
        """
        try:
            session_id = str(uuid.uuid4())  # 为每次会话生成新的 ID
            starttime = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            key = f"session:{session_id}"  # 使用新生成的 session_id 作为 key

            # 使用 Hash 存储结构化数据
            result = self.r.hset(key, mapping={
                "id": self.uudi,
                "sessionid": userid,
                "apply_id": apply_id,
                "group_id": group_id,
                "messages": messages,
                "aimessages": aimessages,
                "starttime": starttime
            })
            print(f"保存结果: {result}, session_id: {session_id}")
            return session_id  # 返回新生成的 session_id
        except Exception as e:
            print(f"保存会话失败: {e}")
            raise e

    # ---------------- 读取 ----------------
    def get_session(self, session_id):
        """
        读取一条会话数据
        """
        key = f"session:{session_id}"
        data = self.r.hgetall(key)
        if data:
            return {k.decode('utf-8'): v.decode('utf-8') for k, v in data.items()}
        return None

    def get_session_apply_group(self, sessionid, apply_id, group_id):
        """
        根据 sessionid、apply_id 和 group_id 三个条件查询会话数据
        """
        result_items = []

        # 遍历所有会话数据
        for key_bytes in self.r.keys('session:*'):
            key = key_bytes.decode('utf-8')
            data = self.r.hgetall(key)

            if not data:
                continue

            # 解码数据
            decoded_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in data.items()}

            # 检查三个条件是否都匹配
            if (decoded_data.get('sessionid') == sessionid and
                    decoded_data.get('apply_id') == apply_id and
                    decoded_data.get('group_id') == group_id):
                result_items.append(decoded_data)

        return result_items

    def get_all_sessions(self):
        """
        获取所有会话数据
        """
        sessions = {}
        for key in self.r.keys('session:*'):
            sid = key.decode('utf-8').split(':')[1]
            sessions[sid] = self.get_session(sid)
        return sessions

    # ---------------- 更新 ----------------
    def update_session(self, session_id, field, value):
        """
        更新单个字段
        """
        key = f"session:{session_id}"
        if self.r.exists(key):
            self.r.hset(key, field, value)
            return True
        return False

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
        """
        seen = set()  # 用来记录已出现的唯一组合
        deleted_count = 0

        for key_bytes in self.r.keys('session:*'):
            key = key_bytes.decode('utf-8')
            data = self.r.hgetall(key)
            if not data:
                continue

            # 获取五个字段的值并解码
            sessionid = data.get(b'sessionid', b'').decode('utf-8')
            user_id = data.get(b'id', b'').decode('utf-8')  # 对应user_id
            group_id = data.get(b'group_id', b'').decode('utf-8')
            messages = data.get(b'messages', b'').decode('utf-8')
            aimessages = data.get(b'aimessages', b'').decode('utf-8')

            # 用五元组作为唯一标识
            identifier = (sessionid, user_id, group_id, messages, aimessages)

            if identifier in seen:
                # 重复，删除该 key
                self.r.delete(key)
                deleted_count += 1
            else:
                # 第一次出现，加入 seen
                seen.add(identifier)

        print(f"[delete_duplicate_sessions] 删除重复会话数量: {deleted_count}")
        return deleted_count

    def find_user_session(self,sessionid):
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
        根据 sessionid、apply_id 和 group_id 三个条件查询会话数据
        """
        result_items = []

        # 遍历所有会话数据
        for key_bytes in self.r.keys('session:*'):
            key = key_bytes.decode('utf-8')
            data = self.r.hgetall(key)

            if not data:
                continue

            # 解码数据
            decoded_data = {k.decode('utf-8'): v.decode('utf-8') for k, v in data.items()}


            # 检查三个条件是否都匹配
            if (decoded_data.get('sessionid') == sessionid and
                    decoded_data.get('apply_id') == apply_id and
                    decoded_data.get('group_id') == group_id):
                history = {
                    "Query": decoded_data.get('messages'),
                    "Answer": decoded_data.get('aimessages')
                }


                result_items.append(history)

        # 如果结果少于等于1条，返回空列表
        if len(result_items) <= 1:
            result_items = []

        return result_items

store = RedisSessionStore(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
    session_id=str(uuid.uuid4())
)
