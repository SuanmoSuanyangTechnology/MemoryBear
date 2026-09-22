"""SceneCommunity configuration and preview API schemas."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


BatchTriggerCount = Annotated[int, Field(ge=1, le=50)]
CandidateCommunityLimit = Annotated[int, Field(ge=1, le=8)]


class SceneCommunityConfig(BaseModel):
    """Persisted SceneCommunity settings returned by both API entry points."""

    model_config = ConfigDict(from_attributes=True)

    config_id: UUID
    batch_trigger_count: BatchTriggerCount = 20
    candidate_community_limit: CandidateCommunityLimit = 3
    compare_all_same_category_communities: bool = False
    rebuild_new_scene_community_count_enabled: bool = True
    rebuild_new_scene_community_count: int = 50
    rebuild_interval_enabled: bool = True
    rebuild_interval_days: int = 1


class SceneCommunityConfigUpdate(BaseModel):
    """Full-update payload; all seven business fields are required."""

    config_id: UUID
    batch_trigger_count: BatchTriggerCount
    candidate_community_limit: CandidateCommunityLimit
    compare_all_same_category_communities: bool
    rebuild_new_scene_community_count_enabled: bool
    rebuild_new_scene_community_count: int
    rebuild_interval_enabled: bool
    rebuild_interval_days: int


CommunityPreviewCase = Literal[
    "EXISTING_COMMUNITY_UPDATE",
    "NEW_SINGLE_MEMBER_COMMUNITY",
    "SECOND_MEMBER_BOUNDARY",
    "NOT_ELIGIBLE",
]


class CommunityPreviewRequest(BaseModel):
    config_id: UUID
    # Keep this as a string so the controller can return the project's business
    # error envelope (code=1003) instead of FastAPI's default HTTP 422 body.
    preview_case: str = Field(min_length=1)


class CommunityPreviewCaseItem(BaseModel):
    preview_case: CommunityPreviewCase
    display_name: str
    sort_order: int
    is_default: bool


class CommunityPreviewInputScene(BaseModel):
    content: str
    display_label: str


class CommunityPreviewPipelineStage(BaseModel):
    stage_name: str
    status: Literal["COMPLETED", "STOPPED", "SKIPPED"]
    result_name: str


class CommunityPreviewClassification(BaseModel):
    result: Literal["ASSIGN_EXISTING", "CREATE_NEW", "NOT_ELIGIBLE"]
    title: str
    reason_label: str
    reason: str


class CommunityPreviewCandidate(BaseModel):
    rank: int
    title: str
    subtitle: str
    score: float
    selected: bool


class CommunityPreviewCandidates(BaseModel):
    total: int
    items: list[CommunityPreviewCandidate]
    empty_text: str | None = None


class CommunityPreviewChange(BaseModel):
    change_label: str
    before: str
    after: str


class CommunityPreviewGraphCommunity(BaseModel):
    name: str
    child_names: list[str]


class CommunityPreviewGraphNewContent(BaseModel):
    node_name: str
    description: str
    joined_community_name: str | None
    created_community_name: str | None


class CommunityPreviewGraph(BaseModel):
    communities: list[CommunityPreviewGraphCommunity]
    caption: str
    new_content: CommunityPreviewGraphNewContent


class CommunityPreviewResult(BaseModel):
    preview_case: CommunityPreviewCase
    display_name: str
    input_scene: CommunityPreviewInputScene
    pipeline: list[CommunityPreviewPipelineStage]
    classification_result: CommunityPreviewClassification
    candidate_communities: CommunityPreviewCandidates
    community_change: CommunityPreviewChange
    graph: CommunityPreviewGraph

    @model_validator(mode="after")
    def validate_cross_field_contract(self):
        result = self.classification_result.result
        candidates = self.candidate_communities
        new_content = self.graph.new_content
        selected_count = sum(item.selected for item in candidates.items)

        if result == "NOT_ELIGIBLE":
            if candidates.total != 0 or candidates.items or self.graph.communities:
                raise ValueError("NOT_ELIGIBLE preview cannot contain communities")
            if self.pipeline[0].status != "STOPPED" or any(
                stage.status != "SKIPPED" for stage in self.pipeline[1:]
            ):
                raise ValueError("NOT_ELIGIBLE pipeline states are inconsistent")
            if new_content.joined_community_name or new_content.created_community_name:
                raise ValueError("NOT_ELIGIBLE preview cannot have a community target")
        elif result == "ASSIGN_EXISTING":
            if selected_count != 1:
                raise ValueError("ASSIGN_EXISTING preview must select exactly one candidate")
            if not new_content.joined_community_name or new_content.created_community_name:
                raise ValueError("ASSIGN_EXISTING preview target is inconsistent")
        elif result == "CREATE_NEW":
            if selected_count != 0:
                raise ValueError("CREATE_NEW preview cannot select an existing candidate")
            if new_content.joined_community_name or not new_content.created_community_name:
                raise ValueError("CREATE_NEW preview target is inconsistent")
        return self
