"""
Unit tests for memory module output path utilities.

Tests cover:
- Output directory retrieval
- Output path generation
- Standard output file paths
- Legacy path resolution
"""

import os
import pytest
import tempfile
from pathlib import Path

from app.core.memory.utils.output_paths import (
    get_output_dir,
    get_output_path,
    ensure_output_dir,
    get_standard_output_path,
    resolve_legacy_path,
    OutputFiles,
)


class TestOutputDirectory:
    """Test cases for output directory management."""
    
    def test_get_output_dir_returns_string(self):
        """Test that get_output_dir returns a string."""
        output_dir = get_output_dir()
        assert isinstance(output_dir, str)
        assert len(output_dir) > 0
    
    def test_get_output_dir_contains_memory(self):
        """Test that output directory path contains 'memory'."""
        output_dir = get_output_dir()
        assert "memory" in output_dir.lower()
    
    def test_ensure_output_dir_creates_directory(self):
        """Test that ensure_output_dir creates the directory."""
        # This test uses the actual output directory
        ensure_output_dir()
        output_dir = get_output_dir()
        
        # Directory should exist after calling ensure_output_dir
        assert os.path.exists(output_dir)
        assert os.path.isdir(output_dir)


class TestOutputPath:
    """Test cases for output path generation."""
    
    def test_get_output_path_with_filename(self):
        """Test getting output path with a filename."""
        filename = "test_file.txt"
        path = get_output_path(filename)
        
        assert isinstance(path, str)
        assert filename in path
        assert path.endswith(filename)
    
    def test_get_output_path_with_subdirectory(self):
        """Test getting output path with subdirectory."""
        filepath = "subdir/test_file.txt"
        path = get_output_path(filepath)
        
        assert isinstance(path, str)
        assert "subdir" in path
        assert "test_file.txt" in path
    
    def test_get_output_path_consistency(self):
        """Test that multiple calls return consistent paths."""
        filename = "consistent_test.txt"
        path1 = get_output_path(filename)
        path2 = get_output_path(filename)
        
        assert path1 == path2


class TestStandardOutputFiles:
    """Test cases for standard output file constants."""
    
    def test_output_files_class_has_constants(self):
        """Test that OutputFiles class has expected constants."""
        assert hasattr(OutputFiles, "CHUNKER_TEST_OUTPUT")
        assert hasattr(OutputFiles, "PREPROCESSED_DATA")
        assert hasattr(OutputFiles, "PRUNED_DATA")
        assert hasattr(OutputFiles, "STATEMENT_EXTRACTION")
        assert hasattr(OutputFiles, "EXTRACTED_RESULT")
    
    def test_output_files_constants_are_strings(self):
        """Test that all OutputFiles constants are strings."""
        assert isinstance(OutputFiles.CHUNKER_TEST_OUTPUT, str)
        assert isinstance(OutputFiles.PREPROCESSED_DATA, str)
        assert isinstance(OutputFiles.PRUNED_DATA, str)
        assert isinstance(OutputFiles.STATEMENT_EXTRACTION, str)
        assert isinstance(OutputFiles.EXTRACTED_RESULT, str)
    
    def test_output_files_constants_have_extensions(self):
        """Test that output file constants have appropriate extensions."""
        assert OutputFiles.PREPROCESSED_DATA.endswith(".json")
        assert OutputFiles.PRUNED_DATA.endswith(".json")
        assert OutputFiles.STATEMENT_EXTRACTION.endswith(".txt")
        assert OutputFiles.EXTRACTED_RESULT.endswith(".json")
    
    def test_get_standard_output_path(self):
        """Test getting standard output paths."""
        path = get_standard_output_path(OutputFiles.PREPROCESSED_DATA)
        
        assert isinstance(path, str)
        assert OutputFiles.PREPROCESSED_DATA in path
    
    def test_all_standard_output_paths_are_valid(self):
        """Test that all standard output file paths can be generated."""
        standard_files = [
            OutputFiles.CHUNKER_TEST_OUTPUT,
            OutputFiles.PREPROCESSED_DATA,
            OutputFiles.PRUNED_DATA,
            OutputFiles.PRUNED_TERMINAL,
            OutputFiles.STATEMENT_EXTRACTION,
            OutputFiles.RELATIONS_OUTPUT,
            OutputFiles.EXTRACTED_TRIPLETS,
            OutputFiles.EXTRACTED_ENTITIES_EDGES,
            OutputFiles.EXTRACTED_TEMPORAL_DATA,
            OutputFiles.DEDUP_ENTITY_OUTPUT,
            OutputFiles.EXTRACTED_RESULT,
            OutputFiles.EXTRACTED_RESULT_READABLE,
            OutputFiles.USER_DASHBOARD,
            OutputFiles.SIGNBOARD,
        ]
        
        for file_constant in standard_files:
            path = get_standard_output_path(file_constant)
            assert isinstance(path, str)
            assert len(path) > 0
            assert file_constant in path


class TestLegacyPathResolution:
    """Test cases for legacy path resolution."""
    
    def test_resolve_legacy_path_with_pipeline_output(self):
        """Test resolving a legacy path containing 'pipeline_output'."""
        legacy_path = "app/core/memory/src/pipeline_output/test_file.txt"
        resolved_path = resolve_legacy_path(legacy_path)
        
        assert isinstance(resolved_path, str)
        assert "pipeline_output" not in resolved_path
        assert "test_file.txt" in resolved_path
        assert "memory" in resolved_path.lower()
    
    def test_resolve_legacy_path_without_pipeline_output(self):
        """Test that non-legacy paths are returned unchanged."""
        normal_path = "some/other/path/file.txt"
        resolved_path = resolve_legacy_path(normal_path)
        
        assert resolved_path == normal_path
    
    def test_resolve_legacy_path_various_formats(self):
        """Test resolving legacy paths in various formats."""
        test_cases = [
            "pipeline_output/file.txt",
            "./pipeline_output/file.txt",
            "../pipeline_output/file.txt",
            "src/pipeline_output/file.txt",
        ]
        
        for legacy_path in test_cases:
            resolved_path = resolve_legacy_path(legacy_path)
            assert "pipeline_output" not in resolved_path
            assert "file.txt" in resolved_path


class TestOutputPathIntegration:
    """Integration tests for output path utilities."""
    
    def test_output_path_can_be_used_for_file_operations(self):
        """Test that generated output paths can be used for file operations."""
        ensure_output_dir()
        
        test_filename = "integration_test.txt"
        test_path = get_output_path(test_filename)
        
        # Write a test file
        with open(test_path, "w") as f:
            f.write("test content")
        
        # Verify file exists
        assert os.path.exists(test_path)
        
        # Read the file
        with open(test_path, "r") as f:
            content = f.read()
        
        assert content == "test content"
        
        # Clean up
        os.remove(test_path)
    
    def test_standard_output_paths_are_in_same_directory(self):
        """Test that all standard output paths are in the same directory."""
        paths = [
            get_standard_output_path(OutputFiles.PREPROCESSED_DATA),
            get_standard_output_path(OutputFiles.STATEMENT_EXTRACTION),
            get_standard_output_path(OutputFiles.EXTRACTED_RESULT),
        ]
        
        # Extract directories from paths
        directories = [os.path.dirname(path) for path in paths]
        
        # All directories should be the same
        assert len(set(directories)) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
