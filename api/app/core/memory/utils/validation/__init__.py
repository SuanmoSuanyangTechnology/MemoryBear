"""Validation utilities for ontology extraction.

This module provides validation classes for ontology class names,
descriptions, and OWL compliance checking.
"""

from .ontology_validator import OntologyValidator
from .owl_validator import OWLValidator

__all__ = ['OntologyValidator', 'OWLValidator']
