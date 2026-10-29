"""Skill package service - Agent Skills standard pack import / export / conversion.

Ported from the monolith ``app/services/skill_package_service.py`` and extended
per the design doc (ADR-13 option A: the skill runtime lives in this service):

- import: zip **or** URL source, optional ``dry_run`` preview, member filtering and
  member-level metadata override, plugin (one folder, many skills) splitting (§7.8)
- export: byte-faithful rebuild from ``skill_files``; legacy rows are converted on the fly
- ``legacy_to_package_files``: deterministic legacy row -> standard pack mapping

Design constraints:
- frontmatter is parsed into a dict (``frontmatter_raw``) while the SKILL.md bytes live in
  ``skill_files``; the two are decoupled and export always returns the original text,
  never a rebuild from the dict.
- small text files inline in ``skill_files.content``; binary or over
  ``SKILL_PACKAGE_INLINE_LIMIT`` files go to object storage (``storage_ref``) and are read
  back on export so round-trips stay byte-identical.
- one transaction per import; object-storage cleanup on delete is best-effort.

Plugin model (§6.6 / §7.8): one SKILL.md is a skill; several SKILL.md entries form a
plugin. Each member becomes an independent package plus its own ``skills`` row linked via
``plugin_id``; the plugin row itself carries no runtime content.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import io
import mimetypes
import re
import threading
import uuid
import zipfile
from typing import Callable, Coroutine, Optional
from urllib.parse import urlparse

import httpx
import yaml
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from ..config import current_settings
from ..errors import BizCode, BusinessException
from ..models.skill_model import Skill
from ..models.skill_package_model import SkillPackage
from ..repositories.skill_package_repository import SkillPackageRepository
from ..storage import StorageFactory
from ..storage.exceptions import StorageError


# Agent Skills spec: frontmatter name is lowercase alnum + hyphen, <=64.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
# Plugin member / call name: letters, digits, underscore, hyphen, <=64 (§9.1).
_CALL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Slugify for legacy rows: everything else collapses to a hyphen.
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9-]+")
_SKILL_MD_GLOB_RE = re.compile(r"(^|/)SKILL\.md$")

DESCRIPTION_MAX = 1024
MAX_REL_PATH_LEN = 1024


def slugify_name(raw_name: str, fallback_id: str = "") -> str:
    """legacy name -> standard frontmatter name (lowercase / digits / hyphen, <=64)."""

    slug = _SLUG_INVALID_RE.sub("-", (raw_name or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")[:64]
    if not slug:
        slug = f"skill-{fallback_id[:8]}" if fallback_id else "skill"
    return slug


def _compute_content_hash(files: list[dict]) -> str:
    """Whole-pack SHA-256: (rel_path, file sha256) pairs, sorted by path."""

    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["rel_path"]):
        h.update(f["rel_path"].encode("utf-8"))
        h.update(b"\x00")
        h.update(f["sha256"].encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


def _parse_frontmatter(skill_md_text: str) -> tuple[dict, str]:
    """Parse the YAML frontmatter; returns ({}, full text) when absent/invalid."""

    if not skill_md_text.startswith("---"):
        return {}, skill_md_text
    lines = skill_md_text.split("\n", 1) if "\n" in skill_md_text else [skill_md_text, ""]
    if len(lines) < 2:
        return {}, skill_md_text
    match = re.match(r"(.*?)\n---\s*\n?(.*)", lines[1], re.DOTALL)
    if not match:
        return {}, skill_md_text
    fm_text, body = match.group(1), match.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, skill_md_text
    return (fm if isinstance(fm, dict) else {}), body


def _build_skill_md(frontmatter: dict, body: str) -> str:
    """Rebuild SKILL.md from a dict (legacy conversion only; standard import/export keeps the bytes)."""

    fm_text = yaml.safe_dump(
        {k: v for k, v in frontmatter.items() if k not in ("_legacy",)},
        allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).strip()
    return f"---\n{fm_text}\n---\n\n{body.lstrip()}"


def _run_async(coro_factory: "Callable[[], Coroutine]"):
    """Run a coroutine from sync code: direct when no loop, else a private thread."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    box: dict = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro_factory())
        except Exception as exc:  # noqa: BLE001 - re-raised on the calling thread
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _skill_storage_key(tenant_id: uuid.UUID, package_id: uuid.UUID, rel_path: str) -> str:
    """Object-storage key: skills/{tenant}/{package}/{rel_path}"""

    return f"skills/{tenant_id}/{package_id}/{rel_path}"


def _detect_binary(content: bytes) -> bool:
    """Binary detection: NUL byte or non-UTF-8 payload."""

    if b"\x00" in content[:8192]:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _normalize_rel_path(raw: str) -> Optional[str]:
    """Normalise a pack-relative path; None when illegal (absolute / traversal / empty)."""

    normalized = (raw or "").replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/"):
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    result = "/".join(parts)
    if len(result) > MAX_REL_PATH_LEN:
        return None
    return result


# OS / archive metadata that is never part of a skill. macOS zips carry a top-level
# ``__MACOSX/`` tree of AppleDouble ``._*`` resource-fork files and ``.DS_Store``
# directories; Windows exports add ``Thumbs.db`` / ``desktop.ini``. Stored verbatim
# they inflate file counts, round-trip on export, and a ``__MACOSX`` top-level
# directory defeats the single-root-prefix detection in ``_root_prefix``.
_JUNK_FILE_BASENAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def _is_junk_path(rel: str) -> bool:
    """True for OS/archive metadata entries (``__MACOSX/``, ``.DS_Store``, ``._*`` …)."""

    parts = rel.split("/")
    if any(part == "__MACOSX" for part in parts):
        return True
    base = parts[-1]
    if base.lower() in _JUNK_FILE_BASENAMES:
        return True
    if base.startswith("._"):  # AppleDouble resource fork, e.g. ._SKILL.md
        return True
    return False


