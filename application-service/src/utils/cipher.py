"""Master-key envelope cipher used to decrypt channel credentials at resolution time."""
from __future__ import annotations

import base64

from redbear_model.crypto import AESGCMEnvCipher

from ..config import current_settings

_CIPHER: AESGCMEnvCipher | None = None


def cipher_from_env() -> AESGCMEnvCipher:
    """MODEL_CREDENTIALS_KEY (base64 32B) → envelope cipher; process-wide singleton."""
    global _CIPHER
    if _CIPHER is None:
        raw = current_settings().model_credentials_key.get_secret_value().strip()
        if not raw:
            raise RuntimeError("MODEL_CREDENTIALS_KEY is not set (base64 32B master key)")
        try:
            _CIPHER = AESGCMEnvCipher(base64.b64decode(raw))
        except ValueError as exc:
            raise RuntimeError(f"MODEL_CREDENTIALS_KEY invalid: {exc}") from exc
    return _CIPHER
