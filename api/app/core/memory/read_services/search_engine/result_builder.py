from abc import ABC, abstractmethod
from typing import TypeVar

from app.core.memory.enums import Neo4jNodeType
from app.core.memory.models.service_models import RelationMemory


class BaseBuilder(ABC):
    def __init__(self, records: dict):
        self.record = records

    @property
    @abstractmethod
    def data(self) -> dict:
        pass

    @property
    @abstractmethod
    def content(self) -> str:
        pass

    @property
    def score(self) -> float:
        return self.record.get("content_score", 0.0) or 0.0

    @property
    def id(self) -> str:
        return self.record.get("id")


T = TypeVar("T", bound=BaseBuilder)


class ChunkBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<chunk>"]
        fields = [
            ("content", self.record.get("content", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</chunk>")
        return "".join(parts)


class StatementBuiler(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("statement"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<statement>"]
        fields = [
            ("statement", self.record.get("statement", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</statement>")
        return "".join(parts)


class EntityBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "name": self.record.get("name"),
            "description": self.record.get("description"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<entity>"]
        fields = [
            ("name", self.record.get("name", "")),
            ("description", self.record.get("description", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</entity>")
        return "".join(parts)


class SummaryBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<summary>"]
        fields = [
            ("content", self.record.get("content", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</summary>")
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
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<history-file-input>"]
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
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</history-file-input>")
        return "".join(parts)


class CommunityBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id"),
            "content": self.record.get("content"),
            "kw_score": self.record.get("kw_score", 0.0),
            "emb_score": self.record.get("embedding_score", 0.0)
        }

    @property
    def content(self) -> str:
        parts = ["<community>"]
        fields = [
            ("content", self.record.get("content", "")),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</community>")
        return "".join(parts)


class MetadataBuilder(BaseBuilder):
    @property
    def data(self) -> dict:
        return {
            "id": self.record.get("id", ""),
            "aliases_name": self.record.get("aliases", []) or [],
            "description": self.record.get("description", ""),
            "anchors": self.record.get("anchors", []) or [],
            "beliefs_or_stances": self.record.get("beliefs_or_stances", []) or [],
            "core_facts": self.record.get("core_facts", []) or [],
            "events": self.record.get("events", []) or [],
            "goals": self.record.get("goals", []) or [],
            "interests": self.record.get("interests", []) or [],
            "relations": self.record.get("relations", []) or [],
            "traits": self.record.get("traits", []) or [],
        }

    @property
    def content(self) -> str:
        parts = ["<user-info>"]
        fields = [
            # ("description", self.record.get("description", "")),
            ("aliases", self.record.get("aliases", [])),
            ("anchors", self.record.get("anchors", [])),
            ("beliefs_or_stances", self.record.get("beliefs_or_stances", [])),
            ("core_facts", self.record.get("core_facts", [])),
            ("events", self.record.get("events", [])),
            ("goals", self.record.get("goals", [])),
            ("interests", self.record.get("interests", [])),
            ("relations", self.record.get("relations", [])),
            ("traits", self.record.get("traits", [])),
        ]
        for tag, value in fields:
            if value:
                parts.append(f"<{tag}>{value}</{tag}>")
        parts.append("</user-info>")
        return "".join(parts)


def data_builder_factory(node_type, data: dict) -> T:
    match node_type:
        case Neo4jNodeType.STATEMENT:
            return StatementBuiler(data)
        case Neo4jNodeType.CHUNK:
            return ChunkBuilder(data)
        case Neo4jNodeType.EXTRACTEDENTITY:
            return EntityBuilder(data)
        case Neo4jNodeType.MEMORYSUMMARY:
            return SummaryBuilder(data)
        case Neo4jNodeType.PERCEPTUAL:
            return PerceptualBuilder(data)
        case Neo4jNodeType.COMMUNITY:
            return CommunityBuilder(data)
        case _:
            raise KeyError(f"Unknown node_type: {node_type}")


def build_relation_content(rel: RelationMemory) -> str:
    parts = ["<relationship>"]
    fields = [
        ("source", rel.source),
        ("relation", rel.relation),
        ("target", rel.target),
        ("target_desc", rel.target_desc),
    ]
    for tag, value in fields:
        if value:
            parts.append(f"<{tag}>{value}</{tag}>")
    parts.append("</relationship>")
    return "".join(parts)
