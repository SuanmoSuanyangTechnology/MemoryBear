"""Compare pure migrated functions with the API's actual implementation."""

import ast
import runpy
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.display import engine_cards

ROOT = Path(__file__).resolve().parents[2]
CASES = {
    "EXTRACTION": dict(statement_count=3, entity_count=2, relation_count=1),
    "CROSS_MODAL": dict(
        modality_counts={"vision": 2, "audio": 1},
        topics=["旅行"],
        chunk_association_count=2,
        entity_association_count=1,
    ),
    "EMOTION": dict(
        emotion_stats={
            "joy": {"count": 2, "intensity_sum": 1.5},
            "fear": {"count": 1, "intensity_sum": 0.5},
            "neutral": {"count": 10, "intensity_sum": 2},
        }
    ),
    "FORGETTING": dict(scanned_count=10, released_count=2, node_type_counts={"Statement": 2}),
    "REFLECTION": dict(entity_merged_count=2, alias_merged_count=1, metadata_extracted_count=3),
    "SCENE_SUMMARY": dict(scene_summary_id="scene-1", turn_count=4, source_message_count=5),
    "MEMORY_VALUE": dict(activity_type="PERMANENT_ADDED", permanent_added_count=2),
    "PREFERENCE": dict(added_item_count=2, updated_item_count=1, deleted_item_count=1),
    "SCENE_RECONSTRUCTION": dict(
        processed_scene_count=4, new_community_count=2, existing_community_update_count=1
    ),
    "PREDICTION": dict(prediction_count=3, round_count=2, agent_count=5, grounded_memory_count=4),
}


@pytest.fixture(scope="module")
def api_functions():
    # Compile only the API's pure functions: importing its service would initialize
    # the API host's ORM/configuration and violate the service import boundary.
    tree = ast.parse(
        (ROOT / "api/app/services/memory_engine_display_service.py").read_text(encoding="utf-8")
    )
    names = {
        "_emotion_label",
        "_as_count",
        "_generate_card_id",
        "_merge_event_details",
        "_generate_card_text",
        "_generate_card_text_zh",
        "_generate_card_text_en",
        "_rank_reflection_counts",
        "_pluralize",
        "_join_english",
        "_format_english_modality",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = dict(
        Any=Any,
        Dict=dict,
        List=list,
        defaultdict=defaultdict,
        uuid=uuid,
        datetime=datetime,
        date=date,
    )
    namespace.update(runpy.run_path(str(ROOT / "api/app/core/memory/emotion/emotion_buckets.py")))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_REFLECTION_COUNT_KEYS" for t in node.targets
        ):
            namespace["_REFLECTION_COUNT_KEYS"] = ast.literal_eval(node.value)
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *nodes,
        ],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), "<api pure card functions>", "exec"), namespace)
    return namespace


@pytest.mark.parametrize("engine_type", CASES)
@pytest.mark.parametrize("language", ["zh", "en"])
def test_aggregation_and_localization_match_api(api_functions, engine_type, language):
    now = datetime(2026, 1, 1, 12)
    events = [
        dict(details=CASES[engine_type], occurred_at=now),
        dict(details=CASES[engine_type], occurred_at=now + timedelta(hours=1)),
    ]
    merged = engine_cards._merge_event_details(engine_type, events)
    api_merged = api_functions["_merge_event_details"](
        engine_type,
        [SimpleNamespace(**event) for event in events],
    )
    assert merged == api_merged
    actual = engine_cards.generate_card_text(engine_type, merged, language)
    assert actual == api_functions["_generate_card_text"](engine_type, api_merged, language)
    assert all(actual)
    assert engine_cards._merge_event_details(engine_type, list(reversed(events))) == merged


def test_card_id_matches_api_and_is_stable(api_functions):
    params = ("user-1", "PREFERENCE", "Asia/Shanghai", date(2026, 1, 1))
    assert engine_cards._generate_card_id(*params) == api_functions["_generate_card_id"](*params)
    group = dict(
        engine_type="PREFERENCE",
        local_date=date(2026, 1, 1),
        max_occurred_at=datetime(2026, 1, 1),
        end_user_id="user-1",
        events=[dict(details=CASES["PREFERENCE"], occurred_at=datetime(2026, 1, 1))],
    )
    args = dict(groups=[group], end_user_id=None, timezone="Asia/Shanghai", language="en")
    assert engine_cards.build_cards_from_groups(**args) == engine_cards.build_cards_from_groups(
        **args
    )
