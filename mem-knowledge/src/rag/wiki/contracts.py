"""Strict model outputs. Handles are validated against each call's own input."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .ontology import OntologyType


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TypeEvidence(Output):
    source_ref: str
    quote: str = Field(min_length=1, max_length=300)


class Candidate(Output):
    semantic_type: OntologyType
    type_evidence: list[TypeEvidence] = Field(min_length=1, max_length=4)
    name: str = Field(min_length=1, max_length=400)
    aliases: list[str]
    scope: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class LocalIdentityDecision(Output):
    decision: Literal["same_object", "different_objects", "insufficient"]
    preferred: Literal["left", "right"] | None = None
    left_label: str | None = Field(default=None, max_length=70)
    right_label: str | None = Field(default=None, max_length=70)


class IdentityEvidenceReview(LocalIdentityDecision):
    evidence_quote: str | None = Field(default=None, max_length=300)


class Candidates(Output):
    candidates: list[Candidate]


class Fact(Output):
    text: str = Field(min_length=1)
    qualifiers: list[str]
    source_refs: list[str] = Field(min_length=1)


class SummaryPoint(Output):
    text: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class DocumentSummary(Output):
    summary_line: str = Field(min_length=1)
    summary_markdown: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class TopicMaterials(Output):
    topic_ref: str
    facts: list[Fact]


class Materials(Output):
    topics: list[TopicMaterials]
    summary_points: list[SummaryPoint]
    document_summary: DocumentSummary | None


class PageContent(Output):
    summary: str = Field(min_length=1)
    content_markdown: str = Field(min_length=1)
    used_fact_refs: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class CompactItem(Output):
    text: str = Field(min_length=1)
    qualifiers: list[str]
    source_item_refs: list[str] = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)


class Compacted(Output):
    items: list[CompactItem]
    covered_item_refs: list[str]
    omitted_item_refs: list[str]


class TopicCitations(Output):
    topic_ref: str
    source_refs: list[str]


class Citations(Output):
    topics: list[TopicCitations]
    new_candidates: list[Candidate]


class SummaryPoints(Output):
    summary_points: list[SummaryPoint]


class IdentityMatch(Output):
    candidate_ref: str
    page_ref: str | None


class IdentityMatches(Output):
    matches: list[IdentityMatch]


class FolderAssignment(Output):
    topic_ref: str
    category_path: list[str] = Field(max_length=2)


class FolderAssignments(Output):
    assignments: list[FolderAssignment]


class EvidencePage(Output):
    summary: str = Field(min_length=1)
    content_markdown: str = Field(min_length=1)
    source_refs: list[str] = Field(min_length=1)
