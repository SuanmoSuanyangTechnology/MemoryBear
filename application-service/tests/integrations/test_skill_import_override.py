"""Import-time presentation overrides (name / call_name / description).

Contract: editing the three fields at import preview/commit changes only the
resource-center face (and the call_name runtime identity); it never rewrites
SKILL.md or pack files, and the model skill index keeps using the original
frontmatter name/description.
"""

from __future__ import annotations

import types
import uuid

from src.agent.skill_runtime_service import skill_index_triple
from src.services.skill_package_service import SkillPackageService


def _skill(**kw):
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="research",
        description="原始行描述", tools=[], config={}, prompt=None,
        is_active=True, is_public=False, created_at=None, updated_at=None,
        skill_format="standard", package_id=uuid.uuid4(), call_name="research",
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _pkg(**kw):
    base = dict(
        id=uuid.uuid4(), plugin_id=None, name="research", display_name="市场调研",
        description="用户改的展示描述", source_ref=None, has_scripts=False,
        frontmatter_raw={"name": "research", "description": "原始 SKILL.md 描述"},
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_display_name_prefers_override_then_falls_back():
    skill, pkg = _skill(), _pkg()
    assert SkillPackageService._display_name(skill, pkg) == "市场调研"
    pkg.display_name = None
    assert SkillPackageService._display_name(skill, pkg) == "research"
    assert SkillPackageService._display_name(skill, None) == "research"


def test_skill_card_shows_overridden_name_but_keeps_call_name():
    skill, pkg = _skill(), _pkg()
    card = SkillPackageService._skill_card(skill, pkg)
    assert card["name"] == "市场调研"          # UI shows the override
    assert card["call_name"] == "research"     # runtime identity untouched
    assert card["description"] == "原始行描述"


def test_model_index_uses_original_frontmatter_not_overrides():
    skill, pkg = _skill(), _pkg()
    call_name, model_name, model_desc = skill_index_triple(skill, pkg)
    assert call_name == "research"
    assert model_name == "research"            # NOT 市场调研
    assert model_desc == "原始 SKILL.md 描述"   # NOT 用户改的展示描述


def test_model_index_falls_back_when_frontmatter_description_missing():
    skill = _skill()
    pkg = _pkg(frontmatter_raw={"name": "research"}, description="展示描述")
    _call, _name, desc = skill_index_triple(skill, pkg)
    assert desc == "展示描述"


def test_model_index_legacy_row_without_pack():
    skill = _skill(skill_format="legacy", package_id=None, description="legacy 描述")
    call_name, name, desc = skill_index_triple(skill, None)
    assert (call_name, name, desc) == ("research", "research", "legacy 描述")
