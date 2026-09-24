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
    def list_cases(locale: str = "zh") -> dict:
        cases = PREVIEW_CASES_BY_LOCALE["en" if locale == "en" else "zh"]
        items = [
            CommunityPreviewCaseItem.model_validate(item).model_dump(mode="json")
            for item in cases
        ]
        return {"items": items}

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
                "name": candidate["title"],
                "child_names": candidate["child_names"],
            }
            for candidate in visible_candidates
        ]

        if preview_case == "EXISTING_COMMUNITY_UPDATE":
            graph_communities[0]["child_names"].append(
                fixture["new_content"]["node_name"]
            )
        elif preview_case == "SECOND_MEMBER_BOUNDARY":
            graph_communities[0]["child_names"] = graph_text[
                "second_member_child_names"
            ]
        elif preview_case == "NEW_SINGLE_MEMBER_COMMUNITY":
            graph_communities.append(
                {
                    "name": fixture["new_content"]["created_community_name"],
                    "child_names": [fixture["new_content"]["node_name"]],
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
                "communities": graph_communities,
                "caption": graph_text["caption_template"].format(
                    count=len(graph_communities)
                ),
                "new_content": fixture["new_content"],
            },
        }
        return SceneCommunityPreviewService._serialize(result)
