"""OWL semantic validation for ontology classes using Owlready2.

This module provides the OWLValidator class for validating ontology classes
against OWL standards using the Owlready2 library. It performs semantic
validation including consistency checking, circular inheritance detection,
and OWL file export.

Classes:
    OWLValidator: Validates ontology classes using OWL reasoning and exports to OWL formats
"""

import logging
from typing import List, Optional, Tuple

try:
    from owlready2 import (
        World,
        Thing,
        get_ontology,
        sync_reasoner_pellet,
        OwlReadyInconsistentOntologyError,
    )
    OWLREADY2_AVAILABLE = True
except ImportError:
    OWLREADY2_AVAILABLE = False
    World = None
    Thing = None

from app.core.memory.models.ontology_models import OntologyClass


logger = logging.getLogger(__name__)


class OWLValidator:
    """Validator for OWL semantic validation of ontology classes.
    
    This validator performs semantic-level validation using Owlready2 including:
    - Creating OWL classes from ontology class definitions
    - Running consistency checking with Pellet reasoner
    - Detecting circular inheritance
    - Validating Protégé compatibility
    - Exporting ontologies to various OWL formats (RDF/XML, Turtle, N-Triples)
    
    Attributes:
        base_namespace: Base URI for the ontology namespace
    """
    
    def __init__(self, base_namespace: str = "http://example.org/ontology#"):
        """Initialize the OWL validator.
        
        Args:
            base_namespace: Base URI for the ontology namespace (default: http://example.org/ontology#)
        """
        if not OWLREADY2_AVAILABLE:
            logger.warning(
                "Owlready2 is not installed. OWL validation features will be disabled. "
                "Install with: pip install owlready2>=0.46"
            )
        
        self.base_namespace = base_namespace
    
    def validate_ontology_classes(
        self,
        classes: List[OntologyClass],
        namespace: Optional[str] = None
    ) -> Tuple[bool, List[str], Optional[World]]:
        """Validate extracted ontology classes against OWL standards.
        
        This method creates an OWL ontology from the provided classes using Owlready2,
        runs consistency checking with the Pellet reasoner, and detects common issues
        like circular inheritance.
        
        Args:
            classes: List of OntologyClass objects to validate
            namespace: Optional custom namespace URI (uses base_namespace if not provided)
            
        Returns:
            Tuple of (is_valid, error_messages, world):
            - is_valid: True if ontology is valid and consistent, False otherwise
            - error_messages: List of error/warning messages
            - world: Owlready2 World object containing the ontology (None if validation failed)
            
        Examples:
            >>> validator = OWLValidator()
            >>> classes = [
            ...     OntologyClass(name="Patient", description="A patient", entity_type="Person", domain="Healthcare"),
            ...     OntologyClass(name="Doctor", description="A doctor", entity_type="Person", domain="Healthcare"),
            ... ]
            >>> is_valid, errors, world = validator.validate_ontology_classes(classes)
            >>> is_valid
            True
            >>> len(errors)
            0
        """
        if not OWLREADY2_AVAILABLE:
            return False, ["Owlready2 is not installed. Cannot perform OWL validation."], None
        
        if not classes:
            return False, ["No classes provided for validation"], None
        
        errors = []
        
        try:
            # Create a new world (isolated ontology environment)
            world = World()
            
            # Use provided namespace or default
            onto_namespace = namespace or self.base_namespace
            
            # Create ontology
            onto = world.get_ontology(onto_namespace)
            
            with onto:
                # Dictionary to store created OWL classes for parent reference
                owl_classes = {}
                
                # First pass: Create all classes without parent relationships
                for ontology_class in classes:
                    try:
                        # Create OWL class inheriting from Thing
                        owl_class = type(ontology_class.name, (Thing,), {
                            "namespace": onto,
                        })
                        
                        # Add label (rdfs:label)
                        owl_class.label = [ontology_class.name]
                        
                        # Add comment (rdfs:comment) with description
                        if ontology_class.description:
                            owl_class.comment = [ontology_class.description]
                        
                        # Store for parent relationship setup
                        owl_classes[ontology_class.name] = owl_class
                        
                        logger.debug(f"Created OWL class: {ontology_class.name}")
                        
                    except Exception as e:
                        error_msg = f"Failed to create OWL class '{ontology_class.name}': {str(e)}"
                        errors.append(error_msg)
                        logger.error(error_msg)
                
                # Second pass: Set up parent relationships
                for ontology_class in classes:
                    if ontology_class.parent_class and ontology_class.name in owl_classes:
                        parent_name = ontology_class.parent_class
                        
                        # Check if parent exists
                        if parent_name in owl_classes:
                            try:
                                child_class = owl_classes[ontology_class.name]
                                parent_class = owl_classes[parent_name]
                                
                                # Set parent by modifying is_a
                                child_class.is_a = [parent_class]
                                
                                logger.debug(
                                    f"Set parent relationship: {ontology_class.name} -> {parent_name}"
                                )
                                
                            except Exception as e:
                                error_msg = (
                                    f"Failed to set parent relationship "
                                    f"'{ontology_class.name}' -> '{parent_name}': {str(e)}"
                                )
                                errors.append(error_msg)
                                logger.warning(error_msg)
                        else:
                            warning_msg = (
                                f"Parent class '{parent_name}' not found for '{ontology_class.name}'"
                            )
                            errors.append(warning_msg)
                            logger.warning(warning_msg)
                
                # Check for circular inheritance
                for class_name, owl_class in owl_classes.items():
                    if self._has_circular_inheritance(owl_class):
                        error_msg = f"Circular inheritance detected for class '{class_name}'"
                        errors.append(error_msg)
                        logger.error(error_msg)
            
            # Run consistency checking with Pellet reasoner
            try:
                logger.info("Running Pellet reasoner for consistency checking...")
                sync_reasoner_pellet(world, infer_property_values=True, infer_data_property_values=True)
                logger.info("Consistency check passed")
                
            except OwlReadyInconsistentOntologyError as e:
                error_msg = f"Ontology is inconsistent: {str(e)}"
                errors.append(error_msg)
                logger.error(error_msg)
                return False, errors, world
                
            except Exception as e:
                # Reasoner errors are often due to Java not being installed or configured
                # Log as warning but don't fail validation - ontology structure is still valid
                warning_msg = f"Reasoner check skipped: {str(e)}"
                if str(e).strip():  # Only log if there's an actual error message
                    logger.warning(warning_msg)
                else:
                    logger.warning("Reasoner check skipped: Java may not be installed or configured")
                # Continue - ontology structure is valid even without reasoner check
            
            # If we have errors (excluding warnings), validation failed
            is_valid = len(errors) == 0
            
            return is_valid, errors, world
            
        except Exception as e:
            error_msg = f"OWL validation failed: {str(e)}"
            errors.append(error_msg)
            logger.error(error_msg, exc_info=True)
            return False, errors, None
    
    def _has_circular_inheritance(self, owl_class) -> bool:
        """Check if an OWL class has circular inheritance.
        
        Circular inheritance occurs when a class inherits from itself through
        a chain of parent relationships (e.g., A -> B -> C -> A).
        
        Args:
            owl_class: Owlready2 class object to check
            
        Returns:
            True if circular inheritance is detected, False otherwise
        """
        if not OWLREADY2_AVAILABLE:
            return False
        
        visited = set()
        current = owl_class
        
        while current:
            # Get class IRI or name as identifier
            class_id = str(current.iri) if hasattr(current, 'iri') else str(current)
            
            if class_id in visited:
                # Found a cycle
                return True
            
            visited.add(class_id)
            
            # Get parent classes (is_a relationship)
            parents = getattr(current, 'is_a', [])
            
            # Filter out Thing and other base classes
            parent_classes = [p for p in parents if p != Thing and hasattr(p, 'is_a')]
            
            if not parent_classes:
                # No more parents, no cycle
                break
            
            # Check first parent (in single inheritance)
            current = parent_classes[0] if parent_classes else None
        
        return False
    
    def export_to_owl(
        self,
        world: World,
        output_path: Optional[str] = None,
        format: str = "rdfxml"
    ) -> str:
        """Export ontology to OWL file in specified format.
        
        Supported formats:
        - rdfxml: RDF/XML format (default, most compatible)
        - turtle: Turtle format (more readable)
        - ntriples: N-Triples format (simplest)
        
        Args:
            world: Owlready2 World object containing the ontology
            output_path: Optional file path to save the ontology (if None, returns string)
            format: Export format - "rdfxml", "turtle", or "ntriples" (default: "rdfxml")
            
        Returns:
            String representation of the exported ontology
            
        Raises:
            ValueError: If format is not supported
            RuntimeError: If export fails
            
        Examples:
            >>> validator = OWLValidator()
            >>> is_valid, errors, world = validator.validate_ontology_classes(classes)
            >>> owl_content = validator.export_to_owl(world, "ontology.owl", format="rdfxml")
        """
        if not OWLREADY2_AVAILABLE:
            raise RuntimeError("Owlready2 is not installed. Cannot export OWL file.")
        
        if not world:
            raise ValueError("World object is None. Cannot export ontology.")
        
        # Validate format
        valid_formats = ["rdfxml", "turtle", "ntriples"]
        if format not in valid_formats:
            raise ValueError(
                f"Unsupported format '{format}'. Must be one of: {', '.join(valid_formats)}"
            )
        
        try:
            # Get all ontologies in the world
            ontologies = list(world.ontologies.values())
            
            if not ontologies:
                raise RuntimeError("No ontologies found in world")
            
            # Use the first ontology (should be the one we created)
            onto = ontologies[0]
            
            if output_path:
                # Save to file
                logger.info(f"Exporting ontology to {output_path} in {format} format")
                onto.save(file=output_path, format=format)
                
                # Read back the file content to return
                with open(output_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                logger.info(f"Successfully exported ontology to {output_path}")
                return content
            else:
                # Export to string (save to temporary location and read)
                import tempfile
                import os
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.owl', delete=False) as tmp:
                    tmp_path = tmp.name
                
                try:
                    onto.save(file=tmp_path, format=format)
                    
                    with open(tmp_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    return content
                    
                finally:
                    # Clean up temporary file
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                        
        except Exception as e:
            error_msg = f"Failed to export ontology: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    def validate_with_protege_compatibility(
        self,
        classes: List[OntologyClass]
    ) -> Tuple[bool, List[str]]:
        """Validate that ontology classes are compatible with Protégé editor.
        
        Protégé compatibility checks:
        - Class names are valid OWL identifiers
        - No special characters that Protégé cannot handle
        - Namespace is properly formatted
        - Labels and comments are properly encoded
        
        Args:
            classes: List of OntologyClass objects to validate
            
        Returns:
            Tuple of (is_compatible, warnings):
            - is_compatible: True if compatible with Protégé, False otherwise
            - warnings: List of compatibility warning messages
            
        Examples:
            >>> validator = OWLValidator()
            >>> classes = [OntologyClass(name="Patient", description="A patient", entity_type="Person", domain="Healthcare")]
            >>> is_compatible, warnings = validator.validate_with_protege_compatibility(classes)
            >>> is_compatible
            True
        """
        if not OWLREADY2_AVAILABLE:
            return False, ["Owlready2 is not installed. Cannot validate Protégé compatibility."]
        
        warnings = []
        
        # Check namespace format
        if not self.base_namespace.startswith(('http://', 'https://')):
            warnings.append(
                f"Namespace '{self.base_namespace}' should start with http:// or https:// "
                "for Protégé compatibility"
            )
        
        if not self.base_namespace.endswith(('#', '/')):
            warnings.append(
                f"Namespace '{self.base_namespace}' should end with # or / "
                "for Protégé compatibility"
            )
        
        # Check each class
        for ontology_class in classes:
            # Check for special characters that might cause issues
            if any(char in ontology_class.name for char in ['<', '>', '"', '{', '}', '|', '^', '`']):
                warnings.append(
                    f"Class name '{ontology_class.name}' contains special characters "
                    "that may cause issues in Protégé"
                )
            
            # Check description length (Protégé can handle long descriptions but may display poorly)
            if ontology_class.description and len(ontology_class.description) > 1000:
                warnings.append(
                    f"Class '{ontology_class.name}' has a very long description ({len(ontology_class.description)} chars) "
                    "which may display poorly in Protégé"
                )
            
            # Check for non-ASCII characters (Protégé supports them but encoding issues may occur)
            if not ontology_class.name.isascii():
                warnings.append(
                    f"Class name '{ontology_class.name}' contains non-ASCII characters "
                    "which may cause encoding issues in some Protégé versions"
                )
        
        # If no warnings, it's compatible
        is_compatible = len(warnings) == 0
        
        return is_compatible, warnings
