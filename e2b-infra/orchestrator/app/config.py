"""Orchestrator configuration"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Orchestrator settings"""
    
    # Server
    ORCHESTRATOR_PORT: int = 3001
    API_SECRET: str = "changeme"
    LOG_LEVEL: str = "info"
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/10"
    
    # Sandbox limits
    MAX_SANDBOXES: int = 50
    DEFAULT_SANDBOX_TIMEOUT: int = 300  # 5 minutes
    MAX_SANDBOX_TIMEOUT: int = 3600  # 1 hour
    
    # Firecracker
    FC_BINARY_PATH: str = "/usr/local/bin/firecracker"
    FC_KERNEL_PATH: str = "/var/e2b/kernels/vmlinux"
    
    # Storage
    TEMPLATE_STORAGE_PATH: str = "/var/e2b/templates"
    SANDBOX_STORAGE_PATH: str = "/var/e2b/sandboxes"
    
    # Sandbox resource defaults
    DEFAULT_VCPU_COUNT: int = 2
    DEFAULT_MEM_SIZE_MB: int = 512
    MAX_VCPU_COUNT: int = 8
    MAX_MEM_SIZE_MB: int = 4096
    
    # Network
    SANDBOX_NETWORK_CIDR: str = "10.100.0.0/16"
    CALLBACK_ALLOWED_HOSTS: str = "*"
    
    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
