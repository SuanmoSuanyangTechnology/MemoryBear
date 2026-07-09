"""Redis-based state storage for sandbox and template metadata"""
import json
import logging
from typing import Optional

import redis.asyncio as aioredis

from app.models import SandboxInfo, SandboxStatus, TemplateInfo

logger = logging.getLogger(__name__)

SANDBOX_KEY_PREFIX = "e2b:sandbox:"
SANDBOX_SET_KEY = "e2b:sandboxes"
TEMPLATE_KEY_PREFIX = "e2b:template:"
TEMPLATE_SET_KEY = "e2b:templates"


class RedisStore:
    """Redis state store for orchestrator"""

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        self._redis = aioredis.from_url(
            self.redis_url,
            decode_responses=True,
            max_connections=20,
        )
        await self._redis.ping()
        logger.info("Redis store connected")

    async def disconnect(self):
        if self._redis:
            await self._redis.close()
            logger.info("Redis store disconnected")

    # ──────────────────────────────────────────────────────────
    # Sandbox operations
    # ──────────────────────────────────────────────────────────

    async def save_sandbox(self, sandbox: SandboxInfo) -> None:
        key = f"{SANDBOX_KEY_PREFIX}{sandbox.sandbox_id}"
        data = sandbox.model_dump_json()
        async with self._redis.pipeline() as pipe:
            pipe.set(key, data)
            pipe.sadd(SANDBOX_SET_KEY, sandbox.sandbox_id)
            await pipe.execute()

    async def get_sandbox(self, sandbox_id: str) -> Optional[SandboxInfo]:
        key = f"{SANDBOX_KEY_PREFIX}{sandbox_id}"
        data = await self._redis.get(key)
        if not data:
            return None
        return SandboxInfo.model_validate_json(data)

    async def delete_sandbox(self, sandbox_id: str) -> None:
        key = f"{SANDBOX_KEY_PREFIX}{sandbox_id}"
        async with self._redis.pipeline() as pipe:
            pipe.delete(key)
            pipe.srem(SANDBOX_SET_KEY, sandbox_id)
            await pipe.execute()

    async def update_sandbox_status(self, sandbox_id: str, status: SandboxStatus, **extra_fields) -> None:
        sandbox = await self.get_sandbox(sandbox_id)
        if sandbox:
            sandbox.status = status
            for k, v in extra_fields.items():
                if hasattr(sandbox, k):
                    setattr(sandbox, k, v)
            await self.save_sandbox(sandbox)

    async def list_sandboxes(self, status: Optional[str] = None) -> list[SandboxInfo]:
        sandbox_ids = await self._redis.smembers(SANDBOX_SET_KEY)
        sandboxes = []
        for sid in sandbox_ids:
            sandbox = await self.get_sandbox(sid)
            if sandbox:
                if status and sandbox.status != status:
                    continue
                sandboxes.append(sandbox)
        return sandboxes

    async def get_active_sandbox_count(self) -> int:
        sandboxes = await self.list_sandboxes()
        return sum(
            1 for s in sandboxes
            if s.status in (SandboxStatus.CREATING, SandboxStatus.RUNNING)
        )

    # ──────────────────────────────────────────────────────────
    # Template operations
    # ──────────────────────────────────────────────────────────

    async def save_template(self, template: TemplateInfo) -> None:
        key = f"{TEMPLATE_KEY_PREFIX}{template.template_id}"
        data = template.model_dump_json()
        async with self._redis.pipeline() as pipe:
            pipe.set(key, data)
            pipe.sadd(TEMPLATE_SET_KEY, template.template_id)
            await pipe.execute()

    async def get_template(self, template_id: str) -> Optional[TemplateInfo]:
        key = f"{TEMPLATE_KEY_PREFIX}{template_id}"
        data = await self._redis.get(key)
        if not data:
            return None
        return TemplateInfo.model_validate_json(data)

    async def get_template_by_name(self, name: str) -> Optional[TemplateInfo]:
        """Find template by name"""
        templates = await self.list_templates()
        for t in templates:
            if t.name == name:
                return t
        return None

    async def list_templates(self) -> list[TemplateInfo]:
        template_ids = await self._redis.smembers(TEMPLATE_SET_KEY)
        templates = []
        for tid in template_ids:
            template = await self.get_template(tid)
            if template:
                templates.append(template)
        return templates

    async def delete_template(self, template_id: str) -> None:
        key = f"{TEMPLATE_KEY_PREFIX}{template_id}"
        async with self._redis.pipeline() as pipe:
            pipe.delete(key)
            pipe.srem(TEMPLATE_SET_KEY, template_id)
            await pipe.execute()
