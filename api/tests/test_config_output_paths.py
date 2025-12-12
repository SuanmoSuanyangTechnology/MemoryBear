"""
Unit tests for memory module output paths using global configuration.

Tests cover:
- Configuration-based output directory
- File write operations using config paths
- File read operations using config paths
- Path consistency across modules
"""

import os
import pytest
import tempfile
from pathlib import Path

from app.core.config import settings


class TestConfigOutputDirectory:
    """Test cases for configuration-based output directory management."""
    
    def test_memory_output_dir_exists(self):
        """Test that MEMORY_OUTPUT_DIR is configured."""
        assert hasattr(settings, 'MEMORY_OUTPUT_DIR')
        assert isinstance(settings.MEMORY_OUTPUT_DIR, str)
        assert len(settings.MEMORY_OUTPUT_DIR) > 0
    
    def test_memory_output_dir_contains_logs(self):
        """Test that output directory is under logs."""
        assert "logs" in settings.MEMORY_OUTPUT_DIR
        assert "memory" in settings.MEMORY_OUTPUT_DIR.lower()
    
    def test_ensure_memory_output_dir_creates_directory(self):
        """Test that ensure_memory_output_dir creates the directory."""
        settings.ensure_memory_output_dir()
        
        # Directory should exist after calling ensure_memory_output_dir
        assert os.path.exists(settings.MEMORY_OUTPUT_DIR)
        assert os.path.isdir(settings.MEMORY_OUTPUT_DIR)
    
    def test_get_memory_output_path_without_filename(self):
        """Test getting output directory path without filename."""
        path = settings.get_memory_output_path()
        
        assert isinstance(path, str)
        # Normalize paths for comparison (handle Windows/Unix differences)
        assert os.path.normpath(path) == os.path.normpath(settings.MEMORY_OUTPUT_DIR)
    
    def test_get_memory_output_path_with_filename(self):
        """Test getting output path with a filename."""
        filename = "test_file.txt"
        path = settings.get_memory_output_path(filename)
        
        assert isinstance(path, str)
        assert filename in path
        assert path.endswith(filename)
        # Normalize paths for comparison (handle Windows/Unix differences)
        assert os.path.normpath(settings.MEMORY_OUTPUT_DIR) in os.path.normpath(path)


class TestFileWriteOperations:
    """Test cases for file write operations using config paths."""
    
    def setup_method(self):
        """Ensure output directory exists before each test."""
        settings.ensure_memory_output_dir()
    
    def test_write_to_config_path(self):
        """Test writing a file to the configured output path."""
        test_filename = "write_test.txt"
        test_content = "This is a test file"
        
        output_path = settings.get_memory_output_path(test_filename)
        
        # Write file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(test_content)
        
        # Verify file exists
        assert os.path.exists(output_path)
        assert os.path.isfile(output_path)
        
        # Clean up
        os.remove(output_path)
    
    def test_write_json_to_config_path(self):
        """Test writing a JSON file to the configured output path."""
        import json
        
        test_filename = "write_test.json"
        test_data = {"key": "value", "number": 42}
        
        output_path = settings.get_memory_output_path(test_filename)
        
        # Write JSON file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(test_data, f)
        
        # Verify file exists
        assert os.path.exists(output_path)
        
        # Clean up
        os.remove(output_path)
    
    def test_write_multiple_files_to_config_path(self):
        """Test writing multiple files to the configured output path."""
        test_files = [
            "multi_test_1.txt",
            "multi_test_2.txt",
            "multi_test_3.txt",
        ]
        
        for filename in test_files:
            output_path = settings.get_memory_output_path(filename)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"Content of {filename}")
            
            assert os.path.exists(output_path)
        
        # Clean up
        for filename in test_files:
            output_path = settings.get_memory_output_path(filename)
            if os.path.exists(output_path):
                os.remove(output_path)


