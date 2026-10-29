"""Import-side guards for plugin bundles (no DB).

Covers two failure modes found on real plugin imports:
- OS/archive junk (``__MACOSX/``, ``.DS_Store``, AppleDouble ``._*``,
  ``Thumbs.db``) must be dropped on unpack so it never enters a member pack and
  never defeats single-root-prefix detection;
- identity / overwrite planning. ``call_name`` is the unique runtime identity of a
  skill, so ``_plan_import_members`` keys off it: a member whose call_name already
  exists is planned as an *overwrite* (the same ``skills`` row is reused on commit,
  preserving id and bindings; cross-plugin owners migrate) with a user-facing
  warning naming the target. The rare package-name clash with a *different*
  call_name is skipped (it cannot overwrite another skill and would hit the package
  unique constraint); duplicate call_names inside one upload are rejected as an
  ambiguous bundle. When every member is skipped no plugin row is created, so a
  zero-member shell can never persist. ``_commit_member`` keeps a defense-in-depth
  refusal to attach an existing package owned by another group on the fresh path.
"""

from __future__ import annotations

import io
import types
import uuid
import zipfile

import pytest

from src.errors import BizCode, BusinessException
from src.services.skill_package_service import (
    SkillPackageService,
    _is_junk_path,
)

# ----------------------------- helpers -------------------------------------

def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _skill_md(name: str) -> bytes:
    return f"---\nname: {name}\ndescription: {name} description\n---\n# {name}\n".encode()


# --------------------------- junk classification --------------------------

@pytest.mark.parametrize(
    "rel",
    [
        "__MACOSX/._SKILL.md",
        "__MACOSX/abc/._run.py",
        "skills/brainstorming/.DS_Store",
        "skills/brainstorming/._SKILL.md",
        "Thumbs.db",
        "a/desktop.ini",
        "a/b/THUMBS.DB",
    ],
)
def test_junk_paths_detected(rel: str):
    assert _is_junk_path(rel)


@pytest.mark.parametrize(
    "rel",
    [
        "SKILL.md",
        "scripts/run.py",
        "references/faq.md",
        "assets/DS_store_notes.md",  # only the exact basename is junk
        "a._b.py",
    ],
)
def test_normal_paths_not_junk(rel: str):
    assert not _is_junk_path(rel)


# ----------------------------- unpack filter -------------------------------

def test_read_zip_entries_drops_os_junk():
    blob = _zip(
        {
            "SKILL.md": _skill_md("alpha"),
            "scripts/run.py": b"print('ok')",
            ".DS_Store": b"\x00junk",
            "__MACOSX/._SKILL.md": b"\x00fork",
            "scripts/._run.py": b"\x00fork",
            "Thumbs.db": b"\x00junk",
        }
    )
    files = SkillPackageService._read_zip_entries(blob)
    assert set(files) == {"SKILL.md", "scripts/run.py"}


def test_macosx_dir_does_not_break_single_root_detection():
    # A typical macOS-wrapped single-skill zip: one real root dir plus a sibling
    # __MACOSX tree. Junk filtering must leave exactly one top-level dir so the
    # wrapper prefix is stripped and the pack is parsed as one rooted member.
    blob = _zip(
        {
            "pack/SKILL.md": _skill_md("brainstorming"),
            "pack/scripts/run.py": b"print(1)",
            "pack/.DS_Store": b"x",
            "__MACOSX/pack/._SKILL.md": b"y",
            "__MACOSX/pack/._.DS_Store": b"y",
        }
    )
    entries = SkillPackageService._read_zip_entries(blob)
    assert all(not _is_junk_path(p) for p in entries)
    prefix = SkillPackageService._root_prefix(entries)
    assert prefix == "pack/"
    stripped = {k[len(prefix):]: v for k, v in entries.items() if k.startswith(prefix)}
    members = SkillPackageService._split_members(stripped)
    assert len(members) == 1
    assert members[0]["name"] == "brainstorming"
    assert set(members[0]["files"]) == {"SKILL.md", "scripts/run.py"}


def test_junk_dropped_from_every_plugin_member():
    blob = _zip(
        {
            "bundle/alpha/SKILL.md": _skill_md("alpha"),
            "alpha/.DS_Store": b"x",  # same top-level count noise stays out via prefix
            "bundle/beta/SKILL.md": _skill_md("beta"),
            "bundle/beta/._SKILL.md": b"fork",
            "bundle/__MACOSX/._anything": b"fork",
        }
    )
    entries = SkillPackageService._read_zip_entries(blob)
    prefix = SkillPackageService._root_prefix(entries)
    if prefix:
        entries = {k[len(prefix):]: v for k, v in entries.items() if k.startswith(prefix)}
    members = SkillPackageService._split_members(entries)
    assert {m["name"] for m in members} == {"alpha", "beta"}
    for member in members:
        assert all(not _is_junk_path(p) for p in member["files"])


