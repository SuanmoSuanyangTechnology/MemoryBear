"""快照读取：Redis 命中即返回；miss 回源（single-flight 防风暴）；Redis 故障抛 SnapshotUnavailable（fail-closed 由网关决定）。"""
import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import datetime

from redis.exceptions import RedisError

from auth_sdk.schema import ApiKeyContext, UserSnapshot

SNAPSHOT_TTL = 86400  # 24h 兜底（决策 #11 修订：通知是主要失效手段，TTL 只兜底丢失）


class SnapshotUnavailable(Exception):
    """快照数据不可得（Redis 故障或回源失败）——调用方应 fail-closed。"""


def api_key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def snapshot_to_json(snap: UserSnapshot) -> bytes:
    data = asdict(snap)
    tib = data.get("token_invalidated_before")
    if tib is not None:
        data["token_invalidated_before"] = tib.isoformat()
    return json.dumps(data).encode()


def snapshot_from_json(raw: bytes) -> UserSnapshot:
    data = json.loads(raw)
    tib = data.get("token_invalidated_before")
    if tib is not None:
        data["token_invalidated_before"] = datetime.fromisoformat(tib)
    return UserSnapshot(**data)


class SnapshotReader:
    def __init__(self, redis, backfill=None, timeout_ms: int = 100):
        self._redis = redis
        self._backfill = backfill          # async (user_id) -> UserSnapshot | None
        self._timeout = timeout_ms / 1000
        self._inflight: dict[str, asyncio.Future] = {}

    async def get_user_snapshot(self, user_id: str) -> UserSnapshot:
        raw = await self._get(f"user:{user_id}", renew=True)   # 命中即续期，快照随会话滑动
        if raw is not None:
            try:
                return snapshot_from_json(raw)
            except (TypeError, ValueError) as exc:  # JSON 解析/字段缺失/时间格式损坏 → 不可用（fail-closed）
                raise SnapshotUnavailable(f"corrupt snapshot for {user_id}: {exc}") from exc
        if self._backfill is None:
            raise SnapshotUnavailable(f"no snapshot and no backfill for {user_id}")
        return await self._singleflight(user_id)

    async def get_api_key_snapshot(self, api_key: str) -> ApiKeyContext | None:
        # 不续期：快照生命周期 = 写入时 TTL（identity 按 key 实际有效期设置，见 api_keys.py）。
        # API key 是长期静态凭证，无"访问续期"动作——续期为 24h 会让仍在有效期的 key
        # 在 24h 无访问后 miss（老单体里该 key 依然有效），且永不过期 key 会被续成 24h 错误缩短。
        raw = await self._get(f"api_key:{api_key_hash(api_key)}")
        if raw is None:
            return None
        try:
            return ApiKeyContext(**json.loads(raw))
        except (TypeError, ValueError) as exc:
            raise SnapshotUnavailable(f"corrupt api_key snapshot: {exc}") from exc

    async def is_token_blacklisted(self, jti: str) -> bool:
        """登出/单点踢人/刷新换新拉黑的 token（老单体写 token_blacklist:{jti}）→ 命中 True。
        不走续期：黑名单 TTL 由老单体按 REFRESH_TOKEN_EXPIRE_DAYS 管理，续期会缩短为 24h。"""
        raw = await self._get(f"token_blacklist:{jti}")
        return raw is not None

    async def _get(self, key: str, renew: bool = False) -> bytes | None:
        try:
            # renew=True 走 GETEX（Redis ≥6.2）：一条命令读 + 刷新 TTL，仍为 1 次 RTT
            if renew:
                return await asyncio.wait_for(self._redis.getex(key, ex=SNAPSHOT_TTL),
                                              timeout=self._timeout)
            return await asyncio.wait_for(self._redis.get(key), timeout=self._timeout)
        except (TimeoutError, RedisError) as exc:  # 连接失败/超时 → 统一不可用
            raise SnapshotUnavailable(str(exc)) from exc

    async def _set(self, key: str, value: bytes) -> None:
        # 5.3：写缓存失败不影响本次请求（下次回源再写），仅带超时防阻塞
        try:
            await asyncio.wait_for(self._redis.set(key, value, ex=SNAPSHOT_TTL),
                                   timeout=self._timeout)
        except (TimeoutError, RedisError):
            pass

    async def _singleflight(self, user_id: str) -> UserSnapshot:
        loop = asyncio.get_running_loop()
        future = self._inflight.get(user_id)
        if future is None:
            future = loop.create_future()
            self._inflight[user_id] = future
            try:
                snap = await self._backfill(user_id)
                if snap is None:
                    raise SnapshotUnavailable(f"backfill returned None for {user_id}")
                await self._set(f"user:{user_id}", snapshot_to_json(snap))
                future.set_result(snap)
            except asyncio.CancelledError:
                # leader 被取消：等待者收到 fail-closed 信号，取消继续向上传播
                future.set_exception(SnapshotUnavailable(f"backfill cancelled for {user_id}"))
                # 无等待者时异常从未被取回 → GC 触发 'Future exception was never retrieved'
                # 警告；exception() 主动取回不影响等待者（await future 仍会抛出该异常）
                future.exception()
                raise
            except Exception as exc:
                # 此处保留宽捕获是有意的：backfill 是外部回调（未来接 identity HTTP 接口），
                # 任何失败（网络/超时/业务异常）都等价于快照不可得——fail-closed 语义要求全捕获
                future.set_exception(SnapshotUnavailable(f"backfill failed for {user_id}: {exc}"))
            finally:
                self._inflight.pop(user_id, None)
        return await future
