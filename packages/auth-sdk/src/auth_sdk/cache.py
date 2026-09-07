"""出站内部 token 缓存：按 aud 缓存 + TTL 剩 1/3 提前刷新 + single-flight（4 节配套策略）。"""
import asyncio
import time
from dataclasses import dataclass

from jose.exceptions import JWTError

from auth_sdk.token import TokenIssuer
from auth_sdk.schema import UserContext


@dataclass
class _Entry:
    token: str
    issued_at: float
    expires_at: float


class TokenCache:
    def __init__(self, issuer: TokenIssuer, ttl: int = 120, refresh_at: float = 2 / 3,
                 identity: UserContext | None = None):
        self._issuer = issuer
        self._ttl = ttl
        self._refresh_at = refresh_at
        self._identity = identity or UserContext(user_id="service", tenant_id="", workspace_id="", roles=())
        self._entries: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    async def get(self, aud: str, tenant: str | None = None) -> str:
        key = (aud, tenant or "")
        entry = self._entries.get(key)
        now = time.monotonic()
        if entry is not None:
            # 提前刷新阈值：TTL 剩 1/3 内（即已过 2/3）触发刷新
            if now < entry.expires_at - self._ttl * (1 - self._refresh_at):
                return entry.token
        return await self._refresh(key, entry)

    async def _refresh(self, key: tuple, stale: _Entry | None) -> str:
        loop = asyncio.get_running_loop()
        future = self._inflight.get(key)          # single-flight：并发只签发一次
        if future is None:
            future = loop.create_future()
            self._inflight[key] = future
            try:
                token = await asyncio.wait_for(
                    asyncio.to_thread(self._issuer.issue_internal_token, self._identity, key[0]),
                    timeout=1.0)                  # 签发失败不无限挂起
                now = time.monotonic()
                self._entries[key] = _Entry(token=token, issued_at=now, expires_at=now + self._ttl)
                future.set_result(token)
            except asyncio.CancelledError:
                # leader 被取消：等待者收到旧 token 或失败信号，取消继续向上传播
                if stale is not None:
                    future.set_result(stale.token)
                else:
                    future.set_exception(RuntimeError(f"token issuance cancelled for {key[0]}"))
                    # 无等待者时主动取回异常，避免 GC 时 'Future exception was never retrieved' 警告
                    future.exception()
                raise
            except (JWTError, ValueError, TimeoutError):  # 签发失败/超时（jose encode 与 wait_for 超时）
                # 失败降级：有旧 token 就用旧 token 撑到 TTL 结束，不拒绝出站
                if stale is not None:
                    future.set_result(stale.token)
                else:
                    future.set_exception(RuntimeError(f"token issuance failed for {key[0]}"))
            finally:
                self._inflight.pop(key, None)
        return await future
