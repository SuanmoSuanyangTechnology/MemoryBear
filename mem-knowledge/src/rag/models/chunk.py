"""Chunk data models copied from the legacy RAG package."""

from pydantic import BaseModel, Field


class ChildDocumentChunk(BaseModel):
    """One child chunk and its arbitrary metadata."""

    page_content: str
    vector: list[float] | None = None
    metadata: dict = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """One retrievable document chunk."""

    page_content: str
    vector: list[float] | None = None
    metadata: dict = Field(default_factory=dict)
    children: list[ChildDocumentChunk] | None = None


def chunk_retrieval_content(chunk: DocumentChunk) -> str:
    metadata = chunk.metadata or {}
    if metadata.get("chunk_type") == "qa":
        return metadata.get("question") or chunk.page_content
    vision_text = metadata.get("vision_text")
    if isinstance(vision_text, str) and vision_text.strip():
        return vision_text
    return chunk.page_content


class GeneralStructureChunk(BaseModel):
    general_chunks: list[str]


class ParentChildChunk(BaseModel):
    parent_content: str
    child_contents: list[str]


class ParentChildStructureChunk(BaseModel):
    parent_child_chunks: list[ParentChildChunk]
    parent_mode: str = "paragraph"


class QAChunk(BaseModel):
    question: str
    answer: str


class QAStructureChunk(BaseModel):
    qa_chunks: list[QAChunk]
