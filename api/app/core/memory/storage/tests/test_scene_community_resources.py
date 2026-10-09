from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.memory.scene.scene_community_resources import (
    PROMPT_DIR,
    load_scene_community_ontology,
)
from app.schemas.scene_community_schema import (
    CommunityJudgeOutput,
    SceneOntologyRouteOutput,
    SceneValueSummaryOutput,
)


def test_runtime_ontology_has_unique_other_and_boundaries():
    ontology = load_scene_community_ontology()
    codes = [item["code"] for item in ontology.available_categories]

    assert len(codes) == len(set(codes))
    assert codes.count("other") == 1
    assert "community_boundary" not in ontology.available_categories[0]
    assert set(ontology.category_boundary("other")) == {
        "purpose",
        "instance_axes",
        "internal_segments",
        "split_signals",
    }


def test_all_four_runtime_prompts_are_packaged():
    assert {path.name for path in Path(PROMPT_DIR).glob("*.jinja2")} == {
        "p1v_scene_value_summary_v1.jinja2",
        "o1_scene_ontology_route_v1.jinja2",
        "p2_community_judge_v2.jinja2",
        "p3_community_boundary_summary_v2.jinja2",
    }


def test_p1v_contract_enforces_decision_field_combinations():
    with pytest.raises(ValidationError):
        SceneValueSummaryOutput(
            decision="NOT_ELIGIBLE",
            topic_scope="must be null",
            summary="still retained",
            reason_code="PHATIC_ONLY",
            short_reason="greeting",
            confidence="HIGH",
        )


def test_o1_other_and_p2_candidate_contracts_are_strict():
    with pytest.raises(ValidationError):
        SceneOntologyRouteOutput(
            category_l1="other",
            reason_code="CLEAR_MAIN_CATEGORY",
            short_reason="invalid pair",
            confidence="LOW",
        )
    with pytest.raises(ValidationError):
        CommunityJudgeOutput(
            decision="CREATE_NEW",
            target_community_id="invented",
            reason_code="NO_MATCHING_INSTANCE",
            short_reason="invalid target",
            confidence="LOW",
        )
