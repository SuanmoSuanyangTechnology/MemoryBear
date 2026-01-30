"""Logging configuration"""
import logging
import sys
from typing import Optional

from app.config import get_config

_logger: Optional[logging.Logger] = None


def setup_logger() -> logging.Logger:
    """Setup application logger"""
    global _logger

    if _logger is not None:
        return _logger

    config = get_config()

    # Create logger
    _logger = logging.getLogger("sandbox")
    _logger.setLevel(logging.DEBUG if config.app.debug else logging.INFO)

    # 只在 logger 没有 handler 时才添加
    if not _logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG if config.app.debug else logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)

        _logger.addHandler(handler)

    return _logger


def get_logger() -> logging.Logger:
    """Get application logger"""
    if _logger is None:
        return setup_logger()
    return _logger
