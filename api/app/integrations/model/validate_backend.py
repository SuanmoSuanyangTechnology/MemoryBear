"""C3 接缝：宿主校验类路径 → 服务侧 `/internal/v1/models/validate`（config_id 形态）。

宿主只声明「探测哪个配置、代表哪个租户」（``RemoteInvokeRef`` 同形），凭据解密、渠道选路与
活体探针全在服务侧（设计 §2.2：明文不出宿主边界）。结果按**中性 kind** 返回，域内 reason
枚举与文案由调用方翻译，本层不认业务文案。

失败面契约（B8 已定，见服务侧 ``_resolve_stored_probe_inputs``）：4003 行缺失 / 4012 无可用
凭据 → ``no_credential``；4013 SpeedBear 缺绑 / 4014 解密失败 / 其余 → ``unavailable``；
200 且 ``valid=false`` → ``probe_failed``；传输层失败（超时/不可达）亦为 ``unavailable``。
"""

from __future__ import annotations

import logging
from typing import Any, Literal, NamedTuple

from app.core.error_codes import BizCode

from .errors import ModelServiceClientError, biz_code_from_remote
from .invoke_backend import RemoteInvokeRef, context_from_ref
from .runtime import get_model_service_client

logger = logging.getLogger(__name__)

_VALIDATE_PATH = "/internal/v1/models/validate"
_NO_CREDENTIAL_CODES = frozenset({BizCode.MODEL_NOT_FOUND, BizCode.NO_AVAILABLE_CHANNEL})


class StoredValidationOutcome(NamedTuple):
    """既有配置探活的中性结果：``kind`` 由调用方翻译为域内 reason。"""

    kind: Literal["valid", "probe_failed", "no_credential", "unavailable"]
    detail: str | None = None


def _detail(value: Any) -> str | None:
    return str(value) if value else None


async def aprobe_stored_config(
    ref: RemoteInvokeRef, *, model_type: str
) -> StoredValidationOutcome:
    """经模型服务对既有配置做活体探测：只上送配置 id 与槽位派生类型。"""

    payload = {"model_config_id": str(ref.config_id), "model_type": model_type}
    try:
        result = await get_model_service_client().call(
            "POST", _VALIDATE_PATH, context=context_from_ref(ref), payload=payload
        )
    except ModelServiceClientError as exc:
        logger.warning(
            "model_service_validate_unreachable config_id=%s error=%s", ref.config_id, exc
        )
        return StoredValidationOutcome("unavailable", str(exc))

    envelope = result.payload if isinstance(result.payload, dict) else {}
    if result.status_code == 200:
        data = envelope.get("data")
        data = data if isinstance(data, dict) else {}
        if data.get("valid"):
            return StoredValidationOutcome("valid")
        return StoredValidationOutcome(
            "probe_failed", _detail(data.get("error") or data.get("message"))
        )

    code = biz_code_from_remote(envelope.get("code"))
    kind: Literal["no_credential", "unavailable"] = (
        "no_credential" if code in _NO_CREDENTIAL_CODES else "unavailable"
    )
    return StoredValidationOutcome(kind, _detail(envelope.get("error") or envelope.get("msg")))


__all__ = ["StoredValidationOutcome", "aprobe_stored_config"]
