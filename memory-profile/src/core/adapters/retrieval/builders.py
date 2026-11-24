"""Content/data builders for retrieved graph nodes.

Ported from ``api/app/core/memory/read_services/search_engine/result_builder.py``.
Each builder turns one raw storage record into the pair the domain model needs:
``data`` (the public payload) and ``content`` (the model-facing XML-ish block).
``score`` is the fused score stored on the record, with the perceptual bonus the
host applies (``min(score * 1.1, 1)``).
"""

from __future__ import annotations

from src.core.adapters.retrieval.metadata import (
    drop_deleted_scene_entries,
    normalize_new_metadata,
    project_domain_names,
    project_l0_profile,
)
from src.core.domain.enums import MemorySource


class BaseBuilder:
    def __init__(self, record: dict) -> None:
        self.record = record

    @property
    def data(self) -> dict:
        raise NotImplementedError

    @property
    def content(self) -> str:
        raise NotImplementedError

    @property
    def score(self) -> float:
        return self.record.get("content_score", 0.0) or 0.0

    @property
    def id(self) -> str:
        return self.record.get("id")


class ChunkBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
        }

    @property
    def content(self) -> str:
        parts = ["<chunk>\n"]
        value = self.record.get("content", "")
        if value:
            parts.append(f"<content>{value}</content>\n")
        parts.append("</chunk>\n")
        return "".join(parts)


class StatementBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("statement"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
            "chunk_id": self.record.get("chunk_id", ""),
        }

    @property
    def content(self) -> str:
        parts = ["<statement>\n"]
        value = self.record.get("statement", "")
        if value:
            parts.append(f"<content>{value}</content>\n")
        parts.append("</statement>\n")
        return "".join(parts)


class EntityBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "name": self.record.get("name"),
            "aliases_name": self.record.get("aliases", []),
            # The retrieval Cypher already returns entity_type; it identifies the
            # profile root node without relying on list position.
            "entity_type": self.record.get("entity_type"),
            "description": self.record.get("description"),
            "description_summary": self.record.get("description_summary"),
            "event_timeline": self.record.get("event_timeline"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
        }

    @property
    def content(self) -> str:
        parts = ["<entity>\n"]
        fields = [
            ("name", self.record.get("name", "")),
            ("aliases-name", self.record.get("aliases", [])),
            (
                "description",
                (self.record.get("description", "") or "")
                + (self.record.get("description_summary", "") or ""),
            ),
            ("event_timeline", self.record.get("event_timeline") or ""),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>\n")
        parts.append("</entity>\n")
        return "".join(parts)


class SummaryBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
            "chunk_ids": self.record.get("chunk_ids", []),
        }

    @property
    def content(self) -> str:
        parts = ["<summary>\n"]
        value = self.record.get("content", "")
        if value:
            parts.append(f"<content>{value}</content>\n")
        parts.append("</summary>\n")
        return "".join(parts)


class PerceptualBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id", ""),
            "perceptual_type": self.record.get("perceptual_type", ""),
            "file_name": self.record.get("file_name", ""),
            "file_path": self.record.get("file_path", ""),
            "summary": self.record.get("summary", ""),
            "topic": self.record.get("topic", ""),
            "domain": self.record.get("domain", ""),
            "keywords": self.record.get("keywords", []),
            "created_at": str(self.record.get("created_at", "")),
            "file_type": self.record.get("file_type", ""),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
        }

    @property
    def content(self) -> str:
        parts = ["<history-file-input>\n"]
        fields = [
            ("file-name", self.record.get("file_name", "")),
            ("file-path", self.record.get("file_path", "")),
            ("summary", self.record.get("summary", "")),
            ("topic", self.record.get("topic", "")),
            ("domain", self.record.get("domain", "")),
            ("keywords", self.record.get("keywords", [])),
            ("file-type", self.record.get("file_type", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>\n")
        parts.append("</history-file-input>\n")
        return "".join(parts)

    @property
    def score(self) -> float:
        return min(super().score * 1.1, 1)


class CommunityBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
        }

    @property
    def content(self) -> str:
        parts = ["<community>\n"]
        value = self.record.get("content", "")
        if value:
            parts.append(f"<content>{value}</content>\n")
        parts.append("</community>\n")
        return "".join(parts)


class DialogueBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0),
        }

    @property
    def content(self) -> str:
        parts = ["<dialogue>\n"]
        value = self.record.get("content", "")
        if value:
            parts.append(f"<content>{value}</content>\n")
        parts.append("</dialogue>\n")
        return "".join(parts)


