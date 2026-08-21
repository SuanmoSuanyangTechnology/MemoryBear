import os
from pathlib import Path
from typing import Annotated, Optional

from dotenv import load_dotenv
from pydantic import Field, TypeAdapter

load_dotenv()


class Settings:
    # ========================================================================
    # Deployment Mode Configuration
    # ========================================================================
    # community: 社区版（开源，功能受限）
    # cloud: SaaS 云服务版（全功能，按量计费）
    # enterprise: 企业私有化版（License 控制）
    DEPLOYMENT_MODE: str = os.getenv("DEPLOYMENT_MODE", "community")

    # License 配置（企业版）
    LICENSE_FILE: str = os.getenv("LICENSE_FILE", "/etc/app/license.json")
    LICENSE_SERVER_URL: str = os.getenv("LICENSE_SERVER_URL", "https://license.yourcompany.com")

    # 计费服务配置（SaaS 版）
    BILLING_SERVICE_URL: str = os.getenv("BILLING_SERVICE_URL", "")

    # 基础 URL（用于 SSO 回调等）
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")

    ENABLE_SINGLE_WORKSPACE: bool = os.getenv("ENABLE_SINGLE_WORKSPACE", "true").lower() == "true"
    # API Keys Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # Speed Bear Service Configuration
    SPEEDBEAR_BASE_URL: str = os.getenv("SPEEDBEAR_BASE_URL", "https://testspeedbear.redbearai.com")
    SPEEDBEAR_AUTH_KEY: str = os.getenv("SPEEDBEAR_AUTH_KEY", "")
    SPEEDBEAR_TIMEOUT: float = float(os.getenv("SPEEDBEAR_TIMEOUT", "30"))
    SPEEDBEAR_SYSTEM_TENANT_ID: str = os.getenv(
        "SPEEDBEAR_SYSTEM_TENANT_ID", "00000000-0000-0000-0000-000000000000"
    )

    # Neo4j Configuration (记忆系统数据库)
    NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://1.94.111.67:7687")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    # Neo4j 连接池参数（通过环境变量可配）
    # 默认值参考文档建议值，兼顾资源占用与并发需求
    NEO4J_MAX_POOL_SIZE: int = int(os.getenv("NEO4J_MAX_POOL_SIZE", "30"))
    NEO4J_ACQ_TIMEOUT: float = float(os.getenv("NEO4J_ACQ_TIMEOUT", "30.0"))
    NEO4J_MAX_CONN_LIFETIME: int = int(os.getenv("NEO4J_MAX_CONN_LIFETIME", "3600"))
    NEO4J_CONN_TIMEOUT: float = float(os.getenv("NEO4J_CONN_TIMEOUT", "30.0"))

    # Database configuration (Postgres)
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "password")
    DB_NAME: str = os.getenv("DB_NAME", "redbear-mem")
    DB_POOL_SIZE: int = int(os.getenv("DB_POOL_SIZE", "50"))
    DB_MAX_OVERFLOW: int = int(os.getenv("DB_MAX_OVERFLOW", "20"))
    DB_POOL_RECYCLE: int = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    DB_POOL_TIMEOUT: int = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    DB_POOL_PRE_PING: bool = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

    # Batch persist queue for async message persistence
    BATCH_PERSIST_QUEUE_SIZE: int = int(os.getenv("BATCH_PERSIST_QUEUE_SIZE", "10000"))
    BATCH_PERSIST_MAX_BATCH: int = int(os.getenv("BATCH_PERSIST_MAX_BATCH", "50"))
    BATCH_PERSIST_MAX_WAIT_MS: int = int(os.getenv("BATCH_PERSIST_MAX_WAIT_MS", "500"))
    BATCH_PERSIST_PUT_TIMEOUT_MS: int = int(os.getenv("BATCH_PERSIST_PUT_TIMEOUT_MS", "100"))

    # Memory retrieval display queue (PG snapshot of user-visible memory reads)
    MEMORY_RETRIEVAL_DISPLAY_QUEUE_SIZE: int = int(
        os.getenv("MEMORY_RETRIEVAL_DISPLAY_QUEUE_SIZE", "5000")
    )
    MEMORY_RETRIEVAL_DISPLAY_MAX_BATCH: int = int(
        os.getenv("MEMORY_RETRIEVAL_DISPLAY_MAX_BATCH", "50")
    )
    MEMORY_RETRIEVAL_DISPLAY_MAX_WAIT_MS: int = int(
        os.getenv("MEMORY_RETRIEVAL_DISPLAY_MAX_WAIT_MS", "500")
    )

    DB_AUTO_UPGRADE = os.getenv("DB_AUTO_UPGRADE", "false").lower() == "true"

    # Redis configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "1"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "100"))

    # ElasticSearch configuration
    ELASTICSEARCH_HOST: str = os.getenv("ELASTICSEARCH_HOST", "https://127.0.0.1")
    ELASTICSEARCH_PORT: int = int(os.getenv("ELASTICSEARCH_PORT", "9200"))
    ELASTICSEARCH_USERNAME: str = os.getenv("ELASTICSEARCH_USERNAME", "elastic")
    ELASTICSEARCH_PASSWORD: str = os.getenv("ELASTICSEARCH_PASSWORD", "")
    ELASTICSEARCH_VERIFY_CERTS: bool = os.getenv("ELASTICSEARCH_VERIFY_CERTS", "False").lower() == "true"
    ELASTICSEARCH_CA_CERTS: str = os.getenv("ELASTICSEARCH_CA_CERTS", "")
    ELASTICSEARCH_REQUEST_TIMEOUT: int = int(os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT", "100000"))
    ELASTICSEARCH_RETRY_ON_TIMEOUT: bool = os.getenv("ELASTICSEARCH_RETRY_ON_TIMEOUT", "True").lower() == "true"
    ELASTICSEARCH_MAX_RETRIES: int = int(os.getenv("ELASTICSEARCH_MAX_RETRIES", "10"))
    KNOWLEDGE_RETRIEVAL_MAX_WORKERS: int = int(os.getenv("KNOWLEDGE_RETRIEVAL_MAX_WORKERS", "3"))
    KNOWLEDGE_RETRIEVAL_GRAPH_MAX_CONCURRENCY: int = int(
        os.getenv("KNOWLEDGE_RETRIEVAL_GRAPH_MAX_CONCURRENCY", "2")
    )
    KNOWLEDGE_GRAPH_EXTRACT_MAX_CONCURRENCY: int = max(
        1,
        min(16, int(os.getenv("KNOWLEDGE_GRAPH_EXTRACT_MAX_CONCURRENCY", "4"))),
    )
    KNOWLEDGE_GRAPH_RETRIEVAL_TIMEOUT_MS: int = max(
        100,
        min(30000, int(os.getenv("KNOWLEDGE_GRAPH_RETRIEVAL_TIMEOUT_MS", "15000"))),
    )

    # Xinference configuration
    XINFERENCE_URL: str = os.getenv("XINFERENCE_URL", "http://127.0.0.1")

    # LangSmith configuration
    LANGCHAIN_TRACING_V2: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true"
    LANGCHAIN_TRACING: bool = os.getenv("LANGCHAIN_TRACING", "false").lower() == "true"
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_ENDPOINT: str = os.getenv("LANGCHAIN_ENDPOINT", "")

    # LLM Request Configuration
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "120.0"))
    LLM_MAX_RETRIES: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    LLM_NETWORK_RETRY_ATTEMPTS: int = int(os.getenv("LLM_NETWORK_RETRY_ATTEMPTS", "3"))
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "10"))

    # Fast Write BERT 
    FAST_WRITE_EMOTION_URL: str = os.getenv("FAST_WRITE_EMOTION_URL", "")
    FAST_WRITE_EMOTION_MODEL: str = os.getenv("FAST_WRITE_EMOTION_MODEL", "")
    FAST_WRITE_EMOTION_API_KEY: str = os.getenv("FAST_WRITE_EMOTION_API_KEY", "")

    # JWT Token Configuration
    SECRET_KEY: str = os.getenv("SECRET_KEY", "a_default_secret_key_that_is_long_and_random")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Single Sign-On configuration
    ENABLE_SINGLE_SESSION: bool = os.getenv("ENABLE_SINGLE_SESSION", "false").lower() == "true"

    # SSO 免登配置
    SSO_TOKEN_EXPIRE_SECONDS: int = int(os.getenv("SSO_TOKEN_EXPIRE_SECONDS", "300"))
    SSO_TRUSTED_SOURCES_CONFIG: str = os.getenv("SSO_TRUSTED_SOURCES_CONFIG", "{}")

    # File Upload
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", "52428800"))
    MAX_FILE_COUNT: int = int(os.getenv("MAX_FILE_COUNT", "20"))
    MAX_CHUNK_BATCH_SIZE: int = int(os.getenv("MAX_CHUNK_BATCH_SIZE", "8"))
    FILE_PATH: str = os.getenv("FILE_PATH", "/files")
    FILE_URL_EXPIRES: int = int(os.getenv("FILE_URL_EXPIRES", "3600"))

    # Storage Configuration
    STORAGE_TYPE: str = os.getenv("STORAGE_TYPE", "local")

    # Aliyun OSS Configuration
    OSS_ENDPOINT: str = os.getenv("OSS_ENDPOINT", "")
    OSS_ACCESS_KEY_ID: str = os.getenv("OSS_ACCESS_KEY_ID", "")
    OSS_ACCESS_KEY_SECRET: str = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    OSS_BUCKET_NAME: str = os.getenv("OSS_BUCKET_NAME", "")

    # AWS S3 Configuration
    S3_REGION: str = os.getenv("S3_REGION", "")
    S3_ACCESS_KEY_ID: str = os.getenv("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY: str = os.getenv("S3_SECRET_ACCESS_KEY", "")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "")
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")

    # MinIO Configuration (required when STORAGE_TYPE=minio)
    # MinIO is a self-hosted S3-compatible object storage, ideal for development
    MINIO_ENDPOINT_URL: str = os.getenv("MINIO_ENDPOINT_URL", "")
    MINIO_ACCESS_KEY_ID: str = os.getenv("MINIO_ACCESS_KEY_ID", "")
    MINIO_SECRET_ACCESS_KEY: str = os.getenv("MINIO_SECRET_ACCESS_KEY", "")
    MINIO_BUCKET_NAME: str = os.getenv("MINIO_BUCKET_NAME", "")
    MINIO_REGION: str = os.getenv("MINIO_REGION", "")

    # VOLC ASR settings
    VOLC_APP_KEY: str = os.getenv("VOLC_APP_KEY", "")
    VOLC_ACCESS_KEY: str = os.getenv("VOLC_ACCESS_KEY", "")
    VOLC_SUBMIT_URL: str = os.getenv("VOLC_SUBMIT_URL", "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit")
    VOLC_QUERY_URL: str = os.getenv("VOLC_QUERY_URL", "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query")

    # Langfuse configuration
    LANGFUSE_ENABLED: bool = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "")

    # Server Configuration
    SERVER_IP: str = os.getenv("SERVER_IP", "127.0.0.1")
    FILE_LOCAL_SERVER_URL: str = os.getenv("FILE_LOCAL_SERVER_URL", "http://localhost:8000/api")

    # ========================================================================
    # Internal Configuration (not in .env, used by application code)
    # ========================================================================

    # Superuser settings (internal defaults)
    FIRST_SUPERUSER_EMAIL: str = os.getenv("FIRST_SUPERUSER_EMAIL", "admin@example.com")
    FIRST_SUPERUSER_USERNAME: str = os.getenv("FIRST_SUPERUSER_USERNAME", "admin")
    FIRST_SUPERUSER_PASSWORD: str = os.getenv("FIRST_SUPERUSER_PASSWORD", "admin_password")

    # Generic File Upload (internal)
    GENERIC_FILE_PATH: str = os.getenv("GENERIC_FILE_PATH", "/uploads")
    ENABLE_FILE_COMPRESSION: bool = os.getenv("ENABLE_FILE_COMPRESSION", "false").lower() == "true"
    ENABLE_VIRUS_SCAN: bool = os.getenv("ENABLE_VIRUS_SCAN", "false").lower() == "true"
    FILE_ACCESS_URL_PREFIX: str = os.getenv("FILE_ACCESS_URL_PREFIX", "http://localhost:8000/api/files")

    # Frontend URL for workspace invitations (internal)
    WEB_URL: str = os.getenv("WEB_URL", "http://localhost:3000")

    # CORS configuration (internal)
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]

    # Language Configuration
    # Supported values: "zh" (Chinese), "en" (English)
    # This controls the language used for memory summary titles and other generated content
    DEFAULT_LANGUAGE: str = os.getenv("DEFAULT_LANGUAGE", "zh")

    # ========================================================================
    # Internationalization (i18n) Configuration
    # ========================================================================
    # Default language for API responses
    I18N_DEFAULT_LANGUAGE: str = os.getenv("I18N_DEFAULT_LANGUAGE", "zh")
    
    # Supported languages (comma-separated)
    I18N_SUPPORTED_LANGUAGES: list[str] = [
        lang.strip()
        for lang in os.getenv("I18N_SUPPORTED_LANGUAGES", "zh,en").split(",")
        if lang.strip()
    ]
    
    # Core locales directory (community edition)
    # Use absolute path to work from any working directory
    I18N_CORE_LOCALES_DIR: str = os.getenv(
        "I18N_CORE_LOCALES_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")
    )
    
    # Premium locales directory (enterprise edition, optional)
    I18N_PREMIUM_LOCALES_DIR: Optional[str] = os.getenv("I18N_PREMIUM_LOCALES_DIR", None)
    
    # Enable translation cache
    I18N_ENABLE_TRANSLATION_CACHE: bool = os.getenv("I18N_ENABLE_TRANSLATION_CACHE", "true").lower() == "true"
    
    # LRU cache size for hot translations
    I18N_LRU_CACHE_SIZE: int = int(os.getenv("I18N_LRU_CACHE_SIZE", "1000"))
    
    # Enable hot reload of translation files
    I18N_ENABLE_HOT_RELOAD: bool = os.getenv("I18N_ENABLE_HOT_RELOAD", "false").lower() == "true"
    
    # Fallback language when translation is missing
    I18N_FALLBACK_LANGUAGE: str = os.getenv("I18N_FALLBACK_LANGUAGE", "zh")
    
    # Log missing translations
    I18N_LOG_MISSING_TRANSLATIONS: bool = os.getenv("I18N_LOG_MISSING_TRANSLATIONS", "true").lower() == "true"

    # Logging settings
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - [%(trace_id)s] -%(name)s - %(levelname)s - %(message)s")
    LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "logs/app.log")
    LOG_MAX_SIZE: int = int(os.getenv("LOG_MAX_SIZE", "10485760"))  # 10MB
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    LOG_TO_CONSOLE: bool = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"
    LOG_TO_FILE: bool = os.getenv("LOG_TO_FILE", "true").lower() == "true"

    # Sensitive Data Filtering
    ENABLE_SENSITIVE_DATA_FILTER: bool = os.getenv("ENABLE_SENSITIVE_DATA_FILTER", "true").lower() == "true"

    # Memory Module Logging
    PROMPT_LOG_LEVEL: str = os.getenv("PROMPT_LOG_LEVEL", "INFO")
    ENABLE_TEMPLATE_LOGGING: bool = os.getenv("ENABLE_TEMPLATE_LOGGING", "false").lower() == "true"
    TIMING_LOG_FILE: str = os.getenv("TIMING_LOG_FILE", "logs/time.log")
    TIMING_LOG_TO_CONSOLE: bool = os.getenv("TIMING_LOG_TO_CONSOLE", "true").lower() == "true"
    AGENT_LOG_FILE: str = os.getenv("AGENT_LOG_FILE", "logs/agent_service.log")
    AGENT_LOG_MAX_SIZE: int = int(os.getenv("AGENT_LOG_MAX_SIZE", "5242880"))  # 5MB
    AGENT_LOG_BACKUP_COUNT: int = int(os.getenv("AGENT_LOG_BACKUP_COUNT", "20"))

    # Log Streaming Configuration
    LOG_STREAM_KEEPALIVE_INTERVAL: int = int(os.getenv("LOG_STREAM_KEEPALIVE_INTERVAL", "300"))  # 5 minutes
    LOG_STREAM_MAX_CONNECTIONS: int = int(os.getenv("LOG_STREAM_MAX_CONNECTIONS", "10"))
    LOG_STREAM_BUFFER_SIZE: int = int(os.getenv("LOG_STREAM_BUFFER_SIZE", "8192"))  # 8KB
    LOG_FILE_MAX_SIZE_MB: int = int(os.getenv("LOG_FILE_MAX_SIZE_MB", "10"))  # 10MB

    # Celery Task Scheduler Configuration
    # 每个 (task_name, user_id) 队列的最大待处理消息数，0 表示不限制
    SCHEDULER_MAX_QUEUE_LEN: int = int(os.getenv("SCHEDULER_MAX_QUEUE_LEN", "1000"))

    # Celery configuration (internal)
    # NOTE: 变量名不以 CELERY_ 开头，避免被 Celery CLI 的前缀匹配机制劫持
    # 详见 docs/celery-env-bug-report.md
    # 默认使用 Redis 作为 broker 和 backend，与业务缓存隔离
    # 如需使用 RabbitMQ，在 .env 中设置 CELERY_BROKER_URL=amqp://user:pass@host:5672/vhost
    REDIS_DB_CELERY_BROKER: int = int(os.getenv("REDIS_DB_CELERY_BROKER", "3"))
    REDIS_DB_CELERY_BACKEND: int = int(os.getenv("REDIS_DB_CELERY_BACKEND", "4"))

    # 通知中心配置
    NOTIFICATION_REDIS_DB: int = int(os.getenv("NOTIFICATION_REDIS_DB", "8"))
    NOTIFICATION_SCAN_INTERVAL_SECONDS: int = int(os.getenv("NOTIFICATION_SCAN_INTERVAL_SECONDS", "60"))
    NOTIFICATION_HEARTBEAT_SECONDS: int = int(os.getenv("NOTIFICATION_HEARTBEAT_SECONDS", "25"))
    NOTIFICATION_MAX_SSE_PER_USER: int = int(os.getenv("NOTIFICATION_MAX_SSE_PER_USER", "3"))

    # SMTP Email Configuration
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    
    SANDBOX_URL: str = os.getenv("SANDBOX_URL", "")
    SANDBOX_API_KEY: str = os.getenv("SANDBOX_API_KEY", "redbear-sandbox")

    # ─── E2B Sandbox Configuration (自建 E2B 基础设施) ───
    E2B_ENABLED: bool = os.getenv("E2B_ENABLED", "false").lower() == "true"
    # Orchestrator connection
    E2B_ORCHESTRATOR_URL: str = os.getenv("E2B_ORCHESTRATOR_URL", "http://e2b-orchestrator:3001")
    E2B_ORCHESTRATOR_SECRET: str = os.getenv("E2B_ORCHESTRATOR_SECRET", "changeme")
    # Docker / template settings
    E2B_TEMPLATE_ID: str = os.getenv("E2B_TEMPLATE_ID", "agent-runtime")
    E2B_SANDBOX_TIMEOUT: int = int(os.getenv("E2B_SANDBOX_TIMEOUT", "600"))
    E2B_SANDBOX_CPU: int = int(os.getenv("E2B_SANDBOX_CPU", "2"))
    E2B_SANDBOX_MEMORY_MB: int = int(os.getenv("E2B_SANDBOX_MEMORY_MB", "512"))
    # Callback (sandbox → API)
    E2B_CALLBACK_URL: str = os.getenv("E2B_CALLBACK_URL", "http://host.docker.internal:8000")
    E2B_CALLBACK_SECRET: str = os.getenv("E2B_CALLBACK_SECRET", "sandbox-callback-secret")
    # Warm pool
    E2B_WARM_POOL_SIZE: int = int(os.getenv("E2B_WARM_POOL_SIZE", "2"))

    REFLECTION_INTERVAL_SECONDS: float = float(os.getenv("REFLECTION_INTERVAL_SECONDS", "300"))
    HEALTH_CHECK_SECONDS: float = float(os.getenv("HEALTH_CHECK_SECONDS", "600"))
    REFLECTION_INTERVAL_TIME: Optional[str] = int(os.getenv("REFLECTION_INTERVAL_TIME", 30))

    # Celery Beat Schedule Configuration (定时任务执行频率)
    MEMORY_INCREMENT_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="cron hour [0, 23]")]
    ).validate_python(int(os.getenv("MEMORY_INCREMENT_HOUR", "2")))
    MEMORY_INCREMENT_MINUTE: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=59, description="cron minute [0, 59]")]
    ).validate_python(int(os.getenv("MEMORY_INCREMENT_MINUTE", "0")))
    WORKSPACE_REFLECTION_INTERVAL_SECONDS: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="reflection interval in seconds, must be >= 1")]
    ).validate_python(int(os.getenv("WORKSPACE_REFLECTION_INTERVAL_SECONDS", "30")))

    # memory cache regeneration 执行时间点（UTC 小时 0-23，默认 18 = 北京时间 2:00）
    MEMORY_CACHE_REGENERATION_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="memory cache regeneration cron hour [0, 23]")]
    ).validate_python(int(os.getenv("MEMORY_CACHE_REGENERATION_HOUR", "18")))
    MEMORY_CACHE_REGENERATION_MINUTE: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=59, description="memory cache regeneration cron minute [0, 59]")]
    ).validate_python(int(os.getenv("MEMORY_CACHE_REGENERATION_MINUTE", "0")))
    # 用户名片 Tag 定时刷新时间（UTC，默认 18:30，北京时间次日 02:30）
    USER_TAG_REFRESH_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="user tag refresh cron hour [0, 23]")]
    ).validate_python(int(os.getenv("USER_TAG_REFRESH_HOUR", "18")))
    USER_TAG_REFRESH_MINUTE: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=59, description="user tag refresh cron minute [0, 59]")]
    ).validate_python(int(os.getenv("USER_TAG_REFRESH_MINUTE", "30")))
    # 洞察/摘要缓存刷新新鲜度窗口（小时）：缓存上次刷新距今 < 该值则本轮跳过（限频，避免频繁重算），默认 72
    MEMORY_CACHE_FRESH_HOURS: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="insight/summary cache freshness window in hours, must be >= 1")]
    ).validate_python(int(os.getenv("MEMORY_CACHE_FRESH_HOURS", "72")))

    # forgetting cycle 执行时间点（UTC 小时 0-23，默认 18 = 北京时间 2:00）
    FORGETTING_CYCLE_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="forgetting cycle cron hour [0, 23]")]
    ).validate_python(int(os.getenv("FORGETTING_CYCLE_HOUR", "18")))
    FORGETTING_CYCLE_MINUTE: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=59, description="forgetting cycle cron minute [0, 59]")]
    ).validate_python(int(os.getenv("FORGETTING_CYCLE_MINUTE", "0")))

    # scan-forget-candidates 定时任务扫描间隔（分钟）
    FORGET_SCAN_INTERVAL_MINUTES: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="forget candidates scan interval in minutes, must be >= 1")]
    ).validate_python(int(os.getenv("FORGET_SCAN_INTERVAL_MINUTES", "5")))

    IMPLICIT_EMOTIONS_UPDATE_HOUR: int = int(os.getenv("IMPLICIT_EMOTIONS_UPDATE_HOUR", "2"))
    # implicit_emotions_update: 每天几分执行（分钟，0-59）
    IMPLICIT_EMOTIONS_UPDATE_MINUTE: int = int(os.getenv("IMPLICIT_EMOTIONS_UPDATE_MINUTE", "0"))  
    LAYER2_REFLECTION_INTERVAL_MINUTES: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="Layer 2 reflection interval in minutes, must be >= 1")]
    ).validate_python(int(os.getenv("LAYER2_REFLECTION_INTERVAL_MINUTES", "10")))
    LAYER2_DEDUP_FULL_SCAN_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="Layer 2 dedup full scan hour, must be 0-23")]
    ).validate_python(int(os.getenv("LAYER2_DEDUP_FULL_SCAN_HOUR", "3")))
    # 反思/去重活跃度过滤：最近一次会话更新距今 >= 该小时数则跳过（仅对活跃用户反思）
    REFLECT_LAYER2_INACTIVE_HOURS: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="Layer 2 inactivity filter in hours, must be >= 1")]
    ).validate_python(int(os.getenv("REFLECT_LAYER2_INACTIVE_HOURS", "36")))
    # Layer2 反思整轮总时间预算（秒）：高频 run() 与低频 _run_dedup_full_scan 共用。
    # 应用层动态预算熔断的上限；500 = Celery soft_time_limit 540 留 40s 框架余量。
    LAYER2_REFLECTION_BUDGET_SECONDS: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="Layer 2 reflection total time budget in seconds")]
    ).validate_python(int(os.getenv("LAYER2_REFLECTION_BUDGET_SECONDS", "500")))

    # 反思失败用户重试：派发任务扫描间隔（分钟）。默认 10。
    # beat 注册需从 settings 读取，故放这里；其余重试策略常量在 retry_registry.py。
    REFLECTION_RETRY_SCAN_INTERVAL_MINUTES: int = int(os.getenv("REFLECTION_RETRY_SCAN_INTERVAL_MINUTES", "10"))
    # GDS 拓扑分数：扫描活跃用户（write_time<24h）派发计算的间隔（分钟）。默认 60。
    GDS_TOPOLOGY_SCAN_INTERVAL_MINUTES: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="GDS topology score scan interval in minutes, must be >= 1")]
    ).validate_python(int(os.getenv("GDS_TOPOLOGY_SCAN_INTERVAL_MINUTES", "60")))
    GDS_TOPOLOGY_INFLIGHT_TTL_SEC: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="GDS topology in-flight lock TTL in seconds, must be >= 1")]
    ).validate_python(int(os.getenv("GDS_TOPOLOGY_INFLIGHT_TTL_SEC", "86400")))
    GDS_TOPOLOGY_ACTIVE_HOURS: int = TypeAdapter(
        Annotated[int, Field(ge=1, description="GDS topology active window in hours, must be >= 1")]
    ).validate_python(int(os.getenv("GDS_TOPOLOGY_ACTIVE_HOURS", "2")))
    # 热门记忆标签缓存预热时间（UTC 小时，0-23）。19 = 北京时间 03:00
    HOT_MEMORY_TAGS_REFRESH_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="Hot memory tags cache refresh hour (UTC), 0-23. 19=Beijing 03:00")]
    ).validate_python(int(os.getenv("HOT_MEMORY_TAGS_REFRESH_HOUR", "19")))
    DRAFT_DATA_CLEAN_HOUR: int = TypeAdapter(
        Annotated[int, Field(ge=0, le=23, description="Draft data clean hour (UTC), 0-23. 16=Beijing 00:00")]
    ).validate_python(int(os.getenv("DRAFT_DATA_CLEAN_HOUR", "16")))
    # Memory Module Configuration (internal)
    
    MEMORY_OUTPUT_DIR: str = os.getenv("MEMORY_OUTPUT_DIR", "logs/memory-output")
    MEMORY_CONFIG_DIR: str = os.getenv("MEMORY_CONFIG_DIR", "app/core/memory")

    # Tool Management Configuration
    TOOL_CONFIG_DIR: str = os.getenv("TOOL_CONFIG_DIR", "app/core/tools")
    TOOL_EXECUTION_TIMEOUT: int = int(os.getenv("TOOL_EXECUTION_TIMEOUT", "60"))
    TOOL_MAX_CONCURRENCY: int = int(os.getenv("TOOL_MAX_CONCURRENCY", "10"))
    ENABLE_TOOL_MANAGEMENT: bool = os.getenv("ENABLE_TOOL_MANAGEMENT", "true").lower() == "true"

    # official environment system version
    SYSTEM_VERSION: str = os.getenv("SYSTEM_VERSION", "v0.2.1")

    # model square loading
    LOAD_MODEL: bool = os.getenv("LOAD_MODEL", "false").lower() == "true"

    # workflow config
    WORKFLOW_IMPORT_CACHE_TIMEOUT: int = int(os.getenv("WORKFLOW_IMPORT_CACHE_TIMEOUT", 1800))
    WORKFLOW_NODE_TIMEOUT: int = int(os.getenv("WORKFLOW_NODE_TIMEOUT", 600))

    # workflow_node_executions 保留策略开关。
    # 开启后该表只保留「最近一次」记录：
    #   - 完整工作流：同 app_id + workflow_config_id 下按执行开始时间最近 1 次的全部节点行
    #     （包括 waiting_human；较旧人工介入执行恢复时也不会覆盖更新执行）
    #   - 单节点调试：同 app_id + workflow_config_id + node_id 下最近 1 条
    # 历史明细的权威数据源是 workflow_executions.output_data["node_outputs"]，不受影响。
    WORKFLOW_NODE_EXECUTION_RETENTION_ENABLED: bool = os.getenv(
        "WORKFLOW_NODE_EXECUTION_RETENTION_ENABLED", "true"
    ).lower() == "true"

    # ========================================================================
    # General Ontology Type Configuration
    # ========================================================================
    # 通用本体文件路径列表（逗号分隔）
    GENERAL_ONTOLOGY_FILES: str = os.getenv("GENERAL_ONTOLOGY_FILES", "api/app/core/memory/ontology_services/General_purpose_entity.ttl")

    # 是否启用通用本体类型功能
    ENABLE_GENERAL_ONTOLOGY_TYPES: bool = os.getenv("ENABLE_GENERAL_ONTOLOGY_TYPES", "true").lower() == "true"

    # Prompt 中最大类型数量
    MAX_ONTOLOGY_TYPES_IN_PROMPT: int = int(os.getenv("MAX_ONTOLOGY_TYPES_IN_PROMPT", "50"))

    # 核心通用类型列表（逗号分隔）—— 与 ontology.md Entity Ontology 保持一致的 13 类
    CORE_GENERAL_TYPES: str = os.getenv(
        "CORE_GENERAL_TYPES",
        "生命体,组织,群体,角色职业,地点设施,物品设备,软件平台,识别联系信息,"
        "文档媒体,知识能力,偏好习惯,具体目标,称呼别名"
    )

    # 实验模式开关（允许通过 API 动态切换本体配置）
    ONTOLOGY_EXPERIMENT_MODE: bool = os.getenv("ONTOLOGY_EXPERIMENT_MODE", "true").lower() == "true"

    # ========================================================================
    # Context Engine History Configuration
    # ========================================================================
    # context engine的agent历史消息条数上限
    AGENT_MAX_HISTORY: int = int(os.getenv("AGENT_MAX_HISTORY", 20))

    def get_memory_output_path(self, filename: str = "") -> str:
        """
        Get the full path for memory module output files.
        
        Args:
            filename: Optional filename to append to the output directory
            
        Returns:
            Full path to the output file or directory
        """
        base_path = Path(self.MEMORY_OUTPUT_DIR)
        if filename:
            return str(base_path / filename)
        return str(base_path)

    def ensure_memory_output_dir(self) -> None:
        """
        Ensure the memory output directory exists.
        Creates the directory if it doesn't exist.
        """
        output_dir = Path(self.MEMORY_OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
