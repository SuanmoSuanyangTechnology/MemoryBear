"""Models for dialogue messages, conversations, and statements.

This module contains Pydantic models for representing dialogue data,
including messages, conversation context, chunks, and statements.

Classes:
    ConversationMessage: Single message in a conversation
    TemporalValidityRange: Temporal validity range for statements
    Statement: Statement extracted from dialogue with metadata
    ConversationContext: Full conversation history
    Chunk: Chunk of conversation text
    DialogData: Complete dialogue data structure
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from uuid import uuid4
from datetime import datetime

from app.core.memory.utils.data.ontology import StatementType, TemporalInfo, RelevenceInfo
from app.core.memory.models.triplet_models import TripletExtractionResponse, Triplet


class ConversationMessage(BaseModel):
    """Represents a single message in a conversation.

    Attributes:
        role: Role of the speaker (e.g., '用户' for user, 'AI' for assistant)
        msg: Text content of the message
    """
    role: str = Field(..., description="The role of the speaker (e.g., '用户', 'AI').")
    msg: str = Field(..., description="The text content of the message.")


class TemporalValidityRange(BaseModel):
    """Represents the temporal validity range of a statement.

    Attributes:
        valid_at: Start date of validity in 'YYYY-MM-DD' format (None if not specified)
        invalid_at: End date of validity in 'YYYY-MM-DD' format (None if not specified)
    """
    valid_at: Optional[str] = Field(
        None,
        description="The start date of the statement's validity, in 'YYYY-MM-DD' format or 'None'.",
    )
    invalid_at: Optional[str] = Field(
        None,
        description="The end date of the statement's validity, in 'YYYY-MM-DD' format or 'None'.",
    )


class Statement(BaseModel):
    """Represents a statement extracted from dialogue with metadata.

    Attributes:
        id: Unique identifier for the statement
        chunk_id: ID of the parent chunk this statement belongs to
        group_id: Optional group ID for multi-tenancy
        statement: The actual statement text content
        statement_embedding: Optional embedding vector for the statement
        stmt_type: Type of the statement (from ontology)
        temporal_info: Temporal information extracted from the statement
        relevence_info: Relevance classification (RELEVANT or IRRELEVANT)
        connect_strength: Optional connection strength ('Strong' or 'Weak')
        temporal_validity: Optional temporal validity range
        triplet_extraction_info: Optional triplet extraction results
        emotion_type: Optional emotion type (joy/sadness/anger/fear/surprise/neutral)
        emotion_intensity: Optional emotion intensity (0.0-1.0)
        emotion_keywords: Optional list of emotion keywords
        emotion_subject: Optional emotion subject (self/other/object)
        emotion_target: Optional emotion target (person or object name)
    """
    id: str = Field(default_factory=lambda: uuid4().hex, description="A unique identifier for the statement.")
    chunk_id: str = Field(..., description="ID of the parent chunk this statement belongs to.")
    group_id: Optional[str] = Field(None, description="ID of the group this statement belongs to.")
    statement: str = Field(..., description="The text content of the statement.")
    statement_embedding: Optional[List[float]] = Field(None, description="The embedding vector of the statement.")
    stmt_type: StatementType = Field(..., description="The type of the statement.")
    temporal_info: TemporalInfo = Field(..., description="The temporal information of the statement.")
    relevence_info: RelevenceInfo = Field(RelevenceInfo.RELEVANT, description="The relevence information of the statement.")
    connect_strength: Optional[str] = Field(None, description="Strong VS Weak about this entity")
    temporal_validity: Optional[TemporalValidityRange] = Field(
        None, description="The temporal validity range of the statement."
    )
    triplet_extraction_info: Optional[TripletExtractionResponse] = Field(
        None, description="The triplet extraction information of the statement."
    )
    # Emotion fields
    emotion_type: Optional[str] = Field(None, description="Emotion type: joy/sadness/anger/fear/surprise/neutral")
    emotion_intensity: Optional[float] = Field(None, ge=0.0, le=1.0, description="Emotion intensity: 0.0-1.0")
    emotion_keywords: Optional[List[str]] = Field(default_factory=list, description="Emotion keywords, max 3")
    emotion_subject: Optional[str] = Field(None, description="Emotion subject: self/other/object")
    emotion_target: Optional[str] = Field(None, description="Emotion target: person or object name")


class ConversationContext(BaseModel):
    """Represents the full conversation history.

    Attributes:
        msgs: List of messages in the conversation

    Properties:
        content: Formatted string representation of the conversation
    """
    msgs: List[ConversationMessage] = Field(..., description="A list of messages in the conversation.")

    @property
    def content(self) -> str:
        """Get the content of the conversation as a formatted string.

        Returns:
            String with format "role: message" for each message, joined by newlines
        """
        return "\n".join([f"{msg.role}: {msg.msg}" for msg in self.msgs])

class Chunk(BaseModel):
    """A chunk of text from the conversation context.

    Attributes:
        id: Unique identifier for the chunk
        text: List of messages in the chunk
        content: The content of the chunk as a formatted string
        statements: List of statements extracted from this chunk
        chunk_embedding: Optional embedding vector for the chunk
        metadata: Additional metadata as key-value pairs
    """
    id: str = Field(default_factory=lambda: uuid4().hex, description="A unique identifier for the chunk.")
    text: List[ConversationMessage] = Field(default_factory=list, description="A list of messages in the chunk.")
    content: str = Field(..., description="The content of the chunk as a string.")
    statements: List[Statement] = Field(default_factory=list, description="A list of statements in the chunk.")
    chunk_embedding: Optional[List[float]] = Field(None, description="The embedding vector of the chunk.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata for the chunk.")

    @classmethod
    def from_messages(cls, messages: List[ConversationMessage], metadata: Optional[Dict[str, Any]] = None):
        """Create a chunk from a list of messages.

        Args:
            messages: List of conversation messages
            metadata: Optional metadata dictionary

        Returns:
            Chunk instance with formatted content
        """
        if metadata is None:
            metadata = {}
        # Generate content from messages
        content = "\n".join([f"{msg.role}: {msg.msg}" for msg in messages])
        return cls(text=messages, content=content, metadata=metadata)


class DialogData(BaseModel):
    """Represents the complete data structure for a dialog record.

    Attributes:
        id: Unique identifier for the dialog
        context: Full conversation context
        dialog_embedding: Optional embedding vector for the entire dialog
        ref_id: Reference ID linking to external dialog system
        group_id: Group ID for multi-tenancy
        user_id: User ID for user-specific data
        apply_id: Application ID for application-specific data
        created_at: Timestamp when the dialog was created
        expired_at: Timestamp when the dialog expires (default: far future)
        metadata: Additional metadata as key-value pairs
        chunks: List of chunks from the conversation
        config_id: Configuration ID used to process this dialog

    Properties:
        content: Formatted string representation of the dialog
    """
    id: str = Field(default_factory=lambda: uuid4().hex, description="A unique identifier for the dialog.")
    context: ConversationContext = Field(..., description="The full conversation context as a single string.")
    dialog_embedding: Optional[List[float]] = Field(None, description="The embedding vector of the dialog.")
    ref_id: str = Field(..., description="Refer to external dialog id. This is used to link to the original dialog.")
    group_id: str = Field(default=..., description="Group ID of dialogue data")
    user_id: str = Field(..., description="USER ID of dialogue data")
    apply_id: str = Field(..., description="APPLY ID of dialogue data")
    run_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for this pipeline run.")
    created_at: datetime = Field(default_factory=datetime.now, description="The timestamp when the dialog was created.")
    expired_at: datetime = Field(default_factory=lambda: datetime(9999, 12, 31), description="The timestamp when the dialog expires.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata for the dialog.")
    chunks: List[Chunk] = Field(default_factory=list, description="A list of chunks from the conversation context.")
    config_id: Optional[int | str] = Field(None, description="Configuration ID used to process this dialog (integer or string)")

    @property
    def content(self) -> str:
        """Get the content of the dialog as a formatted string.

        Returns:
            String representation of the conversation context
        """
        return self.context.content

    def get_statement_chunk(self, statement_id: str) -> Optional[Chunk]:
        """Find the chunk containing a specific statement.

        Args:
            statement_id: ID of the statement to find

        Returns:
            Chunk containing the statement, or None if not found
        """
        for chunk in self.chunks:
            for statement in chunk.statements:
                if statement.id == statement_id:
                    return chunk
        return None

    def get_all_statements(self) -> List[Statement]:
        """Get all statements from all chunks.

        Returns:
            List of all statements in the dialog
        """
        all_statements = []
        for chunk in self.chunks:
            all_statements.extend(chunk.statements)
        return all_statements

    def get_statement_by_id(self, statement_id: str) -> Optional[Statement]:
        """Find a specific statement by its ID.

        Args:
            statement_id: ID of the statement to find

        Returns:
            Statement with the given ID, or None if not found
        """
        for chunk in self.chunks:
            for statement in chunk.statements:
                if statement.id == statement_id:
                    return statement
        return None

    def get_triplets_for_statement(self, statement_id: str) -> List[Triplet]:
        """Get all triplets extracted from a specific statement.

        Args:
            statement_id: ID of the statement

        Returns:
            List of triplets from the statement, or empty list if none found
        """
        statement = self.get_statement_by_id(statement_id)
        if statement and statement.triplet_extraction_info:
            return statement.triplet_extraction_info.triplets
        return []

    def assign_group_id_to_statements(self) -> None:
        """Assign this dialog's group_id to all statements in all chunks.

        This method updates statements that don't have a group_id set.
        """
        for chunk in self.chunks:
            for statement in chunk.statements:
                if statement.group_id is None:
                    statement.group_id = self.group_id
