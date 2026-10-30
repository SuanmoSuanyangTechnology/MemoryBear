"""Skill script execution: lazy, idempotent pack staging + sandbox dispatch (§8.6).

Separated from ``agent/skill_runtime_service.py`` on purpose: ``read_skill`` only reads
the virtual pack from PostgreSQL, while everything here touches the Cube sandbox.

Contract (design doc §8.6):

- **Lazy**: nothing stages during ``read_skill``; the pack reaches the sandbox only when
  the model actually runs a script (the instance is allocated by the first upload).
- **Idempotent**: the pack lands in ``/workspace/skills/<call_name>/<content_hash>/`` and a
  ``.staged`` sentinel (whose size is the hash length) marks a complete copy — the second
  run of the same pack version uploads nothing.
- **Dispatch**: Python goes through the ``code`` channel (``/internal/execute``), Node and
  shell through the ``shell`` channel (``/internal/commands``) with ``cwd`` set to the pack
  directory. Execution-first: only stdout/stderr/exit_code come back, never the source.
- **Identity**: the sandbox is owned by ``(tenant_id, workspace_id, end_user_id)`` plus
  ``conversation_id``; callers must supply all four (no silent fallback to a shared box).
"""

from __future__ import annotations

import logging
import posixpath
import shlex
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.sandbox.client import SandboxPrincipal, SandboxServiceClient
from ..integrations.sandbox.errors import SandboxServiceError
from ..models.skill_model import Skill
from ..models.skill_package_model import SkillFile, SkillPackage
from ..repositories.skill_package_repository import SkillPackageRepository
from .skill_package_service import SkillPackageService

logger = logging.getLogger(__name__)

SKILLS_ROOT = "/workspace/skills"
# Mirrors the sandbox-service ``services/shell.py`` guard (its /internal/commands
# rejects anything longer), kept here so the model gets a clear message before the call.
MAX_COMMAND_BYTES = 64 * 1024
# Sentinel written last: its presence (with the hash as the byte count) proves the copy
# finished. A crashed upload therefore re-stages instead of being mistaken for a hit.
SENTINEL_NAME = ".staged"
# Trailing marker the Python runner appends so the exit code survives the code channel
# (``/internal/execute`` returns stdout/stderr but no exit code).
EXIT_MARKER = "__SKILL_SCRIPT_EXIT__"
PYTHON_RUNNER = '''\
import os, subprocess, sys

_pkg = {pkg!r}
_script = {script!r}
_args = {args!r}
os.chdir(_pkg)
_proc = subprocess.run(
    [sys.executable, _script, *_args],
    cwd=_pkg, capture_output=True, text=True, errors="replace",
)
if _proc.stdout:
    sys.stdout.write(_proc.stdout)
if _proc.stderr:
    sys.stderr.write(_proc.stderr)
sys.stdout.write("\\n{marker}:%d\\n" % _proc.returncode)
'''
INTERPRETERS = {
    "python": ("code", "python3"),
    "python3": ("code", "python3"),
    "node": ("shell", "node"),
    "nodejs": ("shell", "node"),
    "shell": ("shell", "bash"),
    "bash": ("shell", "bash"),
    "sh": ("shell", "sh"),
}


class SkillScriptError(Exception):
    """Caller-facing rejection (bad interpreter, unknown skill/script); never a sandbox fault."""


@dataclass(frozen=True)
class ScriptOutcome:
    interpreter: str
    channel: str
    pack_dir: str
    script: str
    status: str
    exit_code: Optional[int]
    stdout: str
    stderr: str
    error: Optional[str]
    duration_ms: int
    workspace_reset: bool
    stdout_truncated: bool
    stderr_truncated: bool
    staged_files: int
    staged_bytes: int
    pack_reused: bool


def pack_dir(call_name: str, content_hash: str) -> str:
    """``/workspace/skills/<call_name>/<content_hash>`` — hash doubles as the version."""

    return f"{SKILLS_ROOT}/{call_name}/{content_hash}"


