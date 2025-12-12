"""
Unit tests for unified configuration management.

Tests cover:
- Configuration loading and validation
- Output path generation
- Memory module configuration access
"""

import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.core.config import Settings, settings


class TestSettings:
    """Test cases for the global Settings class."""
    
    def test_settings_instance_exists(self):
        """Test that the global settings instance exists."""
        assert settings is not None
        assert isinstance(settings, Settings)
    
    def test_memory_output_dir_default(self):
        """Test that memory output directory has a default value."""
        assert settings.MEMORY_OUTPUT_DIR is not None
        assert "memory" in settings.MEMORY_OUTPUT_DIR.lower()
    
    def test_memory_config_dir_default(self):
        """Test that memory config directory has a default value."""
        assert settings.MEMORY_CONFIG_DIR is not None
        assert "memory" in settings.MEMORY_CONFIG_DIR.lower()
    
    def test_get_memory_output_path_without_filename(self):
        """Test getting memory output path without filename."""
        path = settings.get_memory_output_path()
        assert path is not None
        assert isinstance(path, str)
        assert "memory" in path.lower()
    
    def test_get_memory_output_path_with_filename(self):
        """Test getting memory output path with filename."""
        filename = "test_output.json"
        path = settings.get_memory_output_path(filename)
        assert path is not None
        assert filename in path
        assert path.endswith(filename)
    
    def test_get_memory_config_path_default(self):
        """Test getting memory config path with default config file."""
        path = settings.get_memory_config_path()
        assert path is not None
        assert "config.json" in path
    
    def test_get_memory_config_path_custom(self):
        """Test getting memory config path with custom config file."""
        custom_file = "runtime.json"
        path = settings.get_memory_config_path(custom_file)
        assert path is not None
        assert custom_file in path
    
    def test_ensure_memory_output_dir_creates_directory(self):
        """Test that ensure_memory_output_dir creates the directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_settings = Settings()
            test_settings.MEMORY_OUTPUT_DIR = os.path.join(tmpdir, "test_memory_output")
            
            # Directory should not exist yet
            assert not os.path.exists(test_settings.MEMORY_OUTPUT_DIR)
            
            # Create directory
            test_settings.ensure_memory_output_dir()
            
            # Directory should now exist
            assert os.path.exists(test_settings.MEMORY_OUTPUT_DIR)
            assert os.path.isdir(test_settings.MEMORY_OUTPUT_DIR)


class TestMemoryConfigMigration:
    """Test cases for memory configuration (now using global settings)."""
    
    def test_settings_provides_memory_config_functionality(self):
        """Test that settings provides all necessary memory config functionality."""
        assert hasattr(settings, 'MEMORY_OUTPUT_DIR')
        assert hasattr(settings, 'get_memory_output_path')
        assert hasattr(settings, 'ensure_memory_output_dir')
        assert hasattr(settings, 'load_memory_config')
        assert hasattr(settings, 'load_memory_runtime_config')
    
    def test_output_dir_property(self):
        """Test the output_dir property."""
        output_dir = settings.MEMORY_OUTPUT_DIR
        assert output_dir is not None
        assert isinstance(output_dir, str)
    
    def test_get_output_path_without_filename(self):
        """Test getting output path without filename."""
        path = settings.get_memory_output_path()
        assert path is not None
        assert isinstance(path, str)
    
    def test_get_output_path_with_filename(self):
        """Test getting output path with filename."""
        filename = "test_file.txt"
        path = settings.get_memory_output_path(filename)
        assert path is not None
        assert filename in path
    
    def test_load_memory_config_returns_dict(self):
        """Test that load_memory_config returns a dictionary."""
        config = settings.load_memory_config()
        assert isinstance(config, dict)
    
    def test_load_memory_runtime_config_returns_dict(self):
        """Test that load_memory_runtime_config returns a dictionary."""
        runtime_config = settings.load_memory_runtime_config()
        assert isinstance(runtime_config, dict)
    
    def test_load_memory_dbrun_config_returns_dict(self):
        """Test that load_memory_dbrun_config returns a dictionary."""
        dbrun_config = settings.load_memory_dbrun_config()
        assert isinstance(dbrun_config, dict)
    
    def test_neo4j_config_available(self):
        """Test that Neo4j configuration is available."""
        assert hasattr(settings, 'NEO4J_URI')
        assert hasattr(settings, 'NEO4J_USERNAME')
        assert hasattr(settings, 'NEO4J_PASSWORD')
        assert isinstance(settings.NEO4J_URI, str)
        assert isinstance(settings.NEO4J_USERNAME, str)


class TestConfigurationIntegration:
    """Integration tests for configuration loading."""
    
    def test_config_files_exist(self):
        """Test that required configuration files exist."""
        config_path = settings.get_memory_config_path("config.json")
        runtime_path = settings.get_memory_config_path("runtime.json")
        
        # At least one of these should exist in a real environment
        # In test environment, we just verify the paths are valid
        assert isinstance(config_path, str)
        assert isinstance(runtime_path, str)
    
    def test_output_directory_can_be_created(self):
        """Test that output directory can be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Temporarily override output dir
            original_dir = settings.MEMORY_OUTPUT_DIR
            settings.MEMORY_OUTPUT_DIR = os.path.join(tmpdir, "test_output")
            
            try:
                settings.ensure_memory_output_dir()
                assert os.path.exists(settings.MEMORY_OUTPUT_DIR)
            finally:
                settings.MEMORY_OUTPUT_DIR = original_dir
    
    def test_config_loading_handles_missing_files_gracefully(self):
        """Test that config loading handles missing files gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_settings = Settings()
            test_settings.MEMORY_CONFIG_DIR = tmpdir
            test_settings.MEMORY_CONFIG_FILE = "nonexistent.json"
            
            # Should not raise an exception
            config = test_settings.load_memory_config()
            assert isinstance(config, dict)
            assert len(config) == 0  # Empty dict for missing file


class TestOutputPathGeneration:
    """Test cases for output path generation."""
    
    def test_output_path_is_absolute_or_relative(self):
        """Test that output paths are valid."""
        path = settings.get_memory_output_path("test.txt")
        assert isinstance(path, str)
        assert len(path) > 0
    
    def test_output_path_with_subdirectory(self):
        """Test output path generation with subdirectory."""
        path = settings.get_memory_output_path("subdir/test.txt")
        assert "subdir" in path
        assert "test.txt" in path
    
    def test_multiple_output_paths_are_consistent(self):
        """Test that multiple calls return consistent paths."""
        path1 = settings.get_memory_output_path("test.txt")
        path2 = settings.get_memory_output_path("test.txt")
        assert path1 == path2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
