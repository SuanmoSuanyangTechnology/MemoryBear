"""Plugin detail payload tests (no DB).

The plugin detail endpoint must inline every member skill's files, the same way
a standalone skill's detail does — the drill-down card previously returned only
a summary (``files_count``) with no file content. These tests pin that shape and
prove the nested ``files`` / ``skill_md`` survive Pydantic serialisation (the
``members`` field is ``List[Dict[str, Any]]``, so nested keys must not be
silently dropped the way an undeclared top-level field would be).
"""

from __future__ import annotations

import datetime as dt
import types
import uuid

from src.schemas.skill_schema import Skill
from src.services import skill_package_service as svc_mod
from src.services.skill_package_service import SkillPackageService

# --- tiny SQLAlchemy-ish filter evaluator (== only; or_/is_ are skipped) ----

def _criterion_value(criterion):
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
                    continue  # or_(...) / is_(...) — non-equality filters
                if getattr(row, key, None) != value:
                    return False
            return True

        self._rows = [r for r in self._rows if matches(r)]
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):  # noqa: ANN001
        return _FakeQuery(list(self._rows))


class _FakeRepo:
    """Stand-in for SkillPackageRepository used inside get_entry_detail."""

    def __init__(self, plugin, packages, files_by_pkg):
        self._plugin = plugin
        self._packages = packages
        self._files = files_by_pkg

    def get_plugin_by_id(self, entry_id, tenant_id):  # noqa: ANN001
        return self._plugin if self._plugin and self._plugin.id == entry_id else None

    def list_by_plugin(self, plugin_id, tenant_id):  # noqa: ANN001
        return self._packages

    def get_files(self, package_id):  # noqa: ANN001
        return self._files.get(package_id, [])


def _now():
    return dt.datetime(2026, 10, 29, 4, 0, 0)


def _build_detail(monkeypatch):
    tenant = uuid.uuid4()
    plugin_id = uuid.uuid4()
    pkg_a, pkg_b = uuid.uuid4(), uuid.uuid4()

    plugin = types.SimpleNamespace(
        id=plugin_id, tenant_id=tenant, name="bundle", description="bundle desc",
        created_at=_now(), updated_at=_now(), source_ref="bundle.zip",
    )
    packages = [
        types.SimpleNamespace(id=pkg_a, description="alpha desc", has_scripts=True),
        types.SimpleNamespace(id=pkg_b, description="beta desc", has_scripts=False),
    ]
    files = {
        pkg_a: [
            types.SimpleNamespace(
                rel_path="SKILL.md", content="---\nname: alpha\n---\n# alpha",
                size=30, is_binary=False, storage_ref=None,
            ),
            types.SimpleNamespace(
                rel_path="scripts/run.py", content=None,
                size=12, is_binary=True, storage_ref=None,
            ),
        ],
        pkg_b: [
            types.SimpleNamespace(
                rel_path="SKILL.md", content="# beta",
                size=6, is_binary=False, storage_ref=None,
            ),
        ],
    }

    # Member skills keyed so the entry-id lookup misses (goes to plugin branch)
    # but the package_id + tenant_id member lookup hits.
    skill_rows = [
        types.SimpleNamespace(
            id=uuid.uuid4(), tenant_id=tenant, name="alpha", call_name="alpha",
            is_active=True, package_id=pkg_a,
        ),
        types.SimpleNamespace(
            id=uuid.uuid4(), tenant_id=tenant, name="beta", call_name="beta",
            is_active=True, package_id=pkg_b,
        ),
    ]
    db = _FakeDb(skill_rows)
    monkeypatch.setattr(
        svc_mod, "SkillPackageRepository", lambda session: _FakeRepo(plugin, packages, files)
    )
    return SkillPackageService.get_entry_detail(db, tenant, plugin_id), files, (pkg_a, pkg_b)


def test_plugin_detail_inlines_each_member_files(monkeypatch):
    detail, files, (pkg_a, _pkg_b) = _build_detail(monkeypatch)

    assert detail["kind"] == "plugin"
    members = detail["members"]
    assert [m["name"] for m in members] == ["alpha", "beta"]

    alpha = members[0]
    # Existing summary fields are preserved...
    assert alpha["files_count"] == 2
    assert alpha["has_scripts"] is True
    assert alpha["call_name"] == "alpha"
    # ...and file detail is now present.
    assert alpha["skill_md"].startswith("---\nname: alpha")
    paths = [f["rel_path"] for f in alpha["files"]]
    assert paths == ["SKILL.md", "scripts/run.py"]
    binary = next(f for f in alpha["files"] if f["rel_path"] == "scripts/run.py")
    assert binary["is_binary"] is True
    assert binary["content"] is None  # binary rows carry no inline content
    text = next(f for f in alpha["files"] if f["rel_path"] == "SKILL.md")
    assert text["content"] == files[pkg_a][0].content

    beta = members[1]
    assert beta["files_count"] == 1
    assert beta["skill_md"] == "# beta"
    assert [f["rel_path"] for f in beta["files"]] == ["SKILL.md"]


def test_plugin_detail_survives_pydantic_serialisation(monkeypatch):
    # members is List[Dict[str, Any]]: nested skill_md/files must round-trip
    # instead of being stripped by Skill.model_validate.
    detail, _files, _pkgs = _build_detail(monkeypatch)
    validated = Skill.model_validate(detail)
    dumped = validated.model_dump()

    assert dumped["kind"] == "plugin"
    alpha = dumped["members"][0]
    assert "skill_md" in alpha and "files" in alpha
    assert {f["rel_path"] for f in alpha["files"]} == {"SKILL.md", "scripts/run.py"}
