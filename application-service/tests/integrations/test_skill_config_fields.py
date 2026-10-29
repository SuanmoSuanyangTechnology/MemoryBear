"""Skills config now keys solely off ``skill_ids``.

The legacy ``enabled`` master switch and ``all_skills`` switch are ignored
everywhere (runtime gate, config schema, cluster-supervisor normalization);
historical rows still carrying those keys parse fine via extra=ignore and need
no migration.
"""

from __future__ import annotations

from src.agent.agent_middleware import AgentMiddleware
from src.schemas.app_schema import SkillConfig
from src.schemas.multi_agent_schema import SupervisorConfig


def test_skill_config_drops_legacy_switches_from_old_data():
    cfg = SkillConfig(
        **{"enabled": True, "all_skills": True, "skill_ids": ["a", "b"]}
    )
    assert cfg.skill_ids == ["a", "b"]
    assert not hasattr(cfg, "enabled")
    assert not hasattr(cfg, "all_skills")
    # dumped shape only carries the surviving field
    assert set(cfg.model_dump(exclude_none=True)) == {"skill_ids"}


def test_skill_config_defaults_to_empty():
    assert SkillConfig().skill_ids == []


def test_middleware_only_reads_skill_ids():
    # all_skills=true with empty list must NOT mean "load everything" anymore.
    mw = AgentMiddleware({"enabled": True, "all_skills": True, "skill_ids": []})
    assert mw.skill_ids == []
    assert not hasattr(mw, "enabled")
    assert not hasattr(mw, "all_skills")

    # switches absent + non-empty ids => those ids are the load set.
    mw2 = AgentMiddleware({"skill_ids": ["x"]})
    assert mw2.skill_ids == ["x"]

    # enabled=false can no longer suppress a non-empty list.
    mw3 = AgentMiddleware({"enabled": False, "skill_ids": ["y"]})
    assert mw3.skill_ids == ["y"]


def test_supervisor_normalizer_keeps_ids_even_when_all_skills_true():
    data = {"skills": {"all_skills": True, "enabled": True,
                       "skill_ids": [{"id": "a"}, "b", None, 123]}}
    out = SupervisorConfig._coerce_skill_ids(data)
    assert out["skills"]["skill_ids"] == ["a", "b"]


def test_supervisor_normalizer_plain_strings():
    data = {"skills": {"skill_ids": ["a", "b"]}}
    out = SupervisorConfig._coerce_skill_ids(data)
    assert out["skills"]["skill_ids"] == ["a", "b"]
