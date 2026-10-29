"""Skill / plugin export packaging tests (no DB).

Covers the export-side composition added for "export every skill, plugins
included":
- a legacy row converts on the fly to a single ``SKILL.md`` member;
- a standard pack replays its ``skill_files`` rows byte-for-byte;
- a plugin aggregates every member under a unique ``<slug>/`` directory so the
  importer's ``_split_members`` re-parses it as the same multi-member bundle;
- same-named members get de-duplicated directory prefixes.
"""

from __future__ import annotations

import io
import types
import uuid
import zipfile

import pytest

from src.errors import BizCode, BusinessException
from src.services.skill_package_service import SkillPackageService


def _legacy_skill(name: str, *, skill_id: uuid.UUID | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=skill_id or uuid.uuid4(),
        name=name,
        description=f"{name} 的描述",
        config={"keywords": ["调研"]},
        tools=[],
        prompt="你是一个调研助手。",
        is_public=False,
        skill_format="legacy",
        package_id=None,
    )


def _unzip(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}


class _FakeFile:
    def __init__(self, rel_path: str, content: str, storage_ref: str | None = None):
        self.rel_path = rel_path
        self.content = content
        self.storage_ref = storage_ref


class _StandardRepo:
    """Minimal repo surface used by ``_skill_pack_entries`` for a standard row."""

    def __init__(self, files: list[_FakeFile]):
        self._files = files

    def get_by_id(self, package_id, tenant_id):  # noqa: ANN001
        return types.SimpleNamespace(id=package_id, name="research-pack")

    def get_files(self, package_id):  # noqa: ANN001
        return self._files


def test_legacy_single_skill_exports_one_skill_md():
    skill = _legacy_skill("市场调研")
    entries, stem = SkillPackageService._skill_pack_entries(_StandardRepo([]), uuid.uuid4(), skill)

    paths = {rel for rel, _ in entries}
    assert paths == {"SKILL.md"}
    assert stem  # non-empty download stem derived from the name
    blob = SkillPackageService._write_zip(entries)
    skill_md = _unzip(blob)["SKILL.md"].decode("utf-8")
    assert "# 市场调研" in skill_md
    assert "你是一个调研助手。" in skill_md


def test_standard_pack_replays_files_byte_faithfully():
    files = [
        _FakeFile("SKILL.md", "---\nname: research\n---\n# body\n"),
        _FakeFile("references/method.md", "# method\n"),
    ]
    skill = types.SimpleNamespace(skill_format="standard", package_id=uuid.uuid4())
    repo = _StandardRepo(files)

    entries, stem = SkillPackageService._skill_pack_entries(repo, uuid.uuid4(), skill)
    blob = SkillPackageService._write_zip(entries)
    unpacked = _unzip(blob)

    assert set(unpacked) == {"SKILL.md", "references/method.md"}
    assert unpacked["references/method.md"] == b"# method\n"
    assert stem  # pkg name comes from repo.get_by_id path; here we only assert bytes


class _PluginRepo:
    def __init__(self, pkgs: list):
        self._pkgs = pkgs
        self.calls: list = []

    def list_by_plugin(self, plugin_id, tenant_id):  # noqa: ANN001
        return self._pkgs


class _SkillQuery:
    """Chain stand-in: db.query(Skill).filter(...).first() returns a queued skill."""

    def __init__(self, skills: list):
        self._skills = list(skills)

    def query(self, _model):
        return self

    def filter(self, *_conditions):
        return self

    def first(self):
        return self._skills.pop(0) if self._skills else None


