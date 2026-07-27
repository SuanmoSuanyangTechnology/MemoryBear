"""Orchestrator configuration."""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    PORT: int = int(os.getenv("E2B_ORCHESTRATOR_PORT", "3001"))
    API_KEY: str = os.getenv("E2B_ORCHESTRATOR_SECRET", "changeme")
    LOG_LEVEL: str = os.getenv("ORCHESTRATOR_LOG_LEVEL", "info")

    # Redis (reuses project Redis, same host/port, different key prefix)
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("ORCHESTRATOR_REDIS_DB", "10"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    # Docker hosts: comma-separated, e.g. "unix:///var/run/docker.sock,tcp://host2:2375"
    DOCKER_HOSTS: str = os.getenv("ORCHESTRATOR_DOCKER_HOSTS", "unix:///var/run/docker.sock")

    # Sandbox limits
    SANDBOX_TIMEOUT: int = int(os.getenv("E2B_SANDBOX_TIMEOUT", "300"))
    MAX_SANDBOXES: int = int(os.getenv("ORCHESTRATOR_MAX_SANDBOXES", "50"))
    SANDBOX_MEMORY_MB: int = int(os.getenv("E2B_SANDBOX_MEMORY_MB", "512"))
    SANDBOX_CPU: int = int(os.getenv("E2B_SANDBOX_CPU", "2"))

    # Agent runner image
    TEMPLATE_ID: str = os.getenv("E2B_TEMPLATE_ID", "agent-runtime")
    TEMPLATE_REGISTRY: str = os.getenv("E2B_TEMPLATE_REGISTRY", "")

    @property
    def template_image(self) -> str:
        """Full qualified image name, e.g. harbor.rboa.redbearai.com/rb/agent-runtime"""
        if self.TEMPLATE_REGISTRY:
            return f"{self.TEMPLATE_REGISTRY}/{self.TEMPLATE_ID}"
        return self.TEMPLATE_ID

    # Warm pool
    WARM_POOL_SIZE: int = int(os.getenv("E2B_WARM_POOL_SIZE", "2"))

    @property
    def container_mem_limit(self) -> str:
        return f"{self.SANDBOX_MEMORY_MB}m"

    @property
    def container_cpu_limit(self) -> int:
        return self.SANDBOX_CPU * 1_000_000_000

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
