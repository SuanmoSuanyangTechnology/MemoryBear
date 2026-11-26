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
    REDIS_DB: int = int(os.getenv("REDIS_DB", "1"))
    REDIS_PASSWORD: str | None = os.getenv("REDIS_PASSWORD") or None
    REDIS_URL: str = (
        f"redis://:{quote_plus(REDIS_PASSWORD)}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
        if REDIS_PASSWORD
        else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    )

    # ---- 内部 token 密钥注入（决策 #15：identity 为唯一权威，K8s Secret 管理、不轮换）----
    # 与 gateway 同源变量（INTERNAL_ISSUER_PRIVATE_KEY/INTERNAL_ISSUER_KID），两侧值必须一致；
    # 缺失主密钥时启动即报错（fail-fast）；轮换由 K8s Secret 变更 + 部署重启承载，无代码侧叠加窗口
    INTERNAL_ISSUER_PRIVATE_KEY: str = os.getenv("INTERNAL_ISSUER_PRIVATE_KEY")
    INTERNAL_ISSUER_KID: str = os.getenv("INTERNAL_ISSUER_KID", "internal-1")

    # ---- 服务自身配置 ----
    SERVICE_NAME: str = "identity"
    # The audit stream key is deployment-scoped: empty derives
    # audit:stream:<DB_NAME> so deployments sharing one Redis do not consume
    # each other's events (a shared stream would load-balance events across
    # deployments through the same consumer group).
    AUDIT_STREAM_KEY: str = (
        os.getenv("AUDIT_STREAM_KEY", "").strip() or f"audit:stream:{DB_NAME}"
    )
    # 审计保留（天）：0 禁用清理；设计 §7 要求 append-only 审计流 ≥180 天
    AUDIT_RETENTION_DAYS: int = int(os.getenv("AUDIT_RETENTION_DAYS", "180"))
    AUDIT_RETENTION_INTERVAL_SEC: int = int(os.getenv("AUDIT_RETENTION_INTERVAL_SEC", "3600"))
    # 快照定时校正任务（reconcile）扫描间隔（秒），补偿老单体埋点丢失的快照失效事件
    RECONCILE_INTERVAL_SEC: int = int(os.getenv("RECONCILE_INTERVAL_SEC", "60"))
    # AUTH_INVALIDATION_CHANNEL: subscriber side of the invalidation channel; must
    # match the monolith publisher. Empty derives auth:invalidations:<DB_NAME> so
    # deployments sharing one Redis but owning separate databases stay isolated
    # (same convention as model-service's channel-invalidations).
    AUTH_INVALIDATION_CHANNEL: str = (
        os.getenv("AUTH_INVALIDATION_CHANNEL", "").strip()
        or f"auth:invalidations:{DB_NAME}"
    )
    # ACL rule blob key and hot-reload channel. Empty derives
    # acl:rules:<DB_NAME> / acl:rules:changed:<DB_NAME>; deployments sharing one
    # Redis but owning separate databases must not overwrite each other's rule
    # set. The channel is namespaced too: Redis pub/sub is global, not scoped
    # by logical DB.
    ACL_RULES_KEY: str = (
        os.getenv("ACL_RULES_KEY", "").strip() or f"acl:rules:{DB_NAME}"
    )
    ACL_RULES_CHANGED_CHANNEL: str = (
        os.getenv("ACL_RULES_CHANGED_CHANNEL", "").strip()
        or f"acl:rules:changed:{DB_NAME}"
    )

    # ---- 管理面（sync）与老单体的兼容配置 ----
    # 同步 URL 与 DATABASE_URL 同库同用户，仅驱动换 psycopg2；
    # UTC + 60s statement_timeout 在 db.init_sync_db() 的 connect_args 中注入
    DATABASE_SYNC_URL: str = (
        f"postgresql+psycopg2://{DB_USER}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    # 同步 Redis 线程本地连接池上限（与老单体同名同默认）
    REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "100"))

    # ---- JWT / 会话语义（名称、取值、默认值必须与 core/api/app/core/config.py 一致）----
    # 两侧签发/验签、黑名单、单会话键位共享，切流与回滚期间 token 必须双向可用
    SECRET_KEY: str = os.getenv("SECRET_KEY", "a_default_secret_key_that_is_long_and_random")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    ENABLE_SINGLE_SESSION: bool = (
        os.getenv("ENABLE_SINGLE_SESSION", "false").lower() == "true"
    )
    ENABLE_SINGLE_WORKSPACE: bool = (
        os.getenv("ENABLE_SINGLE_WORKSPACE", "true").lower() == "true"
    )

    # ---- SMTP（同步 smtplib：邮箱验证码与邀请邮件）----
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")

    # ---- 首超管 bootstrap（POST /api/setup，仅在无超管时生效）----
    FIRST_SUPERUSER_EMAIL: str = os.getenv("FIRST_SUPERUSER_EMAIL", "admin@example.com")
    FIRST_SUPERUSER_USERNAME: str = os.getenv("FIRST_SUPERUSER_USERNAME", "admin")
    FIRST_SUPERUSER_PASSWORD: str = os.getenv("FIRST_SUPERUSER_PASSWORD", "admin_password")
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # ---- i18n ----
    I18N_DEFAULT_LANGUAGE: str = os.getenv("I18N_DEFAULT_LANGUAGE", "zh")
    I18N_SUPPORTED_LANGUAGES: list[str] = [
        lang.strip().lower().split("-")[0]
        for lang in os.getenv("I18N_SUPPORTED_LANGUAGES", "zh,en").split(",")
        if lang.strip()
    ]

    # ---- 日志敏感字段过滤（与老单体同名同默认）----
    ENABLE_SENSITIVE_DATA_FILTER: bool = (
        os.getenv("ENABLE_SENSITIVE_DATA_FILTER", "true").lower() == "true"
    )

    # ---- 出站 model-service（空间模型选择校验，Task 8）----
    # read 必须对齐 model-service 探活预算：其 validate 内部按 LLM_TIMEOUT
    # （默认 120s）等真实上游，10s 会把正常慢探活误判为 verify_failed
    MODEL_SERVICE_BASE_URL: str = os.getenv(
        "MODEL_SERVICE_BASE_URL", "http://127.0.0.1:8080"
    )
    MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS: float = float(
        os.getenv("MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS", "5")
    )
    MODEL_SERVICE_READ_TIMEOUT_SECONDS: float = float(
        os.getenv("MODEL_SERVICE_READ_TIMEOUT_SECONDS", "120")
    )

    # ---- 出站老单体（W2-A 过渡回调：在途 reembed job id 与 workspace_models 原子写）----
    # 回调写单元保持老单体单事务（决策 D1）；密钥两端同名同值，未配置时
    # PUT 失败关闭（503）、job id 降级 null
    MONOLITH_BASE_URL: str = os.getenv("MONOLITH_BASE_URL", "http://api:8000")
    MONOLITH_CONNECT_TIMEOUT_SECONDS: float = float(
        os.getenv("MONOLITH_CONNECT_TIMEOUT_SECONDS", "5")
    )
    # 覆盖老单体探活预算（LLM_TIMEOUT 默认 120s）
    MONOLITH_READ_TIMEOUT_SECONDS: float = float(
        os.getenv("MONOLITH_READ_TIMEOUT_SECONDS", "180")
    )
    IDENTITY_CALLBACK_SECRET: str = os.getenv("IDENTITY_CALLBACK_SECRET", "")

    # ---- Celery 生产端（identity 不跑 worker；任务由 notification-service 消费）----
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "")
    REDIS_DB_CELERY_BROKER: int = int(os.getenv("REDIS_DB_CELERY_BROKER", "3"))

    @property
    def celery_broker(self) -> str:
        if self.CELERY_BROKER_URL.strip():
            return self.CELERY_BROKER_URL.strip()
        auth = f":{quote_plus(self.REDIS_PASSWORD)}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB_CELERY_BROKER}"


settings = Settings()