class SkillPackageService:
    """Standard pack import / export / conversion."""

    # ==================== URL source (SSRF guard, §13) ====================

    @staticmethod
    def _allowed_url_hosts() -> set[str]:
        raw = current_settings().skill_import_url_hosts or ""
        return {h.strip().lower() for h in raw.split(",") if h.strip()}

    @staticmethod
    def _validate_import_url(url: str) -> str:
        """Validate an import URL: https only, allow-listed host, no embedded credentials."""

        parsed = urlparse((url or "").strip())
        if parsed.scheme != "https":
            raise BusinessException("导入地址必须是 https 链接", BizCode.BAD_REQUEST)
        if parsed.username or parsed.password:
            raise BusinessException("导入地址不允许内嵌凭据", BizCode.BAD_REQUEST)
        host = (parsed.hostname or "").lower()
        allowed = SkillPackageService._allowed_url_hosts()
        if host not in allowed:
            raise BusinessException(
                f"导入地址域名不在白名单内（允许：{', '.join(sorted(allowed))}）",
                BizCode.BAD_REQUEST,
            )
        return url.strip()

    @staticmethod
    def _resolve_github_url(url: str) -> tuple[str, Optional[str]]:
        """Map a GitHub web address onto the codeload archive it should fetch.

        ``github.com/<owner>/<repo>``                  -> codeload ``/zip/HEAD``
        ``github.com/<owner>/<repo>/tree/<ref>/<dir>`` -> that ref's zip, ``<dir>`` kept
        ``codeload.github.com/...``                    -> passed through unchanged

        Without this the repository landing page (an HTML document) is handed to the zip
        reader; the design doc §17.2 promises both web forms, so the mapping belongs to
        this endpoint rather than to every caller.
        """

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host == "codeload.github.com":
            segs = [p for p in parsed.path.split("/") if p]
            # /<owner>/<repo>/zip/<ref> 或 /<owner>/<repo>/zipball/<ref>
            if len(segs) < 4 or segs[2] not in ("zip", "zipball"):
                raise BusinessException(
                    "codeload 地址需指向 zip / zipball 归档", BizCode.BAD_REQUEST
                )
            return url, None

        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            raise BusinessException(
                "GitHub 地址需形如 https://github.com/<owner>/<repo>", BizCode.BAD_REQUEST
            )
        owner = parts[0]
        repo = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
        rest = parts[2:]

        subdir: Optional[str] = None
        if not rest:
            ref = "HEAD"
        elif rest[0] == "tree" and len(rest) >= 2:
            ref = rest[1]
            subdir = "/".join(rest[2:]) or None
        else:
            raise BusinessException(
                "仅支持仓库根地址或 tree/<分支>/<目录> 地址", BizCode.BAD_REQUEST
            )
        return f"https://codeload.github.com/{owner}/{repo}/zip/{ref}", subdir

    @staticmethod
    def _restrict_entries_to_subdir(
        entries: dict[str, bytes], subdir: str
    ) -> dict[str, bytes]:
        """Keep a single directory subtree (``tree/<ref>/<dir>`` import).

        Paths stay pack-relative (not re-rooted) so ``_split_members`` keys the member
        the way the design doc writes identifiers (``skills/<name>``).
        """

        sub = _normalize_rel_path(subdir.strip().strip("/"))
        if not sub:
            raise BusinessException("地址中的目录不合法", BizCode.BAD_REQUEST)
        prefix = sub + "/"
        kept = {p: b for p, b in entries.items() if p.startswith(prefix)}
        if not kept:
            raise BusinessException(
                f"目录 {sub} 下没有可导入的文件（请确认分支与目录名）", BizCode.BAD_REQUEST
            )
        return kept

    @staticmethod
    def fetch_url_zip(url: str) -> tuple[bytes, Optional[str]]:
        """Download a zip for an allow-listed GitHub address, with timeout and size caps.

        Returns ``(zip_bytes, subdir)``; ``subdir`` is non-null for
        ``github.com/<owner>/<repo>/tree/<ref>/<dir>`` so the caller keeps just that tree.

        Redirects are not followed across hosts (a redirect to another host aborts),
        so an allow-listed entry point cannot bounce the fetch to an arbitrary target.
        """

        target, subdir = SkillPackageService._resolve_github_url(
            SkillPackageService._validate_import_url(url)
        )
        cfg = current_settings()
        max_bytes = cfg.skill_import_url_max_bytes
        timeout = cfg.skill_import_url_timeout

        async def _download() -> bytes:
            headers = {"Accept": "application/zip, application/octet-stream"}
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=cfg.skill_import_url_trust_env,
            ) as client:
                resp = await client.get(target, headers=headers)
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    redirected = urlparse(location).hostname or ""
                    if redirected.lower() != urlparse(target).hostname:
                        raise BusinessException(
                            "导入地址发生跨域跳转，已中止（可能存在 SSRF 风险）",
                            BizCode.BAD_REQUEST,
                        )
                    raise BusinessException("导入地址发生跳转，请直接使用最终地址", BizCode.BAD_REQUEST)
                resp.raise_for_status()
                body = resp.content
                if len(body) > max_bytes:
                    raise BusinessException(
                        f"远端内容超过体积上限 {max_bytes} 字节", BizCode.BAD_REQUEST
                    )
                return body

        try:
            return _run_async(_download), subdir
        except httpx.HTTPStatusError as exc:
            raise BusinessException(
                f"拉取远端地址失败：HTTP {exc.response.status_code}", BizCode.BAD_REQUEST
            ) from exc
        except httpx.HTTPError as exc:
            raise BusinessException(f"拉取远端地址失败：{exc}", BizCode.BAD_REQUEST) from exc

    # ==================== unpack & member splitting ====================

    @staticmethod
    def _read_zip_entries(zip_bytes: bytes) -> dict[str, bytes]:
        """Safely unpack a zip into ``{rel_path: bytes}``.

        Guards: path traversal, symlinks, per-file count and total size caps.
        Layout is left untouched — member splitting decides what a pack root is.
        """

        cfg = current_settings()
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except (zipfile.BadZipFile, ValueError) as e:
            raise BusinessException(f"不是有效的 zip 文件: {e}", BizCode.BAD_REQUEST)

        names = [
            n
            for n in zf.namelist()
            if not n.endswith("/") and not _is_junk_path(_normalize_rel_path(n) or n)
        ]
        if not names:
            raise BusinessException("zip 内没有文件", BizCode.BAD_REQUEST)
        if len(names) > cfg.skill_package_max_files:
            raise BusinessException(
                f"包内文件数 {len(names)} 超过上限 {cfg.skill_package_max_files}"
                f"（可通过 SKILL_PACKAGE_MAX_FILES 调整）",
                BizCode.BAD_REQUEST,
            )

        total = 0
        files: dict[str, bytes] = {}
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            # zipfile assumes CP437 when the UTF-8 flag is missing; CJK names are
            # the usual casualty.
            if not (info.flag_bits & 0x800):
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass
            normalized = _normalize_rel_path(name)
            if normalized is None:
                raise BusinessException(f"包内存在非法路径: {name}", BizCode.BAD_REQUEST)
            if _is_junk_path(normalized):
                continue  # OS/archive metadata, never part of a skill
            if info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BusinessException(f"包内存在符号链接: {name}", BizCode.BAD_REQUEST)
            total += info.file_size
            if total > cfg.skill_package_max_size:
                raise BusinessException(
                    f"解包总大小超过上限 {cfg.skill_package_max_size} 字节"
                    f"（可通过 SKILL_PACKAGE_MAX_SIZE 调整）",
                    BizCode.BAD_REQUEST,
                )
            files[normalized] = zf.read(info)
        return files

    @staticmethod
    def _root_prefix(files: dict[str, bytes]) -> Optional[str]:
        """Detect a zip wrapped in one top-level directory (common in repo exports).

        Returns the prefix to strip, or None when the pack root is the zip root.
        Only applies when the root itself holds no SKILL.md.
        """

        if "SKILL.md" in files:
            return None
        top_dirs = {n.split("/", 1)[0] for n in files if "/" in n}
        if len(top_dirs) == 1:
            only = next(iter(top_dirs))
            if any(n.startswith(only + "/") for n in files):
                return only + "/"
        return None

    @staticmethod
    def _split_members(files: dict[str, bytes]) -> list[dict]:
        """Split a pack into plugin members (§7.8).

        Rules:
        - candidates are every ``(^|/)SKILL.md`` entry;
        - a member's prefix is its SKILL.md directory; its files are every entry
          starting with that prefix, with the prefix stripped (each member pack is
          rooted at its own SKILL.md);
        - nested SKILL.md stays inside the outer member *and* forms its own member;
        - members never share ``references/``/``scripts/`` (prefix stripping isolates them).

        Returns ``[{key, name, description, files}]`` where ``key`` is the member
        identifier echoed by ``dry_run`` and consumed by ``members`` / ``meta``.
        """

        candidates = [p for p in files if _SKILL_MD_GLOB_RE.search(p)]
        if not candidates:
            raise BusinessException("包内未找到 SKILL.md", BizCode.BAD_REQUEST)
        if len(candidates) > current_settings().skill_plugin_max_members:
            raise BusinessException(
                f"插件成员数 {len(candidates)} 超过上限 "
                f"{current_settings().skill_plugin_max_members}"
                f"（可通过 SKILL_PLUGIN_MAX_MEMBERS 调整）",
                BizCode.BAD_REQUEST,
            )

        members: list[dict] = []
        for path in sorted(candidates):
            prefix = path[: -len("SKILL.md")]
            member_files: dict[str, bytes] = {}
            for rel, content in files.items():
                if rel.startswith(prefix):
                    member_key = rel[len(prefix):]
                    if member_key:
                        member_files[member_key] = content
            if "SKILL.md" not in member_files:
                # Defensive: prefix stripping must always yield the member root.
                raise BusinessException(f"成员拆分异常：{path}", BizCode.INTERNAL_ERROR)
            member_md = member_files["SKILL.md"].decode("utf-8", errors="strict")
            frontmatter, _body = _parse_frontmatter(member_md)
            members.append(
                {
                    "key": prefix.rstrip("/") if prefix else frontmatter.get("name", ""),
                    "name": frontmatter.get("name", ""),
                    "description": frontmatter.get("description", ""),
                    "files": member_files,
                    "frontmatter": frontmatter,
                }
            )
        return members

    # ==================== validation & file rows ====================

    @staticmethod
    def _validate_frontmatter(frontmatter: dict, source_type: str) -> list[str]:
        """Validate frontmatter; returns warnings. ``legacy`` sources are relaxed."""

        warnings: list[str] = []

        name = frontmatter.get("name")
        if not name or not isinstance(name, str):
            raise BusinessException("frontmatter 缺少 name 字段", BizCode.BAD_REQUEST)
        if len(name) > 64:
            raise BusinessException(f"name 超长（{len(name)} > 64）", BizCode.BAD_REQUEST)
        if not _SKILL_NAME_RE.match(name):
            raise BusinessException(
                f"name '{name}' 不合法：仅允许小写字母/数字/连字符，"
                "不以连字符开头结尾、不含连续连字符",
                BizCode.BAD_REQUEST,
            )

        description = frontmatter.get("description")
        if source_type != "legacy":
            if not description or not isinstance(description, str):
                raise BusinessException("frontmatter 缺少 description 字段", BizCode.BAD_REQUEST)
        if description and isinstance(description, str) and len(description) > DESCRIPTION_MAX:
            raise BusinessException(
                f"description 超长（{len(description)} > {DESCRIPTION_MAX}）", BizCode.BAD_REQUEST
            )
        return warnings

    @staticmethod
    def validate_call_name(call_name: str) -> str:
        """``call_name`` is the runtime identifier: ^[A-Za-z0-9_-]{1,64}$ (§9.1)."""

        value = (call_name or "").strip()
        if not _CALL_NAME_RE.match(value):
            raise BusinessException(
                "技能调用名称需使用字母、数字、下划线或连字符，最多 64 个字符",
                BizCode.BAD_REQUEST,
            )
        return value

    @staticmethod
    def _prepare_files(files: dict[str, bytes]) -> list[dict]:
        """Stage 1: file bytes -> ``skill_files`` row metadata (sha256 / size / binary).

        Object storage is not touched here; ``storage_ref`` stays None until the
        package id exists and ``_persist_files`` uploads and back-fills it.
        """

        rows = []
        for rel_path, content in sorted(files.items()):
            sha = hashlib.sha256(content).hexdigest()
            size = len(content)
            is_binary = _detect_binary(content)
            row_content = None if is_binary else content.decode("utf-8")
            rows.append({
                "rel_path": rel_path,
                "content": row_content,
                "storage_ref": None,
                "is_binary": is_binary,
                "size": size,
                "sha256": sha,
                "_bytes": content,  # consumed by _persist_files
            })
        return rows

    @staticmethod
    def _persist_files(rows: list[dict], tenant_id: uuid.UUID, package_id: uuid.UUID) -> None:
        """Stage 2: upload binary / oversized files to object storage and back-fill ``storage_ref``."""

        storage = StorageFactory.get_storage()
        inline_limit = current_settings().skill_package_inline_limit
        for row in rows:
            content = row.pop("_bytes")
            use_storage = row["is_binary"] or row["size"] > inline_limit
            if not use_storage:
                continue
            rel_path = row["rel_path"]
            key = _skill_storage_key(tenant_id, package_id, rel_path)
            content_type = mimetypes.guess_type(rel_path)[0]

            async def _upload() -> str:
                return await storage.upload(key, content, content_type)

            _run_async(_upload)
            row["storage_ref"] = key
            row["content"] = None  # binary / oversized text is never inlined

    @staticmethod
    def _delete_storage_objects(db: Session, package_id: uuid.UUID) -> None:
        """Best-effort cleanup of one package's object-storage keys (never blocks the DB delete)."""

        refs = SkillPackageRepository(db).storage_refs(package_id)
        if not refs:
            return
        storage = StorageFactory.get_storage()

        async def _delete_all() -> None:
            for key in refs:
                try:
                    await storage.delete(key)
                except StorageError:
                    pass  # leftovers are reaped by ops

        _run_async(_delete_all)

    @staticmethod
    def _read_storage_object(key: str) -> bytes:
        """Read one object-storage payload (export path)."""

        storage = StorageFactory.get_storage()

        async def _download() -> bytes:
            return await storage.download(key)

        return _run_async(_download)# ==================== import ====================

    @staticmethod
    def import_package(
        db: Session,
        tenant_id: uuid.UUID,
        *,
        zip_bytes: Optional[bytes] = None,
        url: Optional[str] = None,
        dry_run: bool = False,
        members: Optional[list[str]] = None,
        meta: Optional[list[dict]] = None,
        plugin_name: Optional[str] = None,
        plugin_description: Optional[str] = None,
        source_ref: Optional[str] = None,
        overwrite: bool = False,
        conflict_strategy: str = "rename",
    ) -> dict:
        """Import one standard pack from a zip or an allow-listed URL.

        ``dry_run=True`` runs the whole pipeline (unpack, split, validate, hash) and
        returns a preview without touching the database or the quota. The preview and
        the committed response share one shape, the only difference being that
        ``package_id`` / ``skill_id`` are null in previews.

        Plugin handling (§6.6 / §7.8): two or more SKILL.md entries make a plugin and every
        member is imported — ``members`` is deprecated and ignored because the bundle imports
        as one indivisible unit; a plugin that already exists is rejected, not re-imported.
        """

        url_subdir: Optional[str] = None
        if url and not zip_bytes:
            zip_bytes, url_subdir = SkillPackageService.fetch_url_zip(url)
            source_ref = source_ref or url
        if not zip_bytes:
            raise BusinessException("缺少技能包内容（file 或 url 二选一）", BizCode.BAD_REQUEST)

        entries = SkillPackageService._read_zip_entries(zip_bytes)
        prefix = SkillPackageService._root_prefix(entries)
        if prefix:
            entries = {k[len(prefix):]: v for k, v in entries.items() if k.startswith(prefix)}
        if url_subdir:
            entries = SkillPackageService._restrict_entries_to_subdir(entries, url_subdir)

        split = SkillPackageService._split_members(entries)
        is_plugin = len(split) > 1
        kind = "plugin" if is_plugin else "skill"

        # --- member selection ---------------------------------------------------
        # A plugin imports as a whole (product decision): ``members`` stays in the signature
        # for wire compatibility but no longer filters — every member is kept.
        selected = split

        # --- member-level metadata override -------------------------------------
        meta_by_key = {m["identifier"]: m for m in (meta or []) if m.get("identifier")}
        unknown_meta = set(meta_by_key) - {m["key"] for m in selected}
        if unknown_meta:
            raise BusinessException(
                f"meta 含未知成员标识：{', '.join(sorted(unknown_meta))}", BizCode.BAD_REQUEST
            )

        # --- per-member prepare (shared by preview and commit) -------------------
        prepared: list[dict] = []
        for member in selected:
            fm = dict(member["frontmatter"])
            warnings = SkillPackageService._validate_frontmatter(fm, source_type="imported")

            override = meta_by_key.get(member["key"])
            display = (override or {}).get("name") or fm["name"]
            description = ((override or {}).get("description") or fm.get("description") or "")[:DESCRIPTION_MAX]
            call_name = ((override or {}).get("call_name") or fm["name"])
            call_name = SkillPackageService.validate_call_name(call_name)

            skill_md = member["files"]["SKILL.md"].decode("utf-8", errors="strict")
            if len(skill_md) > 500 * 80:
                warnings.append(
                    f"技能「{display}」的 SKILL.md 超过约 500 行，"
                    "建议拆分到 references/（渐进披露最佳实践）"
                )

            has_scripts = any(p.startswith("scripts/") for p in member["files"])
            has_assets = any(p.startswith("assets/") for p in member["files"])
            if has_scripts:
                warnings.append(
                    f"技能「{display}」包含 scripts/ 脚本，导入后可在隔离环境中执行"
                )

            rows = SkillPackageService._prepare_files(member["files"])
            prepared.append({
                "identifier": member["key"],
                "pkg_name": fm["name"],
                "name": display,
                "description": description,
                "call_name": call_name,
                "frontmatter": fm,
                "rows": rows,
                "content_hash": _compute_content_hash(rows),
                "has_scripts": has_scripts,
                "has_assets": has_assets,
                "warnings": warnings,
            })

        # --- plugin grouping + call_name identity planning (both preview & commit) --
        # ``call_name`` is the unique runtime identity of a skill (ADR-11): import
        # dedup keys off it, not the package name. A member whose call_name already
        # exists is planned as an *overwrite* (the existing skills row is reused, so
        # Agent/workspace bindings survive; cross-plugin owners migrate). The rare
        # package-name clash with a *different* call_name stays a skip (it would hit
        # the tenant package-name unique constraint and cannot overwrite another
        # skill). Planning runs before the dry_run fork and before create_plugin, so
        # the preview already shows every overwrite/skip and a plugin row is only
        # created when at least one member is importable (no zero-member shells).
        repo = SkillPackageRepository(db) if db is not None else None
        resolved_plugin_name: Optional[str] = None
        if is_plugin:
            resolved_plugin_name = (plugin_name or "").strip() or SkillPackageService._derive_plugin_name(source_ref)
            if repo is not None and repo.get_plugin_by_name(tenant_id, resolved_plugin_name):
                raise BusinessException(
                    f"插件 {resolved_plugin_name} 已存在；插件为整体导入，不支持重复导入"
                    "（如需重装请先删除原插件）",
                    BizCode.DUPLICATE_NAME,
                )

        if repo is not None:
            SkillPackageService._plan_import_members(
                db, repo, tenant_id, prepared,
                is_plugin=is_plugin, resolved_plugin_name=resolved_plugin_name,
            )

        importable = [item for item in prepared if not item.get("skipped")]

        # --- dry-run preview: no writes, no quota --------------------------------
        if dry_run:
            return SkillPackageService._preview_response(
                kind=kind, is_plugin=is_plugin, prepared=prepared,
                plugin_name=plugin_name, plugin_description=plugin_description,
                source_ref=source_ref,
            )

        # --- commit -------------------------------------------------------------
        # Never create the plugin row when every member was skipped, otherwise the
        # bundle would persist as an unreachable zero-member shell.
        plugin_id: Optional[uuid.UUID] = None
        if is_plugin and importable:
            plugin = repo.create_plugin(
                tenant_id=tenant_id,
                name=resolved_plugin_name,
                description=plugin_description,
                source_type="imported",
                source_ref=source_ref,
            )
            plugin_id = plugin.id
        results: list[dict] = []
        for item in prepared:
            if item.get("skipped"):
                results.append(SkillPackageService._skipped_member_result(item))
                continue
            results.append(
                SkillPackageService._commit_member(
                    db, repo, tenant_id=tenant_id, item=item, plugin_id=plugin_id,
                    source_ref=source_ref, overwrite=overwrite,
                    conflict_strategy=conflict_strategy,
                )
            )

        db.commit()

        for item, res in zip(prepared, results):
            if res.get("skill_id"):
                db.refresh(res["_skill"])
                del res["_skill"]

        first = results[0]
        response = {
            "dry_run": False,
            "kind": kind,
            "plugin_id": str(plugin_id) if plugin_id else None,
            "name": (resolved_plugin_name or first["name"]) if is_plugin else first["name"],
            "description": (plugin_description or first["description"]) if is_plugin else first["description"],
            "source": source_ref,
            "package_id": None if is_plugin else first["package_id"],
            "skill_id": None if is_plugin else first["skill_id"],
            "call_name": None if is_plugin else first["call_name"],
            "content_hash": None if is_plugin else first["content_hash"],
            "files_count": None if is_plugin else first["files_count"],
            "idempotent": all(r["idempotent"] for r in results),
            "warnings": [w for item in prepared for w in item["warnings"]],
            "members": [
                {
                    "identifier": item["identifier"],
                    "name": res["name"],
                    "description": item["description"],
                    "call_name": res["call_name"],
                    "package_id": res["package_id"],
                    "skill_id": res["skill_id"],
                    "files_count": res["files_count"],
                    "has_scripts": item["has_scripts"],
                    "idempotent": res["idempotent"],
                    "conflict_resolved": res["conflict_resolved"],
                    "will_overwrite": res.get("will_overwrite", False),
                    "overwrite_target": item.get("overwrite_target_name"),
                    "cross_plugin": bool(item.get("cross_plugin")),
                    "skipped": res.get("skipped", False),
                    "skip_reason": res.get("skip_reason"),
                }
                for item, res in zip(prepared, results)
            ],
        }
        return response

    @staticmethod
    def _derive_plugin_name(source_ref: Optional[str]) -> str:
        """Default plugin name from its source: ``owner/repo`` name for URLs, file stem for zips."""

        raw = (source_ref or "").strip()
        if not raw:
            return "plugin"
        if raw.startswith("http://") or raw.startswith("https://"):
            parts = [p for p in urlparse(raw).path.split("/") if p]
            if len(parts) >= 2:
                return parts[1][:255]
            if parts:
                return parts[0][:255]
            return urlparse(raw).hostname or "plugin"
        stem = raw.rsplit("/", 1)[-1]
        return (stem[:-4] if stem.lower().endswith(".zip") else stem)[:255] or "plugin"

    @staticmethod
    def _preview_response(
        *,
        kind: str,
        is_plugin: bool,
        prepared: list[dict],
        plugin_name: Optional[str],
        plugin_description: Optional[str],
        source_ref: Optional[str],
    ) -> dict:
        """Preview shape — identical to the committed response but with null ids."""

        first = prepared[0]
        return {
            "dry_run": True,
            "kind": kind,
            "plugin_id": None,
            "name": (plugin_name or SkillPackageService._derive_plugin_name(source_ref)) if is_plugin else first["name"],
            "description": (plugin_description or first["description"]) if is_plugin else first["description"],
            "source": source_ref,
            "package_id": None,
            "skill_id": None,
            "call_name": None if is_plugin else first["call_name"],
            "content_hash": None if is_plugin else first["content_hash"],
            "files_count": None if is_plugin else len(first["rows"]),
            "idempotent": False,
            "warnings": [w for item in prepared for w in item["warnings"]],
            "members": [
                {
                    "identifier": item["identifier"],
                    "name": item["name"],
                    "description": item["description"],
                    "call_name": item["call_name"],
                    "package_id": None,
                    "skill_id": None,
                    "files": [r["rel_path"] for r in item["rows"]],
                    # Preview-only: full file metadata incl. text content, so the
                    # wizard can show what is about to be imported before anything is
                    # written. ``files`` stays the path array for compatibility; list
                    # responses never carry this field (kept lean on purpose).
                    "files_detail": [
                        {
                            "rel_path": r["rel_path"],
                            "content": r["content"],
                            "size": r["size"],
                            "is_binary": r["is_binary"],
                        }
                        for r in item["rows"]
                    ],
                    "has_scripts": item["has_scripts"],
                    "idempotent": False,
                    "will_overwrite": bool(item.get("will_overwrite")),
                    "overwrite_target": item.get("overwrite_target_name"),
                    "cross_plugin": bool(item.get("cross_plugin")),
                    "conflict_resolved": (
                        "skipped" if item.get("skipped")
                        else ("overwrite" if item.get("will_overwrite") else None)
                    ),
                    "skipped": bool(item.get("skipped")),
                    "skip_reason": item.get("skip_reason"),
                }
                for item in prepared
            ],
        }

    @staticmethod
    def _find_active_skill_by_call_name(
        db: Session, tenant_id: uuid.UUID, call_name: str
    ) -> Optional[Skill]:
        """The tenant's active skill owning ``call_name`` (the import identity key)."""

        return (
            db.query(Skill)
            .filter(
                Skill.tenant_id == tenant_id,
                Skill.call_name == call_name,
                Skill.is_active.is_(True),
            )
            .first()
        )

    @staticmethod
    def _plan_import_members(
        db: Session,
        repo: SkillPackageRepository,
        tenant_id: uuid.UUID,
        prepared: list[dict],
        *,
        is_plugin: bool,
        resolved_plugin_name: Optional[str],
    ) -> list[dict]:
        """Plan each member against ``call_name`` (the unique skill identity).

        Decision per member (annotated on the item in place):

        - an **active** skill in the tenant already owns the ``call_name`` ->
          ``will_overwrite=True``. Commit reuses that exact ``skills`` row (id and
          Agent/workspace bindings are preserved), deletes its old package and
          attaches the freshly imported one. When the old skill belongs to another
          plugin it migrates into this import (``cross_plugin=True``). The preview
          carries a user-facing warning naming the overwritten skill;
        - no call_name owner, but the package name is taken by a *different* skill
          (the tenant ``(tenant_id, name)`` unique constraint) -> ``skipped``, since
          it cannot overwrite another skill and cannot share the package name;
        - otherwise a brand-new import.

        Two members of the same upload sharing a call_name / package name is an
        ambiguous bundle and rejected up front (BAD_REQUEST). When every member is
        skipped the caller creates no plugin/skill, so zero-member shells cannot be
        persisted. Returns the list of skipped items.
        """

        # Reject ambiguous duplicates within one upload before any DB lookup.
        dup_errors: list[str] = []
        seen_call: dict[str, int] = {}
        seen_pkg: dict[str, int] = {}
        for index, item in enumerate(prepared):
            prior_call = seen_call.get(item["call_name"])
            if prior_call is not None:
                dup_errors.append(
                    f"调用名称 '{item['call_name']}' 在本次导入中重复（第 {prior_call}、{index + 1} 个成员）"
                )
            else:
                seen_call[item["call_name"]] = index + 1
            prior_pkg = seen_pkg.get(item["pkg_name"])
            if prior_pkg is not None:
                dup_errors.append(
                    f"技能名 '{item['pkg_name']}' 在本次导入中重复（第 {prior_pkg}、{index + 1} 个成员）"
                )
            else:
                seen_pkg[item["pkg_name"]] = index + 1
        if dup_errors:
            raise BusinessException(
                "导入包中存在重复成员，无法判定覆盖关系：" + "；".join(dup_errors),
                BizCode.BAD_REQUEST,
            )

        skipped: list[dict] = []
        for item in prepared:
            item.setdefault("will_overwrite", False)

            owner = SkillPackageService._find_active_skill_by_call_name(
                db, tenant_id, item["call_name"]
            )
            if owner is not None:
                owner_pkg = (
                    repo.get_by_id(owner.package_id, tenant_id)
                    if owner.package_id
                    else None
                )
                owner_plugin_id = owner_pkg.plugin_id if owner_pkg else None
                cross_plugin = owner_plugin_id is not None and is_plugin
                item["will_overwrite"] = True
                item["overwrite_skill_id"] = str(owner.id)
                item["overwrite_target_name"] = owner.name
                item["cross_plugin"] = cross_plugin

                if cross_plugin:
                    owner_plugin = repo.get_plugin_by_id(owner_plugin_id, tenant_id)
                    plugin_label = owner_plugin.name if owner_plugin else str(owner_plugin_id)
                    note = (
                        f"该技能原属于插件「{plugin_label}」，将从原插件迁移到本次导入的插件"
                        f"「{resolved_plugin_name}」"
                    )
                elif owner_plugin_id is not None and not is_plugin:
                    note = "该技能原为某插件的成员，覆盖后将变为独立技能（从原插件移出）"
                elif owner_pkg is None:
                    note = "该技能当前为 legacy 技能，覆盖后将转换为标准技能格式"
                else:
                    note = "将用新内容就地更新该技能（保留技能 ID 与已绑定关系）"
                item["warnings"].append(
                    f"调用名称 '{item['call_name']}' 已存在（技能「{owner.name}」），"
                    f"导入后将覆盖：{note}。"
                )
                continue

            clash_pkg = repo.get_by_name(tenant_id, item["pkg_name"])
            if clash_pkg is not None:
                owner_kind = "其他插件" if clash_pkg.plugin_id is not None else "独立技能"
                reason = (
                    f"技能名 '{item['pkg_name']}' 已被{owner_kind}占用，但其调用名称不同，"
                    "无法覆盖（将跳过）；如需并入请先调整调用名称或删除原技能"
                )
                item["skipped"] = True
                item["skip_reason"] = reason
                item["warnings"].append(f"已跳过成员 {item['name']}：{reason}")
                skipped.append(item)

        if prepared and len(skipped) == len(prepared):
            prepared[0]["warnings"].append(
                "所有成员均因名称被占用而跳过，未创建任何技能或插件；"
                "请先删除占用名称的技能/插件，或统一调用名称后再导入。"
            )

        return skipped

    @staticmethod
    def _skipped_member_result(item: dict) -> dict:
        """Result shape for a member that was skipped (no package/skill written)."""

        return {
            "name": item["name"],
            "call_name": item["call_name"],
            "description": item["description"],
            "content_hash": item["content_hash"],
            "package_id": None,
            "skill_id": None,
            "files_count": len(item["rows"]),
            "idempotent": False,
            "conflict_resolved": "skipped",
            "skipped": True,
            "will_overwrite": False,
            "skip_reason": item.get("skip_reason"),
            "_skill": None,
        }

    @staticmethod
    def _commit_member(
        db: Session,
        repo: SkillPackageRepository,
        *,
        tenant_id: uuid.UUID,
        item: dict,
        plugin_id: Optional[uuid.UUID],
        source_ref: Optional[str],
        overwrite: bool,
        conflict_strategy: str,
    ) -> dict:
        """Persist one member: package + files, anchored on the existing ``skills`` row.

        Identity is ``call_name``. When ``item['will_overwrite']`` is set (an active
        skill already owns the call_name — planned by ``_plan_import_members``) the
        same ``skills`` row is reused (id and Agent/workspace bindings preserved):
        its old package is purged and the freshly imported package is attached,
        migrating it across plugins or up-converting it from legacy as needed.
        Otherwise a brand-new package + skill row are created (the legacy
        same-name rename rule is preserved for that path).
        """

        name = item["pkg_name"]
        content_hash = item["content_hash"]

        skill_row: Optional[Skill] = None
        conflict_resolved: Optional[str] = None
        legacy_fm: Optional[dict] = None

        if item.get("will_overwrite"):
            # --- overwrite: reuse the active skill that owns the call_name --------
            skill_row = db.query(Skill).filter(
                Skill.id == uuid.UUID(item["overwrite_skill_id"]),
                Skill.tenant_id == tenant_id,
            ).first()
            # Defense in depth: preview => commit must see the same owner.
            if (
                skill_row is None
                or not skill_row.is_active
                or skill_row.call_name != item["call_name"]
            ):
                raise BusinessException(
                    f"调用名称 '{item['call_name']}' 的占用状态在导入期间发生变化，"
                    "请重新预览后再导入",
                    BizCode.DUPLICATE_NAME,
                )

            old_pkg = (
                repo.get_by_id(skill_row.package_id, tenant_id)
                if skill_row.package_id
                else None
            )
            # Idempotent only when it stays in the same group (standalone re-import):
            # a cross-group identical member still has to be rebuilt so it migrates.
            if (
                old_pkg is not None
                and old_pkg.plugin_id == plugin_id
                and old_pkg.name == name
                and old_pkg.content_hash == content_hash
                and skill_row.skill_format == "standard"
            ):
                # Identical re-import: keep the pack bytes, but still apply the
                # presentation-only overrides (rename / re-describe), never touching
                # SKILL.md or the runtime call_name identity.
                old_pkg.display_name = item["name"]
                old_pkg.description = item["description"]
                skill_row.description = item["description"]
                db.flush()
                return {
                    "name": item["name"], "call_name": skill_row.call_name,
                    "description": item["description"], "content_hash": content_hash,
                    "package_id": str(old_pkg.id), "skill_id": str(skill_row.id),
                    "files_count": len(item["rows"]),
                    "idempotent": True, "conflict_resolved": None,
                    "will_overwrite": True, "_skill": skill_row,
                }

            # Preserve the legacy archive before converting the row to standard.
            if skill_row.skill_format == "legacy":
                legacy_fm = dict(item["frontmatter"])
                legacy_fm["_legacy"] = {
                    "prompt": skill_row.prompt,
                    "keywords": (skill_row.config or {}).get("keywords"),
                    "tools": skill_row.tools,
                    "config": skill_row.config,
                }

            # Purge the old package (object storage first; repo detaches the FK).
            if old_pkg is not None:
                SkillPackageService._delete_storage_objects(db, old_pkg.id)
                repo.delete(old_pkg.id, tenant_id)

            skill_row.skill_format = "standard"
            skill_row.prompt = None
            skill_row.tools = []
            skill_row.config = {}
            conflict_resolved = "overwrite"
        else:
            # --- fresh path: an occupied package name here was meant to be skipped
            # by the planner (different call_name). The one safe exception is an
            # identical, same-group re-import (direct call / idempotent replay).
            existing = repo.get_by_name(tenant_id, name)
            if existing is not None:
                if existing.plugin_id == plugin_id and existing.content_hash == content_hash:
                    skill = db.query(Skill).filter(
                        Skill.package_id == existing.id, Skill.tenant_id == tenant_id
                    ).first()
                    # Identical replay: still apply presentation-only overrides so a
                    # re-import that only renames/re-describes lands without touching
                    # pack bytes, SKILL.md or the call_name identity.
                    existing.display_name = item["name"]
                    existing.description = item["description"]
                    if skill is not None:
                        skill.description = item["description"]
                    db.flush()
                    return {
                        "name": item["name"],
                        "call_name": (skill.call_name if skill else item["call_name"]),
                        "description": item["description"], "content_hash": content_hash,
                        "package_id": str(existing.id),
                        "skill_id": str(skill.id) if skill else None,
                        "files_count": len(item["rows"]),
                        "idempotent": True, "conflict_resolved": None,
                        "will_overwrite": False, "_skill": skill,
                    }
                raise BusinessException(
                    f"技能 {name} 的技能包已被其他技能/插件占用且调用名称不同（预览中应标记为跳过）；"
                    "如状态变化请重新预览后再导入",
                    BizCode.DUPLICATE_NAME,
                )

        pkg = repo.create(
            tenant_id=tenant_id, name=name, display_name=item["name"],
            description=item["description"], content_hash=content_hash,
            plugin_id=plugin_id,
            source_type="imported", source_ref=source_ref,
            frontmatter_raw=item["frontmatter"],
            license=item["frontmatter"].get("license"),
            compatibility=item["frontmatter"].get("compatibility"),
            has_scripts=item["has_scripts"], has_assets=item["has_assets"],
        )
        if legacy_fm is not None:
            pkg.frontmatter_raw = legacy_fm
        SkillPackageService._persist_files(item["rows"], tenant_id, pkg.id)
        repo.add_files(pkg.id, item["rows"])

        if skill_row is not None:
            # Overwrite path: attach the imported package to the preserved skill row.
            skill_row.package_id = pkg.id
            skill_row.description = item["description"]
            skill_row.is_active = True
            pkg.display_name = item["name"]
            db.flush()
            skill = skill_row
        else:
            # Fresh path: locate/create the skill row (legacy rename rule preserved).
            skill = db.query(Skill).filter(
                Skill.tenant_id == tenant_id, Skill.name == name, Skill.skill_format == "standard"
            ).first()
            if not skill:
                legacy = db.query(Skill).filter(
                    Skill.tenant_id == tenant_id, Skill.name == name, Skill.skill_format == "legacy"
                ).first()
                if legacy:
                    if conflict_strategy == "overwrite":
                        fm = dict(item["frontmatter"])
                        fm["_legacy"] = {
                            "prompt": legacy.prompt, "keywords": (legacy.config or {}).get("keywords"),
                            "tools": legacy.tools, "config": legacy.config,
                        }
                        pkg.frontmatter_raw = fm
                        db.flush()
                        legacy.skill_format = "standard"
                        legacy.package_id = pkg.id
                        legacy.prompt = None
                        legacy.tools = []
                        legacy.config = {}
                        skill = legacy
                        conflict_resolved = "overwrite"
                    else:
                        display = f"{name}-imported"
                        skill = Skill(
                            name=display, description=item["description"], tenant_id=tenant_id,
                            tools=[], config={}, prompt=None,
                            is_active=True, is_public=False,
                            skill_format="standard", package_id=pkg.id,
                        )
                        # ``display`` only disambiguates the unique skill.name column
                        # against the legacy row; the UI-facing name keeps the import /
                        # frontmatter name so a meta rename survives this branch.
                        pkg.display_name = item["name"]
                        db.add(skill)
                        db.flush()
                        conflict_resolved = "rename"
                        item["warnings"].append(f"同名 legacy 技能存在，已按改名导入为 '{display}'")
                else:
                    skill = Skill(
                        name=name, description=item["description"], tenant_id=tenant_id,
                        tools=[], config={}, prompt=None,
                        is_active=True, is_public=False,
                        skill_format="standard", package_id=pkg.id,
                    )
                    db.add(skill)
                    db.flush()
            else:
                # A standard row with this name already exists — typically a member that a
                # previous import created and a plugin delete soft-deleted (its
                # ``package_id`` was detached then). Re-attach and reactivate it.
                skill.package_id = pkg.id
                skill.description = item["description"]
                skill.is_active = True
                db.flush()

        # call_name is the runtime identifier and must be unique per tenant (ADR-11).
        SkillPackageService._assert_call_name_free(db, tenant_id, item["call_name"], skill.id)
        skill.call_name = item["call_name"]
        db.flush()

        return {
            "name": skill.name, "call_name": skill.call_name,
            "description": item["description"],
            "content_hash": content_hash,
            "package_id": str(pkg.id), "skill_id": str(skill.id),
            "files_count": len(item["rows"]),
            "idempotent": False, "conflict_resolved": conflict_resolved,
            "will_overwrite": item.get("will_overwrite", False),
            "_skill": skill,
        }

    @staticmethod
    def _assert_call_name_free(
        db: Session, tenant_id: uuid.UUID, call_name: str, current_skill_id: uuid.UUID
    ) -> None:
        """Raise when another skill of the tenant already owns ``call_name``."""

        clash = db.query(Skill).filter(
            Skill.tenant_id == tenant_id,
            Skill.call_name == call_name,
            Skill.id != current_skill_id,
            Skill.is_active.is_(True),
        ).first()
        if clash:
            raise BusinessException(
                f"技能调用名称 '{call_name}' 已被其他技能使用，请换一个", BizCode.DUPLICATE_NAME
            )

    # ==================== export ====================

    @staticmethod
    def export_zip(db: Session, tenant_id: uuid.UUID, entry_id: uuid.UUID) -> tuple[bytes, str]:
        """Export a skill OR a whole plugin as a standard pack zip.

        Returns ``(zip_bytes, download_filename)``. ``entry_id`` accepts either a
        ``skills`` row id (one member) or a ``skill_plugins`` row id (every member),
        mirroring :meth:`delete_entry`.

        ``standard`` rows rebuild the pack from ``skill_files`` byte-faithfully (object
        storage is read back for binary/oversized entries, so round-trips preserve the
        original ``content_hash``). ``legacy`` rows hit the same endpoint and are
        converted on the fly without writing back (design doc §10.2). A plugin is rebuilt
        as one multi-member zip (``<member>/SKILL.md`` + its files) so the existing
        plugin importer re-imports it as a unit.
        """

        repo = SkillPackageRepository(db)
        skill = db.query(Skill).filter(
            Skill.id == entry_id, Skill.tenant_id == tenant_id
        ).first()
        if not skill:
            # May be a plugin id: fall back to plugin-level aggregate export.
            plugin = repo.get_plugin_by_id(entry_id, tenant_id)
            if plugin:
                return SkillPackageService._export_plugin_zip(db, repo, tenant_id, plugin)
            raise BusinessException("技能不存在或无权限", BizCode.NOT_FOUND)

        # A soft-deleted skill is gone for good as far as content is concerned (its
        # pack is hard-deleted on delete), so it must never export — not even a
        # legacy-conversion placeholder. Detail/list still surface the row for bound-
        # id echo, but export is a content artifact and has nothing to return.
        if not skill.is_active:
            raise BusinessException("技能不存在或已删除", BizCode.NOT_FOUND)

        member = SkillPackageService._skill_pack_entries(repo, tenant_id, skill)
        if member is None:
            raise BusinessException("技能包内容为空，无法导出", BizCode.NOT_FOUND)
        entries, download_stem = member
        return SkillPackageService._write_zip(entries), f"{download_stem}.zip"

    @staticmethod
    def _skill_pack_entries(
        repo: SkillPackageRepository,
        tenant_id: uuid.UUID,
        skill: Skill,
    ) -> tuple[list[tuple[str, bytes]], str] | None:
        """One member's pack as pack-relative ``(rel_path, bytes)`` entries + download stem.

        Returns ``None`` for a standard row whose package/files are missing (nothing to
        export); a legacy row is converted on the fly to a single ``SKILL.md`` member.
        """

        if (skill.skill_format or "legacy") == "standard" and skill.package_id:
            pkg = repo.get_by_id(skill.package_id, tenant_id)
            if not pkg:
                return None
            files = repo.get_files(pkg.id)
            if not files:
                return None
            return SkillPackageService._pack_file_entries(files), pkg.name

        # legacy -> standard on-the-fly conversion (never written back)
        _frontmatter, files = SkillPackageService.legacy_to_package_files(skill)
        return (
            [(rel, text.encode("utf-8")) for rel, text in files.items()],
            slugify_name(skill.name, str(skill.id)),
        )

    @staticmethod
    def _pack_file_entries(files) -> list[tuple[str, bytes]]:
        """Resolve skill_files rows to bytes (object storage read back for big/binary)."""

        entries: list[tuple[str, bytes]] = []
        for f in files:
            if f.storage_ref:
                payload = SkillPackageService._read_storage_object(f.storage_ref)
            else:
                payload = (f.content or "").encode("utf-8")
            entries.append((f.rel_path, payload))
        return entries

    @staticmethod
    def _write_zip(entries: list[tuple[str, bytes]]) -> bytes:
        """Write pack-relative entries to a deterministic zip (UTF-8 filename flag set)."""

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path, payload in entries:
                info = zipfile.ZipInfo(rel_path, date_time=(2026, 1, 1, 0, 0, 0))
                info.flag_bits |= 0x800
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, payload)
        return buffer.getvalue()

    @staticmethod
    def _export_plugin_zip(
        db: Session,
        repo: SkillPackageRepository,
        tenant_id: uuid.UUID,
        plugin,
    ) -> tuple[bytes, str]:
        """Aggregate every member of a plugin into one multi-member zip.

        Each member is placed under its own ``<slug>/`` directory (``<slug>/SKILL.md``
        plus its files) so the importer's :meth:`_split_members` re-imports the bundle as
        one plugin, byte-faithfully for standard members and via on-the-fly conversion
        for legacy rows. Directory names are de-duplicated to keep members isolated.
        """

        aggregate: list[tuple[str, bytes]] = []
        used_dirs: set[str] = set()
        for pkg in repo.list_by_plugin(plugin.id, tenant_id):
            skill = db.query(Skill).filter(
                Skill.package_id == pkg.id, Skill.tenant_id == tenant_id
            ).first()
            if not skill:
                continue
            member = SkillPackageService._skill_pack_entries(repo, tenant_id, skill)
            if member is None:
                continue
            entries, _stem = member
            base = slugify_name(skill.name, str(skill.id))
            dir_name, n = base, 2
            while dir_name in used_dirs:
                dir_name = f"{base}-{n}"
                n += 1
            used_dirs.add(dir_name)
            for rel_path, payload in entries:
                aggregate.append((f"{dir_name}/{rel_path}", payload))

        if not aggregate:
            raise BusinessException("插件内容为空，无法导出", BizCode.NOT_FOUND)
        return SkillPackageService._write_zip(aggregate), f"{plugin.name}.zip"

    # ==================== legacy conversion ====================

    @staticmethod
    def legacy_to_package_files(skill: Skill) -> tuple[dict, dict[str, str]]:
        """Deterministic legacy row -> (frontmatter dict, {rel_path: text}) mapping.

        Follows design doc §10.2. The original row is never mutated, and the result is
        only used for cross-product export — it never creates a standard skill.
        - ``name``            -> slugified frontmatter name (Chinese/spaces -> hyphens, <=64;
          empty slug falls back to ``skill-{id[:8]}``); the original name stays in the body title
        - ``description``     -> frontmatter description; empty becomes a "请补充描述" placeholder
        - ``config.keywords`` -> appended to the description as a bracketed trigger note
        - ``tools``           -> a body note (the standard protocol has no tool binding)
        - ``_legacy``         -> full legacy archive; ``_build_skill_md`` strips it from output
        """

        fallback = str(skill.id)
        fm_name = slugify_name(skill.name, fallback)
        config = skill.config or {}
        keywords = config.get("keywords") or []

        description = (skill.description or "").strip()
        if not description:
            description = f"技能 {skill.name}：请补充描述"
        if keywords:
            kw_text = "、".join(str(k) for k in keywords)
            description = f"{description}（关键词触发场景：{kw_text}）"
        description = description[:DESCRIPTION_MAX]

        legacy_tools = skill.tools or []
        tool_note = ""
        if legacy_tools:
            tool_note = (
                f"> 本技能在记忆熊内绑定了 {len(legacy_tools)} 个平台工具，"
                f"导出到第三方产品时需自行配置对应工具能力。\n\n"
            )

        prompt = (skill.prompt or "").strip()
        if prompt:
            body = f"# {skill.name}\n\n{tool_note}{prompt}"
        else:
            body = (
                f"# {skill.name}\n\n（此技能未配置提示词正文。请在记忆熊技能编辑页补充，"
                "或编辑导出后的 SKILL.md。）"
            )

        frontmatter: dict = {
            "name": fm_name,
            "description": description,
            "metadata": {"generated_by": "memorybear", "source_skill_id": fallback},
        }
        if skill.is_public:
            frontmatter["metadata"]["public"] = True
        frontmatter["_legacy"] = {
            "tools": legacy_tools,
            "config": {k: v for k, v in config.items() if k != "keywords"},
            "keywords": keywords,
            "prompt": skill.prompt,
        }
        return frontmatter, {"SKILL.md": body}

    @staticmethod
    def legacy_pack_bytes(skill: Skill) -> bytes:
        """Render a converted legacy pack as zip bytes ready to stream (UTF-8 filename flag set)."""

        frontmatter, files = SkillPackageService.legacy_to_package_files(skill)
        skill_md = _build_skill_md(frontmatter, files["SKILL.md"])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path, text in files.items():
                payload = skill_md if rel_path == "SKILL.md" else text
                info = zipfile.ZipInfo(rel_path, date_time=(2026, 1, 1, 0, 0, 0))
                info.flag_bits |= 0x800
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, payload.encode("utf-8"))
        return buffer.getvalue()

    @staticmethod
    def read_skill_files(db: Session, tenant_id: uuid.UUID, package_id: uuid.UUID) -> list[dict]:
        """Read-only file listing for detail / file preview (no content payload for binaries)."""

        repo = SkillPackageRepository(db)
        pkg = repo.get_by_id(package_id, tenant_id)
        if not pkg:
            raise BusinessException("技能包不存在", BizCode.NOT_FOUND)
        return [
            {
                "path": f.rel_path,
                "size": f.size,
                "is_binary": f.is_binary,
                "content": f.content,
            }
            for f in repo.get_files(package_id)
        ]

    # ==================== list / detail ====================

    @staticmethod
    def list_entries(
        db: Session,
        tenant_id: uuid.UUID,
        *,
        search: Optional[str] = None,
        ids: Optional[str] = None,
        is_active: Optional[bool] = None,
        is_public: Optional[bool] = None,
        kind: str = "all",
        page: int = 1,
        pagesize: int = 10,
    ) -> tuple[list[dict], int]:
        """List cards: plugin rows + standalone skill rows (design doc §11.1).

        Plugin members never become cards of their own — they are reached by
        drilling into the plugin detail (``members``), though search still hits
        them. ``kind`` filters the granularity: ``all`` | ``plugin`` | ``skill``.

        ``ids`` is the comma-separated card-id filter: every value is matched
        **exactly** against the ids a card exposes (skill row id / package id /
        plugin id) and the values are OR-ed together, then AND-ed with the other
        filters. A plugin card is returned when its own id or any member id
        matches, mirroring how ``search`` treats member hits.

        Cards are assembled in Python and paginated together, which keeps the
        plugin/standalone merge readable at this scale (skill quota is counted in
        units, not thousands).
        """

        if kind not in ("all", "plugin", "skill"):
            raise BusinessException("kind 只接受 all | plugin | skill", BizCode.BAD_REQUEST)

        id_filter = SkillPackageService._parse_ids_filter(ids)

        repo = SkillPackageRepository(db)
        cards: list[dict] = []

        if kind in ("all", "skill"):
            cards.extend(
                SkillPackageService._standalone_skill_cards(
                    db, tenant_id, search=search, ids=id_filter,
                    is_active=is_active, is_public=is_public
                )
            )

        if kind in ("all", "plugin"):
            like = f"%{search}%" if search else None
            for plugin in repo.list_plugins(tenant_id):
                members = repo.list_by_plugin(plugin.id, tenant_id)
                member_rows = []
                for pkg in members:
                    row = db.query(Skill).filter(
                        Skill.package_id == pkg.id, Skill.tenant_id == tenant_id
                    ).first()
                    if row:
                        member_rows.append((row, pkg))

                active_members = [(s, p) for s, p in member_rows if s.is_active]
                if is_active is not None and bool(active_members) != is_active:
                    continue
                if like is not None and not SkillPackageService._plugin_matches(
                    plugin, member_rows, search
                ):
                    continue
                if id_filter is not None and not SkillPackageService._plugin_hits_ids(
                    plugin, member_rows, id_filter
                ):
                    continue

                cards.append(
                    SkillPackageService._plugin_card(plugin, active_members)
                )

        cards.sort(key=lambda c: c.get("created_at") or datetime.min, reverse=True)
        total = len(cards)
        start = (page - 1) * pagesize
        return cards[start:start + pagesize], total

    @staticmethod
    def _parse_ids_filter(ids: Optional[str]) -> Optional[set[uuid.UUID]]:
        """Parse the comma-separated ``ids`` filter into a set of UUIDs.

        Blank segments are ignored (so a trailing comma is harmless); a segment
        that is not a UUID is a request error (``1000`` BAD_REQUEST, HTTP 400),
        matching how an invalid ``kind`` is reported — silently returning an empty
        page would hide the typo from the caller.
        """

        if ids is None:
            return None
        values = [part.strip() for part in ids.split(",") if part.strip()]
        if not values:
            return None

        parsed: set[uuid.UUID] = set()
        for value in values:
            try:
                parsed.add(uuid.UUID(value))
            except (ValueError, AttributeError, TypeError) as exc:
                raise BusinessException(
                    f"ids 含非法 UUID：{value}", BizCode.BAD_REQUEST
                ) from exc
        return parsed

    @staticmethod
    def _standalone_skill_cards(
        db: Session,
        tenant_id: uuid.UUID,
        *,
        search: Optional[str],
        ids: Optional[set[uuid.UUID]] = None,
        is_active: Optional[bool],
        is_public: Optional[bool],
    ) -> list[dict]:
        """Skill cards whose package is absent (legacy) or ungrouped (``plugin_id`` NULL)."""

        query = (
            db.query(Skill, SkillPackage)
            .outerjoin(SkillPackage, Skill.package_id == SkillPackage.id)
            .filter(or_(Skill.tenant_id == tenant_id, Skill.is_public.is_(True)))
            .filter(or_(SkillPackage.id.is_(None), SkillPackage.plugin_id.is_(None)))
        )
        if search:
            like = f"%{search}%"
            query = query.filter(
                or_(
                    Skill.name.ilike(like),
                    Skill.call_name.ilike(like),
                    # 行描述：standard 由导入时同步、legacy 已有，两者都应可搜（
                    # 先前只搜了包描述，legacy 行描述是搜索盲区）
                    Skill.description.ilike(like),
                    SkillPackage.description.ilike(like),
                    SkillPackage.name.ilike(like),
                    # 用户在导入时改的展示名也要可搜到（不影响身份列）
                    SkillPackage.display_name.ilike(like),
                    # id 也参与命中：卡片上可见的 skill id / package_id 按 UUID 文本比对，
                    # 前端拿到 id 后可直接回填搜索框。
                    cast(Skill.id, String).ilike(like),
                    cast(SkillPackage.id, String).ilike(like),
                )
            )
        if ids:
            # Exact id match on the two ids a standalone card exposes; ``in_`` on a
            # NULL ``skill_packages`` row simply does not match, so legacy rows are
            # still reachable through ``skills.id``.
            query = query.filter(or_(Skill.id.in_(ids), SkillPackage.id.in_(ids)))
        if is_active is not None:
            query = query.filter(Skill.is_active.is_(is_active))
        if is_public is not None:
            query = query.filter(Skill.is_public.is_(is_public))

        return [SkillPackageService._skill_card(s, p, with_members=True) for s, p in query.all()]

    @staticmethod
    def _plugin_matches(plugin, member_rows: list, search: str) -> bool:
        """A plugin matches when its own fields, its id, or any member matches.

        Plugin cards are assembled in Python (no SQL row to filter on), so the id
        dimension is a text containment test as well — plugin id, member skill id
        and member package id, mirroring the standalone path's ``cast`` ilike.
        """

        term = (search or "").lower()
        if term in (plugin.name or "").lower() or term in (plugin.description or "").lower():
            return True
        if term in str(plugin.id).lower():
            return True
        for skill, pkg in member_rows:
            haystack = " ".join(
                filter(None, [skill.name, skill.call_name, pkg.description, pkg.name,
                              pkg.display_name,
                              str(skill.id), str(pkg.id)])
            ).lower()
            if term in haystack:
                return True
        return False

    @staticmethod
    def _plugin_hits_ids(plugin, member_rows: list, ids: set[uuid.UUID]) -> bool:
        """True when the plugin card or any of its members owns one of ``ids``.

        A member hit yields the *plugin* card, matching the search semantics: a
        member id never becomes a card of its own.
        """

        if plugin.id in ids:
            return True
        return any(skill.id in ids or pkg.id in ids for skill, pkg in member_rows)

    @staticmethod
    def _display_name(skill: Skill, pkg: SkillPackage | None) -> str:
        """UI-facing name: the import-time override (``pkg.display_name``) wins.

        ``skill.name`` / ``pkg.name`` stay the original frontmatter name (used for
        identity, dedupe and the model skill index), so renaming a skill at import is
        presentation-only and never rewrites SKILL.md. Legacy rows without a pack fall
        back to the row name.
        """

        if pkg is not None and pkg.display_name:
            return pkg.display_name
        return skill.name

    @staticmethod
    def _skill_card(
        skill: Skill, pkg: Optional[SkillPackage], *, with_members: bool = False
    ) -> dict:
        """One standalone skill card (row fields + standard-pack face).

        ``with_members`` fills ``members`` for list cards: a standalone skill is a
        one-member collection, so the front-end can render ``members`` uniformly
        without branching on ``kind``. The entry is the skill itself, using the same
        member shape as a plugin card (``package_id`` / ``has_scripts`` stay null for
        legacy rows that own no pack). The detail path leaves it off — a detail
        response either inlines ``files`` (standard skill) or lists real members
        (plugin), never a self-referencing single member.
        """

        display_name = SkillPackageService._display_name(skill, pkg)
        card = {
            "id": skill.id,
            "tenant_id": skill.tenant_id,
            "name": display_name,
            "description": skill.description,
            "tools": skill.tools or [],
            "config": skill.config or {},
            "prompt": skill.prompt,
            "is_active": skill.is_active,
            "is_public": skill.is_public,
            "created_at": skill.created_at,
            "updated_at": skill.updated_at,
            "skill_format": skill.skill_format or "legacy",
            "package_id": skill.package_id,
            "kind": "skill",
            "plugin_id": pkg.plugin_id if pkg else None,
            "call_name": skill.call_name,
            "source": pkg.source_ref if pkg else None,
            "has_scripts": pkg.has_scripts if pkg else None,
        }
        if with_members:
            card["members"] = [
                {
                    "id": skill.id,
                    "name": display_name,
                    "description": skill.description,
                    "call_name": skill.call_name,
                    "package_id": pkg.id if pkg else None,
                    "has_scripts": pkg.has_scripts if pkg else None,
                    "is_active": skill.is_active,
                }
            ]
        return card

    @staticmethod
    def _plugin_card(plugin, member_rows: list) -> dict:
        """One plugin card; ``members`` carries the drill-down summary (§9.1)."""

        return {
            "id": plugin.id,
            "tenant_id": plugin.tenant_id,
            "name": plugin.name,
            "description": plugin.description,
            "tools": [],
            "config": {},
            "prompt": None,
            "is_active": bool(member_rows),
            "is_public": False,
            "created_at": plugin.created_at,
            "updated_at": plugin.updated_at,
            "skill_format": "standard",
            "package_id": None,
            "kind": "plugin",
            "plugin_id": plugin.id,
            "call_name": None,
            "source": plugin.source_ref,
            "has_scripts": any(pkg.has_scripts for _s, pkg in member_rows),
            "members": [
                {
                    "id": skill.id,
                    "name": SkillPackageService._display_name(skill, pkg),
                    "description": pkg.description,
                    "call_name": skill.call_name,
                    "package_id": pkg.id,
                    "has_scripts": pkg.has_scripts,
                    "is_active": skill.is_active,
                }
                for skill, pkg in member_rows
            ],
        }

    @staticmethod
    def get_entry_detail(
        db: Session, tenant_id: uuid.UUID, entry_id: uuid.UUID
    ) -> dict:
        """The single detail entry point (§9): skill row first, then plugin row.

        For a skill, standard rows inline ``skill_md`` / ``files`` / ``has_scripts``
        for the read-only detail page; legacy rows return their row fields only.
        For a plugin, ``members`` lists the member skills for drilling down.
        """

        repo = SkillPackageRepository(db)
        skill = db.query(Skill).filter(
            Skill.id == entry_id,
            or_(Skill.tenant_id == tenant_id, Skill.is_public.is_(True)),
        ).first()

        if skill:
            detail = SkillPackageService._skill_card(skill, None)
            detail["plugin_id"] = None
            if (skill.skill_format or "legacy") == "standard" and skill.package_id:
                pkg = repo.get_by_id(skill.package_id, tenant_id)
                if pkg:
                    detail = SkillPackageService._skill_card(skill, pkg)
                    files = repo.get_files(pkg.id)
                    detail["skill_md"] = SkillPackageService._file_text(pkg.id, "SKILL.md", files)
                    detail["files"] = [
                        {
                            "rel_path": f.rel_path,
                            "content": f.content,
                            "size": f.size,
                            "is_binary": f.is_binary,
                        }
                        for f in files
                    ]
                    detail["has_scripts"] = pkg.has_scripts
                    detail["members"] = None
                    detail["content_hash"] = pkg.content_hash
            return detail

        plugin = repo.get_plugin_by_id(entry_id, tenant_id)
        if not plugin:
            raise BusinessException("技能不存在", BizCode.NOT_FOUND)

        members = []
        for pkg in repo.list_by_plugin(plugin.id, tenant_id):
            row = db.query(Skill).filter(
                Skill.package_id == pkg.id, Skill.tenant_id == tenant_id
            ).first()
            if row:
                members.append((row, pkg))
        detail = SkillPackageService._plugin_card(plugin, members)
        # Detail view inlines every member's files (same shape as a standalone
        # skill's detail): each member pack is fetched once and reused for the
        # count, SKILL.md text and the file list. Text content comes from the
        # inline column (binary / over-size rows stored in object storage keep
        # content=null, mirroring the standalone-skill detail response).
        member_payloads: list[dict] = []
        for skill, pkg in members:
            pkg_files = repo.get_files(pkg.id)
            member_payloads.append(
                {
                    "id": skill.id,
                    "name": SkillPackageService._display_name(skill, pkg),
                    "description": pkg.description,
                    "call_name": skill.call_name,
                    "package_id": pkg.id,
                    "has_scripts": pkg.has_scripts,
                    "is_active": skill.is_active,
                    "files_count": len(pkg_files),
                    "skill_md": SkillPackageService._file_text(pkg.id, "SKILL.md", pkg_files),
                    "files": [
                        {
                            "rel_path": f.rel_path,
                            "content": f.content,
                            "size": f.size,
                            "is_binary": f.is_binary,
                        }
                        for f in pkg_files
                    ],
                }
            )
        detail["members"] = member_payloads
        return detail

    @staticmethod
    def _file_text(package_id: uuid.UUID, rel_path: str, files: list) -> Optional[str]:
        """Read one pack file as text for detail/read_skill (object storage read back when needed)."""

        for f in files:
            if f.rel_path != rel_path:
                continue
            if f.content is not None:
                return f.content
            if f.storage_ref:
                return SkillPackageService._read_storage_object(f.storage_ref).decode(
                    "utf-8", errors="replace"
                )
            return None
        return None

    # ==================== delete ====================

    @staticmethod
    def delete_entry(db: Session, tenant_id: uuid.UUID, entry_id: uuid.UUID) -> dict:
        """Delete a skill or a whole plugin (ADR-13 option A).

        Row semantics stay the historical soft delete (``is_active=False``) so
        existing callers and the skill quota keep working unchanged; the pack
        itself is hard-deleted (files cascade + best-effort object-storage
        cleanup) and ``skills.package_id`` is detached first, since the FK has no
        ``ON DELETE`` handler (§7.7).

        ``entry_id`` accepts either a ``skills`` row id (one member) or a
        ``skill_plugins`` row id (every member + the plugin row). Removing the last
        member of a plugin removes the plugin row as well.
        """

        repo = SkillPackageRepository(db)
        skill = db.query(Skill).filter(
            Skill.id == entry_id, Skill.tenant_id == tenant_id
        ).first()

        if skill:
            # The plugin linkage lives on the *package* row, so read it before the pack
            # is purged; ``skills.package_id`` is the member's package id, never the
            # plugin id. Reading it after the purge would yield None (the FK has no
            # ON DELETE handler, so the pack is detached first).
            plugin_id: Optional[uuid.UUID] = None
            if skill.package_id:
                pkg = repo.get_by_id(skill.package_id, tenant_id)
                plugin_id = pkg.plugin_id if pkg else None

            removed_package = SkillPackageService._purge_pack(db, repo, tenant_id, skill)
            skill.is_active = False
            skill.package_id = None
            db.flush()

            if plugin_id:
                # ``list_by_plugin`` filters on ``skill_packages.plugin_id``: pass the
                # plugin id (not the deleted package id) or every call comes back empty
                # and the plugin row would be left orphaned.
                remaining = repo.list_by_plugin(plugin_id, tenant_id)
                if not remaining:
                    repo.delete_plugin(plugin_id, tenant_id)
            db.commit()
            return {
                "kind": "skill",
                "skill_id": str(skill.id),
                "package_id": removed_package,
                "plugin_id": str(plugin_id) if plugin_id else None,
            }

        plugin = repo.get_plugin_by_id(entry_id, tenant_id)
        if not plugin:
            raise BusinessException("技能不存在", BizCode.NOT_FOUND)

        member_ids: list[str] = []
        for pkg in repo.list_by_plugin(plugin.id, tenant_id):
            row = db.query(Skill).filter(
                Skill.package_id == pkg.id, Skill.tenant_id == tenant_id
            ).first()
            if row:
                member_ids.append(str(row.id))
                row.is_active = False
                row.package_id = None
            SkillPackageService._delete_storage_objects(db, pkg.id)
            repo.delete(pkg.id, tenant_id)
        repo.delete_plugin(plugin.id, tenant_id)
        db.commit()
        return {
            "kind": "plugin",
            "plugin_id": str(plugin.id),
            "skill_ids": member_ids,
            "members_removed": len(member_ids),
        }

    @staticmethod
    def _purge_pack(db: Session, repo: SkillPackageRepository, tenant_id: uuid.UUID, skill: Skill) -> Optional[str]:
        """Hard-delete a skill's pack (object storage first) and detach ``skills.package_id``.

        Returns the removed package id, or None when the row had no pack.
        """

        if not skill.package_id:
            return None
        pkg = repo.get_by_id(skill.package_id, tenant_id)
        if not pkg:
            skill.package_id = None
            return None
        removed = str(pkg.id)
        SkillPackageService._delete_storage_objects(db, pkg.id)
        repo.delete(pkg.id, tenant_id)
        return removed