class MetadataBuilder(BaseBuilder):
    """The L0 user profile item."""

    def __init__(self, record: dict, scene_names_by_id: dict[str, str] | None = None) -> None:
        super().__init__(record)
        self.scene_names_by_id = scene_names_by_id

    @property
    def data(self) -> dict:
        new_meta = normalize_new_metadata(self.record)
        return {
            "id": self.record.get("id", ""),
            "aliases_name": self.record.get("aliases", []) or [],
            "description": self.record.get("description", ""),
            "anchors": self.record.get("anchors", []) or [],
            "beliefs_or_stances": self.record.get("beliefs_or_stances", []) or [],
            "core_facts": self.record.get("core_facts", []) or [],
            "event_timeline": self.record.get("event_timeline", []) or [],
            "goals": self.record.get("goals", []) or [],
            "interests": self.record.get("interests", []) or [],
            "relations": self.record.get("relations", []) or [],
            "traits": self.record.get("traits", []) or [],
            "identity_background": new_meta["identity_background"],
            "relationships": new_meta["relationships"],
            "domain_specific_info": project_domain_names(
                drop_deleted_scene_entries(
                    new_meta["domain_specific_info"],
                    self.scene_names_by_id,
                ),
                self.scene_names_by_id,
            ),
        }

    @property
    def content(self) -> str:
        parts = ["<user-info>\n"]
        fields: list[tuple[str, object]] = [
            ("aliases-name", self.record.get("aliases", [])),
            ("anchors", self.record.get("anchors", [])),
            ("beliefs-or-stances", self.record.get("beliefs_or_stances", [])),
            ("core-facts", self.record.get("core_facts", [])),
            ("event_timeline", self.record.get("event_timeline", [])),
            ("goals", self.record.get("goals", [])),
            ("interests", self.record.get("interests", [])),
            ("relations", self.record.get("relations", [])),
        ]
        # Thirteen generic keys: complete three-part entries grouped by level-2 key.
        fields.extend(
            (key.replace("_", "-"), entries)
            for key, entries in project_l0_profile(self.record).items()
        )
        domain_specific_info = {
            scene: items
            for scene, items in project_domain_names(
                drop_deleted_scene_entries(
                    self.record.get("domain_specific_info"),
                    self.scene_names_by_id,
                ),
                self.scene_names_by_id,
            ).items()
            if items
        }
        fields.append(("domain-specific-info", domain_specific_info))
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>\n")
        parts.append("</user-info>\n")
        return "".join(parts)


_BUILDERS: dict[str, type[BaseBuilder]] = {
    MemorySource.STATEMENT.value: StatementBuilder,
    MemorySource.CHUNK.value: ChunkBuilder,
    MemorySource.EXTRACTED_ENTITY.value: EntityBuilder,
    MemorySource.MEMORY_SUMMARY.value: SummaryBuilder,
    MemorySource.PERCEPTUAL.value: PerceptualBuilder,
    MemorySource.COMMUNITY.value: CommunityBuilder,
    MemorySource.DIALOGUE.value: DialogueBuilder,
}


def data_builder_factory(source: str, data: dict) -> BaseBuilder:
    """Builder for one node family (host ``data_builder_factory``)."""

    try:
        builder = _BUILDERS[source]
    except KeyError:
        raise KeyError(f"Unknown node_type: {source}") from None
    return builder(data)


def build_relation_content(source: str, relation: str, target: str, target_desc: str) -> str:
    """Render one relation as the host's ``<relationship>`` block."""

    parts = ["<relationship>\n"]
    for tag, value in (
        ("source", source),
        ("relation", relation),
        ("target", target),
        ("target_desc", target_desc),
    ):
        if value:
            parts.append(f"<{tag}>{value}</{tag}>\n")
    parts.append("</relationship>\n")
    return "".join(parts)


__all__ = [
    "BaseBuilder",
    "ChunkBuilder",
    "CommunityBuilder",
    "DialogueBuilder",
    "EntityBuilder",
    "MetadataBuilder",
    "PerceptualBuilder",
    "StatementBuilder",
    "SummaryBuilder",
    "build_relation_content",
    "data_builder_factory",
]