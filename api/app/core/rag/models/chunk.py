from pydantic import BaseModel, Field


class ChildDocumentChunk(BaseModel):
    """Class for storing a piece of text and associated metadata."""

    page_content: str

    vector: list[float] | None = None

    """Arbitrary metadata about the page content (e.g., source, relationships to other
        documents, etc.).
    """
    metadata: dict = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """Class for storing a piece of text and associated metadata."""

    page_content: str

    vector: list[float] | None = None

    """Arbitrary metadata about the page content (e.g., source, relationships to other
        documents, etc.).
    """
    metadata: dict = Field(default_factory=dict)

    children: list[ChildDocumentChunk] | None = None


class GeneralStructureChunk(BaseModel):
    """
    General Structure Chunk.
    """

    general_chunks: list[str]


class ParentChildChunk(BaseModel):
    """
    Parent Child Chunk.
    """

    parent_content: str
    child_contents: list[str]


class ParentChildStructureChunk(BaseModel):
    """
    Parent Child Structure Chunk.
    """

    parent_child_chunks: list[ParentChildChunk]
    parent_mode: str = "paragraph"


class QAChunk(BaseModel):
    """
    QA Chunk.
    """

    question: str
    answer: str


class QAStructureChunk(BaseModel):
    """
    QAStructureChunk.
    """

    qa_chunks: list[QAChunk]
