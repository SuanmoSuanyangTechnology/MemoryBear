"""网关服务配置。

Redis 连接复用老单体 .env 的原始变量命名（REDIS_HOST/REDIS_PORT/REDIS_DB）；
用户 JWT 验签参数与老单体一致（SECRET_KEY/HS256）；内部 token 签发的密钥与
TTL 为网关特有配置（决策 #5：本地 RS256 签发，KMS 就绪后切远程签发）。
"""
import json
import os
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


class Settings:
    # ---- Redis（用户快照 + 审计队列）----
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

    # ---- 用户 JWT 验签（与老单体 security.py 一致）----
    SECRET_KEY: str = os.getenv("SECRET_KEY")                  # 无默认：缺失时验签必然失败，fail-closed
    USER_JWT_ALGORITHM: str = os.getenv("USER_JWT_ALGORITHM", "HS256")

    # ---- 内部 token 签发（决策 #5：本地 RS256；KMS 降级：环境变量/挂载注入私钥）----
    INTERNAL_ISSUER_PRIVATE_KEY: str = os.getenv("INTERNAL_ISSUER_PRIVATE_KEY")
    INTERNAL_ISSUER_KID: str = os.getenv("INTERNAL_ISSUER_KID", "internal-1")
    INTERNAL_TOKEN_TTL: int = int(os.getenv("INTERNAL_TOKEN_TTL", "120"))
    INTERNAL_TOKEN_LEEWAY: int = int(os.getenv("INTERNAL_TOKEN_LEEWAY", "30"))

    # ---- 服务自身配置 ----
    SERVICE_NAME: str = "gateway"
    # 审计入队 Stream（identity 消费者 XREADGROUP + event_id 幂等落库）
    AUDIT_STREAM_KEY: str = os.getenv("AUDIT_STREAM_KEY", "audit:stream")
    # 凭据类型判定路径前缀（设计 §3.2）：命中前缀 → API key 路径，其余 → 用户 JWT 路径
    API_KEY_PATH_PREFIXES: tuple[str, ...] = tuple(
        p for p in os.getenv("API_KEY_PATH_PREFIXES", "/v1/").split(",") if p)

    # ---- 鉴权策略（direct 内置社区版 / gateway 企业插件）----
    # 惰性读 env（理由同 TARGET_ROUTES：类体 env 在 import 时冻结，property
    # 保证部署/测试运行期按环境切换生效）
    @property
    def auth_strategy_name(self) -> str:
        return os.getenv("AUTH_STRATEGY", "direct")

    # ---- 用户路径固定窗口限流（评审稿 4.2.2）----
    # 惰性读 env：类体 env 在 import 时冻结，property 支持部署/测试运行期按环境切换
    @property
    def user_rate_limit_per_minute(self) -> int:
        return int(os.getenv("USER_RATE_LIMIT_PER_MINUTE", "600"))

    # ---- 转发目标路由（TARGET_ROUTES JSON，惰性读）----
    @property
    def target_routes(self) -> list:
        from src.forward import TargetRoute  # 延迟导入避免循环
        raw = json.loads(os.getenv("TARGET_ROUTES", "{}")) if os.getenv("TARGET_ROUTES") else {}
        return [TargetRoute(**item) for item in raw.get("routes", [])]


settings = Settings()
