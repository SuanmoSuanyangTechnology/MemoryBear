"""Skill Package Service - Agent Skills 标准包的导入/导出/转换

职责：
- import_package：zip 解包 → frontmatter 解析校验 → 两表落库（幂等）
- export_package：按 skill_files 重建标准文件夹 zip（SKILL.md 原样字节级还原）
- legacy_to_package_files：legacy skills 行 → 标准包文件集合（确定性映射，无 AI）

设计约束（见 docs/Skill现状分析与升级方向报告.md 5.2/5.3）：
- frontmatter 解析存 dict（frontmatter_raw），SKILL.md 全文存 skill_files 行，两者解耦、
  导出永远取原文，不从 dict 重建 md。
- 文本文件内联 skill_files.content；二进制及超 SKILL_PACKAGE_INLINE_LIMIT 的文件走
  对象存储（StorageFactory）、storage_ref 回填，导出时回读，round-trip 字节保真。
- 单事务落库；包删除时 best-effort 清理对象存储，清理失败不阻断 DB 操作。
"""
import asyncio
import hashlib
import io
import mimetypes
import re
import threading
import uuid
import zipfile
from typing import Coroutine, Optional

import yaml
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.storage import StorageFactory
from app.core.storage_exceptions import StorageError
from app.models.skill_model import Skill
from app.models.skill_package_model import SkillPackage
from app.repositories.skill_package_repository import SkillPackageRepository


# frontmatter name 合法性（Agent Skills 规范）
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
# name slug 化：非法字符→连字符（legacy 迁移用）
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9-]+")

DESCRIPTION_MAX = 1024
SKILL_MD_MAX_CHARS = 16 * 1024  # read_skill 截断阈值（服务导出时仅用于 warning 判断）


