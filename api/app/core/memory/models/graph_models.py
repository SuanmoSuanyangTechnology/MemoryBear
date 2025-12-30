"""Graph models for Neo4j knowledge graph nodes and edges.

This module contains Pydantic models representing nodes and edges
in the Neo4j knowledge graph, including dialogues, statements,
chunks, entities, and their relationships.

Classes:
    Edge: Base class for all graph edges
    ChunkEdge: Edge connecting chunks
    ChunkEntityEdge: Edge connecting chunks to entities
    ChunkDialogEdge: Edge connecting chunks to dialogues
    StatementChunkEdge: Edge connecting statements to chunks
    StatementEntityEdge: Edge connecting statements to entities
    EntityEntityEdge: Edge connecting related entities
    Node: Base class for all graph nodes
    DialogueNode: Node representing a dialogue
    StatementNode: Node representing a statement
    ChunkNode: Node representing a conversation chunk
    ExtractedEntityNode: Node representing an extracted entity
    MemorySummaryNode: Node representing a memory summary
"""

import re
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from app.core.memory.utils.alias_utils import validate_aliases
from app.core.memory.utils.data.ontology import TemporalInfo
from pydantic import BaseModel, Field, field_validator


def parse_historical_datetime(v):
    """支持任意年份的日期解析，包括历史日期（如公元755年）
    
    Python datetime 支持公元1年到9999年的日期
    此函数手动解析 ISO 8601 格式的日期字符串，支持1-4位年份
    
    Args:
        v: 日期值（可以是 None、datetime 对象或字符串）
        
    Returns:
        datetime 对象或 None
    """
    if v is None or isinstance(v, datetime):
        return v
    
    if isinstance(v, str):
        # 匹配 ISO 8601 格式：YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS[.ffffff][Z|±HH:MM]
        # 支持1-4位年份
        pattern = r'^(\d{1,4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(?:Z|([+-]\d{2}:\d{2}))?)?'
        match = re.match(pattern, v)
        
        if match:
            try:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))
                hour = int(match.group(4)) if match.group(4) else 0
                minute = int(match.group(5)) if match.group(5) else 0
                second = int(match.group(6)) if match.group(6) else 0
                microsecond = 0
                
                # 处理微秒
                if match.group(7):
                    # 补齐或截断到6位
                    us_str = match.group(7).ljust(6, '0')[:6]
                    microsecond = int(us_str)
                
                # 处理时区
                tzinfo = None
                if 'Z' in v or match.group(8):
                    tzinfo = timezone.utc
                
                # 创建 datetime 对象
                return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=tzinfo)
                
            except (ValueError, OverflowError):
                # 日期值无效（如月份13、日期32等）
                return None
        
        # 如果不匹配模式，尝试使用 fromisoformat（用于标准格式）
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except Exception:
            return None
    
    return v


class Edge(BaseModel):
    """Base class for all graph edges in the knowledge graph.

    Attributes:
        id: Unique identifier for the edge
        source: ID of the source node
        target: ID of the target node
        group_id: Group ID for multi-tenancy
        user_id: User ID for user-specific data
        apply_id: Application ID for application-specific data
        run_id: Unique identifier for the pipeline run that created this edge
        created_at: Timestamp when the edge was created (system perspective)
        expired_at: Optional timestamp when the edge expires (system perspective)
    """
    id: str = Field(default_factory=lambda: uuid4().hex, description="A unique identifier for the edge.")
    source: str = Field(..., description="The ID of the source node.")
    target: str = Field(..., description="The ID of the target node.")
    group_id: str = Field(..., description="The group ID of the edge.")
    user_id: str = Field(..., description="The user ID of the edge.")
    apply_id: str = Field(..., description="The apply ID of the edge.")
    run_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for this pipeline run.")
    created_at: datetime = Field(..., description="The valid time of the edge from system perspective.")
    expired_at: Optional[datetime] = Field(None, description="The expired time of the edge from system perspective.")


