"""Host validation-class paths -> service-side `/internal/v1/models/validate` (config_id shape).

The host only names "which config to probe, on behalf of which tenant" (same shape
as ``RemoteInvokeRef``); credential decryption, channel routing, and live probing all
live service-side (plaintext never leaves the host boundary). Results come back with
a **neutral kind**; the domain reason enum and copy are translated by the caller, so
this layer knows no business copy. For ``valid`` it also carries the neutral ``usage``
measured service-side (e.g. embedding's ``vector_dimension``), letting the caller run
shape guards on its side.

Failure-surface contract (see service-side ``_resolve_stored_probe_inputs``): 4003 row
missing / 4012 no available credential -> ``no_credential``; 4013 SpeedBear binding
missing / 4014 decryption failure / anything else -> ``unavailable``; 200 with
``valid=false`` -> ``probe_failed``; transport failures (timeout/unreachable) are also
``unavailable``.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, NamedTuple

from ...errors import BizCode

from .errors import ModelServiceClientError, biz_code_from_remote
from .invoke_backend import RemoteInvokeRef, context_from_ref
from .runtime import get_model_service_client

logger = logging.getLogger(__name__)

_VALIDATE_PATH = "/internal/v1/models/validate"
_NO_CREDENTIAL_CODES = frozenset({BizCode.MODEL_NOT_FOUND, BizCode.NO_AVAILABLE_CHANNEL})


class StoredValidationOutcome(NamedTuple):
    """Neutral outcome of probing an existing config: ``kind`` is translated by the caller into a domain reason.

    ``usage`` passes the neutral usage body returned service-side through verbatim
    (probe-measured facts, e.g. embedding's ``vector_dimension``) without translating
    field names; it is ``None`` when the probe produces no usage or the shape does not
    match.
    """

    kind: Literal["valid", "probe_failed", "no_credential", "unavailable"]
    detail: str | None = None
    usage: dict[str, Any] | None = None


def _detail(value: Any) -> str | None:
    return str(value) if value else None


async def aprobe_stored_config(
    ref: RemoteInvokeRef, *, model_type: str
) -> StoredValidationOutcome:
    """Probe an existing config live through the model service: submits only the config id and the slot-derived type."""

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
            usage = data.get("usage")
            return StoredValidationOutcome(
                "valid", usage=usage if isinstance(usage, dict) else None
            )
        return StoredValidationOutcome(
            "probe_failed", _detail(data.get("error") or data.get("message"))
        )

    code = biz_code_from_remote(envelope.get("code"))
    kind: Literal["no_credential", "unavailable"] = (
        "no_credential" if code in _NO_CREDENTIAL_CODES else "unavailable"
    )
    return StoredValidationOutcome(kind, _detail(envelope.get("error") or envelope.get("msg")))


__all__ = ["StoredValidationOutcome", "aprobe_stored_config"]
