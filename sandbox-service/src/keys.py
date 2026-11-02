"""Identity keys (design doc chapter 3).

``sandbox_key = (tenant_id, workspace_id, end_user_id)`` owns the microVM;
``kernel_key = sandbox_key + conversation_id (+ language)`` owns one kernel.
Keys are only ever built from an already verified principal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .errors import invalid_request

MAX_ID_LENGTH = 128
_SEP = "\x1f"
LANGUAGES = frozenset({"python3", "nodejs"})
# A conversation owns one kernel per channel: ``code`` runs user code, ``shell`` only
# launches terminal commands, so a long command never blocks the code interpreter.
CHANNEL_CODE = "code"
CHANNEL_SHELL = "shell"
CHANNELS = frozenset({CHANNEL_CODE, CHANNEL_SHELL})


def _check(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_request(f"{name} is required", field=name)
    if len(value) > MAX_ID_LENGTH:
        raise invalid_request(f"{name} is too long (max {MAX_ID_LENGTH})", field=name)
    if _SEP in value:
        raise invalid_request(f"{name} contains a reserved character", field=name)
    return value


@dataclass(frozen=True)
class SandboxKey:
    tenant_id: str
    workspace_id: str
    end_user_id: str

    def __post_init__(self) -> None:
        _check("tenant_id", self.tenant_id)
        _check("workspace_id", self.workspace_id)
        _check("end_user_id", self.end_user_id)

    @property
    def digest(self) -> str:
        raw = _SEP.join((self.tenant_id, self.workspace_id, self.end_user_id))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class KernelKey:
    sandbox: SandboxKey
    conversation_id: str
    language: str = "python3"
    channel: str = CHANNEL_CODE

    def __post_init__(self) -> None:
        _check("conversation_id", self.conversation_id)
        if self.channel not in CHANNELS:
            raise invalid_request(f"unsupported channel {self.channel!r}", field="channel")
        if self.language not in LANGUAGES:
            raise invalid_request(
                f"unsupported language {self.language!r}; expected one of {sorted(LANGUAGES)}",
                field="language",
            )

    @property
    def token(self) -> str:
        base = f"{self.conversation_id}{_SEP}{self.language}"
        # The code channel keeps the historical token so existing records stay valid.
        return base if self.channel == CHANNEL_CODE else f"{base}{_SEP}{self.channel}"

    @property
    def lock_key(self) -> str:
        return f"{self.sandbox.digest}:{self.token}"
