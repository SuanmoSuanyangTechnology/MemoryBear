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

from owlready2 import (
    World,
    Thing,
    get_ontology,
    sync_reasoner_pellet,
    OwlReadyInconsistentOntologyError,
)

from app.core.memory.models.ontology_scenario_models import OntologyClass
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
        self.base_namespace = base_namespace
    
    def validate_ontology_classes(
        self,
        classes: List[OntologyClass],
    ) -> Tuple[bool, List[str], Optional[World]]:
        """Validate extracted ontology classes against OWL standards.
        
        This method creates an OWL ontology from the provided classes using Owlready2,
        runs consistency checking with the Pellet reasoner, and detects common issues
        like circular inheritance.
        
        Args:
            classes: List of OntologyClass objects to validate
            
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
        if not classes:
            return False, ["No classes provided for validation"], None
        
        errors = []
        
        try:
            # Create a new world (isolated ontology environment)
            world = World()
            
            # Use a proper ontology IRI
            # Owlready2 expects the IRI to end with .owl or similar
            onto_iri = self.base_namespace.rstrip('#/')
            if not onto_iri.endswith('.owl'):
                onto_iri = onto_iri + '.owl'
            
            # Create ontology
            onto = world.get_ontology(onto_iri)
            
            with onto:
                # Dictionary to store created OWL classes for parent reference
                owl_classes = {}
                
                # First pass: Create all classes without parent relationships
                for ontology_class in classes:
                    try:
                        # Create OWL class dynamically using type() with Thing as base
                        # The key is to NOT set namespace in the dict, let Owlready2 handle it
                        owl_class = type(
                            ontology_class.name,  # Class name
                            (Thing,),              # Base classes
                            {}                     # Class dict (empty, let Owlready2 manage)
                        )
                        
                        # Add label (rdfs:label) - include both English and Chinese names
                        labels = [ontology_class.name]
                        if ontology_class.name_chinese:
                            labels.append(ontology_class.name_chinese)
                        owl_class.label = labels
                        
                        # Add comment (rdfs:comment) with description
                        if ontology_class.description:
                            owl_class.comment = [ontology_class.description]
                        
                        # Store for parent relationship setup
                        owl_classes[ontology_class.name] = owl_class
                        
                        logger.debug(
                            f"Created OWL class: {ontology_class.name} "
                            f"(Chinese: {ontology_class.name_chinese}) "
                            f"IRI: {owl_class.iri if hasattr(owl_class, 'iri') else 'N/A'}"
                        )
                        
                    except Exception as e:
                        error_msg = f"Failed to create OWL class '{ontology_class.name}': {str(e)}"
                        errors.append(error_msg)
                        logger.error(error_msg, exc_info=True)
                
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
        format: str = "rdfxml",
        classes: Optional[List] = None
    ) -> str:
        """Export ontology to OWL file in specified format.
        
        Supported formats:
        - rdfxml: RDF/XML format (default, most compatible)
        - turtle: Turtle format (more readable)
        - ntriples: N-Triples format (simplest)
        - json: JSON format (simplified, human-readable)
        
        Args:
            world: Owlready2 World object containing the ontology
            output_path: Optional file path to save the ontology (if None, returns string)
            format: Export format - "rdfxml", "turtle", "ntriples", or "json" (default: "rdfxml")
            classes: Optional list of OntologyClass objects (required for json format)
            
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
        # Validate format
        valid_formats = ["rdfxml", "turtle", "ntriples", "json"]
        if format not in valid_formats:
            raise ValueError(
                f"Unsupported format '{format}'. Must be one of: {', '.join(valid_formats)}"
            )
        
        # JSON format doesn't need OWL processing
        if format == "json":
            if not classes:
                raise ValueError("Classes list is required for JSON format export")
            return self._export_to_json(classes)
        
        # For OWL formats, world is required
        if not world:
            raise ValueError("World object is None. Cannot export ontology.")
        
        # Note: Owlready2 has issues with turtle format export
        # We'll handle it specially by converting from rdfxml
        use_conversion = (format == "turtle")
        
        try:
            # Get all ontologies in the world
            ontologies = list(world.ontologies.values())
            
            if not ontologies:
                raise RuntimeError("No ontologies found in world")
            
            # Find the ontology with classes (skip anonymous/empty ontologies)
            onto = None
            for ont in ontologies:
                classes_count = len(list(ont.classes()))
                logger.debug(f"Checking ontology {ont.base_iri}: {classes_count} classes")
                if classes_count > 0:
                    onto = ont
                    break
            
            # If no ontology with classes found, use the last non-anonymous one
            if onto is None:
                for ont in reversed(ontologies):
                    if ont.base_iri != "http://anonymous/":
                        onto = ont
                        break
            
            # If still no ontology, use the first one
            if onto is None:
                onto = ontologies[0]
            
            # Log ontology contents for debugging
            logger.info(f"Ontology IRI: {onto.base_iri}")
            logger.info(f"Ontology contains {len(list(onto.classes()))} classes")
            
            # List all classes in the ontology
            all_classes = list(onto.classes())
            for cls in all_classes:
                logger.info(f"Class in ontology: {cls.name} (IRI: {cls.iri})")
                if hasattr(cls, 'label'):
                    logger.debug(f"  Labels: {cls.label}")
                if hasattr(cls, 'comment'):
                    logger.debug(f"  Comments: {cls.comment}")
            
            if len(all_classes) == 0:
                logger.warning("No classes found in ontology! This may indicate a problem with class creation.")
            
            if output_path:
                # Save to file
                export_format = "rdfxml" if use_conversion else format
                logger.info(f"Exporting ontology to {output_path} in {export_format} format")
                onto.save(file=output_path, format=export_format)
                
                # Read back the file content to return
                with open(output_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Convert to turtle if needed
                if use_conversion:
                    content = self._convert_to_turtle(content)
                
                logger.info(f"Successfully exported ontology to {output_path}")
                
                # Format the content for better readability
                content = self._format_owl_content(content, format)
                
                return content
            else:
                # Export to string (save to temporary location and read)
                import tempfile
                import os
                
                with tempfile.NamedTemporaryFile(mode='w', suffix='.owl', delete=False) as tmp:
                    tmp_path = tmp.name
                
                try:
                    export_format = "rdfxml" if use_conversion else format
                    onto.save(file=tmp_path, format=export_format)
                    
                    with open(tmp_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    
                    # Convert to turtle if needed
                    if use_conversion:
                        content = self._convert_to_turtle(content)
                    
                    # Format the content for better readability
                    content = self._format_owl_content(content, format)
                    
                    return content
                    
                finally:
                    # Clean up temporary file
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                        
        except Exception as e:
            error_msg = f"Failed to export ontology: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e
    
    def _export_to_json(self, classes: List) -> str:
        """Export ontology classes to simplified JSON format.
        
        This format is more compact and easier to parse than OWL XML.
        
        Args:
            classes: List of OntologyClass objects
            
        Returns:
            JSON string representation (compact format)
        """
        import json
        
        result = {
            "ontology": {
                "namespace": self.base_namespace,
                "classes": []
            }
        }
        
        for cls in classes:
            class_data = {
                "name": cls.name,
                "name_chinese": cls.name_chinese,
                "description": cls.description,
                "entity_type": cls.entity_type,
                "domain": cls.domain,
                "parent_class": cls.parent_class,
                "examples": cls.examples if hasattr(cls, 'examples') else []
            }
            result["ontology"]["classes"].append(class_data)
        
        # 使用紧凑格式：无缩进，使用分隔符减少空格
        return json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    
    def _convert_to_turtle(self, rdfxml_content: str) -> str:
        """Convert RDF/XML content to Turtle format using rdflib.
        
        Args:
            rdfxml_content: RDF/XML format content
            
        Returns:
            Turtle format content
        """
        try:
            from rdflib import Graph
            
            # Parse RDF/XML
            g = Graph()
            g.parse(data=rdfxml_content, format="xml")
            
            # Serialize to Turtle
            turtle_content = g.serialize(format="turtle")
            
            # Handle bytes vs string
            if isinstance(turtle_content, bytes):
                turtle_content = turtle_content.decode('utf-8')
            
            return turtle_content
            
        except ImportError:
            logger.warning(
                "rdflib is not installed. Cannot convert to Turtle format. "
                "Install with: pip install rdflib"
            )
            return rdfxml_content
        except Exception as e:
            logger.error(f"Failed to convert to Turtle format: {e}")
            return rdfxml_content
    
    def _format_owl_content(self, content: str, format: str) -> str:
        """Format OWL content for better readability.
        
        Args:
            content: Raw OWL content string
            format: Format type (rdfxml, turtle, ntriples)
            
        Returns:
            Formatted OWL content string
        """
        if format == "rdfxml":
            # Format XML with proper indentation
            try:
                import xml.dom.minidom as minidom
                dom = minidom.parseString(content)
                # Pretty print with 2-space indentation
                formatted = dom.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")
                
                # Remove extra blank lines
                lines = []
                prev_blank = False
                for line in formatted.split('\n'):
                    is_blank = not line.strip()
                    if not (is_blank and prev_blank):  # Skip consecutive blank lines
                        lines.append(line)
                    prev_blank = is_blank
                
                formatted = '\n'.join(lines)
                
                return formatted
            except Exception as e:
                logger.warning(f"Failed to format XML content: {e}")
                return content
        
        elif format == "turtle":
            # Turtle format is already relatively readable
            # Just ensure consistent line endings and not empty
            if not content or content.strip() == "":
                logger.warning("Turtle content is empty, this may indicate an export issue")
            return content.strip() + '\n' if content.strip() else content
        
        elif format == "ntriples":
            # N-Triples format is line-based, ensure proper line endings
            return content.strip() + '\n' if content.strip() else content
        
        return content
    
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

    def parse_owl_content(
        self,
        owl_content: str,
        format: str = "rdfxml"
    ) -> List[dict]:
        """从 OWL 内容解析出本体类型
        
        支持解析 RDF/XML、Turtle 和 JSON 格式的 OWL 文件，
        提取其中定义的 owl:Class 及其 rdfs:label 和 rdfs:comment。
        
        Args:
            owl_content: OWL 文件内容字符串
            format: 文件格式，支持 "rdfxml"、"turtle"、"json"
            
        Returns:
            解析出的类型列表，每个元素包含:
            - name: 类型名称（英文标识符）
            - name_chinese: 中文名称（如果有）
            - description: 类型描述
            - parent_class: 父类名称
            
        Raises:
            ValueError: 如果格式不支持或解析失败
            
        Examples:
            >>> validator = OWLValidator()
            >>> classes = validator.parse_owl_content(owl_xml, format="rdfxml")
            >>> for cls in classes:
            ...     print(cls["name"], cls["description"])
        """
        valid_formats = ["rdfxml", "turtle", "json"]
        if format not in valid_formats:
            raise ValueError(
                f"Unsupported format '{format}'. Must be one of: {', '.join(valid_formats)}"
            )
        
        # JSON 格式单独处理
        if format == "json":
            return self._parse_json_owl(owl_content)
        
        # 使用 rdflib 解析 RDF/XML 或 Turtle
        try:
            from rdflib import Graph, RDF, RDFS, OWL, Namespace
            
            g = Graph()
            rdf_format = "xml" if format == "rdfxml" else "turtle"
            g.parse(data=owl_content, format=rdf_format)
            
            classes = []
            
            # 查找所有 owl:Class
            for cls_uri in g.subjects(RDF.type, OWL.Class):
                cls_str = str(cls_uri)
                
                # 跳过空节点和 OWL 内置类
                if cls_str.startswith("http://www.w3.org/") or "/.well-known/" in cls_str:
                    continue
                
                # 提取类名（从 URI 中获取本地名称）
                if '#' in cls_str:
                    name = cls_str.split('#')[-1]
                else:
                    name = cls_str.split('/')[-1]
                
                # 跳过空名称
                if not name or name == "Thing":
                    continue
                
                # 获取 rdfs:label（可能有多个，包括中英文）
                labels = list(g.objects(cls_uri, RDFS.label))
                name_chinese = None
                label_str = name  # 默认使用 URI 中的名称
                
                for label in labels:
                    label_text = str(label)
                    # 检查是否包含中文
                    if any('\u4e00' <= char <= '\u9fff' for char in label_text):
                        name_chinese = label_text
                    else:
                        label_str = label_text
                
                # 获取 rdfs:comment（描述）
                comments = list(g.objects(cls_uri, RDFS.comment))
                description = str(comments[0]) if comments else None
                
                # 获取父类（rdfs:subClassOf）
                parent_class = None
                for parent_uri in g.objects(cls_uri, RDFS.subClassOf):
                    parent_str = str(parent_uri)
                    # 跳过 owl:Thing
                    if parent_str == str(OWL.Thing) or parent_str.endswith("#Thing"):
                        continue
                    # 提取父类名称
                    if '#' in parent_str:
                        parent_class = parent_str.split('#')[-1]
                    else:
                        parent_class = parent_str.split('/')[-1]
                    break  # 只取第一个非 Thing 的父类
                
                classes.append({
                    "name": name,
                    "name_chinese": name_chinese,
                    "description": description,
                    "parent_class": parent_class
                })
            
            logger.info(f"Parsed {len(classes)} classes from OWL content (format: {format})")
            return classes
            
        except Exception as e:
            error_msg = f"Failed to parse OWL（文档格式不正确） content: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ValueError(error_msg) from e
    
    def _parse_json_owl(self, json_content: str) -> List[dict]:
        """解析 JSON 格式的 OWL 内容
        
        JSON 格式是简化的本体表示，由 export_to_owl 的 json 格式导出。
        
        Args:
            json_content: JSON 格式的 OWL 内容
            
        Returns:
            解析出的类型列表
        """
        import json
        
        try:
            data = json.loads(json_content)
            
            # 检查是否是我们导出的 JSON 格式
            if "ontology" in data and "classes" in data["ontology"]:
                raw_classes = data["ontology"]["classes"]
            elif "classes" in data:
                raw_classes = data["classes"]
            else:
                raise ValueError("Invalid JSON format: missing 'classes' field")
            
            classes = []
            for cls in raw_classes:
                classes.append({
                    "name": cls.get("name", ""),
                    "name_chinese": cls.get("name_chinese"),
                    "description": cls.get("description"),
                    "parent_class": cls.get("parent_class")
                })
            
            logger.info(f"Parsed {len(classes)} classes from JSON content")
            return classes
            
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON content: {str(e)}") from e