def normalize_script_path(raw: str) -> Optional[str]:
    """Normalize a pack-relative script path; ``None`` for absolute paths or traversal."""

    normalized = (raw or "").strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        return None
    return posixpath.normpath(normalized)


async def _chunks(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start:start + size]


async def resolve_pack(
    db: AsyncSession, tenant_id: uuid.UUID, identifier: str
) -> tuple[Skill, SkillPackage, list[SkillFile]]:
    """Locate a standard skill by ``call_name`` (then display name) and load its files.

    ``identifier`` is the runtime call name (ADR-11), not the frontmatter name.
    """

    base = (
        Skill.tenant_id == tenant_id,
        Skill.is_active.is_(True),
        Skill.skill_format == "standard",
    )
    for column in (Skill.call_name, Skill.name):
        row = (
            await db.execute(
                select(Skill, SkillPackage)
                .join(SkillPackage, Skill.package_id == SkillPackage.id)
                .where(*base, column == identifier)
                .limit(1)
            )
        ).first()
        if row:
            skill, package = row
            files = await SkillPackageRepository.get_files_async(db, package.id)
            return skill, package, files
    raise SkillScriptError(f"未找到标准技能 '{identifier}'（可用技能见系统提示的技能索引段）")


async def _read_pack_bytes(file_row: SkillFile) -> bytes:
    """Inline text content, or the object-storage payload for binary/large files."""

    if file_row.content is not None:
        return file_row.content.encode("utf-8")
    if not file_row.storage_ref:
        return b""
    from ..storage import StorageFactory

    return await StorageFactory.get_storage().download(file_row.storage_ref)


async def ensure_pack_staged(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    *,
    call_name: str,
    content_hash: str,
    files: list[SkillFile],
) -> tuple[str, int, int, bool]:
    """Stage the pack into the sandbox once per user; returns ``(dir, files, bytes, reused)``.

    The probe is a read-only ``files/stat`` on the sentinel, so a
    ``(tenant, workspace, end_user)`` combination that never had a sandbox stays
    unallocated when stat misses and upload is the first call to allocate.
    """

    target_dir = pack_dir(call_name, content_hash)
    sentinel = f"{target_dir}/{SENTINEL_NAME}"
    existing = await client.stat(principal, sentinel)
    if existing is not None and int(existing.get("size") or 0) == len(content_hash):
        return target_dir, 0, 0, True

    total = 0
    for file_row in files:
        rel = normalize_script_path(file_row.rel_path)
        if rel is None:
            continue  # a malformed row must not escape the pack directory
        payload = await _read_pack_bytes(file_row)
        await client.upload_file(
            principal, f"{target_dir}/{rel}", _chunks(payload), overwrite=True
        )
        total += len(payload)
    await client.upload_file(
        principal, sentinel, _chunks(content_hash.encode("utf-8")), overwrite=True
    )
    logger.info(
        "skill_pack_staged call_name=%s hash=%s files=%s bytes=%s",
        call_name, content_hash[:12], len(files), total,
    )
    return target_dir, len(files), total, False


def build_command(interpreter: str, script_rel: str, args: list[str], *, max_bytes: int) -> str:
    """Assemble the ``shell``-channel command line (Node / shell interpreters)."""

    binary = INTERPRETERS[interpreter][1]
    parts = [binary, shlex.quote(script_rel), *[shlex.quote(str(a)) for a in args]]
    command = " ".join(parts)
    if len(command.encode("utf-8")) > max_bytes:
        raise SkillScriptError(f"命令长度超过 {max_bytes} 字节上限，请缩短参数后重试")
    return command


