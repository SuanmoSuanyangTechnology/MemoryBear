"""身份与计费服务配置。

DB/Redis 连接参数复用老单体 .env 的原始变量命名（DB_HOST/DB_PORT/DB_USER/
DB_PASSWORD/DB_NAME、REDIS_HOST/REDIS_PORT/REDIS_DB），部署时沿用根 .env
即可；默认值为常规端口（5432/6379），直连真实库。
"""
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ---- 数据库（PostgreSQL）----
    # 变量命名与 core/api/app/core/config.py 一致；默认值为常规端口（5432/6379），直连真实库
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME: str = os.getenv("DB_NAME", "redbear-mem-enterprise")
    # 连接池参数（core 同名配置；微服务按实例负载调小默认值：单体 50/20 → 服务 10/5）
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "10"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "5"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

    # 微服务全部异步（asyncpg），统一 async URL；密码含特殊字符须 URL 编码
    DATABASE_URL: str = (
        f"postgresql+asyncpg://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )

    # ---- Redis ----
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD") or None
    REDIS_URL: str = (
        f"redis://:{quote_plus(REDIS_PASSWORD)}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
        if REDIS_PASSWORD
        else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    )
    # 微服务新增：Redis 单命令超时（ms）——快照读取 fail-closed 的判定窗口
    REDIS_CMD_TIMEOUT_MS: int = int(os.getenv("REDIS_CMD_TIMEOUT_MS", "500"))

    # ---- 内部 token 密钥注入（决策 #15：identity 为唯一权威，K8s Secret 管理、不轮换）----
    # 与 gateway 同源变量（INTERNAL_ISSUER_PRIVATE_KEY/INTERNAL_ISSUER_KID），两侧值必须一致；
    # 缺失主密钥时启动即报错（fail-fast）；轮换由 K8s Secret 变更 + 部署重启承载，无代码侧叠加窗口
    INTERNAL_ISSUER_PRIVATE_KEY: str = os.getenv("INTERNAL_ISSUER_PRIVATE_KEY")
    INTERNAL_ISSUER_KID: str = os.getenv("INTERNAL_ISSUER_KID", "internal-1")

    # ---- 服务自身配置 ----
    SERVICE_NAME: str = "identity"
    AUDIT_QUEUE_KEY: str = os.getenv("AUDIT_QUEUE_KEY", "audit:queue")
    AUDIT_STREAM_KEY: str = os.getenv("AUDIT_STREAM_KEY", "audit:stream")
    # 审计保留（天）：0 禁用清理；设计 §7 要求 append-only 审计流 ≥180 天
    AUDIT_RETENTION_DAYS: int = int(os.getenv("AUDIT_RETENTION_DAYS", "180"))
    AUDIT_RETENTION_INTERVAL_SEC: int = int(os.getenv("AUDIT_RETENTION_INTERVAL_SEC", "3600"))
    # 快照定时校正任务（reconcile）扫描间隔（秒），补偿老单体埋点丢失的快照失效事件
    RECONCILE_INTERVAL_SEC: int = int(os.getenv("RECONCILE_INTERVAL_SEC", "60"))


settings = Settings()
