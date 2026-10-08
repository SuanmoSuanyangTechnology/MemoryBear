"""mem-knowledge → model-service invoke 接缝（镜像宿主 ``app/integrations/model``）。

km 独立部署不能 import 宿主，故镜像复制该范式并做减法：只保留运行面 invoke 两档
（LLM 档 / 媒体档），无管理面客户端。凭据解密、渠道选路与 failover 全在服务侧
（设计 §2.2）；km 侧只持有非解密视图（``ModelConfigSnapshot``）与调用方租户。
"""

from .errors import (
    ModelInvokeClientError,
    ModelInvokeConfigurationError,
    ModelInvokeFailedError,
    ModelInvokeProtocolError,
    ModelInvokeTimeoutError,
    ModelInvokeUnavailableError,
)
from .invoke import ModelInvokeClient, ModelInvokeSyncClient, invoke_target
from .invoke_backend import RemoteInvokeRef, ref_from_view
from .runtime import ModelInvokeRuntime

__all__ = [
    "ModelInvokeClient",
    "ModelInvokeClientError",
    "ModelInvokeConfigurationError",
    "ModelInvokeFailedError",
    "ModelInvokeProtocolError",
    "ModelInvokeRuntime",
    "ModelInvokeSyncClient",
    "ModelInvokeTimeoutError",
    "ModelInvokeUnavailableError",
    "RemoteInvokeRef",
    "invoke_target",
    "ref_from_view",
]
