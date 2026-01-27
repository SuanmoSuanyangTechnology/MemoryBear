"""Models for ontology classes and extraction responses.

This module contains Pydantic models for representing extracted ontology classes
from scenario descriptions, following OWL ontology engineering standards.

Classes:
    OntologyClass: Represents an extracted ontology class
    OntologyExtractionResponse: Response model containing extracted ontology classes
"""

from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class OntologyClass(BaseModel):
    """Represents an extracted ontology class from scenario description.

    An ontology class represents an abstract category or concept in a domain,
    following OWL ontology engineering standards and naming conventions.

    Attributes:
        id: Unique string identifier for the ontology class
        name: Name of the class in PascalCase format (e.g., 'MedicalProcedure')
        name_chinese: Chinese translation of the class name (e.g., '医疗程序')
        description: Textual description of the class
        examples: List of concrete instance examples of this class
        parent_class: Optional name of the parent class in the hierarchy
        entity_type: Type/category of the entity (e.g., 'Person', 'Organization', 'Concept')
        domain: Domain this class belongs to (e.g., 'Healthcare', 'Education')

    Config:
        extra: Ignore extra fields from LLM output
    """
    model_config = ConfigDict(extra='ignore')
    
    id: str = Field(
        default_factory=lambda: uuid4().hex,
        description="Unique identifier for the ontology class"
    )
    name: str = Field(
        ...,
        description="Name of the class in PascalCase format"
    )
    name_chinese: Optional[str] = Field(
        None,
        description="Chinese translation of the class name"
    )
    description: str = Field(
        ...,
        description="Description of the class"
    )
    examples: List[str] = Field(
        default_factory=list,
        description="List of concrete instance examples"
    )
    parent_class: Optional[str] = Field(
        None,
        description="Name of the parent class in the hierarchy"
    )
    entity_type: str = Field(
        ...,
        description="Type/category of the entity"
    )
    domain: str = Field(
        ...,
        description="Domain this class belongs to"
    )

    @field_validator('name')
    @classmethod
    def validate_pascal_case(cls, v: str) -> str:
        """Validate that the class name follows PascalCase convention.

        PascalCase rules:
        - Must start with an uppercase letter
        - Cannot contain spaces
        - Should not contain special characters except underscores

        Args:
            v: The class name to validate

        Returns:
            The validated class name

        Raises:
            ValueError: If the name doesn't follow PascalCase convention
        """
        if not v:
            raise ValueError("Class name cannot be empty")
        
        if not v[0].isupper():
            raise ValueError(
                f"Class name '{v}' must start with an uppercase letter (PascalCase)"
            )
        
        if ' ' in v:
            raise ValueError(
                f"Class name '{v}' cannot contain spaces (PascalCase)"
            )
        
        # Check for invalid characters (allow alphanumeric and underscore only)
        if not all(c.isalnum() or c == '_' for c in v):
            raise ValueError(
                f"Class name '{v}' contains invalid characters. "
                "Only alphanumeric characters and underscores are allowed"
            )
        
        return v


class OntologyExtractionResponse(BaseModel):
    """Response model for ontology extraction from LLM.

    This model represents the structured output from the LLM when
    extracting ontology classes from scenario descriptions.

    Attributes:
        classes: List of extracted ontology classes
        domain: Domain/field the scenario belongs to
        namespace: Optional OWL namespace URI for the ontology

    Config:
        extra: Ignore extra fields from LLM output
    """
    model_config = ConfigDict(extra='ignore')
    
    classes: List[OntologyClass] = Field(
        default_factory=list,
        description="List of extracted ontology classes"
    )
    domain: str = Field(
        ...,
        description="Domain/field the scenario belongs to"
    )
    namespace: Optional[str] = Field(
        None,
        description="OWL namespace URI for the ontology"
    )
