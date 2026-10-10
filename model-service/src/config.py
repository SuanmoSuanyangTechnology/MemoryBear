"""Validated configuration contracts for the model service."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ModelServiceSettings(BaseSettings):
    """Immutable settings constructed only from the bootstrap merge."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    service_name: Literal["model-service"] = "model-service"

    # Shared environment variables
    deployment_mode: str = Field(default="community", validation_alias="DEPLOYMENT_MODE")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    enable_sensitive_data_filter: bool = Field(
        default=True,
        validation_alias="ENABLE_SENSITIVE_DATA_FILTER",
    )
    i18n_default_language: str = Field(default="zh", validation_alias="I18N_DEFAULT_LANGUAGE")
    db_host: str = Field(default="127.0.0.1", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, ge=1, le=65535, validation_alias="DB_PORT")
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: SecretStr = Field(default=SecretStr("password"), validation_alias="DB_PASSWORD")
    db_name: str = Field(default="redbear-mem", validation_alias="DB_NAME")
    redis_host: str = Field(default="127.0.0.1", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, ge=1, le=65535, validation_alias="REDIS_PORT")
    redis_password: SecretStr = Field(default=SecretStr(""), validation_alias="REDIS_PASSWORD")
    # Must match the host api's Redis DB: model:usage stream (host alert bridge),
    # acl:rules (identity-written) and runtime_model_info invalidation keys are shared.
    redis_db: int = Field(default=0, ge=0, validation_alias="REDIS_DB")

    # 渠道凭据主密钥（base64 32B，与 core/api 同一把）：model_channels 凭据解密用，
    # AAD = provider:tenant_id
    model_credentials_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="MODEL_CREDENTIALS_KEY",
    )
    speedbear_base_url: str = Field(
        default="https://testspeedbear.redbearai.com",
        validation_alias="SPEEDBEAR_BASE_URL",
    )
    speedbear_auth_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SPEEDBEAR_AUTH_KEY",
    )
    # 平台代管模型上游（SpeedBear 网关管理面）调用超时
    speedbear_timeout: float = Field(
        default=30.0,
        gt=0,
        validation_alias="SPEEDBEAR_TIMEOUT",
    )

    # Provider 调用参数（活体验证 / 可用性探测 / 调用面共用）
    llm_timeout: float = Field(default=120.0, gt=0, validation_alias="LLM_TIMEOUT")
    llm_max_retries: int = Field(default=2, ge=0, validation_alias="LLM_MAX_RETRIES")
    embedding_batch_size: int = Field(
        default=10,
        ge=1,
        validation_alias="EMBEDDING_BATCH_SIZE",
    )
    embedding_max_workers: int = Field(
        default=3,
        ge=1,
        validation_alias="EMBEDDING_MAX_WORKERS",
    )
    model_concurrency: int = Field(default=5, ge=1, validation_alias="MODEL_CONCURRENCY")
    model_http_max_connections: int = Field(
        default=300,
        ge=1,
        validation_alias="MODEL_HTTP_MAX_CONNECTIONS",
    )
    model_http_max_keepalive_connections: int = Field(
        default=50,
        ge=0,
        validation_alias="MODEL_HTTP_MAX_KEEPALIVE_CONNECTIONS",
    )
    model_http_trust_env: bool = Field(
        default=True,
        validation_alias="MODEL_HTTP_TRUST_ENV",
    )
    bedrock_max_pool_connections: int = Field(
        default=50,
        ge=1,
        validation_alias="BEDROCK_MAX_POOL_CONNECTIONS",
    )
    bedrock_max_retries: int = Field(
        default=2,
        ge=0,
        validation_alias="BEDROCK_MAX_RETRIES",
    )

    # Model service environment variables
    # 预定义模型（YAML 种子）启动同步（M7 D-M7-5：服务=唯一写者；宿主同批删除该调用）
    model_load_seed: bool = Field(default=False, validation_alias="LOAD_MODEL")
    model_service_host: str = Field(default="0.0.0.0", validation_alias="MODEL_SERVICE_HOST")
    model_service_port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        validation_alias="MODEL_SERVICE_PORT",
    )
    model_service_log_level: str = Field(
        default="INFO",
        validation_alias="MODEL_SERVICE_LOG_LEVEL",
    )
    model_service_db_pool_size: int = Field(
        default=20,
        ge=1,
        validation_alias="MODEL_SERVICE_DB_POOL_SIZE",
    )
    model_service_db_max_overflow: int = Field(
        default=10,
        ge=0,
        validation_alias="MODEL_SERVICE_DB_MAX_OVERFLOW",
    )
    model_service_db_pool_recycle: int = Field(
        default=1800,
        ge=1,
        validation_alias="MODEL_SERVICE_DB_POOL_RECYCLE",
    )
    model_service_db_pool_timeout: int = Field(
        default=30,
        ge=1,
        validation_alias="MODEL_SERVICE_DB_POOL_TIMEOUT",
    )
    model_service_db_pool_pre_ping: bool = Field(
        default=True,
        validation_alias="MODEL_SERVICE_DB_POOL_PRE_PING",
    )
    model_service_db_statement_timeout_ms: int = Field(
        default=60000,
        ge=1,
        validation_alias="MODEL_SERVICE_DB_STATEMENT_TIMEOUT_MS",
    )
    model_service_redis_pool_size: int = Field(
        default=50,
        ge=1,
        validation_alias="MODEL_SERVICE_REDIS_POOL_SIZE",
    )
    model_service_health_probe_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        validation_alias="MODEL_SERVICE_HEALTH_PROBE_TIMEOUT_SECONDS",
    )
    # 运行面 invoke 上游首块档（设计 §2.9 服务侧第一档，**按候选**生效）：
    # 单次候选调用超过即判瞬时错误 → 同候选重试 → 仍失败换渠道（换渠道只在此档有意义）。
    invoke_first_result_timeout_s: float = Field(
        default=15.0,
        gt=0,
        validation_alias="MODEL_INVOKE_FIRST_RESULT_TIMEOUT_S",
    )
    # 运行面 invoke 首帧上限（**整次请求**，含选路/解密/换渠道）：
    # 不变量①「必须小于宿主 invoke 预算」——宿主 SSE 与 stream=false 都以 idle 计时
    # （包内 InvokeTimeouts.idle_s 默认 60s），服务先答才不会白跑；
    # 不变量②「须 ≥ 3 × 首块档」——同候选重试与换渠道要有容身之处，否则首块档形同虚设。
    # **llm 族例外**（下方 invoke_llm_*）：生成整段回复天然慢，沿用此式会把首块档
    # 压到无意义的秒级；故 llm 族单独给档，代价是慢响应超时后无重试/换渠道余量——
    # 快速失败（连接失败/5xx/401）仍按候选完整换渠道，只有「首块档超时」这一条路径
    # 拿不到同候选重试。改档前先读宿主 MODEL_SERVICE_INVOKE_IDLE_TIMEOUT_SECONDS。
    # 超时 → 504（SERVICE_UNAVAILABLE）：上游未在预算内产出，宿主可整轮重试。
    invoke_total_timeout_s: float = Field(
        default=45.0,
        gt=0,
        validation_alias="MODEL_INVOKE_TOTAL_TIMEOUT_S",
    )
    # llm 族（G2 非流式 / G3 流式）同义档：首块档 = 单次生成上界（非流式下首块即结果；
    # 流式下 = 连接 + 首个 chunk，由 open_astream 在候选循环内 eager 拉出，故换渠道仍有效）。
    # 默认 120s 覆盖长文生成；总档 150s 只留选路/解密/一次换渠道的余量。
    invoke_llm_first_result_timeout_s: float = Field(
        default=120.0,
        gt=0,
        validation_alias="MODEL_INVOKE_LLM_FIRST_RESULT_TIMEOUT_S",
    )
    invoke_llm_total_timeout_s: float = Field(
        default=150.0,
        gt=0,
        validation_alias="MODEL_INVOKE_LLM_TOTAL_TIMEOUT_S",
    )
    # llm 流式块间空闲档（**服务侧执行**，设计 §2.5 的显式偏离）：供应商流中途长时间无增量
    # → 服务先答 error 帧，宿主拿到结构化失败而非裸 idle 断连。不变量③「服务 idle(60) <
    # 宿主 idle(180，MODEL_SERVICE_INVOKE_IDLE_TIMEOUT_SECONDS)」；总档 150s **不包排流**
    # （只包解析 + 首块），故长回复不受总档截断，只受本档逐块约束。
    invoke_llm_idle_timeout_s: float = Field(
        default=60.0,
        gt=0,
        validation_alias="MODEL_INVOKE_LLM_IDLE_TIMEOUT_S",
    )
    # 媒体族（G4a：asr/image/video）超时档：候选调用即整段媒体操作（asr/video 任务式轮询、
    # image 单次生成），首块档 = 总档同设（默认 660s），与 llm 族例外同构——快速失败
    # （连接失败/5xx/401）仍按候选完整换渠道，仅「首块档超时」无同候选重试余量。
    # 须 < 宿主媒体 idle 档（MODEL_SERVICE_INVOKE_MEDIA_IDLE_TIMEOUT_SECONDS，见宿主 runtime）。
    invoke_media_first_result_timeout_s: float = Field(
        default=660.0,
        gt=0,
        validation_alias="MODEL_INVOKE_MEDIA_FIRST_RESULT_TIMEOUT_S",
    )
    invoke_media_total_timeout_s: float = Field(
        default=660.0,
        gt=0,
        validation_alias="MODEL_INVOKE_MEDIA_TOTAL_TIMEOUT_S",
    )
    # 媒体任务式轮询（asr 转写 / video 生成共用）：间隔与上限；上限须 < 首块档，先于门面
    # wait_for 给出结构化超时。asr 提交后轮询归服务侧（宿主/km 零协议面）。
    invoke_media_poll_interval_s: float = Field(
        default=1.0,
        gt=0,
        validation_alias="MODEL_INVOKE_MEDIA_POLL_INTERVAL_S",
    )
    invoke_media_poll_timeout_s: float = Field(
        default=600.0,
        gt=0,
        validation_alias="MODEL_INVOKE_MEDIA_POLL_TIMEOUT_S",
    )
    # 渠道 least-used 选路：计量表滚动窗口（D14）与全局开关
    model_usage_load_window_minutes: int = Field(
        default=15,
        ge=1,
        validation_alias="MODEL_USAGE_LOAD_WINDOW_MINUTES",
    )
    model_usage_least_used_enabled: bool = Field(
        default=True,
        validation_alias="MODEL_USAGE_LEAST_USED_ENABLED",
    )
    # Usage-backlog warning threshold: consumer-group lag / pending above this
    # value logs a WARNING (XLEN is not a backlog gauge and is not checked).
    model_usage_backlog_warn: int = Field(
        default=50_000,
        ge=1,
        validation_alias="MODEL_USAGE_BACKLOG_WARN",
    )
    # 渠道熔断冷却（M9）：失败置冷时长与开关；读侧软排除（部分冷却跳过，全冷却放行整链）
    model_channel_cooldown_enabled: bool = Field(
        default=True,
        validation_alias="MODEL_CHANNEL_COOLDOWN_ENABLED",
    )
    model_channel_cooldown_seconds: int = Field(
        default=60,
        ge=1,
        validation_alias="MODEL_CHANNEL_COOLDOWN_SECONDS",
    )
    # 内部面鉴权：direct（社区默认，信任 X-Model-* 内部头 + NetworkPolicy）；
    # gateway（企业，auth-sdk 内部 token 验签，M10 收紧批次落地）
    model_service_auth_mode: Literal["direct", "gateway"] = Field(
        default="direct",
        validation_alias="MODEL_SERVICE_AUTH_MODE",
    )
    model_service_internal_name: str = Field(
        default="model-service",
        validation_alias="MODEL_SERVICE_INTERNAL_NAME",
    )
    model_service_jwks_url: str | None = Field(
        default=None,
        validation_alias="MODEL_SERVICE_JWKS_URL",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Use only the mapping already merged by bootstrap."""

        del cls, settings_cls, env_settings, dotenv_settings, file_secret_settings
        return (init_settings,)

    @field_validator("model_service_log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("MODEL_SERVICE_LOG_LEVEL is invalid")
        return normalized

    @property
    def database_url_sync(self) -> str:
        return self._database_url("postgresql+psycopg")

    @property
    def database_url_async(self) -> str:
        return self._database_url("postgresql+asyncpg")

    def _database_url(self, scheme: str) -> str:
        user = quote_plus(self.db_user)
        password = quote_plus(self.db_password.get_secret_value())
        database = quote_plus(self.db_name)
        return f"{scheme}://{user}:{password}@{self.db_host}:{self.db_port}/{database}"

    def redis_url_for_db(self, database: int) -> str:
        password = self.redis_password.get_secret_value()
        auth = f":{quote_plus(password)}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{database}"

    @property
    def redis_url(self) -> str:
        return self.redis_url_for_db(self.redis_db)

    def safe_summary(self) -> dict[str, Any]:
        """Return only non-sensitive startup metadata."""

        return {
            "service": self.service_name,
            "deployment_mode": self.deployment_mode,
            "host": self.model_service_host,
            "port": self.model_service_port,
            "auth_mode": self.model_service_auth_mode,
            "db_host": self.db_host,
            "db_port": self.db_port,
            "redis_host": self.redis_host,
            "redis_port": self.redis_port,
            "db_pool_size": self.model_service_db_pool_size,
        }


_configured_settings: ModelServiceSettings | None = None


def configure_settings(settings: ModelServiceSettings) -> None:
    """应用装配期记录进程级 settings（迁移模块的 settings 入口）。

    宿主侧重依赖 `app.core.config.settings` 模块级单例；服务侧改由装配期显式注入，
    使 `create_app(settings)` 的测试注入与运行期取值一致。
    """

    global _configured_settings
    _configured_settings = settings


def current_settings() -> ModelServiceSettings:
    """返回当前生效的 settings；未装配时按环境变量构造（脚本/裸单测场景）。"""

    if _configured_settings is not None:
        return _configured_settings
    from .bootstrap import get_settings

    return get_settings()
