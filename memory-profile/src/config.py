import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


class Settings:
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

    ELASTICSEARCH_HOST: str = os.getenv("ELASTICSEARCH_HOST", "https://127.0.0.1")
    ELASTICSEARCH_PORT: int = int(os.getenv("ELASTICSEARCH_PORT", "9200"))
    ELASTICSEARCH_USERNAME: str = os.getenv("ELASTICSEARCH_USERNAME", "elastic")
    ELASTICSEARCH_PASSWORD: str = os.getenv("ELASTICSEARCH_PASSWORD", "")
    ELASTICSEARCH_VERIFY_CERTS: bool = os.getenv("ELASTICSEARCH_VERIFY_CERTS", "False").lower() == "true"
    ELASTICSEARCH_CA_CERTS: str = os.getenv("ELASTICSEARCH_CA_CERTS", "")
    ELASTICSEARCH_REQUEST_TIMEOUT: int = int(os.getenv("ELASTICSEARCH_REQUEST_TIMEOUT", "100000"))
    ELASTICSEARCH_RETRY_ON_TIMEOUT: bool = os.getenv("ELASTICSEARCH_RETRY_ON_TIMEOUT", "True").lower() == "true"
    ELASTICSEARCH_MAX_RETRIES: int = int(os.getenv("ELASTICSEARCH_MAX_RETRIES", "10"))

    HTTP_MAX_CONNECTIONS: int = int(os.getenv("HTTP_MAX_CONNECTIONS", "100"))
    HTTP_KEEPALIVE_CONNECTIONS: int = int(os.getenv("HTTP_KEEPALIVE_CONNECTIONS", "20"))
    HTTP_KEEPALIVE_EXPIRY: int = int(os.getenv("HTTP_KEEPALIVE_EXPIRY", "30"))
    HTTP_SSRF_PROXY: str | None = os.getenv("HTTP_SSRF_PROXY")

    LOG_FILE_PATH: str = os.getenv("LOG_FILE_PATH", "logs/app.log")
    LOG_LEVEL: str = str(os.getenv("LOG_LEVEL", "INFO")).upper()
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - [%(trace_id)s] - %(name)s - %(levelname)s - %(message)s")
    LOG_MAX_SIZE: int = int(os.getenv("LOG_MAX_SIZE", "10485760"))  # 10MB
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    LOG_TO_CONSOLE: bool = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"
    LOG_TO_FILE: bool = os.getenv("LOG_TO_FILE", "true").lower() == "true"

    # Neo4j Configuration (记忆系统数据库)
    NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    # Neo4j 连接池参数（通过环境变量可配）
    # 默认值参考文档建议值，兼顾资源占用与并发需求
    NEO4J_MAX_POOL_SIZE: int = int(os.getenv("NEO4J_MAX_POOL_SIZE", "30"))
    NEO4J_ACQ_TIMEOUT: float = float(os.getenv("NEO4J_ACQ_TIMEOUT", "30.0"))
    NEO4J_MAX_CONN_LIFETIME: int = int(os.getenv("NEO4J_MAX_CONN_LIFETIME", "3600"))
    NEO4J_CONN_TIMEOUT: float = float(os.getenv("NEO4J_CONN_TIMEOUT", "30.0"))

    # Redis configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "1"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    REDIS_POOL_SIZE: int = int(os.getenv("REDIS_POOL_SIZE", "100"))
    REDIS_URL: str = (
        f"redis://:{quote_plus(REDIS_PASSWORD)}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
        if REDIS_PASSWORD
        else f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    )

    # Auth configuration（对齐 mem-knowledge 评审稿 4.3 双模式）：
    # direct 社区默认（独立部署自验 JWT/API key）；gateway 企业版经 enterprise-extensions
    # 验内部 token + ACL，缺失即启动期 RuntimeError。企业部署须显式设 MEMORY_AUTH_MODE=gateway。
    MEMORY_AUTH_MODE: str = os.getenv("MEMORY_AUTH_MODE", "direct")
    MEMORY_SERVICE_NAME: str = os.getenv("MEMORY_SERVICE_NAME", "memory")
    MEMORY_JWKS_URL: str | None = os.getenv("MEMORY_JWKS_URL")
    MEMORY_SECRET: str | None = os.getenv("MEMORY_SECRET")
    MEMORY_KILL_SWITCH_FILE: str | None = os.getenv("MEMORY_KILL_SWITCH_FILE")
    # direct 模式 API key 集中校验端点（identity POST /internal/api-key-verify）；None 时
    # direct 模式 x-api-key 请求 fail-closed 拒绝
    MEMORY_API_KEY_VERIFY_URL: str | None = os.getenv("MEMORY_API_KEY_VERIFY_URL")

    MEMORY_READ_BACKEND: str = os.getenv("MEMORY_READ_BACKEND", "ELASTIC").strip().upper()

    # ---- i18n（响应消息多语言，见 src/i18n/）----
    # 默认语言与兜底语言：翻译缺失时按 fallback 再试，最终回落为 key 本身
    I18N_DEFAULT_LANGUAGE: str = os.getenv("I18N_DEFAULT_LANGUAGE", "zh")
    I18N_FALLBACK_LANGUAGE: str = os.getenv("I18N_FALLBACK_LANGUAGE", "zh")
    # 受支持语言（逗号分隔）；不在列表内的请求语言会被改判为默认语言
    I18N_SUPPORTED_LANGUAGES: list[str] = [
        lang.strip()
        for lang in os.getenv("I18N_SUPPORTED_LANGUAGES", "zh,en").split(",")
        if lang.strip()
    ]
    # core 翻译目录（社区版，必需）：默认 <服务根>/locales
    I18N_CORE_LOCALES_DIR: str = os.getenv(
        "I18N_CORE_LOCALES_DIR",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")
    )
    # premium 翻译目录（企业版覆盖，可选）；None 时探测 ./premium/locales
    I18N_PREMIUM_LOCALES_DIR: str | None = os.getenv("I18N_PREMIUM_LOCALES_DIR")
    I18N_ENABLE_TRANSLATION_CACHE: bool = os.getenv("I18N_ENABLE_TRANSLATION_CACHE", "true").lower() == "true"
    I18N_LRU_CACHE_SIZE: int = int(os.getenv("I18N_LRU_CACHE_SIZE", "1000"))
    I18N_ENABLE_HOT_RELOAD: bool = os.getenv("I18N_ENABLE_HOT_RELOAD", "false").lower() == "true"
    # 缺翻译时是否 warning（排查漏翻时开；生产可关以免日志噪音）
    I18N_LOG_MISSING_TRANSLATIONS: bool = os.getenv("I18N_LOG_MISSING_TRANSLATIONS", "true").lower() == "true"


settings = Settings()
