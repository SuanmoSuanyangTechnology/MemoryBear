"""失效通知发布（设计文档决策 #11 修订）：业务变更点发 pub/sub 消息，identity 重建快照。

async 端点/服务用 notify_*_async（await pubsub_manager.publish）；
sync 服务用 notify_*_sync（get_thread_safe_sync_redis().publish）。
publish 失败仅记日志，绝不阻塞主流程。
"""
import asyncio
import hashlib
import json
import logging
from typing import Any

from app.aioRedis import get_thread_safe_sync_redis, pubsub_manager

logger = logging.getLogger(__name__)

CHANNEL = "auth:invalidations"


def api_key_hash(plain: str) -> str:
    """与 auth_sdk/snapshot.py 的算法一致：sha256(完整明文).hexdigest()。"""
    return hashlib.sha256(plain.encode()).hexdigest()


async def _publish_async(message: dict[str, Any]) -> None:
    try:
        # 1s 超时：Redis 挂起（网络分区/CLIENT PAUSE）时不得无限挂起登录/refresh 热路径
        await asyncio.wait_for(pubsub_manager.publish(CHANNEL, message), timeout=1)
    except Exception:
        logger.exception("invalidation publish failed: %s", message)


def _publish_sync(message: dict[str, Any]) -> None:
    try:
        get_thread_safe_sync_redis().publish(CHANNEL, json.dumps(message, ensure_ascii=False))
    except Exception:
        logger.exception("invalidation publish failed: %s", message)


async def notify_user_async(user_id: str) -> None:
    await _publish_async({"kind": "user", "id": str(user_id)})


async def notify_api_key_async(api_key_hash_: str) -> None:
    await _publish_async({"kind": "api_key", "hash": api_key_hash_})


async def notify_api_key_created_async(plain: str) -> None:
    """API key 创建/重建通知：必须带明文，identity 才能删旧 + 直连 DB 组装新快照写回
    （网关快照 miss 即 401 无回源，不带明文的新 key 首次访问必然失败）。"""
    await _publish_async({"kind": "api_key", "hash": api_key_hash(plain), "key": plain})


async def notify_tenant_async(tenant_id: str) -> None:
    await _publish_async({"kind": "tenant", "id": str(tenant_id)})


def notify_user_sync(user_id: str) -> None:
    _publish_sync({"kind": "user", "id": str(user_id)})


def notify_api_key_sync(api_key_hash_: str) -> None:
    _publish_sync({"kind": "api_key", "hash": api_key_hash_})


def notify_api_key_created_sync(plain: str) -> None:
    """API key 创建/重建通知（见 notify_api_key_created_async：必须带明文）。"""
    _publish_sync({"kind": "api_key", "hash": api_key_hash(plain), "key": plain})


def notify_tenant_sync(tenant_id: str) -> None:
    _publish_sync({"kind": "tenant", "id": str(tenant_id)})
