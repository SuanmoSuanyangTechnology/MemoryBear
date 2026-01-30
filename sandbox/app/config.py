"""Configuration management"""
import os
from typing import List, Optional
from pydantic import BaseModel, Field
import yaml

DEFAULT_PYTHON_LIB_REQUIREMENTS_AMD = [
    "/usr/local/lib/python3.12",
    "/usr/lib/python3",
    "/usr/lib/x86_64-linux-gnu",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/nsswitch.conf",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/localtime",
    "/usr/share/zoneinfo",
    "/etc/timezone",
]

DEFAULT_NODEJS_LIB_REQUIREMENTS = [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/nsswitch.conf",
    "/etc/resolv.conf",
    "/etc/hosts",
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

    enable_network: bool = True
    enable_preload: bool = False

    python_path: str = ""
    python_lib_paths: list = Field(default=DEFAULT_PYTHON_LIB_REQUIREMENTS_AMD)
    python_deps_update_interval: str = "30m"

    nodejs_path: str = ""
    nodejs_lib_paths: list = Field(default=DEFAULT_NODEJS_LIB_REQUIREMENTS)

    allowed_syscalls: List[int] = Field(default_factory=list)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)

    sandbox_user: str = "sandbox"
    sandbox_uid: int = 65537
    sandbox_gid: int = 0

    def set_sandbox_gid(self, gid: int):
        """Update sandbox GID dynamically"""
        self.sandbox_gid = gid

    def override_with_env(self):
        """Override configuration with environment variables"""
        env_map = {
            "DEBUG": ("app.debug", lambda v: v.lower() in ("true", "1", "yes")),
            "MAX_WORKERS": ("max_workers", int),
            "MAX_REQUESTS": ("max_requests", int),
            "SANDBOX_PORT": ("app.port", int),
            "WORKER_TIMEOUT": ("worker_timeout", int),
            "API_KEY": ("app.key", str),
            "NODEJS_PATH": ("nodejs_path", str),
            "ENABLE_NETWORK": ("enable_network", lambda v: v.lower() in ("true", "1", "yes")),
            "ENABLE_PRELOAD": ("enable_preload", lambda v: v.lower() in ("true", "1", "yes")),
            "ALLOWED_SYSCALLS": ("allowed_syscalls", lambda v: [int(x) for x in v.split(",")]),
            "SOCKS5_PROXY": ("proxy.socks5", str),
            "HTTP_PROXY": ("proxy.http", str),
            "HTTPS_PROXY": ("proxy.https", str),
            "PYTHON_PATH": ("python_path", str),
            "PYTHON_LIB_PATH": ("python_lib_paths", lambda v: v.split(",")),
            "PYTHON_DEPS_UPDATE_INTERVAL": ("python_deps_update_interval", str),
            "NODEJS_LIB_PATH": ("nodejs_lib_paths", lambda v: v.split(",")),
        }

        for env_var, (attr_path, cast) in env_map.items():
            value = os.getenv(env_var)
            if value is not None:
                # Support nested attributes like 'app.debug'
                parts = attr_path.split(".")
                obj = self
                for part in parts[:-1]:
                    obj = getattr(obj, part)
                setattr(obj, parts[-1], cast(value))


# Global configuration instance
_config: Optional[Config] = None


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from YAML file and override with env variables"""
    global _config
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            data = yaml.safe_load(f) or {}
            _config = Config(**data)
    else:
        _config = Config()

    # Override from environment
    _config.override_with_env()
    return _config


config_path = os.getenv("CONFIG_PATH", "config.yaml")
load_config(config_path)


def get_config() -> Config:
    """Get global configuration"""
    if _config is None:
        raise RuntimeError("Configuration not loaded. Call load_config() first.")
    return _config
