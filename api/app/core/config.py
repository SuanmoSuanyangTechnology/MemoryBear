import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

class Settings:
    ENABLE_SINGLE_WORKSPACE: bool = os.getenv("ENABLE_SINGLE_WORKSPACE", "true").lower() == "true"
    # API Keys Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    
    # Neo4j Configuration (记忆系统数据库)
    NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://1.94.111.67:7687")
    NEO4J_USERNAME: str = os.getenv("NEO4J_USERNAME", "neo4j")
    NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")
    
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

    DB_AUTO_UPGRADE = os.getenv("DB_AUTO_UPGRADE", "false").lower() == "true"

    # Redis configuration
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "1"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    
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
    
    # JWT Token Configuration
    SECRET_KEY: str = os.getenv("SECRET_KEY", "a_default_secret_key_that_is_long_and_random")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    
    # Single Sign-On configuration
    ENABLE_SINGLE_SESSION: bool = os.getenv("ENABLE_SINGLE_SESSION", "false").lower() == "true"

    # File Upload
    MAX_FILE_SIZE: int = int(os.getenv("MAX_FILE_SIZE", "52428800"))
    FILE_PATH: str = os.getenv("FILE_PATH", "/files")

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

    # Logging settings
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
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


    # Celery configuration (internal)
    CELERY_BROKER: int = int(os.getenv("CELERY_BROKER", "1"))
    CELERY_BACKEND: int = int(os.getenv("CELERY_BACKEND", "2"))
    REFLECTION_INTERVAL_SECONDS: float = float(os.getenv("REFLECTION_INTERVAL_SECONDS", "300"))
    HEALTH_CHECK_SECONDS: float = float(os.getenv("HEALTH_CHECK_SECONDS", "600"))
    MEMORY_INCREMENT_INTERVAL_HOURS: float = float(os.getenv("MEMORY_INCREMENT_INTERVAL_HOURS", "24"))
    DEFAULT_WORKSPACE_ID: Optional[str] = os.getenv("DEFAULT_WORKSPACE_ID", None)
    REFLECTION_INTERVAL_TIME:Optional[str] = int(os.getenv("REFLECTION_INTERVAL_TIME", 30))
    
    # Memory Cache Regeneration Configuration
    MEMORY_CACHE_REGENERATION_HOURS: int = int(os.getenv("MEMORY_CACHE_REGENERATION_HOURS", "24"))

    # Memory Module Configuration (internal)
    MEMORY_OUTPUT_DIR: str = os.getenv("MEMORY_OUTPUT_DIR", "logs/memory-output")
    MEMORY_CONFIG_DIR: str = os.getenv("MEMORY_CONFIG_DIR", "app/core/memory")
    
    # Tool Management Configuration
    TOOL_CONFIG_DIR: str = os.getenv("TOOL_CONFIG_DIR", "app/core/tools")
    TOOL_EXECUTION_TIMEOUT: int = int(os.getenv("TOOL_EXECUTION_TIMEOUT", "60"))
    TOOL_MAX_CONCURRENCY: int = int(os.getenv("TOOL_MAX_CONCURRENCY", "10"))
    ENABLE_TOOL_MANAGEMENT: bool = os.getenv("ENABLE_TOOL_MANAGEMENT", "true").lower() == "true"
    
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
