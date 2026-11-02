"""Path policy of the file interfaces (design doc 7.2).

Every path a caller names must end up under ``/workspace`` after lexical
normalisation. Symlinks inside the sandbox are not resolved here: the directories are
an organisation convention, the isolation boundary is the microVM itself.
"""

from __future__ import annotations

import posixpath

from ..errors import SandboxError, invalid_request

WORKSPACE_ROOT = "/workspace"
MAX_PATH_LENGTH = 4096
SESSIONS_DIR = f"{WORKSPACE_ROOT}/sessions"


def normalize(raw: object, *, field: str = "path") -> str:
    """Return the canonical absolute path, or raise ``path_forbidden`` / ``invalid_request``."""

    if not isinstance(raw, str) or not raw.strip():
        raise invalid_request(f"{field} is required", field=field)
    if "\x00" in raw:
        raise SandboxError("path_forbidden", f"{field} contains a NUL byte")
    if len(raw) > MAX_PATH_LENGTH:
        raise invalid_request(f"{field} is too long (max {MAX_PATH_LENGTH})", field=field)
    if not raw.startswith("/"):
        raise SandboxError("path_forbidden", f"{field} must be an absolute path")
    # posixpath keeps a leading "//" as is; fold repeated slashes first.
    path = posixpath.normpath("/" + raw.lstrip("/"))
    if path != WORKSPACE_ROOT and not path.startswith(WORKSPACE_ROOT + "/"):
        raise SandboxError("path_forbidden", f"{field} must be inside {WORKSPACE_ROOT}")
    return path


def _segment(conversation_id: str) -> str:
    if (
        not conversation_id
        or "/" in conversation_id
        or "\\" in conversation_id
        or "\x00" in conversation_id
        or conversation_id in (".", "..")
    ):
        raise invalid_request(
            "conversation_id cannot be used as a directory name", field="conversation_id"
        )
    return conversation_id


def session_dir(conversation_id: str) -> str:
    return f"{SESSIONS_DIR}/{_segment(conversation_id)}"


def out_dir(conversation_id: str) -> str:
    return f"{session_dir(conversation_id)}/out"


def is_within(path: str, directory: str) -> bool:
    """``path`` is strictly below ``directory`` (both already normalised)."""

    return path.startswith(directory.rstrip("/") + "/")
