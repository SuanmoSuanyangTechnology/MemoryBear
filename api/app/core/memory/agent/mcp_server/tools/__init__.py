"""
MCP Tools module.

This module contains all MCP tool implementations organized by functionality.

Tools are organized into the following modules:
- problem_tools: Question segmentation and extension
- retrieval_tools: Database and context retrieval
- verification_tools: Data verification
- summary_tools: Summarization and summary retrieval
- data_tools: Data type differentiation and writing
"""

# Import all tool modules to register them with the MCP server
from . import problem_tools
from . import retrieval_tools
from . import verification_tools
from . import summary_tools
from . import data_tools

__all__ = [
    'problem_tools',
    'retrieval_tools',
    'verification_tools',
    'summary_tools',
    'data_tools',
]
