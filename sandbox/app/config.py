"""Configuration management"""
import os
from typing import List, Optional
from pydantic import BaseModel, Field
import yaml

SANDBOX_USER_ID = 1000
SANDBOX_GROUP_ID = 1000

DEFAULT_PYTHON_LIB_REQUIREMENTS_AMD = [
    "/usr/local/lib/python3.12",
    "/usr/lib/python3",
    "/usr/lib/x86_64-linux-gnu",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/nsswitch.conf",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/run/systemd/resolve/stub-resolv.conf",
    "/run/resolvconf/resolv.conf",
    "/etc/localtime",
    "/usr/share/zoneinfo",
    "/etc/timezone",
]


class AppConfig(BaseModel):
    """Application configuration"""
    port: int = 8194
    debug: bool = True
    key: str = "redbear-sandbox"


class ProxyConfig(BaseModel):
    """Proxy configuration"""
    socks5: str = ""
    http: str = ""
    https: str = ""


class Config(BaseModel):
    """Global configuration"""
    app: AppConfig = Field(default_factory=AppConfig)
    max_workers: int = 4
    max_requests: int = 50
    worker_timeout: int = 30
    nodejs_path: str = "node"
    enable_network: bool = True
    enable_preload: bool = False

    python_path: str = ""
    python_lib_paths: list = Field(default=DEFAULT_PYTHON_LIB_REQUIREMENTS_AMD)
    python_deps_update_interval: str = "30m"
    allowed_syscalls: List[int] = Field(default_factory=list)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)


# Global configuration instance
_config: Optional[Config] = None


def load_config(config_path: str) -> Config:
    """Load configuration from YAML file"""
    global _config

    # Load from file
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f)
            _config = Config(**data)
    else:
        _config = Config()

    # Override with environment variables
    if os.getenv("DEBUG"):
        _config.app.debug = os.getenv("DEBUG").lower() in ("true", "1", "yes")

    if os.getenv("MAX_WORKERS"):
        _config.max_workers = int(os.getenv("MAX_WORKERS"))

    if os.getenv("MAX_REQUESTS"):
        _config.max_requests = int(os.getenv("MAX_REQUESTS"))

    if os.getenv("SANDBOX_PORT"):
        _config.app.port = int(os.getenv("SANDBOX_PORT"))

    if os.getenv("WORKER_TIMEOUT"):
        _config.worker_timeout = int(os.getenv("WORKER_TIMEOUT"))

    if os.getenv("API_KEY"):
        _config.app.key = os.getenv("API_KEY")

    if os.getenv("NODEJS_PATH"):
        _config.nodejs_path = os.getenv("NODEJS_PATH")

    if os.getenv("ENABLE_NETWORK"):
        _config.enable_network = os.getenv("ENABLE_NETWORK").lower() in ("true", "1", "yes")

    if os.getenv("ENABLE_PRELOAD"):
        _config.enable_preload = os.getenv("ENABLE_PRELOAD").lower() in ("true", "1", "yes")

    if os.getenv("ALLOWED_SYSCALLS"):
        _config.allowed_syscalls = [int(x) for x in os.getenv("ALLOWED_SYSCALLS").split(",")]

    if os.getenv("SOCKS5_PROXY"):
        _config.proxy.socks5 = os.getenv("SOCKS5_PROXY")

    if os.getenv("HTTP_PROXY"):
        _config.proxy.http = os.getenv("HTTP_PROXY")

    if os.getenv("HTTPS_PROXY"):
        _config.proxy.https = os.getenv("HTTPS_PROXY")

    # python
    if os.getenv("PYTHON_PATH"):
        _config.python_path = os.getenv("PYTHON_PATH")

    if os.getenv("PYTHON_LIB_PATH"):
        _config.python_lib_paths = os.getenv("PYTHON_LIB_PATH").split(',')

    if os.getenv("PYTHON_DEPS_UPDATE_INTERVAL"):
        _config.python_deps_update_interval = os.getenv("PYTHON_DEPS_UPDATE_INTERVAL")

    return _config


config_path = os.getenv("CONFIG_PATH", "config.yaml")
load_config(config_path)


def get_config() -> Config:
    """Get global configuration"""
    if _config is None:
        raise RuntimeError("Configuration not loaded. Call load_config() first.")
    return _config
