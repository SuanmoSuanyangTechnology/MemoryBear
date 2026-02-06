"""String validation for ontology class names and descriptions.

This module provides the OntologyValidator class for validating and sanitizing
ontology class names according to OWL standards and naming conventions.

Classes:
    OntologyValidator: Validates class names, removes duplicates, and truncates descriptions
"""

import logging
import re
from typing import List, Tuple

from app.core.memory.models.ontology_scenario_models import OntologyClass


logger = logging.getLogger(__name__)


class OntologyValidator:
    """Validator for ontology class names and descriptions.
    
    This validator performs string-level validation including:
    - PascalCase naming convention validation
    - OWL reserved word checking
    - Duplicate class name removal
    - Description length truncation
    
    Attributes:
        OWL_RESERVED_WORDS: Set of OWL reserved words that cannot be used as class names
    """
    
    # OWL reserved words that cannot be used as class names
    OWL_RESERVED_WORDS = {
        'Thing', 'Nothing', 'Class', 'Property',
        'ObjectProperty', 'DatatypeProperty', 'FunctionalProperty',
        'InverseFunctionalProperty', 'TransitiveProperty', 'SymmetricProperty',
        'AsymmetricProperty', 'ReflexiveProperty', 'IrreflexiveProperty',
        'Restriction', 'Ontology', 'Individual', 'NamedIndividual',
        'Annotation', 'AnnotationProperty', 'Axiom',
        'AllDifferent', 'AllDisjointClasses', 'AllDisjointProperties',
        'Datatype', 'DataRange', 'Literal',
        'DeprecatedClass', 'DeprecatedProperty',
        'Imports', 'IncompatibleWith', 'PriorVersion', 'VersionInfo',
        'BackwardCompatibleWith', 'OntologyProperty',
    }
    
    def validate_class_name(self, name: str) -> Tuple[bool, str]:
        """Validate that a class name follows OWL naming conventions.
        
        Validation rules:
        1. Must not be empty
        2. Must start with an uppercase letter (PascalCase)
        3. Cannot contain spaces
        4. Can only contain alphanumeric characters and underscores
        5. Cannot be an OWL reserved word
        
        Args:
            name: The class name to validate
            
        Returns:
            Tuple of (is_valid, error_message)
            - is_valid: True if the name is valid, False otherwise
            - error_message: Empty string if valid, error description if invalid
            
        Examples:
            >>> validator = OntologyValidator()
            >>> validator.validate_class_name("MedicalProcedure")
            (True, "")
            >>> validator.validate_class_name("medical procedure")
            (False, "Class name 'medical procedure' cannot contain spaces")
            >>> validator.validate_class_name("Thing")
            (False, "Class name 'Thing' is an OWL reserved word")
        """
        logger.debug(f"Validating class name: '{name}'")
        
        # Check if empty
        if not name or not name.strip():
            error_msg = "Class name cannot be empty"
            logger.warning(f"Validation failed: {error_msg}")
            return False, error_msg
        
        name = name.strip()
        
        # Check if it's an OWL reserved word
        if name in self.OWL_RESERVED_WORDS:
            error_msg = f"Class name '{name}' is an OWL reserved word"
            logger.warning(f"Validation failed: {error_msg}")
            return False, error_msg
        
        # Check if starts with uppercase letter
        if not name[0].isupper():
            error_msg = f"Class name '{name}' must start with an uppercase letter (PascalCase)"
            logger.warning(f"Validation failed: {error_msg}")
            return False, error_msg
        
        # Check for spaces
        if ' ' in name:
            error_msg = f"Class name '{name}' cannot contain spaces"
            logger.warning(f"Validation failed: {error_msg}")
            return False, error_msg
        
        # Check for invalid characters (only alphanumeric and underscore allowed)
        if not re.match(r'^[A-Za-z0-9_]+$', name):
            error_msg = f"Class name '{name}' contains invalid characters. Only alphanumeric characters and underscores are allowed"
            logger.warning(f"Validation failed: {error_msg}")
            return False, error_msg
        
        logger.debug(f"Class name '{name}' is valid")
        return True, ""
    
    def sanitize_class_name(self, name: str) -> str:
        """Attempt to sanitize an invalid class name into a valid format.
        
        Sanitization steps:
        1. Strip whitespace
        2. Remove invalid characters
        3. Replace spaces with empty string (PascalCase)
        4. Capitalize first letter of each word
        5. If result is empty or starts with number, prefix with 'Class'
        
        Args:
            name: The class name to sanitize
            
        Returns:
            Sanitized class name that should pass validation
            
        Examples:
            >>> validator = OntologyValidator()
            >>> validator.sanitize_class_name("medical procedure")
            'MedicalProcedure'
            >>> validator.sanitize_class_name("patient-record")
            'PatientRecord'
            >>> validator.sanitize_class_name("123invalid")
            'Class123Invalid'
        """
        logger.debug(f"Sanitizing class name: '{name}'")
        
        if not name or not name.strip():
            logger.warning("Empty class name provided for sanitization, returning 'UnnamedClass'")
            return "UnnamedClass"
        
        # Strip whitespace
        name = name.strip()
        original_name = name
        
        # Split on spaces, hyphens, and underscores, then capitalize each word
        words = re.split(r'[\s\-_]+', name)
        
        # Capitalize first letter of each word and keep rest as is
        sanitized_words = []
        for word in words:
            if word:
                # Remove non-alphanumeric characters except underscore
                clean_word = re.sub(r'[^A-Za-z0-9_]', '', word)
                if clean_word:
                    # Capitalize first letter
                    sanitized_words.append(clean_word[0].upper() + clean_word[1:])
        
        # Join words
        sanitized = ''.join(sanitized_words)
        
        # If empty or starts with number, prefix with 'Class'
        if not sanitized or sanitized[0].isdigit():
            sanitized = 'Class' + sanitized
            logger.info(f"Prefixed class name with 'Class': '{original_name}' -> '{sanitized}'")
        
        # If it's a reserved word, append 'Class' suffix
        if sanitized in self.OWL_RESERVED_WORDS:
            sanitized = sanitized + 'Class'
            logger.info(f"Appended 'Class' suffix to reserved word: '{original_name}' -> '{sanitized}'")
        
        logger.info(f"Sanitized class name: '{original_name}' -> '{sanitized}'")
        return sanitized
    
    def remove_duplicates(self, classes: List[OntologyClass]) -> List[OntologyClass]:
        """Remove duplicate ontology classes based on case-insensitive name comparison.
        
        When duplicates are found, keeps the first occurrence and discards subsequent ones.
        Comparison is case-insensitive to catch variations like 'Patient' and 'patient'.
        
        Args:
            classes: List of OntologyClass objects
            
        Returns:
            List of OntologyClass objects with duplicates removed
            
        Examples:
            >>> validator = OntologyValidator()
            >>> classes = [
            ...     OntologyClass(name="Patient", description="A patient", entity_type="Person", domain="Healthcare"),
            ...     OntologyClass(name="patient", description="Another patient", entity_type="Person", domain="Healthcare"),
            ...     OntologyClass(name="Doctor", description="A doctor", entity_type="Person", domain="Healthcare"),
            ... ]
            >>> unique = validator.remove_duplicates(classes)
            >>> len(unique)
            2
            >>> [c.name for c in unique]
            ['Patient', 'Doctor']
        """
        if not classes:
            logger.debug("No classes to check for duplicates")
            return classes
        
        logger.debug(f"Checking {len(classes)} classes for duplicates")
        
        seen_names = set()
        unique_classes = []
        duplicates_found = []
        
        for ontology_class in classes:
            # Use lowercase for comparison
            name_lower = ontology_class.name.lower()
            
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                unique_classes.append(ontology_class)
            else:
                duplicates_found.append(ontology_class.name)
                logger.debug(f"Duplicate class found and removed: '{ontology_class.name}'")
        
        if duplicates_found:
            logger.info(
                f"Removed {len(duplicates_found)} duplicate classes: {duplicates_found}"
            )
        else:
            logger.debug("No duplicate classes found")
        
        return unique_classes
    
    def truncate_description(self, description: str, max_length: int = 500) -> str:
        """Truncate a description to a maximum length.
        
        If the description exceeds max_length, it will be truncated and
        an ellipsis (...) will be appended to indicate truncation.
        
        Args:
            description: The description text to truncate
            max_length: Maximum allowed length (default: 500)
            
        Returns:
            Truncated description string
            
        Examples:
            >>> validator = OntologyValidator()
            >>> long_desc = "A" * 600
            >>> truncated = validator.truncate_description(long_desc, max_length=500)
            >>> len(truncated)
            500
            >>> truncated.endswith("...")
            True
        """
        if not description:
            return ""
        
        if len(description) <= max_length:
            return description
        
        # Truncate and add ellipsis
        # Reserve 3 characters for "..."
        truncate_at = max_length - 3
        truncated = description[:truncate_at] + "..."
        
        logger.debug(
            f"Truncated description from {len(description)} to {len(truncated)} characters"
        )
        
        return truncated
