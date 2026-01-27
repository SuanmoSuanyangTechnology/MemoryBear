"""Ontology class extraction from scenario descriptions using LLM.

This module provides the OntologyExtractor class for extracting ontology classes
from natural language scenario descriptions. It uses LLM-driven extraction combined
with two-layer validation (string validation + OWL semantic validation).

Classes:
    OntologyExtractor: Extracts ontology classes from scenario descriptions
"""

import asyncio
import logging
import time
from typing import List, Optional

from app.core.memory.llm_tools.openai_client import OpenAIClient
from app.core.memory.models.ontology_models import (
    OntologyClass,
    OntologyExtractionResponse,
)
from app.core.memory.utils.validation.ontology_validator import OntologyValidator
from app.core.memory.utils.validation.owl_validator import OWLValidator
from app.core.memory.utils.prompt.prompt_utils import render_ontology_extraction_prompt


logger = logging.getLogger(__name__)


class OntologyExtractor:
    """Extractor for ontology classes from scenario descriptions.
    
    This extractor uses LLM to identify abstract classes and concepts from
    natural language scenario descriptions, following OWL ontology engineering
    standards. It performs two-layer validation:
    1. String validation (naming conventions, reserved words, duplicates)
    2. OWL semantic validation (consistency checking, circular inheritance)
    
    Attributes:
        llm_client: OpenAI client for LLM calls
        validator: String validator for class names and descriptions
        owl_validator: OWL validator for semantic validation
    """
    
    def __init__(self, llm_client: OpenAIClient):
        """Initialize the OntologyExtractor.
        
        Args:
            llm_client: OpenAIClient instance for LLM processing
        """
        self.llm_client = llm_client
        self.validator = OntologyValidator()
        self.owl_validator = OWLValidator()
        
        logger.info("OntologyExtractor initialized")
    
    async def extract_ontology_classes(
        self,
        scenario: str,
        domain: Optional[str] = None,
        max_classes: int = 15,
        min_classes: int = 5,
        enable_owl_validation: bool = True,
        llm_temperature: float = 0.3,
        llm_max_tokens: int = 2000,
        max_description_length: int = 500,
        timeout: Optional[float] = None,
    ) -> OntologyExtractionResponse:
        """Extract ontology classes from a scenario description.
        
        This is the main extraction method that orchestrates the entire process:
        1. Call LLM to extract ontology classes
        2. Perform first-layer validation (string validation and cleaning)
        3. Perform second-layer validation (OWL semantic validation)
        4. Filter invalid classes based on validation errors
        5. Return validated ontology classes
        
        Args:
            scenario: Natural language scenario description
            domain: Optional domain hint (e.g., "Healthcare", "Education")
            max_classes: Maximum number of classes to extract (default: 15)
            min_classes: Minimum number of classes to extract (default: 5)
            enable_owl_validation: Whether to enable OWL validation (default: True)
            llm_temperature: LLM temperature parameter (default: 0.3)
            llm_max_tokens: LLM max tokens parameter (default: 2000)
            max_description_length: Maximum description length (default: 500)
            timeout: Optional timeout in seconds for LLM call (default: None, no timeout)
            
        Returns:
            OntologyExtractionResponse containing validated ontology classes
            
        Raises:
            ValueError: If scenario is empty or invalid
            asyncio.TimeoutError: If extraction times out
            
        Examples:
            >>> extractor = OntologyExtractor(llm_client)
            >>> response = await extractor.extract_ontology_classes(
            ...     scenario="A hospital manages patient records...",
            ...     domain="Healthcare",
            ...     max_classes=10,
            ...     timeout=30.0
            ... )
            >>> len(response.classes)
            7
        """
        # Start timing
        start_time = time.time()
        
        # Validate input
        if not scenario or not scenario.strip():
            logger.error("Scenario description is empty")
            raise ValueError("Scenario description cannot be empty")
        
        scenario = scenario.strip()
        
        logger.info(
            f"Starting ontology extraction - scenario_length={len(scenario)}, "
            f"domain={domain}, max_classes={max_classes}, min_classes={min_classes}, "
            f"timeout={timeout}"
        )
        
        try:
            # Step 1: Call LLM for extraction with timeout
            logger.info("Step 1: Calling LLM for ontology extraction")
            llm_start_time = time.time()
            
            if timeout is not None:
                # Wrap LLM call with timeout
                try:
                    response = await asyncio.wait_for(
                        self._call_llm_for_extraction(
                            scenario=scenario,
                            domain=domain,
                            max_classes=max_classes,
                            llm_temperature=llm_temperature,
                            llm_max_tokens=llm_max_tokens,
                        ),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    llm_duration = time.time() - llm_start_time
                    logger.error(
                        f"LLM extraction timed out after {timeout} seconds "
                        f"(actual duration: {llm_duration:.2f}s)"
                    )
                    # Return empty response on timeout
                    return OntologyExtractionResponse(
                        classes=[],
                        domain=domain or "Unknown",
                        namespace=None,
                    )
            else:
                # No timeout specified, call directly
                response = await self._call_llm_for_extraction(
                    scenario=scenario,
                    domain=domain,
                    max_classes=max_classes,
                    llm_temperature=llm_temperature,
                    llm_max_tokens=llm_max_tokens,
                )
            
            llm_duration = time.time() - llm_start_time
            logger.info(
                f"LLM returned {len(response.classes)} classes in {llm_duration:.2f}s"
            )
            
            # Step 2: First-layer validation (string validation and cleaning)
            logger.info("Step 2: Performing first-layer validation (string validation)")
            validation_start_time = time.time()
            
            response = self._validate_and_clean(
                response=response,
                max_description_length=max_description_length,
            )
            
            validation_duration = time.time() - validation_start_time
            logger.info(
                f"After first-layer validation: {len(response.classes)} classes remain "
                f"(validation took {validation_duration:.2f}s)"
            )
            
            # Check if we have enough classes after first-layer validation
            if len(response.classes) < min_classes:
                logger.warning(
                    f"Only {len(response.classes)} classes remain after validation, "
                    f"which is below minimum of {min_classes}"
                )
            
            # Step 3: Second-layer validation (OWL semantic validation)
            if enable_owl_validation and response.classes:
                logger.info("Step 3: Performing second-layer validation (OWL validation)")
                owl_start_time = time.time()
                
                is_valid, errors, world = self.owl_validator.validate_ontology_classes(
                    classes=response.classes,
                    namespace=response.namespace,
                )
                
                owl_duration = time.time() - owl_start_time
                
                if not is_valid:
                    logger.warning(
                        f"OWL validation found {len(errors)} issues in {owl_duration:.2f}s: {errors}"
                    )
                    
                    # Filter invalid classes based on errors
                    response = self._filter_invalid_classes(
                        response=response,
                        errors=errors,
                    )
                    
                    logger.info(
                        f"After second-layer validation: {len(response.classes)} classes remain"
                    )
                else:
                    logger.info(f"OWL validation passed successfully in {owl_duration:.2f}s")
            else:
                if not enable_owl_validation:
                    logger.info("Step 3: OWL validation disabled, skipping")
                else:
                    logger.info("Step 3: No classes to validate, skipping OWL validation")
            
            # Calculate total duration
            total_duration = time.time() - start_time
            
            # Log extraction statistics
            logger.info(
                f"Ontology extraction completed - "
                f"final_class_count={len(response.classes)}, "
                f"domain={response.domain}, "
                f"total_duration={total_duration:.2f}s, "
                f"llm_duration={llm_duration:.2f}s"
            )
            
            return response
            
        except asyncio.TimeoutError:
            # Re-raise timeout errors
            total_duration = time.time() - start_time
            logger.error(
                f"Ontology extraction timed out after {timeout} seconds "
                f"(total duration: {total_duration:.2f}s)",
                exc_info=True
            )
            raise
        except Exception as e:
            total_duration = time.time() - start_time
            logger.error(
                f"Ontology extraction failed after {total_duration:.2f}s: {str(e)}",
                exc_info=True
            )
            # Return empty response on failure
            return OntologyExtractionResponse(
                classes=[],
                domain=domain or "Unknown",
                namespace=None,
            )
    
    async def _call_llm_for_extraction(
        self,
        scenario: str,
        domain: Optional[str],
        max_classes: int,
        llm_temperature: float,
        llm_max_tokens: int,
    ) -> OntologyExtractionResponse:
        """Call LLM to extract ontology classes from scenario.
        
        This method renders the extraction prompt using the Jinja2 template
        and calls the LLM with structured output to get ontology classes.
        
        Args:
            scenario: Scenario description text
            domain: Optional domain hint
            max_classes: Maximum number of classes to extract
            llm_temperature: LLM temperature parameter
            llm_max_tokens: LLM max tokens parameter
            
        Returns:
            OntologyExtractionResponse from LLM
            
        Raises:
            Exception: If LLM call fails
        """
        try:
            # Render prompt using template
            prompt_content = await render_ontology_extraction_prompt(
                scenario=scenario,
                domain=domain,
                max_classes=max_classes,
                json_schema=OntologyExtractionResponse.model_json_schema(),
            )
            
            logger.debug(f"Rendered prompt length: {len(prompt_content)}")
            
            # Create messages for LLM
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are an expert ontology engineer specializing in knowledge "
                        "representation and OWL standards. Extract ontology classes from "
                        "scenario descriptions following the provided instructions. "
                        "Return valid JSON conforming to the schema."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt_content,
                },
            ]
            
            # Call LLM with structured output
            logger.debug(
                f"Calling LLM with temperature={llm_temperature}, "
                f"max_tokens={llm_max_tokens}"
            )
            
            response = await self.llm_client.response_structured(
                messages=messages,
                response_model=OntologyExtractionResponse,
            )
            
            logger.info(
                f"LLM extraction successful - extracted {len(response.classes)} classes"
            )
            
            return response
            
        except Exception as e:
            logger.error(
                f"LLM extraction failed: {str(e)}",
                exc_info=True
            )
            raise
    
    def _validate_and_clean(
        self,
        response: OntologyExtractionResponse,
        max_description_length: int,
    ) -> OntologyExtractionResponse:
        """Perform first-layer validation: string validation and cleaning.
        
        This method validates and cleans the extracted ontology classes:
        1. Validate class names (PascalCase, no reserved words)
        2. Sanitize invalid class names
        3. Truncate long descriptions
        4. Remove duplicate classes
        
        Args:
            response: OntologyExtractionResponse from LLM
            max_description_length: Maximum description length
            
        Returns:
            Cleaned OntologyExtractionResponse
        """
        if not response.classes:
            logger.debug("No classes to validate")
            return response
        
        logger.debug(f"Validating {len(response.classes)} classes")
        
        validated_classes = []
        
        for ontology_class in response.classes:
            # Validate class name
            is_valid, error_msg = self.validator.validate_class_name(
                ontology_class.name
            )
            
            if not is_valid:
                logger.warning(
                    f"Invalid class name '{ontology_class.name}': {error_msg}"
                )
                
                # Attempt to sanitize
                sanitized_name = self.validator.sanitize_class_name(
                    ontology_class.name
                )
                
                logger.info(
                    f"Sanitized class name: '{ontology_class.name}' -> '{sanitized_name}'"
                )
                
                # Update class name
                ontology_class.name = sanitized_name
                
                # Re-validate sanitized name
                is_valid, error_msg = self.validator.validate_class_name(
                    sanitized_name
                )
                
                if not is_valid:
                    logger.error(
                        f"Failed to sanitize class name '{ontology_class.name}': {error_msg}. "
                        "Skipping this class."
                    )
                    continue
            
            # Truncate description if too long
            if ontology_class.description:
                original_length = len(ontology_class.description)
                ontology_class.description = self.validator.truncate_description(
                    ontology_class.description,
                    max_length=max_description_length,
                )
                
                if len(ontology_class.description) < original_length:
                    logger.debug(
                        f"Truncated description for '{ontology_class.name}': "
                        f"{original_length} -> {len(ontology_class.description)} chars"
                    )
            
            validated_classes.append(ontology_class)
        
        # Remove duplicates (case-insensitive)
        original_count = len(validated_classes)
        validated_classes = self.validator.remove_duplicates(validated_classes)
        
        if len(validated_classes) < original_count:
            logger.info(
                f"Removed {original_count - len(validated_classes)} duplicate classes"
            )
        
        # Return cleaned response
        return OntologyExtractionResponse(
            classes=validated_classes,
            domain=response.domain,
            namespace=response.namespace,
        )
    
    def _filter_invalid_classes(
        self,
        response: OntologyExtractionResponse,
        errors: List[str],
    ) -> OntologyExtractionResponse:
        """Filter invalid classes based on OWL validation errors.
        
        This method analyzes OWL validation errors and removes classes
        that caused validation failures (e.g., circular inheritance,
        inconsistencies).
        
        Args:
            response: OntologyExtractionResponse to filter
            errors: List of error messages from OWL validation
            
        Returns:
            Filtered OntologyExtractionResponse
        """
        if not errors:
            return response
        
        logger.debug(f"Filtering classes based on {len(errors)} OWL validation errors")
        
        # Extract class names mentioned in errors
        invalid_class_names = set()
        
        for error in errors:
            # Look for class names in error messages
            for ontology_class in response.classes:
                if ontology_class.name in error:
                    invalid_class_names.add(ontology_class.name)
                    logger.debug(
                        f"Class '{ontology_class.name}' marked as invalid due to error: {error}"
                    )
        
        # Filter out invalid classes
        if invalid_class_names:
            original_count = len(response.classes)
            
            filtered_classes = [
                c for c in response.classes
                if c.name not in invalid_class_names
            ]
            
            logger.info(
                f"Filtered out {original_count - len(filtered_classes)} invalid classes: "
                f"{invalid_class_names}"
            )
            
            return OntologyExtractionResponse(
                classes=filtered_classes,
                domain=response.domain,
                namespace=response.namespace,
            )
        
        return response
