from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.common.responses import ApiResponse

UNASSIGNED_ID = "virtual:scene-community:unassigned"
TIME_FIELDS = ("started_at", "ended_at", "created_at", "updated_at")
Milliseconds = Annotated[
    int,
    Field(
        ge=1_000_000_000_000,
        le=9_999_999_999_999,
        description="毫秒级时间戳（13 位整数）",
        json_schema_extra={
            "format": "int64",
            "x-apifox-mock": "@integer(1609459200000, 1893456000000)",
        },
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Times(StrictModel):
    started_at: Milliseconds | None = None
    ended_at: Milliseconds | None = None
    created_at: Milliseconds | None = None
    updated_at: Milliseconds | None = None


class CommunityProperties(Times):
    category_l1: str | None = None
    summary: str | None = None
    member_count: int = Field(ge=0)
    boundary_instance_anchor: str | None = None
    boundary_lifecycle_anchor: str | None = None
    boundary_primary_matter: str | None = None
    boundary_include_rule: str | None = None
    boundary_exclude_rule: str | None = None


class NamedCommunityProperties(CommunityProperties):
    topic_name: str


class ScopedCommunityProperties(CommunityProperties):
    topic_scope: str | None = None


class SceneProperties(Times):
    content: str
    topic_scope: str | None = None
    conversation_id: str | None = None
    turn_count: int | None = Field(default=None, ge=0)
    close_reason: str | None = None
    community_eligibility: str | None = None
    community_category_l1: str | None = None
    community_status: str | None = None
    scene_community_id: str | None = None


# Query both stored topic fields; response models expose exactly one of them.
COMMUNITY_FIELDS = (*CommunityProperties.model_fields, "topic_name", "topic_scope")
SCENE_FIELDS = tuple(SceneProperties.model_fields)


def validate_business_id(value: str) -> str:
    if not value or value != value.strip() or value.startswith("virtual:"):
        raise ValueError("invalid business ID")
    return value


class Scene(StrictModel):
    id: str
    label: Literal["SceneSummary"] = "SceneSummary"
    properties: SceneProperties
    unassigned_reason: Literal["NO_PARENT", "INVALID_PARENT_ID", "PARENT_NOT_FOUND"] | None = None

    _valid_id = field_validator("id")(validate_business_id)


class CommunityGroup(StrictModel):
    id: str
    label: Literal["SceneCommunity"] = "SceneCommunity"
    kind: Literal["COMMUNITY"] = "COMMUNITY"
    properties: NamedCommunityProperties | ScopedCommunityProperties
    scene_count: int = Field(ge=0)
    scenes: list[Scene]

    _valid_id = field_validator("id")(validate_business_id)


class UnassignedGroup(StrictModel):
    id: Literal["virtual:scene-community:unassigned"] = UNASSIGNED_ID
    label: Literal["UnassignedSceneGroup"] = "UnassignedSceneGroup"
    kind: Literal["UNASSIGNED"] = "UNASSIGNED"
    properties: ScopedCommunityProperties
    scene_count: int = Field(ge=1)
    scenes: list[Scene] = Field(min_length=1)

    @model_validator(mode="after")
    def unassigned_properties(self) -> Self:
        props = self.properties.model_dump()
        if any(value is not None for key, value in props.items() if key != "member_count"):
            raise ValueError("virtual group must not contain real community properties")
        if self.properties.member_count != self.scene_count:
            raise ValueError("virtual member_count mismatch")
        return self


Group = Annotated[CommunityGroup | UnassignedGroup, Field(discriminator="kind")]


class Statistics(StrictModel):
    total_scene_communities: int = Field(ge=0)
    total_scene_summaries: int = Field(ge=0)
    assigned_scene_summaries: int = Field(ge=0)
    unassigned_scene_summaries: int = Field(ge=0)
    total_groups: int = Field(ge=0)


class GraphData(StrictModel):
    groups: list[Group]
    statistics: Statistics

    @model_validator(mode="after")
    def check_invariants(self) -> Self:
        ids = [g.id for g in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate group ID")
        communities = {g.id for g in self.groups if g.kind == "COMMUNITY"}
        seen: set[str] = set()
        assigned = unassigned = 0
        for index, group in enumerate(self.groups):
            if group.scene_count != len(group.scenes):
                raise ValueError("scene_count mismatch")
            if group.kind == "UNASSIGNED" and index != len(self.groups) - 1:
                raise ValueError("virtual group must be last")
            for scene in group.scenes:
                if scene.id in seen:
                    raise ValueError("duplicate scene ID")
                seen.add(scene.id)
                parent = scene.properties.scene_community_id
                if group.kind == "UNASSIGNED":
                    unassigned += 1
                    if scene.unassigned_reason is None or parent in communities:
                        raise ValueError("invalid unassigned scene")
                    if scene.unassigned_reason == "PARENT_NOT_FOUND" and not parent:
                        raise ValueError("dangling key must be nonblank")
                    if scene.unassigned_reason != "PARENT_NOT_FOUND" and parent is not None:
                        raise ValueError("empty/invalid parent must serialize as null")
                else:
                    assigned += 1
                    if parent != group.id or scene.unassigned_reason is not None:
                        raise ValueError("scene parent mismatch")
        expected = Statistics(
            total_scene_communities=len(communities),
            total_scene_summaries=len(seen),
            assigned_scene_summaries=assigned,
            unassigned_scene_summaries=unassigned,
            total_groups=len(self.groups),
        )
        if self.statistics != expected:
            raise ValueError("statistics mismatch")
        return self


class GraphResponse(ApiResponse[GraphData]):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: Literal[0] = 0
    msg: Literal["OK"] = "OK"
    data: GraphData
    error: Literal[""] = ""
    time: Milliseconds


class ErrorResponse(StrictModel):
    code: int = Field(ge=1)
    msg: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str
    time: Milliseconds


SCENE_GRAPH_ERROR_RESPONSES = {
    **{status: {"model": ErrorResponse} for status in (400, 500, 503)},
    **{
        status: {"description": "Existing authentication/workspace error"}
        for status in (401, 403, 404)
    },
    200: {
        "model": GraphResponse | ErrorResponse,
        "description": "Check code: USER_NOT_FOUND is HTTP 200 / code 4001.",
    },
}