class ChunkEdge(Edge):
    """Edge connecting two chunks in sequence."""
    pass


class ChunkEntityEdge(Edge):
    """Edge connecting a chunk to an entity mentioned in it."""
    pass


class ChunkDialogEdge(Edge):
    """Edge connecting a chunk to its parent dialog.

    Attributes:
        sequence_number: Order of this chunk within the dialog
    """
    sequence_number: int = Field(..., description="Order of this chunk within the dialog")


class StatementChunkEdge(Edge):
    """Edge connecting a statement to its parent chunk."""
    pass


class StatementEntityEdge(Edge):
    """Edge connecting a statement to entities extracted from it.

    Attributes:
        connect_strength: Classification of connection strength ('Strong' or 'Weak')
    """
    connect_strength: str = Field(..., description="Strong VS Weak about this statement-entity edge")


class EntityEntityEdge(Edge):
    """Edge connecting related entities (from triplet relationships).

    Attributes:
        relation_type: Type of relationship as defined in ontology
        relation_value: Optional value of the relation
        statement: The statement text where this relationship was found
        source_statement_id: ID of the statement where this relationship was extracted
        valid_at: Optional start date of temporal validity
        invalid_at: Optional end date of temporal validity
    """
    relation_type: str = Field(..., description="Relation type as defined in ontology")
    relation_value: Optional[str] = Field(None, description="Value of the relation")
    statement: str = Field(..., description='The statement of the edge.')
    source_statement_id: str = Field(..., description="Statement where this relationship was extracted")
    valid_at: Optional[datetime] = Field(None, description="Temporal validity start")
    invalid_at: Optional[datetime] = Field(None, description="Temporal validity end")
    
    @field_validator('valid_at', 'invalid_at', mode='before')
    @classmethod
    def validate_datetime(cls, v):
        """使用通用的历史日期解析函数"""
        return parse_historical_datetime(v)


class Node(BaseModel):
    """Base class for all graph nodes in the knowledge graph.

    Attributes:
        id: Unique identifier for the node
        name: Name of the node
        group_id: Group ID for multi-tenancy
        user_id: User ID for user-specific data
        apply_id: Application ID for application-specific data
        run_id: Unique identifier for the pipeline run that created this node
        created_at: Timestamp when the node was created (system perspective)
        expired_at: Optional timestamp when the node expires (system perspective)
    """
    id: str = Field(..., description="The unique identifier for the node.")
    name: str = Field(..., description="The name of the node.")
    group_id: str = Field(..., description="The group ID of the node.")
    user_id: str = Field(..., description="The user ID of the edge.")
    apply_id: str = Field(..., description="The apply ID of the edge.")
    run_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for this pipeline run.")
    created_at: datetime = Field(..., description="The valid time of the node from system perspective.")
    expired_at: Optional[datetime] = Field(None, description="The expired time of the node from system perspective.")


class DialogueNode(Node):
    """Node representing a dialogue in the knowledge graph.

    Attributes:
        ref_id: Reference identifier linking to external dialog system
        content: Full dialogue content as text
        dialog_embedding: Optional embedding vector for the entire dialogue
        config_id: Configuration ID used to process this dialogue
    """
    ref_id: str = Field(..., description="Reference identifier of the dialog")
    content: str = Field(..., description="Dialogue content")
    dialog_embedding: Optional[List[float]] = Field(None, description="Dialog embedding vector")
    config_id: Optional[int | str] = Field(None, description="Configuration ID used to process this dialogue (integer or string)")


