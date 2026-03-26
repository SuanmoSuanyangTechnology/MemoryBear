# """
# Recent Activity Stats Cache

# 记忆提取活动统计缓存模块
# 用于缓存每次记忆提取流程的统计数据，按 workspace_id 存储，24小时后释放
# 查询命令：cache:memory:activity_stats:by_workspace:7de31a97-40a6-4fc0-b8d3-15c89f523843
# """
# import json
# import logging
# from typing import Optional, Dict, Any
# from datetime import datetime

# from app.aioRedis import aio_redis

# logger = logging.getLogger(__name__)

# # 缓存过期时间：24小时
# ACTIVITY_STATS_CACHE_EXPIRE = 86400


# class ActivityStatsCache:
#     """记忆提取活动统计缓存类"""

#     PREFIX = "cache:memory:activity_stats"

#     @classmethod
#     def _get_key(cls, workspace_id: str) -> str:
#         """生成 Redis key

#         Args:
#             workspace_id: 工作空间ID

#         Returns:
#             完整的 Redis key
#         """
#         return f"{cls.PREFIX}:by_workspace:{workspace_id}"

#     @classmethod
#     async def set_activity_stats(
#         cls,
#         workspace_id: str,
#         stats: Dict[str, Any],
#         expire: int = ACTIVITY_STATS_CACHE_EXPIRE,
#     ) -> bool:
#         """设置记忆提取活动统计缓存

#         Args:
#             workspace_id: 工作空间ID
#             stats: 统计数据，格式：
#                 {
#                     "chunk_count": int,
#                     "statements_count": int,
#                     "triplet_entities_count": int,
#                     "triplet_relations_count": int,
#                     "temporal_count": int,
#                 }
#             expire: 过期时间（秒），默认24小时

#         Returns:
#             是否设置成功
#         """
#         try:
#             key = cls._get_key(workspace_id)
#             payload = {
#                 "stats": stats,
#                 "generated_at": datetime.now().isoformat(),
#                 "workspace_id": workspace_id,
#                 "cached": True,
#             }
#             value = json.dumps(payload, ensure_ascii=False)
#             await aio_redis.set(key, value, ex=expire)
#             logger.info(f"设置活动统计缓存成功: {key}, 过期时间: {expire}秒")
#             return True
#         except Exception as e:
#             logger.error(f"设置活动统计缓存失败: {e}", exc_info=True)
#             return False

#     @classmethod
#     async def get_activity_stats(
#         cls,
#         workspace_id: str,
#     ) -> Optional[Dict[str, Any]]:
#         """获取记忆提取活动统计缓存

#         Args:
#             workspace_id: 工作空间ID

#         Returns:
#             统计数据字典，缓存不存在或已过期返回 None
#         """
#         try:
#             key = cls._get_key(workspace_id)
#             value = await aio_redis.get(key)
#             if value:
#                 payload = json.loads(value)
#                 logger.info(f"命中活动统计缓存: {key}")
#                 return payload
#             logger.info(f"活动统计缓存不存在或已过期: {key}")
#             return None
#         except Exception as e:
#             logger.error(f"获取活动统计缓存失败: {e}", exc_info=True)
#             return None

#     @classmethod
#     async def delete_activity_stats(
#         cls,
#         workspace_id: str,
#     ) -> bool:
#         """删除记忆提取活动统计缓存

#         Args:
#             workspace_id: 工作空间ID

#         Returns:
#             是否删除成功
#         """
#         try:
#             key = cls._get_key(workspace_id)
#             result = await aio_redis.delete(key)
#             logger.info(f"删除活动统计缓存: {key}, 结果: {result}")
#             return result > 0
#         except Exception as e:
#             logger.error(f"删除活动统计缓存失败: {e}", exc_info=True)
#             return False