# -------------------- plugin member-availability precheck ------------------

class _FakeRepo:
    def __init__(self, packages: dict[str, object] | None = None):
        # name -> plugin_id (None = a standalone, non-plugin skill package).
        self._by_name = packages or {}
        self._ids = {name: uuid.uuid4() for name in self._by_name}

    def get_by_name(self, tenant_id, name):  # noqa: ANN001
        if name in self._by_name:
            return types.SimpleNamespace(
                id=self._ids[name], name=name, plugin_id=self._by_name[name]
            )
        return None

    def get_by_id(self, package_id, tenant_id=None):  # noqa: ANN001
        for name, pid in self._by_name.items():
            if self._ids[name] == package_id:
                return types.SimpleNamespace(id=package_id, name=name, plugin_id=pid)
        return None

    def get_plugin_by_id(self, plugin_id, tenant_id=None):  # noqa: ANN001
        return types.SimpleNamespace(id=plugin_id, name="owner-plugin")


def _criterion_value(criterion):
    """Extract (column-key, value) from a simple SQLAlchemy ``col == value``."""

    right = getattr(criterion, "right", None)
    value = getattr(right, "effective_value", None)
    if value is None:
        value = getattr(right, "value", None)
    left = getattr(criterion, "left", None)
    key = getattr(left, "key", None) or getattr(left, "name", None)
    return key, value


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *criteria):
        def matches(row):
            for crit in criteria:
                key, value = _criterion_value(crit)
                if key is None or value is None:
                    continue  # is_(...) and other non-equality filters
                if getattr(row, key, None) != value:
                    return False
            return True

        self._rows = [r for r in self._rows if matches(r)]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, skill_rows):
        self._rows = skill_rows

    def query(self, model):  # noqa: ANN001
        return _FakeQuery(list(self._rows))


def _prepared(*names: str) -> list[dict]:
    return [
        {
            "pkg_name": n,
            "name": n,
            "call_name": n,
            "warnings": [],
        }
        for n in names
    ]


def _owner(tenant, *, call_name="alpha", name="Old Alpha", package_id=None,
           skill_format="standard"):
    return types.SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant, call_name=call_name, name=name,
        is_active=True, package_id=package_id, skill_format=skill_format,
    )


_TENANT = uuid.uuid4()


def test_plan_marks_nothing_when_all_members_free():
    prepared = _prepared("alpha", "beta")
    skipped = SkillPackageService._plan_import_members(
        _FakeDb([]), _FakeRepo(), _TENANT, prepared,
        is_plugin=True, resolved_plugin_name="bundle",
    )
    assert skipped == []
    assert all(not item.get("will_overwrite") for item in prepared)
    assert all(not item.get("skipped") for item in prepared)
    assert all(item["warnings"] == [] for item in prepared)


def test_plan_overwrite_owner_by_call_name():
    # Legacy owner (no package): same call_name -> overwrite + up-convert note.
    owner = _owner(_TENANT, call_name="alpha", name="Old Alpha",
                   package_id=None, skill_format="legacy")
    prepared = _prepared("alpha")
    skipped = SkillPackageService._plan_import_members(
        _FakeDb([owner]), _FakeRepo(), _TENANT, prepared,
        is_plugin=False, resolved_plugin_name=None,
    )
    assert skipped == []
    item = prepared[0]
    assert item["will_overwrite"] is True
    assert item["overwrite_skill_id"] == str(owner.id)
    assert item["overwrite_target_name"] == "Old Alpha"
    assert item["cross_plugin"] is False
    assert any("覆盖" in w and "Old Alpha" in w for w in item["warnings"])
    assert any("legacy" in w for w in item["warnings"])


def test_plan_overwrite_cross_plugin_marks_migration():
    other_plugin = uuid.uuid4()
    repo = _FakeRepo({"alpha-pkg": other_plugin})
    old_pkg = repo.get_by_name(_TENANT, "alpha-pkg")
    owner = _owner(_TENANT, call_name="alpha", name="Alpha", package_id=old_pkg.id)
    prepared = _prepared("alpha")  # pkg name 'alpha' differs from the old 'alpha-pkg'
    SkillPackageService._plan_import_members(
        _FakeDb([owner]), repo, _TENANT, prepared,
        is_plugin=True, resolved_plugin_name="new-bundle",
    )
    item = prepared[0]
    assert item["will_overwrite"] is True
    assert item["cross_plugin"] is True
    assert any("迁移" in w and "new-bundle" in w for w in item["warnings"])