class StatementNode(Node):
    """Node representing a statement extracted from dialogue.

    Attributes:
        chunk_id: ID of the parent chunk this statement belongs to
        stmt_type: Type of the statement (from ontology)
        statement: The actual statement text content
        emotion_intensity: Optional emotion intensity (0.0-1.0) - displayed on node
        emotion_target: Optional emotion target (person or object name)
        emotion_subject: Optional emotion subject (self/other/object)
        emotion_type: Optional emotion type (joy/sadness/anger/fear/surprise/neutral)
        emotion_keywords: Optional list of emotion keywords (max 3)
        temporal_info: Temporal information extracted from the statement
        valid_at: Optional start date of temporal validity
        invalid_at: Optional end date of temporal validity
        statement_embedding: Optional embedding vector for the statement
        chunk_embedding: Optional embedding vector for the parent chunk
        connect_strength: Classification of connection strength ('Strong' or 'Weak')
        config_id: Configuration ID used to process this statement
    """
    # Core fields (ordered as requested)
    chunk_id: str = Field(..., description="ID of the parent chunk")
    stmt_type: str = Field(..., description="Type of the statement")
    statement: str = Field(..., description="The statement text content")
    
    # Emotion fields (ordered as requested, emotion_intensity first for display)
    emotion_intensity: Optional[float] = Field(
        None, 
        ge=0.0, 
        le=1.0,
        description="Emotion intensity: 0.0-1.0 (displayed on node)"
    )
    emotion_target: Optional[str] = Field(
        None,
        description="Emotion target: person or object name"
    )
    emotion_subject: Optional[str] = Field(
        None,
        description="Emotion subject: self/other/object"
    )
    emotion_type: Optional[str] = Field(
        None, 
        description="Emotion type: joy/sadness/anger/fear/surprise/neutral"
    )
    emotion_keywords: Optional[List[str]] = Field(
        default_factory=list,
        description="Emotion keywords list, max 3 items"
    )
    
    # Temporal fields
    temporal_info: TemporalInfo = Field(..., description="Temporal information")
    valid_at: Optional[datetime] = Field(None, description="Temporal validity start")
    invalid_at: Optional[datetime] = Field(None, description="Temporal validity end")
    
    # Embedding and other fields
    statement_embedding: Optional[List[float]] = Field(None, description="Statement embedding vector")
    chunk_embedding: Optional[List[float]] = Field(None, description="Chunk embedding vector")
    connect_strength: str = Field(..., description="Strong VS Weak classification of this statement")
    config_id: Optional[int | str] = Field(None, description="Configuration ID used to process this statement (integer or string)")
    
    @field_validator('valid_at', 'invalid_at', mode='before')
    @classmethod
    def validate_datetime(cls, v):
        """使用通用的历史日期解析函数"""
        return parse_historical_datetime(v)
    
    @field_validator('emotion_type', mode='before')
    @classmethod
    def validate_emotion_type(cls, v):
        """Validate emotion type is one of the valid values"""
        if v is None:
            return v
        valid_types = ['joy', 'sadness', 'anger', 'fear', 'surprise', 'neutral']
        if v not in valid_types:
            raise ValueError(f"emotion_type must be one of {valid_types}, got {v}")
        return v
    
    @field_validator('emotion_subject', mode='before')
    @classmethod
    def validate_emotion_subject(cls, v):
        """Validate emotion subject is one of the valid values"""
        if v is None:
            return v
        valid_subjects = ['self', 'other', 'object']
        if v not in valid_subjects:
            raise ValueError(f"emotion_subject must be one of {valid_subjects}, got {v}")
        return v
    
    @field_validator('emotion_keywords', mode='before')
    @classmethod
    def validate_emotion_keywords(cls, v):
        """Validate emotion keywords list has max 3 items"""
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        # Limit to max 3 keywords
        return v[:3]


class ChunkNode(Node):
    """Node representing a chunk of conversation in the knowledge graph.

    Attributes:
        dialog_id: ID of the parent dialog
        content: The text content of the chunk
        chunk_embedding: Optional embedding vector for the chunk
        sequence_number: Order of this chunk within the dialog
        metadata: Additional chunk metadata as key-value pairs
    """
    dialog_id: str = Field(..., description="ID of the parent dialog")
    content: str = Field(..., description="The text content of the chunk")
    chunk_embedding: Optional[List[float]] = Field(None, description="Chunk embedding vector")
    sequence_number: int = Field(..., description="Order of this chunk within the dialog")
    metadata: dict = Field(default_factory=dict, description="Additional chunk metadata")


