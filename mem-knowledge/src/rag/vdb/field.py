"""Elasticsearch field names copied from the legacy RAG package."""

from enum import StrEnum, auto


class Field(StrEnum):
    CONTENT_KEY = "page_content"
    METADATA_KEY = "metadata"
    GROUP_KEY = "end_user_id"
    VECTOR = auto()
    SPARSE_VECTOR = auto()
    TEXT_KEY = "text"
    PRIMARY_KEY = "id"
    DOC_ID = "metadata.doc_id"
    FILE_ID = "metadata.file_id"
    FILE_NAME = "metadata.file_name"
    FILE_CREATED_AT = "metadata.file_created_at"
    DOCUMENT_ID = "metadata.document_id"
    KNOWLEDGE_ID = "metadata.knowledge_id"
    SORT_ID = "metadata.sort_id"
    STATUS = "metadata.status"
    VISION_TEXT = "metadata.vision_text"
    ASSET_FILE_IDS = "metadata.asset_file_ids"
    CHUNK_TYPE = "chunk_type"
    QUESTION = "question"
    ANSWER = "answer"
    SOURCE_CHUNK_ID = "source_chunk_id"
    PARENT_ID = "parent_id"