class TestFileReadOperations:
    """Test cases for file read operations using config paths."""
    
    def setup_method(self):
        """Ensure output directory exists and create test files."""
        settings.ensure_memory_output_dir()
    
    def test_read_from_config_path(self):
        """Test reading a file from the configured output path."""
        test_filename = "read_test.txt"
        test_content = "This is test content for reading"
        
        output_path = settings.get_memory_output_path(test_filename)
        
        # Write file first
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(test_content)
        
        # Read file
        with open(output_path, "r", encoding="utf-8") as f:
            read_content = f.read()
        
        assert read_content == test_content
        
        # Clean up
        os.remove(output_path)
    
    def test_read_json_from_config_path(self):
        """Test reading a JSON file from the configured output path."""
        import json
        
        test_filename = "read_test.json"
        test_data = {"message": "Hello", "count": 123}
        
        output_path = settings.get_memory_output_path(test_filename)
        
        # Write JSON file first
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(test_data, f)
        
        # Read JSON file
        with open(output_path, "r", encoding="utf-8") as f:
            read_data = json.load(f)
        
        assert read_data == test_data
        
        # Clean up
        os.remove(output_path)
    
    def test_read_nonexistent_file_raises_error(self):
        """Test that reading a nonexistent file raises FileNotFoundError."""
        test_filename = "nonexistent_file.txt"
        output_path = settings.get_memory_output_path(test_filename)
        
        with pytest.raises(FileNotFoundError):
            with open(output_path, "r", encoding="utf-8") as f:
                f.read()


class TestPathConsistency:
    """Test cases for path consistency across modules."""
    
    def test_same_filename_returns_same_path(self):
        """Test that requesting the same filename returns the same path."""
        filename = "consistency_test.txt"
        
        path1 = settings.get_memory_output_path(filename)
        path2 = settings.get_memory_output_path(filename)
        
        assert path1 == path2
    
    def test_different_filenames_return_different_paths(self):
        """Test that different filenames return different paths."""
        filename1 = "file1.txt"
        filename2 = "file2.txt"
        
        path1 = settings.get_memory_output_path(filename1)
        path2 = settings.get_memory_output_path(filename2)
        
        assert path1 != path2
        assert filename1 in path1
        assert filename2 in path2
    
    def test_all_paths_in_same_directory(self):
        """Test that all output paths are in the same directory."""
        filenames = [
            "test1.txt",
            "test2.json",
            "test3.log",
        ]
        
        paths = [settings.get_memory_output_path(f) for f in filenames]
        directories = [os.path.dirname(p) for p in paths]
        
        # All directories should be the same
        assert len(set(directories)) == 1
        # Normalize paths for comparison (handle Windows/Unix differences)
        assert os.path.normpath(directories[0]) == os.path.normpath(settings.MEMORY_OUTPUT_DIR)


class TestStandardOutputFiles:
    """Test cases for standard output file paths."""
    
    def test_standard_files_can_be_accessed(self):
        """Test that standard output files can be accessed via config."""
        standard_files = [
            "chunker_test_output.txt",
            "preprocessed_data.json",
            "pruned_data.json",
            "statement_extraction.txt",
            "extracted_result.json",
        ]
        
        for filename in standard_files:
            path = settings.get_memory_output_path(filename)
            assert isinstance(path, str)
            assert filename in path
            # Normalize paths for comparison (handle Windows/Unix differences)
            assert os.path.normpath(settings.MEMORY_OUTPUT_DIR) in os.path.normpath(path)


class TestMigrationFromPipelineOutput:
    """Test cases to verify migration from pipeline_output."""
    
    def test_no_pipeline_output_in_config_paths(self):
        """Test that config paths don't contain 'pipeline_output'."""
        test_files = [
            "test1.txt",
            "test2.json",
            "subdir/test3.txt",
        ]
        
        for filename in test_files:
            path = settings.get_memory_output_path(filename)
            assert "pipeline_output" not in path
    
    def test_config_path_uses_logs_directory(self):
        """Test that config paths use the logs directory."""
        path = settings.get_memory_output_path("test.txt")
        assert "logs" in path
        assert "memory" in path.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