def _split_exit_marker(stdout: str) -> tuple[str, Optional[int]]:
    """Peel the runner's trailing ``__SKILL_SCRIPT_EXIT__:<n>`` line off stdout."""

    marker = f"{EXIT_MARKER}:"
    index = stdout.rfind(marker)
    if index < 0:
        return stdout, None
    tail = stdout[index + len(marker):].strip()
    body = stdout[:index]
    if body.endswith("\n"):
        body = body[:-1]
    try:
        return body, int(tail)
    except ValueError:
        return stdout, None


async def run_skill_script(
    client: SandboxServiceClient,
    principal: SandboxPrincipal,
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: str,
    identifier: str,
    script: str,
    interpreter: str = "python",
    args: Optional[list[str]] = None,
    timeout_s: Optional[float] = None,
) -> ScriptOutcome:
    """Stage the pack on demand, then run one script inside the sandbox."""

    key = (interpreter or "python").strip().lower()
    if key not in INTERPRETERS:
        raise SkillScriptError(
            f"不支持的解释器 '{interpreter}'（可用：python / node / shell）"
        )
    channel, _binary = INTERPRETERS[key]

    script_rel = normalize_script_path(script)
    if script_rel is None:
        raise SkillScriptError(f"非法脚本路径 '{script}'（只允许包内相对路径）")

    skill, package, files = await resolve_pack(db, tenant_id, identifier)
    available = {f.rel_path for f in files}
    if script_rel not in available:
        listing = "\n".join(f"- {p}" for p in sorted(available)[:50])
        raise SkillScriptError(
            f"包内不存在脚本 '{script_rel}'。可用文件：\n{listing}"
        )

    call_name = skill.call_name or package.name
    target_dir, staged_files, staged_bytes, reused = await ensure_pack_staged(
        client,
        principal,
        call_name=call_name,
        content_hash=package.content_hash,
        files=files,
    )

    arg_list = [str(a) for a in (args or [])]
    timeout = float(timeout_s) if timeout_s not in (None, "") else None

    if channel == "code":
        code = PYTHON_RUNNER.format(
            pkg=target_dir, script=f"{target_dir}/{script_rel}", args=arg_list, marker=EXIT_MARKER
        )
        result = await client.execute(
            principal, conversation_id=conversation_id, code=code, language="python3",
            timeout_s=timeout,
        )
        stdout, exit_code = _split_exit_marker(result.stdout)
        return ScriptOutcome(
            interpreter=key, channel=channel, pack_dir=target_dir, script=script_rel,
            status=result.status, exit_code=exit_code, stdout=stdout, stderr=result.stderr,
            error=result.error, duration_ms=result.duration_ms,
            workspace_reset=result.workspace_reset,
            stdout_truncated=result.stdout_truncated, stderr_truncated=result.stderr_truncated,
            staged_files=staged_files, staged_bytes=staged_bytes, pack_reused=reused,
        )

    command = build_command(key, script_rel, arg_list, max_bytes=MAX_COMMAND_BYTES)
    command_result = await client.run_command(
        principal, conversation_id=conversation_id, cmd=command, cwd=target_dir,
        timeout_s=timeout,
    )
    return ScriptOutcome(
        interpreter=key, channel=channel, pack_dir=target_dir, script=script_rel,
        status=command_result.status, exit_code=command_result.exit_code,
        stdout=command_result.stdout, stderr=command_result.stderr, error=command_result.error,
        duration_ms=command_result.duration_ms, workspace_reset=command_result.workspace_reset,
        stdout_truncated=command_result.stdout_truncated,
        stderr_truncated=command_result.stderr_truncated,
        staged_files=staged_files, staged_bytes=staged_bytes, pack_reused=reused,
    )


__all__ = [
    "SkillScriptError",
    "ScriptOutcome",
    "pack_dir",
    "normalize_script_path",
    "resolve_pack",
    "ensure_pack_staged",
    "build_command",
    "run_skill_script",
    "SKILLS_ROOT",
]


# Kept for callers that only need the storage read helper (mirrors the package service).
_read_storage_object = SkillPackageService._read_storage_object