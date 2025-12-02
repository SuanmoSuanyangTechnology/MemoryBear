from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.core.rag.models.chunk import DocumentChunk


class BaseVector(ABC):
    def __init__(self, collection_name: str):
        self._collection_name = collection_name

    @abstractmethod
    def get_type(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def create(self, chunks: list[DocumentChunk], embeddings: list[list[float]], **kwargs):
        raise NotImplementedError

    @abstractmethod
    def add_texts(self, chunks: list[DocumentChunk], embeddings: list[list[float]], **kwargs):
        raise NotImplementedError

    @abstractmethod
    def text_exists(self, id: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete_by_ids(self, ids: list[str]):
        raise NotImplementedError

    def get_ids_by_metadata_field(self, key: str, value: str):
        raise NotImplementedError

    @abstractmethod
    def delete_by_metadata_field(self, key: str, value: str):
        raise NotImplementedError

    @abstractmethod
    def search_by_vector(self, query: str, **kwargs: Any) -> list[DocumentChunk]:
        raise NotImplementedError

    @abstractmethod
    def search_by_full_text(self, query: str, **kwargs: Any) -> list[DocumentChunk]:
        raise NotImplementedError

    @abstractmethod
    def delete(self):
        raise NotImplementedError

    def _filter_duplicate_texts(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        for chunk in chunks.copy():
            if chunk.metadata and "doc_id" in chunk.metadata:
                doc_id = chunk.metadata["doc_id"]
                exists_duplicate_node = self.text_exists(doc_id)
                if exists_duplicate_node:
                    chunks.remove(chunk)

        return chunks

    def _get_uuids(self, chunks: list[DocumentChunk]) -> list[str]:
        return [chunk.metadata["doc_id"] for chunk in chunks if chunk.metadata and "doc_id" in chunk.metadata]

    @property
    def collection_name(self):
        return self._collection_name
