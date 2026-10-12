"""平台面企业语义加载器：惰性 try-import ``enterprise_ext.model``（D8，M7-5）。

开源侧只到中性 ``source='platform'`` 与中立模型行数据；SpeedBear 上游协议
（OpenAPI 路径与字段名、``billing_type`` 映射、公共模型 features 策略）在私有
扩展包实现。扩展缺失即 RuntimeError——misconfiguration 响亮暴露，不静默降级
（fail-closed 语义）；开源构建装不到该包，平台代管模型写路径不可用属预期。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import current_settings


@dataclass(frozen=True)
class PlatformModelConfig:
    """平台面扩展装配参数（host → ``enterprise_ext.model.PlatformModelSemantics``）。"""

    base_url: str
    auth_key: str | None
    timeout: float


_semantics: Any | None = None


def get_platform_semantics() -> Any:
    """平台代管模型语义单例（首次调用装配；配置变更走进程重启，不做运行期热更）。"""
    global _semantics
    if _semantics is None:
        try:
            from enterprise_ext.model import PlatformModelSemantics
        except ImportError as exc:
            raise RuntimeError(
                "enterprise_ext.model 不可用：平台代管模型写路径需要企业扩展包"
            ) from exc
        settings = current_settings()
        _semantics = PlatformModelSemantics(
            PlatformModelConfig(
                base_url=settings.speedbear_base_url,
                auth_key=settings.speedbear_auth_key.get_secret_value() or None,
                timeout=settings.speedbear_timeout,
            )
        )
    return _semantics


__all__ = ["PlatformModelConfig", "get_platform_semantics"]