class ExtractedEntityNode(Node):
    """Node representing an extracted entity in the knowledge graph.
    
    This class represents entities extracted from dialogue statements. Each entity
    has a primary name and can have multiple aliases (alternative names). The aliases
    feature enables better entity deduplication and disambiguation by tracking all
    known names for an entity.

    Attributes:
        entity_idx: Unique numeric identifier for the entity
        statement_id: ID of the statement this entity was extracted from
        entity_type: Type/category of the entity (e.g., PERSON, ORGANIZATION, LOCATION)
        description: Textual description of the entity
        aliases: List of alternative names for the entity. This field:
                 - Stores all known alternative names in the SAME LANGUAGE as the entity name
                 - Automatically filters out invalid values (None, empty strings)
                 - Removes duplicates (case-insensitive) and names matching the primary name
                 - Is used in fuzzy matching to improve entity deduplication
                 - Is populated during triplet extraction and entity merging processes
                 - Has a recommended maximum of 50 aliases per entity
                 - CRITICAL: Aliases must be in the same language as the entity name (no translation)
        name_embedding: Optional embedding vector for the entity name
        fact_summary: Summary of facts about this entity
        connect_strength: Classification of connection strength ('Strong', 'Weak', or 'Both')
        config_id: Configuration ID used to process this entity (integer or string)
    """
    entity_idx: int = Field(..., description="Unique identifier for the entity")
    statement_id: str = Field(..., description="Statement this entity was extracted from")
    entity_type: str = Field(..., description="Type of the entity")
    description: str = Field(..., description="Entity description")
    aliases: List[str] = Field(
        default_factory=list, 
        description="Entity aliases - alternative names for this entity"
    )
    name_embedding: Optional[List[float]] = Field(default_factory=list, description="Name embedding vector")
    fact_summary: str = Field(default="", description="Summary of the fact about this entity")
    connect_strength: str = Field(..., description="Strong VS Weak about this entity")
    config_id: Optional[int | str] = Field(None, description="Configuration ID used to process this entity (integer or string)")
    
    @field_validator('aliases', mode='before')
    @classmethod
    def validate_aliases_field(cls, v): # 字段验证器 自动清理和验证 aliases 字段
        """Validate and clean aliases field using utility function.
        
        This validator ensures that the aliases field is always a valid list of strings.
        It filters out:
        - None values
        - Empty strings
        - Non-string types (after converting to string)
        - Duplicate values
        
        Args:
            v: The raw aliases value (can be None, list, or other types)
            
        Returns:
            A cleaned list of unique string aliases
            
        Example:
            >>> # Input: [None, "", "alias1", "alias1", 123]
            >>> # Output: ["alias1", "123"]
        """
        return validate_aliases(v)


class MemorySummaryNode(Node):
    """Node representing a memory summary with vector embedding.

    Attributes:
        summary_id: Unique identifier for the summary
        dialog_id: ID of the parent dialog
        chunk_ids: List of chunk IDs used to generate this summary
        content: Summary text content
        summary_embedding: Optional embedding vector for the summary
        metadata: Additional metadata for the summary
        config_id: Configuration ID used to process this summary
    """
    summary_id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for the summary")
    dialog_id: str = Field(..., description="ID of the parent dialog")
    chunk_ids: List[str] = Field(default_factory=list, description="List of chunk IDs used in the summary")
    content: str = Field(..., description="Summary text content")
    summary_embedding: Optional[List[float]] = Field(None, description="Embedding vector for the summary")
    metadata: dict = Field(default_factory=dict, description="Additional metadata for the summary")
    config_id: Optional[int | str] = Field(None, description="Configuration ID used to process this summary (integer or string)")
