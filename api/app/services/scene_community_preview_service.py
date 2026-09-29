"""Pure, deterministic SceneCommunity configuration preview service."""

from copy import deepcopy

from app.schemas.scene_community_schema import (
    CommunityPreviewCaseItem,
    CommunityPreviewResult,
)
from app.services.scene_community_preview_cases import (
    CANDIDATE_COMMUNITIES_BY_LOCALE,
    PREVIEW_CANDIDATE_TEXT_BY_LOCALE,
    PREVIEW_CASE_DATA_BY_LOCALE,
    PREVIEW_CASES_BY_LOCALE,
    PREVIEW_GRAPH_TEXT_BY_LOCALE,
)


class UnsupportedPreviewCaseError(ValueError):
    """Raised when a caller requests a preview fixture that does not exist."""


class SceneCommunityPreviewService:
    """Build preview responses without models, queues, or persistence writes."""

    @staticmethod
    def list_cases(locale: str = "zh") -> list[dict]:
        cases = PREVIEW_CASES_BY_LOCALE["en" if locale == "en" else "zh"]
        return [
            CommunityPreviewCaseItem.model_validate(item).model_dump(mode="json")
            for item in cases
        ]

    @staticmethod
    def _serialize(result: dict) -> dict:
        data = CommunityPreviewResult.model_validate(result).model_dump(mode="json")
        if data["candidate_communities"]["empty_text"] is None:
            data["candidate_communities"].pop("empty_text")
        return data

    @staticmethod
    def build(
        preview_case: str,
        candidate_community_limit: int,
        locale: str = "zh",
    ) -> dict:
        locale = "en" if locale == "en" else "zh"
        fixture = PREVIEW_CASE_DATA_BY_LOCALE[locale].get(preview_case)
        if fixture is None:
            raise UnsupportedPreviewCaseError(preview_case)
        candidate_text = PREVIEW_CANDIDATE_TEXT_BY_LOCALE[locale]
        graph_text = PREVIEW_GRAPH_TEXT_BY_LOCALE[locale]

        if preview_case == "NOT_ELIGIBLE":
            result = {
                "preview_case": preview_case,
                "display_name": fixture["display_name"],
                "input_scene": fixture["input_scene"],
                "pipeline": fixture["pipeline"],
                "classification_result": fixture["classification_result"],
                "candidate_communities": {
                    "total": 0,
                    "note": candidate_text["not_eligible"],
                    "count_text": candidate_text["not_queried"],
                    "empty_text": graph_text["empty_text"],
                    "items": [],
                },
                "community_change": fixture["community_change"],
                "graph": {
                    "default_selected_node_id": None,
                    "communities": [],
                    "caption": graph_text["empty_caption"],
                    "new_content": fixture["new_content"],
                },
            }
            return SceneCommunityPreviewService._serialize(result)

        visible_candidates = deepcopy(
            CANDIDATE_COMMUNITIES_BY_LOCALE[locale][:candidate_community_limit]
        )
        classification = fixture["classification_result"]["result"]
        candidate_items = [
            {
                "rank": candidate["rank"],
                "title": candidate["title"],
                "subtitle": candidate["subtitle"],
                "score": candidate["score"],
                "selected": classification == "ASSIGN_EXISTING"
                and candidate["rank"] == 1,
            }
            for candidate in visible_candidates
        ]
        graph_communities = [
            {
                "node_id": candidate["node_id"],
                "node_type": "COMMUNITY",
                "name": candidate["title"],
                "description": candidate["description"],
                "children": [
                    {
                        **child,
                        "node_type": "MEMORY",
                        "is_new_content": False,
                    }
                    for child in candidate["children"]
                ],
            }
            for candidate in visible_candidates
        ]
        new_content_node = {
            "node_id": fixture["new_content"]["node_id"],
            "node_type": "MEMORY",
            "name": fixture["new_content"]["node_name"],
            "description": fixture["new_content"]["description"],
            "is_new_content": True,
        }

        if preview_case == "EXISTING_COMMUNITY_UPDATE":
            graph_communities[0]["children"].append(new_content_node)
        elif preview_case == "SECOND_MEMBER_BOUNDARY":
            graph_communities[0]["children"] = [
                graph_communities[0]["children"][0],
                new_content_node,
            ]
        elif preview_case == "NEW_SINGLE_MEMBER_COMMUNITY":
            graph_communities.append(
                {
                    "node_id": fixture["new_content"][
                        "created_community_node_id"
                    ],
                    "node_type": "COMMUNITY",
                    "name": fixture["new_content"]["created_community_name"],
                    "description": fixture["created_community_description"],
                    "children": [new_content_node],
                }
            )

        result = {
            "preview_case": preview_case,
            "display_name": fixture["display_name"],
            "input_scene": fixture["input_scene"],
            "pipeline": fixture["pipeline"],
            "classification_result": fixture["classification_result"],
            "candidate_communities": {
                "total": len(candidate_items),
                "note": candidate_text["with_candidates"],
                "count_text": candidate_text["count_template"].format(
                    actual=len(candidate_items),
                    limit=candidate_community_limit,
                ),
                "items": candidate_items,
            },
            "community_change": fixture["community_change"],
            "graph": {
                "default_selected_node_id": fixture["new_content"]["node_id"],
                "communities": graph_communities,
                "caption": graph_text["caption_template"].format(
                    count=len(graph_communities)
                ),
                "new_content": fixture["new_content"],
            },
        }
        return SceneCommunityPreviewService._serialize(result)