def slugify_name(raw_name: str, fallback_id: str = "") -> str:
    """legacy name → 标准 frontmatter name（小写/数字/连字符，≤64）"""
    slug = _SLUG_INVALID_RE.sub("-", (raw_name or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")[:64]
    if not slug:
        slug = f"skill-{fallback_id[:8]}" if fallback_id else "skill"
    return slug


def _compute_content_hash(files: list[dict]) -> str:
    """全包内容 SHA-256：按 rel_path 排序后逐文件聚合 (rel_path, sha256)"""
    h = hashlib.sha256()
    for f in sorted(files, key=lambda x: x["rel_path"]):
        h.update(f["rel_path"].encode("utf-8"))
        h.update(b"\x00")
        h.update(f["sha256"].encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


def _parse_frontmatter(skill_md_text: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter，返回 (frontmatter dict, 正文)。无 frontmatter 时返回 ({}, 原文)。"""
    if not skill_md_text.startswith("---"):
        return {}, skill_md_text
    lines = skill_md_text.split("\n", 1) if "\n" in skill_md_text else [skill_md_text, ""]
    if len(lines) < 2:
        return {}, skill_md_text
    rest = lines[1]
    # 找闭合 ---
    match = re.match(r"(.*?)\n---\s*\n?(.*)", rest, re.DOTALL)
    if not match:
        return {}, skill_md_text
    fm_text, body = match.group(1), match.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, skill_md_text
    return (fm if isinstance(fm, dict) else {}), body


def _build_skill_md(frontmatter: dict, body: str) -> str:
    """从 frontmatter dict + 正文重建 SKILL.md（仅 legacy 转换路径使用；标准导入导出永远取原文）"""
    fm_text = yaml.safe_dump(
        {k: v for k, v in frontmatter.items() if k not in ("_legacy",)},
        allow_unicode=True, sort_keys=False, default_flow_style=False,
    ).strip()
    return f"---\n{fm_text}\n---\n\n{body.lstrip()}"


def _run_async(coro_factory: "Callable[[], Coroutine]"):
    """在同步代码中执行协程：无事件循环时直接 run，已在循环中时用独立线程。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    box: dict = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro_factory())
        except Exception as exc:  # noqa: BLE001 - 透传到调用线程
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _skill_storage_key(
    tenant_id: uuid.UUID, package_id: uuid.UUID, rel_path: str
) -> str:
    """对象存储 key：skills/{tenant}/{package}/{rel_path}"""
    return f"skills/{tenant_id}/{package_id}/{rel_path}"


def _detect_binary(content: bytes) -> bool:
    """二进制判定：含 NUL 或非 UTF-8。"""
    if b"\x00" in content[:8192]:
        return True
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


class SkillPackageService:
    """技能包导入导出服务"""

    # ==================== 解包与校验 ====================

    @staticmethod
    def _unpack_zip(zip_bytes: bytes) -> dict[str, bytes]:
        """安全解包 zip → {rel_path: content}。

        兼容两种布局：根即技能目录（含 SKILL.md）或根下唯一子目录含 SKILL.md。
        防：路径穿越、符号链接、超文件数、超总大小。
        """
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except (zipfile.BadZipFile, ValueError) as e:
            raise BusinessException(f"不是有效的 zip 文件: {e}", BizCode.BAD_REQUEST)

        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise BusinessException("zip 内没有文件", BizCode.BAD_REQUEST)
        if len(names) > settings.SKILL_PACKAGE_MAX_FILES:
            raise BusinessException(
                f"包内文件数 {len(names)} 超过上限 {settings.SKILL_PACKAGE_MAX_FILES}（可通过 SKILL_PACKAGE_MAX_FILES 调整）",
                BizCode.BAD_REQUEST,
            )

        total = 0
        files: dict[str, bytes] = {}
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            # 修正 zipfile 历史编码行为：无 UTF-8 flag 位时默认 CP437，中文文件名乱码高发区
            if not (info.flag_bits & 0x800):
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass
            # 路径安全
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise BusinessException(f"包内存在非法路径: {name}", BizCode.BAD_REQUEST)
            if info.create_system == 3 and (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise BusinessException(f"包内存在符号链接: {name}", BizCode.BAD_REQUEST)
            total += info.file_size
            if total > settings.SKILL_PACKAGE_MAX_SIZE:
                raise BusinessException(
                    f"解包总大小超过上限 {settings.SKILL_PACKAGE_MAX_SIZE} 字节（可通过 SKILL_PACKAGE_MAX_SIZE 调整）",
                    BizCode.BAD_REQUEST,
                )
            files[normalized] = zf.read(info)

        # 布局归一：根下唯一子目录含 SKILL.md → 剥掉前缀
        if "SKILL.md" not in files:
            top_dirs = {n.split("/", 1)[0] for n in files if "/" in n}
            if len(top_dirs) == 1 and f"{top_dirs.pop()}/SKILL.md" in files:
                files = {
                    n.split("/", 1)[1]: c for n, c in files.items() if "/" in n
                }
        if "SKILL.md" not in files:
            raise BusinessException("包内未找到 SKILL.md（根目录或唯一子目录下）", BizCode.BAD_REQUEST)
        return files

    @staticmethod
    def _validate_frontmatter(frontmatter: dict, source_type: str) -> list[str]:
        """frontmatter 校验，返回 warnings。legacy 源放宽（description 可空、name 允许程序生成）。"""
        warnings: list[str] = []

        name = frontmatter.get("name")
        if not name or not isinstance(name, str):
            raise BusinessException("frontmatter 缺少 name 字段", BizCode.BAD_REQUEST)
        if len(name) > 64:
            raise BusinessException(f"name 超长（{len(name)} > 64）", BizCode.BAD_REQUEST)
        if not _SKILL_NAME_RE.match(name):
            raise BusinessException(
                f"name '{name}' 不合法：仅允许小写字母/数字/连字符，不以连字符开头结尾、不含连续连字符",
                BizCode.BAD_REQUEST,
            )

        description = frontmatter.get("description")
        if source_type != "legacy":
            if not description or not isinstance(description, str):
                raise BusinessException("frontmatter 缺少 description 字段", BizCode.BAD_REQUEST)
        if description and isinstance(description, str) and len(description) > DESCRIPTION_MAX:
            raise BusinessException(f"description 超长（{len(description)} > {DESCRIPTION_MAX}）", BizCode.BAD_REQUEST)

        # books 级提示（5.2.4）：SKILL.md 超长建议拆分
        return warnings

    @staticmethod
    def _prepare_files(files: dict[str, bytes]) -> list[dict]:
        """阶段一：zip/编辑器文件集 → skill_files 行元数据（sha256/size/二进制判定）。

        此阶段不访问对象存储，也不决定最终落盘方式；storage_ref 统一置 None，
        待包创建拿到 package_id 后由 _persist_files 上传并回填。
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
                "_bytes": content,  # 阶段二上传用，add_files 前会剔除
            })
        return rows

    @staticmethod
    def _persist_files(
        rows: list[dict],
        tenant_id: uuid.UUID,
        package_id: uuid.UUID,
    ) -> None:
        """阶段二：需走对象存储的文件（二进制，或文本超内联上限）上传并回填 storage_ref。

        内联文本仍保留在 content；上传后剔除临时 _bytes。任一上传失败抛异常，
        由调用方事务回滚 + 已传对象走失败清理。
        """
        storage = StorageFactory.get_storage()
        for row in rows:
            content = row.pop("_bytes")
            use_storage = row["is_binary"] or row["size"] > settings.SKILL_PACKAGE_INLINE_LIMIT
            if not use_storage:
                continue
            rel_path = row["rel_path"]
            key = _skill_storage_key(tenant_id, package_id, rel_path)
            content_type = mimetypes.guess_type(rel_path)[0]

            async def _upload() -> str:
                return await storage.upload(key, content, content_type)

            _run_async(_upload)
            row["storage_ref"] = key
            # 二进制/超大文本不内联
            row["content"] = None

    @staticmethod
    def _delete_storage_objects(db: Session, package_id: uuid.UUID) -> None:
        """删除包内所有 storage_ref 指向的对象（best-effort，不阻断主流程）。"""
        from app.models.skill_package_model import SkillFile

        refs = [
            r[0]
            for r in db.query(SkillFile.storage_ref)
            .filter(
                SkillFile.package_id == package_id,
                SkillFile.storage_ref.isnot(None),
            )
            .all()
        ]
        if not refs:
            return
        storage = StorageFactory.get_storage()

        async def _delete_all() -> None:
            for key in refs:
                try:
                    await storage.delete(key)
                except StorageError:
                    pass  # 残留对象交由运维清理

        _run_async(_delete_all)

    @staticmethod
    def _read_storage_object(key: str) -> bytes:
        """同步读取对象存储内容。"""
        storage = StorageFactory.get_storage()

        async def _download() -> bytes:
            return await storage.download(key)

        return _run_async(_download)

    # ==================== 导入 ====================

    @staticmethod
    def import_package(
        db: Session,
        zip_bytes: bytes,
        tenant_id: uuid.UUID,
        source_ref: Optional[str] = None,
        overwrite: bool = False,
        conflict_strategy: str = "rename",
    ) -> dict:
        """导入标准包。幂等：同 (tenant, name) 且 content_hash 相同 → 返回已有 package。

        Returns:
            {package_id, skill_id, name, content_hash, files_count, warnings,
             idempotent: bool, conflict_resolved: str | None}
        """
        files = SkillPackageService._unpack_zip(zip_bytes)
        skill_md_raw = files["SKILL.md"].decode("utf-8", errors="strict")
        frontmatter, _body = _parse_frontmatter(skill_md_raw)

        warnings = SkillPackageService._validate_frontmatter(frontmatter, source_type="imported")
        if len(skill_md_raw) > 500 * 80:  # 约 500 行
            warnings.append("SKILL.md 超过约 500 行，建议将细节拆分到 references/（渐进披露最佳实践）")

        name = frontmatter["name"]
        description = (frontmatter.get("description") or "")[:DESCRIPTION_MAX]

        has_scripts = any(p.startswith("scripts/") and not p.endswith("/") for p in files)
        has_assets = any(p.startswith("assets/") for p in files)
        if has_scripts:
            warnings.append("包含 scripts/ 脚本，需沙箱执行环境，当前仅存储、不可执行")
        if has_assets:
            warnings.append("包含 assets/ 资产文件")

        file_rows = SkillPackageService._prepare_files(files)
        content_hash = _compute_content_hash(file_rows)

        # 幂等 / 同名冲突
        existing = SkillPackageRepository.get_by_name(db, tenant_id, name)
        if existing:
            if existing.content_hash == content_hash:
                # 找到或重建索引行（可能已被删）
                skill = db.query(Skill).filter(
                    Skill.package_id == existing.id, Skill.tenant_id == tenant_id
                ).first()
                return {
                    "package_id": str(existing.id),
                    "skill_id": str(skill.id) if skill else None,
                    "name": name, "content_hash": content_hash,
                    "files_count": len(file_rows), "warnings": warnings,
                    "idempotent": True, "conflict_resolved": None,
                }
            if not overwrite:
                raise BusinessException(
                    f"技能 {name} 已存在（内容不同）。传 overwrite=true 覆盖更新",
                    BizCode.DUPLICATE_NAME,
                )
            # overwrite：删旧包（级联删 files）重导
            SkillPackageRepository.delete(db, existing.id, tenant_id)

        # 落库
        pkg = SkillPackageRepository.create(
            db,
            tenant_id=tenant_id, name=name, display_name=name,
            description=description, content_hash=content_hash,
            source_type="imported", source_ref=source_ref,
            frontmatter_raw=frontmatter,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            has_scripts=has_scripts, has_assets=has_assets,
        )
        SkillPackageService._persist_files(file_rows, tenant_id, pkg.id)
        SkillPackageRepository.add_files(db, pkg.id, file_rows)

        # upsert skills 索引行 + 同名 legacy 冲突策略
        conflict_resolved = None
        skill = db.query(Skill).filter(
            Skill.tenant_id == tenant_id, Skill.name == name, Skill.skill_format == "standard"
        ).first()
        if not skill:
            legacy = db.query(Skill).filter(
                Skill.tenant_id == tenant_id, Skill.name == name, Skill.skill_format == "legacy"
            ).first()
            if legacy:
                if conflict_strategy == "overwrite":
                    fm = dict(frontmatter)
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
                        name=display, description=description, tenant_id=tenant_id,
                        tools=[], config={}, prompt=None,
                        is_active=True, is_public=False,
                        skill_format="standard", package_id=pkg.id,
                    )
                    pkg.display_name = display
                    db.add(skill)
                    db.flush()
                    conflict_resolved = "rename"
                    warnings.append(f"同名 legacy 技能存在，已按改名导入为 '{display}'")
            else:
                skill = Skill(
                    name=name, description=description, tenant_id=tenant_id,
                    tools=[], config={}, prompt=None,
                    is_active=True, is_public=False,
                    skill_format="standard", package_id=pkg.id,
                )
                db.add(skill)
                db.flush()
        else:
            skill.package_id = pkg.id
            skill.description = description

        db.commit()
        db.refresh(skill)
        return {
            "package_id": str(pkg.id),
            "skill_id": str(skill.id),
            "name": name, "content_hash": content_hash,
            "files_count": len(file_rows), "warnings": warnings,
            "idempotent": False, "conflict_resolved": conflict_resolved,
        }

    # ==================== 在线创建 / 编辑（包编辑视图） ====================

    @staticmethod
    def create_from_editor(
        db: Session,
        tenant_id: uuid.UUID,
        skill_md: str,
        files: Optional[dict[str, str]] = None,
    ) -> dict:
        """在线创建标准技能：SKILL.md 全文 + 可选附加文件（references/ 等）。

        校验/落库链路与 import_package 完全一致（frontmatter 解析 → 校验 → 幂等 → 两表落库 →
        upsert skills 索引行），文件源从 zip 换成请求体文本。

        Returns:
            同 import_package 的返回结构。
        """
        if not skill_md or not skill_md.strip():
            raise BusinessException("SKILL.md 内容不能为空", BizCode.BAD_REQUEST)

        package_files: dict[str, bytes] = {"SKILL.md": skill_md.encode("utf-8")}
        for rel_path, text in (files or {}).items():
            normalized = rel_path.replace("\\", "/").strip("/")
            if not normalized or normalized == "SKILL.md":
                continue
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise BusinessException(f"文件路径非法: {rel_path}", BizCode.BAD_REQUEST)
            if len(normalized) > 255:
                raise BusinessException(f"文件路径超长: {rel_path}", BizCode.BAD_REQUEST)
            package_files[normalized] = text.encode("utf-8") if isinstance(text, str) else text

        if len(package_files) - 1 > settings.SKILL_PACKAGE_MAX_FILES:
            raise BusinessException(
                f"文件数超过上限 {settings.SKILL_PACKAGE_MAX_FILES}", BizCode.BAD_REQUEST,
            )
        total = sum(len(c) for c in package_files.values())
        if total > settings.SKILL_PACKAGE_MAX_SIZE:
            raise BusinessException(
                f"总大小 {total} 超过上限 {settings.SKILL_PACKAGE_MAX_SIZE} 字节",
                BizCode.BAD_REQUEST,
            )

        frontmatter, _body = _parse_frontmatter(skill_md)
        if not frontmatter:
            raise BusinessException(
                "SKILL.md 缺少 frontmatter（--- name/description --- 头部块）",
                BizCode.BAD_REQUEST,
            )
        warnings = SkillPackageService._validate_frontmatter(frontmatter, source_type="imported")
        if len(skill_md) > 500 * 80:
            warnings.append("SKILL.md 超过约 500 行，建议将细节拆分到 references/（渐进披露最佳实践）")

        name = frontmatter["name"]
        description = (frontmatter.get("description") or "")[:DESCRIPTION_MAX]

        has_scripts = any(p.startswith("scripts/") for p in package_files)
        has_assets = any(p.startswith("assets/") for p in package_files)
        if has_scripts:
            warnings.append("包含 scripts/ 脚本，需沙箱执行环境，当前仅存储、不可执行")

        file_rows = SkillPackageService._prepare_files(package_files)
        content_hash = _compute_content_hash(file_rows)

        existing = SkillPackageRepository.get_by_name(db, tenant_id, name)
        if existing:
            if existing.content_hash == content_hash:
                skill = db.query(Skill).filter(
                    Skill.package_id == existing.id, Skill.tenant_id == tenant_id
                ).first()
                return {
                    "package_id": str(existing.id),
                    "skill_id": str(skill.id) if skill else None,
                    "name": name, "content_hash": content_hash,
                    "files_count": len(file_rows), "warnings": warnings,
                    "idempotent": True, "conflict_resolved": None,
                }
            raise BusinessException(
                f"技能 {name} 已存在（内容不同）。请更换 name，或先在技能编辑页修改",
                BizCode.DUPLICATE_NAME,
            )

        pkg = SkillPackageRepository.create(
            db,
            tenant_id=tenant_id, name=name, display_name=name,
            description=description, content_hash=content_hash,
            source_type="imported", source_ref=None,
            frontmatter_raw=frontmatter,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            has_scripts=has_scripts, has_assets=has_assets,
        )
        SkillPackageService._persist_files(file_rows, tenant_id, pkg.id)
        SkillPackageRepository.add_files(db, pkg.id, file_rows)

        skill = Skill(
            name=name, description=description, tenant_id=tenant_id,
            tools=[], config={}, prompt=None,
            is_active=True, is_public=False,
            skill_format="standard", package_id=pkg.id,
        )
        db.add(skill)
        db.flush()

        db.commit()
        db.refresh(skill)
        return {
            "package_id": str(pkg.id),
            "skill_id": str(skill.id),
            "name": name, "content_hash": content_hash,
            "files_count": len(file_rows), "warnings": warnings,
            "idempotent": False, "conflict_resolved": None,
        }

    @staticmethod
    def get_package_content(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
        """读取技能包内容（编辑器回显用）：SKILL.md 全文 + 附加文件列表与内容。"""
        skill = db.query(Skill).filter(
            Skill.id == skill_id, Skill.tenant_id == tenant_id
        ).first()
        if not skill:
            raise BusinessException(f"技能{skill_id}不存在或无权限", BizCode.NOT_FOUND)
        if not skill.package_id or (skill.skill_format or "legacy") != "standard":
            raise BusinessException("该技能不是标准格式技能", BizCode.BAD_REQUEST)

        pkg = SkillPackageRepository.get_by_id(db, skill.package_id, tenant_id)
        if not pkg:
            raise BusinessException(f"技能包{skill.package_id}不存在", BizCode.NOT_FOUND)
        rows = SkillPackageRepository.get_files(db, pkg.id)

        skill_md = ""
        extra_files = []
        for row in rows:
            if row.rel_path == "SKILL.md":
                skill_md = row.content or ""
            else:
                extra_files.append({
                    "rel_path": row.rel_path,
                    "content": row.content,  # 二进制/存储文件为 None
                    "is_binary": row.is_binary,
                    "storage_ref": row.storage_ref,
                    "size": row.size,
                    "sha256": row.sha256,
                })
        return {
            "skill_id": str(skill.id),
            "package_id": str(pkg.id),
            "name": pkg.name,
            "display_name": skill.name,
            "description": pkg.description,
            "skill_md": skill_md,
            "files": extra_files,
            "has_scripts": pkg.has_scripts,
            "has_assets": pkg.has_assets,
        }

    @staticmethod
    def update_package_content(
        db: Session,
        skill_id: uuid.UUID,
        tenant_id: uuid.UUID,
        skill_md: str,
        files: Optional[dict[str, str]] = None,
    ) -> dict:
        """编辑标准技能：整体替换包内容（编辑语义，非版本演进）。

        frontmatter 中的 name 变化走冲突检测并同步 skills 索引行；
        内容不变幂等跳过，内容变化删旧包重建。
        """
        skill = db.query(Skill).filter(
            Skill.id == skill_id, Skill.tenant_id == tenant_id
        ).first()
        if not skill:
            raise BusinessException(f"技能{skill_id}不存在或无权限", BizCode.NOT_FOUND)
        if not skill.package_id or (skill.skill_format or "legacy") != "standard":
            raise BusinessException("该技能不是标准格式技能", BizCode.BAD_REQUEST)
        if not skill_md or not skill_md.strip():
            raise BusinessException("SKILL.md 内容不能为空", BizCode.BAD_REQUEST)

        old_pkg = SkillPackageRepository.get_by_id(db, skill.package_id, tenant_id)
        if not old_pkg:
            raise BusinessException(f"技能包{skill.package_id}不存在", BizCode.NOT_FOUND)

        package_files: dict[str, bytes] = {"SKILL.md": skill_md.encode("utf-8")}
        for rel_path, text in (files or {}).items():
            normalized = rel_path.replace("\\", "/").strip("/")
            if not normalized or normalized == "SKILL.md":
                continue
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise BusinessException(f"文件路径非法: {rel_path}", BizCode.BAD_REQUEST)
            package_files[normalized] = text.encode("utf-8") if isinstance(text, str) else text

        if len(package_files) - 1 > settings.SKILL_PACKAGE_MAX_FILES:
            raise BusinessException(
                f"文件数超过上限 {settings.SKILL_PACKAGE_MAX_FILES}", BizCode.BAD_REQUEST,
            )
        total = sum(len(c) for c in package_files.values())
        if total > settings.SKILL_PACKAGE_MAX_SIZE:
            raise BusinessException(
                f"总大小 {total} 超过上限 {settings.SKILL_PACKAGE_MAX_SIZE} 字节",
                BizCode.BAD_REQUEST,
            )

        frontmatter, _body = _parse_frontmatter(skill_md)
        if not frontmatter:
            raise BusinessException(
                "SKILL.md 缺少 frontmatter（--- name/description --- 头部块）",
                BizCode.BAD_REQUEST,
            )
        warnings = SkillPackageService._validate_frontmatter(frontmatter, source_type="imported")

        name = frontmatter["name"]
        description = (frontmatter.get("description") or "")[:DESCRIPTION_MAX]

        # name 变更：同步 skills 索引行 name（DSL name 绑定随之变化，warning 提示）
        if name != old_pkg.name:
            dup = db.query(Skill).filter(
                Skill.tenant_id == tenant_id, Skill.name == name,
                Skill.id != skill.id,
            ).first()
            if dup:
                raise BusinessException(
                    f"技能名 '{name}' 已被其他技能占用", BizCode.DUPLICATE_NAME,
                )
            warnings.append(
                f"包 name 已从 '{old_pkg.name}' 改为 '{name}'；按 name 绑定该技能的 App 引用将失效，需重新绑定"
            )

        has_scripts = any(p.startswith("scripts/") for p in package_files)
        has_assets = any(p.startswith("assets/") for p in package_files)

        file_rows = SkillPackageService._prepare_files(package_files)
        content_hash = _compute_content_hash(file_rows)

        # 内容相同 → 幂等跳过
        if content_hash == old_pkg.content_hash:
            return {
                "package_id": str(old_pkg.id), "skill_id": str(skill.id),
                "name": name, "content_hash": content_hash,
                "files_count": len(file_rows), "warnings": warnings,
                "idempotent": True, "conflict_resolved": None,
            }

        # 内容不同 → 覆盖旧包（删旧建新，skills 指针改指新包）
        SkillPackageRepository.delete(db, old_pkg.id, tenant_id)

        pkg = SkillPackageRepository.create(
            db,
            tenant_id=tenant_id, name=name, display_name=skill.name,
            description=description, content_hash=content_hash,
            source_type=old_pkg.source_type, source_ref=old_pkg.source_ref,
            frontmatter_raw=frontmatter,
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            has_scripts=has_scripts, has_assets=has_assets,
        )
        SkillPackageService._persist_files(file_rows, tenant_id, pkg.id)
        SkillPackageRepository.add_files(db, pkg.id, file_rows)

        skill.package_id = pkg.id
        skill.description = description
        if name != old_pkg.name:
            skill.name = name

        db.commit()
        return {
            "package_id": str(pkg.id), "skill_id": str(skill.id),
            "name": name, "content_hash": content_hash,
            "files_count": len(file_rows), "warnings": warnings,
            "idempotent": False, "conflict_resolved": None,
        }

    # ==================== 导出 ====================

    @staticmethod
    def export_package(db: Session, skill_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[bytes, str]:
        """导出技能为标准包 zip。返回 (zip_bytes, filename)。

        standard 技能：SKILL.md 及全部文件从 skill_files 原文还原（字节级）。
        legacy 技能：先按 5.3.1 规则转换为标准包结构再导出（不落库，纯转换）。
        """
        skill = db.query(Skill).filter(
            Skill.id == skill_id, Skill.tenant_id == tenant_id
        ).first()
        if not skill:
            raise BusinessException(f"技能{skill_id}不存在或无权限", BizCode.NOT_FOUND)

        if (skill.skill_format or "legacy") == "standard" and skill.package_id:
            pkg = SkillPackageRepository.get_by_id(db, skill.package_id, tenant_id)
            if not pkg:
                raise BusinessException(f"技能包{skill.package_id}不存在", BizCode.NOT_FOUND)
            rows = SkillPackageRepository.get_files(db, pkg.id)
            if not rows:
                raise BusinessException("技能包内容为空", BizCode.NOT_FOUND)
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for row in rows:
                    if row.storage_ref:
                        # 二进制/超大文件从对象存储回读，字节级还原
                        data = SkillPackageService._read_storage_object(row.storage_ref)
                    else:
                        data = (row.content or "").encode("utf-8")
                    info = zipfile.ZipInfo(row.rel_path, date_time=(2026, 1, 1, 0, 0, 0))
                    info.flag_bits |= 0x800  # UTF-8 文件名
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, data)
            return zip_buf.getvalue(), f"{pkg.name}.zip"

        # legacy → 标准包转换导出（不落库）
        frontmatter, files = SkillPackageService.legacy_to_package_files(skill)
        skill_md = _build_skill_md(frontmatter, files["SKILL.md"])
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path, text in files.items():
                data = skill_md.encode("utf-8") if rel_path == "SKILL.md" else text.encode("utf-8")
                info = zipfile.ZipInfo(rel_path, date_time=(2026, 1, 1, 0, 0, 0))
                info.flag_bits |= 0x800
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, data)
        name = frontmatter["name"]
        return zip_buf.getvalue(), f"{name}.zip"

    # ==================== legacy → 标准转换（确定性映射） ====================

    @staticmethod
    def legacy_to_package_files(skill: Skill) -> tuple[dict, dict[str, str]]:
        """legacy skills 行 → (frontmatter dict, files: {rel_path: 文本内容})。

        映射规则（报告 5.3.1）：
        - name → slug 化 frontmatter name；display_name 保留原名
        - prompt → SKILL.md 正文；keywords 并入 description 尾部括注
        - tools → 归档 frontmatter._legacy.tools，正文头部加提示注释
        - config 其余键 → 归档 frontmatter._legacy.config
        """
        fallback = str(skill.id)
        name = slugify_name(skill.name, fallback)
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
        body = f"# {skill.name}\n\n{tool_note}{prompt}" if prompt else (
            f"# {skill.name}\n\n（此技能未配置提示词正文。请在记忆熊技能编辑页补充，或编辑导出后的 SKILL.md。）"
        )

        frontmatter = {
            "name": name,
            "description": description,
            "metadata": {
                "generated_by": "memorybear",
                "source_skill_id": fallback,
            },
        }
        if skill.is_public:
            frontmatter["metadata"]["public"] = True
        # 归档 legacy 字段（不进导出的 frontmatter，_build_skill_md 会剔除 _legacy）
        frontmatter["_legacy"] = {
            "tools": legacy_tools,
            "config": {k: v for k, v in config.items() if k != "keywords"},
            "keywords": keywords,
            "prompt": skill.prompt,
        }

        files = {"SKILL.md": body}
        return frontmatter, files