def test_plan_skips_package_name_clash_with_different_call_name():
    # 'beta' package exists under another skill whose call_name is not 'beta-new',
    # so it cannot be overwritten by call_name identity -> skip, not steal.
    repo = _FakeRepo({"beta": uuid.uuid4()})
    prepared = [{
        "pkg_name": "beta", "name": "beta", "call_name": "beta-new", "warnings": [],
    }]
    skipped = SkillPackageService._plan_import_members(
        _FakeDb([]), repo, _TENANT, prepared,
        is_plugin=True, resolved_plugin_name="bundle",
    )
    assert [item["pkg_name"] for item in skipped] == ["beta"]
    item = prepared[0]
    assert item["skipped"] is True
    assert not item.get("will_overwrite")
    assert "其他插件" in item["skip_reason"]


def test_plan_rejects_duplicate_call_name_within_one_upload():
    prepared = [
        {"pkg_name": "a1", "name": "a1", "call_name": "dup", "warnings": []},
        {"pkg_name": "a2", "name": "a2", "call_name": "dup", "warnings": []},
    ]
    with pytest.raises(BusinessException) as exc:
        SkillPackageService._plan_import_members(
            _FakeDb([]), _FakeRepo(), _TENANT, prepared,
            is_plugin=True, resolved_plugin_name="bundle",
        )
    assert exc.value.code == BizCode.BAD_REQUEST
    assert "dup" in exc.value.message


def test_plan_all_skipped_adds_summary_warning():
    # Single member whose package name clashes under a different call_name.
    repo = _FakeRepo({"beta": None})
    prepared = [{
        "pkg_name": "beta", "name": "beta", "call_name": "gamma", "warnings": [],
    }]
    skipped = SkillPackageService._plan_import_members(
        _FakeDb([]), repo, _TENANT, prepared,
        is_plugin=True, resolved_plugin_name="bundle",
    )
    assert len(skipped) == 1
    assert all(item.get("skipped") for item in prepared)
    assert any("所有成员均" in w for item in prepared for w in item["warnings"])


# ----------------------- _commit_member ownership --------------------------

def _commit_item(name: str, content_hash: str) -> dict:
    return {
        "pkg_name": name,
        "name": name,
        "call_name": name,
        "description": f"{name} description",
        "content_hash": content_hash,
        "rows": [{"rel_path": "SKILL.md"}],
        "frontmatter": {"name": name},
        "warnings": [],
    }


class _CommitRepo:
    def __init__(self, existing):
        self._existing = existing

    def get_by_name(self, tenant_id, name):  # noqa: ANN001
        return self._existing if self._existing and self._existing.name == name else None


def test_commit_member_refuses_package_owned_by_another_plugin():
    other_plugin = uuid.uuid4()
    new_plugin = uuid.uuid4()
    existing = types.SimpleNamespace(
        id=uuid.uuid4(), name="alpha", content_hash="h1", plugin_id=other_plugin
    )
    with pytest.raises(BusinessException) as exc:
        SkillPackageService._commit_member(
            _FakeDb([]),
            _CommitRepo(existing),
            tenant_id=uuid.uuid4(),
            item=_commit_item("alpha", "h1"),
            plugin_id=new_plugin,
            source_ref="bundle.zip",
            overwrite=False,
            conflict_strategy="rename",
        )
    assert exc.value.code == BizCode.DUPLICATE_NAME


def test_commit_member_same_plugin_reimport_is_idempotent():
    plugin_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    package_id = uuid.uuid4()
    skill_id = uuid.uuid4()
    existing = types.SimpleNamespace(
        id=package_id, name="alpha", content_hash="h1", plugin_id=plugin_id
    )
    skill_row = types.SimpleNamespace(
        id=skill_id, call_name="alpha", package_id=package_id, tenant_id=tenant_id
    )
    result = SkillPackageService._commit_member(
        _FakeDb([skill_row]),
        _CommitRepo(existing),
        tenant_id=tenant_id,
        item=_commit_item("alpha", "h1"),
        plugin_id=plugin_id,
        source_ref="bundle.zip",
        overwrite=False,
        conflict_strategy="rename",
    )
    assert result["idempotent"] is True
    assert result["package_id"] == str(package_id)
    assert result["skill_id"] == str(skill_id)
