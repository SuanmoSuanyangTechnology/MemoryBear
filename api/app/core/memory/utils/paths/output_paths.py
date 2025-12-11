"""
Output Path Management for Memory Module

This module provides utilities for managing output file paths in the memory module.
All output files are now centralized in the logs/memory-output directory.

Migration from: app/core/memory/src/pipeline_output/
Migration to: logs/memory-output/
"""

import os
from pathlib import Path
from typing import Optional

try:
    from app.core.config import settings
    USE_UNIFIED_CONFIG = True
except ImportError:
    USE_UNIFIED_CONFIG = False
    settings = None


def get_output_dir() -> str:
    """
    Get the base output directory for memory module files.

    Returns:
        str: Path to the output directory
    """
    if USE_UNIFIED_CONFIG:
        return settings.MEMORY_OUTPUT_DIR
    else:
        # Fallback to default path
        return "logs/memory-output"


def get_output_path(filename: str) -> str:
    """
    Get the full path for a memory module output file.

    Args:
        filename: Name of the output file

    Returns:
        str: Full path to the output file
    """
    if USE_UNIFIED_CONFIG:
        return settings.get_memory_output_path(filename)
    else:
        # Fallback to default path
        return os.path.join("logs/memory-output", filename)


def ensure_output_dir() -> None:
    """
    Ensure the output directory exists.
    Creates the directory if it doesn't exist.
    """
    if USE_UNIFIED_CONFIG:
        settings.ensure_memory_output_dir()
    else:
        # Fallback: create directory manually
        output_dir = Path("logs/memory-output")
        output_dir.mkdir(parents=True, exist_ok=True)


# Standard output file names (for consistency across the module)
class OutputFiles:
    """Standard output file names for the memory module."""

    # Chunker output
    CHUNKER_TEST_OUTPUT = "chunker_test_output.txt"

    # Preprocessing output
    PREPROCESSED_DATA = "preprocessed_data.json"
    PRUNED_DATA = "pruned_data.json"
    PRUNED_TERMINAL = "pruned_terminal.json"

    # Extraction output
    STATEMENT_EXTRACTION = "statement_extraction.txt"
    RELATIONS_OUTPUT = "relations_output.txt"
    EXTRACTED_TRIPLETS = "extracted_triplets.txt"
    EXTRACTED_ENTITIES_EDGES = "extracted_entities_edges.txt"
    EXTRACTED_TEMPORAL_DATA = "extracted_temporal_data.txt"

    # Deduplication output
    DEDUP_ENTITY_OUTPUT = "dedup_entity_output.txt"

    # Summary output
    EXTRACTED_RESULT = "extracted_result.json"
    EXTRACTED_RESULT_READABLE = "extracted_result_readable.txt"

    # Analytics output
    USER_DASHBOARD = "User-Dashboard.json"
    SIGNBOARD = "Signboard.json"


def get_standard_output_path(file_constant: str) -> str:
    """
    Get the full path for a standard output file.

    Args:
        file_constant: One of the OutputFiles constants

    Returns:
        str: Full path to the output file
    """
    return get_output_path(file_constant)


# Backward compatibility: Legacy path resolution
def resolve_legacy_path(legacy_path: str) -> str:
    """
    Resolve a legacy pipeline_output path to the new unified output path.

    This function helps migrate code that uses hardcoded pipeline_output paths.

    Args:
        legacy_path: Path containing 'pipeline_output'

    Returns:
        str: New path using unified output directory
    """
    if "pipeline_output" in legacy_path:
        # Extract filename from legacy path
        filename = os.path.basename(legacy_path)
        return get_output_path(filename)
    return legacy_path


# Aliases for backward compatibility with test code
get_memory_output_dir = get_output_dir
get_memory_output_path = get_output_path
