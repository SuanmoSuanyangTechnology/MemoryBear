from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MemoryStatement(BaseModel):
    id: str
    text: str
    stmt_type: str = "FACT"
    speaker: str | None = None
    emotion_type: str | None = None
    emotion_intensity: float | None = None
    dialog_at: str | None = None
    valid_at: str | None = None
    entity_ids: list[str] = Field(default_factory=list)
    entity_names: list[str] = Field(default_factory=list)


class EntityRecord(BaseModel):
    id: str
    name: str
    entity_type: str | None = None
    description: str = ""
    example: str = ""
    aliases: list[str] = Field(default_factory=list)
    ref_count: int = 0


class RelationRecord(BaseModel):
    source_id: str
    source_name: str
    source_type: str | None = None
    predicate: str
    other_id: str
    other_name: str
    other_type: str | None = None
    evidence: str = ""
    valid_at: str | None = None


class UserProfile(BaseModel):
    entity_id: str
    name: str
    core_facts: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    relations: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    beliefs_or_stances: list[str] = Field(default_factory=list)
    anchors: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)

    def is_usable(self) -> bool:
        return bool(self.core_facts or self.traits or self.goals or self.events)


class QueryTerms(BaseModel):
    core_terms: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)

    def all_terms(self) -> list[str]:
        return list(dict.fromkeys([*self.core_terms, *self.terms]))


class EvidenceGate(BaseModel):
    has_topical_evidence: bool
    covered_aspects: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    reason: str = ""
    topical_statement_ids: list[str] = Field(default_factory=list)


AgencyKind = Literal["actor", "environment", "concept"]
Relevance = Literal["high", "medium", "low", "none"]


class AgencyVerdict(BaseModel):
    name: str
    kind: AgencyKind
    relevance: Relevance = "none"
    reason: str = ""
    is_vague_collective: bool = False
    has_representative: bool = False

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> str:
        text = str(value).strip().lower()
        return text if text in {"actor", "environment", "concept"} else "concept"

    @field_validator("relevance", mode="before")
    @classmethod
    def normalize_relevance(cls, value: object) -> str:
        text = str(value).strip().lower()
        return text if text in {"high", "medium", "low", "none"} else "none"


class AgencyBatch(BaseModel):
    verdicts: list[AgencyVerdict] = Field(default_factory=list)


class WorldCast(BaseModel):
    actors: list[EntityRecord] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)
    verdicts: list[AgencyVerdict] = Field(default_factory=list)
    insufficiency_reason: str = ""


class AgentCardDraft(BaseModel):
    role: str = ""
    goals: list[str] = Field(default_factory=list)
    stance: list[str] = Field(default_factory=list)
    attitude_to_user: str = ""
    behavior_tendency: list[str] = Field(default_factory=list)
    speaking_style: str = ""
    source_statement_ids: list[str] = Field(default_factory=list)


class AgentCard(BaseModel):
    entity_id: str
    name: str
    entity_type: str | None = None
    role: str = ""
    goals: list[str] = Field(default_factory=list)
    stance: list[str] = Field(default_factory=list)
    attitude_to_user: str = ""
    behavior_tendency: list[str] = Field(default_factory=list)
    speaking_style: str = ""
    recent_timeline: list[str] = Field(default_factory=list)
    source_statement_ids: list[str] = Field(default_factory=list)
    perspective_caveat: str = ""
    is_protagonist: bool = False
    agent_kind: AgencyKind = "actor"
    activity: float = 0.5
    response_delay: Literal["immediate", "short", "medium", "long"] = "medium"
    influence_weight: float = 0.5
    evolution_role: str = "状态传播"
    response_policy: str = "按状态变化传播"
    relation_effects: list[str] = Field(default_factory=list)
    configuration_basis: list[str] = Field(default_factory=list)


ResponseType = Literal[
    "advance", "request_more", "delay", "raise_concern", "decline", "support"
]


class ActionDraft(BaseModel):
    response_type: ResponseType
    action: str
    rationale: str = ""
    grounded_on: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)


class StopDraft(BaseModel):
    should_stop: bool
    reason: str = ""


class SimEvent(BaseModel):
    turn: int
    actor: str
    response_type: ResponseType = "advance"
    action: str
    low_grounding: bool = False
    rationale: str = ""
    grounded_on: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)


class TurnRecord(BaseModel):
    turn: int
    title: str = ""
    time_window: str = ""
    events: list[SimEvent] = Field(default_factory=list)
    recalled: dict[str, list[str]] = Field(default_factory=dict)
    environment_event: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    main_agent_action: str = ""
    state_changes: list[str] = Field(default_factory=list)
    graph_state_delta: list[str] = Field(default_factory=list)
    state_propagations: list[str] = Field(default_factory=list)
    propagation_summary: str = ""
    focus_agent_names: list[str] = Field(default_factory=list)
    temporary_memory: str = ""
    entity_state_updates: int = 0
    should_stop: bool = False
    stop_reason: str = ""


