"""Code execution engine"""
import os
from typing import Optional
from abc import ABC, abstractmethod

from app.config import get_config
from app.logger import get_logger
from app.models import RunnerOptions


class ExecutionResult:
    """Result of code execution"""

    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0, error: Optional[str] = None):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


class CodeExecutor(ABC):
    """Base code executor"""

    def __init__(self):
        self.logger = get_logger()
        self.config = get_config()

    @abstractmethod
    async def run(
            self,
            code: str,
            options: RunnerOptions,
            preload: str = "",
            timeout: Optional[int] = None
    ) -> ExecutionResult:
        pass

    def cleanup_temp_file(self, file_path: str) -> None:
        """Remove temporary file
        
        Args:
            file_path: Path to file to remove
        """
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            self.logger.warning(f"Failed to cleanup temp file {file_path}: {e}")
