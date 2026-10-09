"""请求失败触发的渠道熔断冷却（M9）：分类 → 软排除 → 守卫 UPDATE + 失效广播。

- 触发分类（仅渠道健康问题置冷）：HTTP 429 → rate_limit、401/403 → auth、连接/超时类
  瞬时错误 → connection；5xx（服务端短窗口自愈）、terminal 400 类、凭据解密失败
  （本地凭据问题，渠道无辜）不冷却。
- 读侧软排除（build_availability_hook）：存在未冷却候选时跳过冷却渠道；全冷却时返回
  None 放行整链按计划序（背压语义——全挂时仍尝试，失败再置冷，不 409 拒绝）。
- 写侧（CooldownTracker + mark_cooldowns）：请求内零 IO 收集放弃决策点的置冷决定，
  ``finally`` 建后台任务落库；守卫 UPDATE（SQL 级 max，防并发回拨）rowcount>0 才按
  (tenant_id, provider) 去重广播 ``notify_channel_change``（本地失效 + 跨副本广播）。
  全程 best-effort：写失败只告警不阻断调用链（快照 TTL 60s 兜底收敛）。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from redbear_model import (
    ChannelSnapshot,
    CredentialDecryptError,
    FailoverCandidate,
    is_transient_channel_error,
    provider_http_status,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..models.models_model import ModelChannel
from .channel_registry import notify_channel_change

logger = logging.getLogger(__name__)


def cooldown_trigger_reason(exc: BaseException) -> str | None:
    """失败 → 触发原因（不冷却返回 None）；分类矩阵见模块 docstring。"""
    if isinstance(exc, CredentialDecryptError):
        return None
    status = provider_http_status(exc)
    if status is not None:
        if status == 429:
            return "rate_limit"
        if status in (401, 403):
            return "auth"
        return None
    if is_transient_channel_error(exc):
        return "connection"
    return None


def is_cooled(channel: ChannelSnapshot, *, now_ms: int) -> bool:
    return channel.cooldown_until_ms is not None and channel.cooldown_until_ms > now_ms


def build_availability_hook(
    candidates: Sequence[FailoverCandidate],
    *,
    enabled: bool,
    now_ms: int | None = None,
) -> Callable[[FailoverCandidate], bool] | None:
    """软排除谓词：存在未冷却候选 → 跳过冷却项；全冷却/开关关 → None（放行整链）。"""
    if not enabled:
        return None
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if all(is_cooled(candidate.channel, now_ms=now) for candidate in candidates):
        return None
    return lambda candidate: not is_cooled(candidate.channel, now_ms=now)


@dataclass(frozen=True)
class CooldownEntry:
    channel_id: uuid.UUID
    tenant_id: uuid.UUID
    provider: str
    until_ms: int


class CooldownTracker:
    """请求内零 IO 收集置冷决定；flush() 建后台任务落库（best-effort，不阻退出）。"""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
        enabled: bool,
        seconds: int,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._enabled = enabled and sessionmaker is not None
        self._seconds = seconds
        self._entries: dict[uuid.UUID, CooldownEntry] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def record(self, candidate: FailoverCandidate, exc: BaseException) -> None:
        """编排层放弃决策点回调（解密失败/瞬时耗尽/可换渠道三处触发）。"""
        if not self._enabled:
            return
        reason = cooldown_trigger_reason(exc)
        if reason is None:
            return
        channel = candidate.channel
        # 同渠道多次放弃取最新 until（单调推进）；快照已过冷却期的重复置冷无害（守卫拦截）
        self._entries[channel.id] = CooldownEntry(
            channel_id=channel.id,
            tenant_id=channel.tenant_id,
            provider=channel.provider,
            until_ms=int(time.time() * 1000) + self._seconds * 1000,
        )
        logger.warning(
            "channel %s cooled for %ss (provider=%s, reason=%s)",
            channel.id,
            self._seconds,
            channel.provider,
            reason,
        )

    def flush(self) -> None:
        sessionmaker = self._sessionmaker
        if sessionmaker is None or not self._entries:
            return
        entries = tuple(self._entries.values())
        self._entries.clear()
        task = asyncio.get_running_loop().create_task(
            mark_cooldowns(sessionmaker, entries)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


async def mark_cooldowns(
    sessionmaker: async_sessionmaker[AsyncSession],
    entries: Sequence[CooldownEntry],
) -> None:
    """守卫 UPDATE（SQL 级 max 防回拨）→ rowcount>0 才广播失效；best-effort 全吞。"""
    notified: set[tuple[uuid.UUID, str]] = set()
    try:
        async with sessionmaker() as session:
            for entry in entries:
                result = await session.execute(
                    update(ModelChannel)
                    .where(
                        ModelChannel.id == entry.channel_id,
                        (ModelChannel.cooldown_until_ms.is_(None))
                        | (ModelChannel.cooldown_until_ms < entry.until_ms),
                    )
                    .values(cooldown_until_ms=entry.until_ms)
                )
                if result.rowcount > 0:
                    notified.add((entry.tenant_id, entry.provider))
            await session.commit()
    except Exception:
        logger.warning("熔断冷却写入失败（best-effort，快照 TTL 兜底）", exc_info=True)
        return
    for tenant_id, provider in notified:
        try:
            await asyncio.to_thread(notify_channel_change, tenant_id, provider)
        except Exception:
            logger.warning(
                "熔断冷却失效广播失败（本地已失效，他副本 TTL 兜底）: tenant=%s provider=%s",
                tenant_id,
                provider,
                exc_info=True,
            )