class WorldState(BaseModel):
    end_user_id: str
    question: str
    as_of: str | None = None
    prediction_deadline: str
    protagonist: AgentCard
    others: list[AgentCard] = Field(default_factory=list)
    environment: list[str] = Field(default_factory=list)
    turns: list[TurnRecord] = Field(default_factory=list)

    def all_agents(self) -> list[AgentCard]:
        return [self.protagonist, *self.others]

    def event_log(self) -> list[SimEvent]:
        return [event for turn in self.turns for event in turn.events]


class Prediction(BaseModel):
    statement: str
    llm_self_rated_confidence: Literal["high", "medium", "low"]
    grounded_on: list[str] = Field(default_factory=list)
    simulation_artifacts: list[str] = Field(default_factory=list)
    reasoning: str = ""


class ReportDraft(BaseModel):
    evidence_sufficient: bool
    insufficiency_reason: str = ""
    headline: str = ""
    predictions: list[Prediction] = Field(default_factory=list)
    divergence_points: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class PredictionReport(BaseModel):
    question: str
    end_user_id: str
    as_of: str | None = None
    prediction_deadline: str | None = None
    evidence_sufficient: bool = True
    insufficiency_reason: str = ""
    headline: str = ""
    predictions: list[Prediction] = Field(default_factory=list)
    divergence_points: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class MemorySeedItem(BaseModel):
    id: str
    memory_type: str
    title: str
    occurred_at: str | None = None
    content: str
    score: float
    selected: bool
    recency: float
    importance: float
    relevance: float
    graph_relevance: float
    lexical_relevance: float


class MemorySeedTitle(BaseModel):
    id: str
    title: str


class MemorySeedTitles(BaseModel):
    items: list[MemorySeedTitle] = Field(default_factory=list)


class MemorySeedStage(BaseModel):
    candidate_count: int
    selected_count: int
    excluded_count: int
    coverage_rate: float
    strategy: str
    score_formula: str
    relevance_formula: str
    items: list[MemorySeedItem] = Field(default_factory=list)


class StateGraphEntity(BaseModel):
    id: str
    name: str
    entity_type: str | None = None


class StateRelation(BaseModel):
    source: str
    predicate: str
    target: str
    evidence: str = ""


class GroundedStateItem(BaseModel):
    text: str
    statement_ids: list[str] = Field(default_factory=list)


class StateGraphDraft(BaseModel):
    behavior_path: list[GroundedStateItem] = Field(default_factory=list)
    hard_constraints: list[GroundedStateItem] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)


class StateGraphStage(BaseModel):
    entity_count: int
    state_relation_count: int
    hard_constraint_count: int
    information_gap_count: int
    main_entity: StateGraphEntity
    target_node: GroundedStateItem
    prediction_deadline: str
    entity_type_counts: dict[str, int] = Field(default_factory=dict)
    entities: list[StateGraphEntity] = Field(default_factory=list)
    behavior_path: list[GroundedStateItem] = Field(default_factory=list)
    state_relations: list[StateRelation] = Field(default_factory=list)
    hard_constraints: list[GroundedStateItem] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    graph_rules: list[str] = Field(default_factory=list)


class EnvironmentStage(BaseModel):
    max_rounds: int
    parallel_branches: int = 2
    entity_agent_count: int
    convergence_window: int = 2
    prediction_deadline: str
    simulation_object: str
    time_mapping: str
    branches: list[str] = Field(default_factory=list)
    scheduling_rule: str
    convergence_rule: str


class AgentProfilesStage(BaseModel):
    total_agents: int
    responsive_agents: int
    state_agents: int
    agents: list[AgentCard] = Field(default_factory=list)
    boundary: str


class InitialVariablesStage(BaseModel):
    prediction_request: str
    initial_event: str
    participant_count: int
    participation_scope: str = ""
    resources: list[str] = Field(default_factory=list)
    exogenous_variables: list[str] = Field(default_factory=list)
    memory_isolation: str


class TemporalMemoryRecord(BaseModel):
    turn: int
    time_window: str
    summary: str
    entity_state_updates: int
    grounded_on: list[str] = Field(default_factory=list)


class TemporalMemoryStage(BaseModel):
    time_slice_count: int
    entity_state_update_count: int
    temporary_memory_count: int
    history_write_count: int = 0
    sandbox_id: str
    records: list[TemporalMemoryRecord] = Field(default_factory=list)


class ReportAgentStage(BaseModel):
    planned_sections: list[str] = Field(default_factory=list)
    retrieval_tools: list[str] = Field(default_factory=list)
    simulation_fact_references: int
    commonsense_additions: int = 0
    execution_steps: list[str] = Field(default_factory=list)
