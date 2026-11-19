from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "file-service"
    auth_mode: str = "direct"                     # direct | gateway
    identity_jwks_url: str = ""                   # gateway 模式必填
    file_service_internal_token: SecretStr = SecretStr("")  # X-File-Service-Token
    database_url_override: str = ""               # 测试/特殊环境整串覆盖
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: SecretStr = SecretStr("")
    db_name: str = "memorybear"
    file_local_server_url: str = "http://localhost:8000/api"
    file_url_expires: int = 3600
    max_file_size: int = 52428800
    file_info_allow_private_networks: bool = False  # info-by-url SSRF 例外开关
    secret_key: SecretStr = SecretStr("")         # 与老单体同值，签名 URL
    storage_type: str = "local"
    file_path: str = "/files"                     # 与 application-service 一致：storage/files
    minio_endpoint_url: str = ""
    minio_access_key_id: str = ""
    minio_secret_access_key: SecretStr = SecretStr("")
    minio_bucket_name: str = ""
    minio_region: str = ""
    oss_endpoint: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: SecretStr = SecretStr("")
    oss_bucket_name: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_bucket_name: str = ""
    s3_endpoint_url: str = ""

    @property
    def database_url_async(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        pwd = quote(self.db_password.get_secret_value(), safe="")
        return (f"postgresql+asyncpg://{quote(self.db_user, safe='')}:{pwd}"
                f"@{self.db_host}:{self.db_port}/{quote(self.db_name, safe='')}")

    @property
    def database_url_sync(self) -> str:
        return self.database_url_async.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def current_settings() -> Settings:
    """Shim kept so modules copied from application-service compile unchanged."""
    return settings
