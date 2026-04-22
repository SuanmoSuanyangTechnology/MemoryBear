from abc import ABC, abstractmethod
from typing import TypeVar

from app.core.memory.enums import Neo4jNodeType


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
        return self.record.get("content")


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
        return self.record.get("statement")


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
        return (f"<entity>"
                f"<name>{self.record.get("name")}<name>"
                f"<description>{self.record.get("description")}</description>"
                f"</entity>")


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
        return self.record.get("content")


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
        return ("<history-file-info>"
                f"<file-name>{self.record.get('file_name')}</file-name>"
                f"<file-path>{self.record.get('file_path')}</file-path>"
                f"<summary>{self.record.get('summary')}</summary>"
                f"<topic>{self.record.get('topic')}</topic>"
                f"<domain>{self.record.get('domain')}</domain>"
                f"<keywords>{self.record.get('keywords')}</keywords>"
                f"<file-type>{self.record.get('file_type')}</file-type>"
                "</history-file-info>")


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
        return self.record.get("content")


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