def test_plugin_export_aggregates_members_and_round_trips():
    tenant = uuid.uuid4()
    skill_a = _legacy_skill("市场调研")
    skill_b = _legacy_skill("竞品分析")
    pkgs = [types.SimpleNamespace(id=uuid.uuid4()), types.SimpleNamespace(id=uuid.uuid4())]
    plugin = types.SimpleNamespace(id=uuid.uuid4(), name="调研合集")

    repo = _PluginRepo(pkgs)
    db = _SkillQuery([skill_a, skill_b])

    blob, filename = SkillPackageService._export_plugin_zip(db, repo, tenant, plugin)
    assert filename == "调研合集.zip"

    files = _unzip(blob)
    member_roots = {n.split("/", 1)[0] for n in files}
    assert len(member_roots) == 2
    assert all(n.endswith("/SKILL.md") for n in files if n.count("/") == 1)

    # The existing importer must re-split the exported bundle as one plugin.
    members = SkillPackageService._split_members(files)
    assert len(members) == 2
    assert {m["key"] for m in members} == member_roots
    for m in members:
        assert "SKILL.md" in m["files"]


def test_plugin_export_dedupes_same_named_member_dirs():
    tenant = uuid.uuid4()
    # Identical ASCII names slugify to the same directory; dedup must suffix -2.
    skill_a = _legacy_skill("research")
    skill_b = _legacy_skill("research")
    pkgs = [types.SimpleNamespace(id=uuid.uuid4()), types.SimpleNamespace(id=uuid.uuid4())]
    plugin = types.SimpleNamespace(id=uuid.uuid4(), name="bundle")

    blob, _ = SkillPackageService._export_plugin_zip(
        db=_SkillQuery([skill_a, skill_b]),
        repo=_PluginRepo(pkgs),
        tenant_id=tenant,
        plugin=plugin,
    )
    dirs = {n.split("/", 1)[0] for n in _unzip(blob)}
    assert dirs == {"research", "research-2"}


class _OneSkillQuery:
    """db.query(Skill).filter(...).first() always returns the queued skill."""

    def __init__(self, skill):
        self._skill = skill

    def filter(self, *_conditions):
        return self

    def first(self):
        return self._skill


class _OneSkillDb:
    def __init__(self, skill):
        self._skill = skill

    def query(self, _model):
        return _OneSkillQuery(self._skill)


def test_soft_deleted_skill_cannot_be_exported():
    # After DELETE the row is is_active=False with its pack hard-deleted
    # (package_id=None). Export is a content artifact: it must NOT_FOUND rather
    # than fall through to the legacy-conversion placeholder.
    from src.services.skill_package_service import SkillPackageRepository

    soft_deleted = types.SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="removed-skill",
        is_active=False,
        skill_format="standard",
        package_id=None,
    )
    db = _OneSkillDb(soft_deleted)
    repo = SkillPackageRepository(db)

    with pytest.raises(BusinessException) as exc:
        SkillPackageService.export_zip(db, soft_deleted.tenant_id, soft_deleted.id, )
    assert exc.value.code == BizCode.NOT_FOUND


class _Chain:
    """Stand-in for a SQLAlchemy query chain ending in .first()/.all()."""

    def __init__(self, first=None, all_rows=None):
        self._first = first
        self._all = all_rows or []

    def filter(self, *_conditions):
        return self

    def order_by(self, *_cols):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all


class _ModelDb:
    """Routes ``db.query(Model)`` to canned rows by ORM model class."""

    def __init__(self, *, skill=None, package=None, files=None):
        self._skill = skill
        self._package = package
        self._files = files or []

    def query(self, model):
        from src.models.skill_model import Skill
        from src.models.skill_package_model import SkillFile, SkillPackage

        if model is Skill:
            return _Chain(first=self._skill)
        if model is SkillPackage:
            return _Chain(first=self._package)
        if model is SkillFile:
            return _Chain(all_rows=self._files)
        return _Chain()


def test_active_skill_still_exports():
    # Regression guard: the is_active gate must not block a live standard skill.
    pkg_id = uuid.uuid4()
    active = types.SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        name="live-skill",
        is_active=True,
        skill_format="standard",
        package_id=pkg_id,
    )
    package = types.SimpleNamespace(id=pkg_id, name="live-pack")
    files = [_FakeFile("SKILL.md", "---\nname: live\n---\n# body\n")]
    db = _ModelDb(skill=active, package=package, files=files)

    blob, filename = SkillPackageService.export_zip(db, active.tenant_id, active.id)
    assert filename == "live-pack.zip"
    assert "SKILL.md" in _unzip(blob)
