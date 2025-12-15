"""Models for knowledge triplets and entities.

This module contains Pydantic models for representing extracted knowledge
in the form of entities and triplets (subject-predicate-object relationships).

Classes:
    Entity: Represents an extracted entity
    Triplet: Represents a knowledge triplet (subject-predicate-object)
    TripletExtractionResponse: Response model containing extracted triplets and entities
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict
from uuid import uuid4


class Entity(BaseModel):
    """Represents an extracted entity from dialogue.

    Attributes:
        id: Unique string identifier for the entity
        entity_idx: Numeric index for the entity
        name: Name of the entity
        name_embedding: Optional embedding vector for the entity name
        type: Type/category of the entity (e.g., 'Person', 'Organization')
        description: Textual description of the entity
        aliases: List of alternative names for the entity (e.g., abbreviations, full names, 
                 different language expressions). Extracted during triplet extraction phase.

    Config:
        extra: Ignore extra fields from LLM output
    """
    model_config = ConfigDict(extra='ignore')
    id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for the entity.")
    entity_idx: int = Field(..., description="Unique identifier for the entity")
    name: str = Field(..., description="Name of the entity")
    name_embedding: Optional[List[float]] = Field(None, description="Embedding vector for the entity name")
    type: str = Field(..., description="Type/category of the entity")
    description: str = Field(..., description="Description of the entity")
    aliases: List[str] = Field(
        default_factory=list,
        description="Alternative names for this entity (abbreviations, full names, translations, etc.)"
    )


class Triplet(BaseModel):
    """Represents an extracted knowledge triplet (subject-predicate-object).

    A triplet represents a relationship between two entities, forming
    the basic unit of knowledge in the knowledge graph.

    Attributes:
        id: Unique string identifier for the triplet
        statement_id: Optional ID of the parent statement (set programmatically)
        subject_name: Name of the subject entity
        subject_id: Numeric ID of the subject entity
        predicate: Relationship/predicate between subject and object
        object_name: Name of the object entity
        object_id: Numeric ID of the object entity
        value: Optional additional value or context for the relationship

    Config:
        extra: Ignore extra fields from LLM output
    """
    model_config = ConfigDict(extra='ignore')
    id: str = Field(default_factory=lambda: uuid4().hex, description="Unique identifier for the triplet.")
    statement_id: Optional[str] = Field(None, description="ID of the parent statement this triplet was extracted from.")
    subject_name: str = Field(..., description="Name of the subject entity")
    subject_id: int = Field(..., description="ID of the subject entity")
    predicate: str = Field(..., description="Relationship/predicate between subject and object")
    object_name: str = Field(..., description="Name of the object entity")
    object_id: int = Field(..., description="ID of the object entity")
    value: Optional[str] = Field(None, description="Additional value or context")


class TripletExtractionResponse(BaseModel):
    """Response model for triplet extraction from LLM.

    This model represents the structured output from the LLM when
    extracting knowledge triplets and entities from statements.

    Attributes:
        triplets: List of extracted knowledge triplets
        entities: List of extracted entities

    Config:
        extra: Ignore extra fields from LLM output
    """
    model_config = ConfigDict(extra='ignore')
    triplets: List[Triplet] = Field(default_factory=list, description="List of extracted triplets")
    entities: List[Entity] = Field(default_factory=list, description="List of extracted entities")
