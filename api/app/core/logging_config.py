import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.sensitive_filter import SensitiveDataFilter


class SensitiveDataLoggingFilter(logging.Filter):
    """日志过滤器：自动过滤敏感信息"""
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录中的敏感信息
        
        Args:
            record: 日志记录
            
        Returns:
            True表示允许记录，False表示拒绝
        """
        # 过滤消息中的敏感信息
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            record.msg = SensitiveDataFilter.filter_string(record.msg)
        
        # 过滤参数中的敏感信息
        if hasattr(record, 'args') and record.args:
            if isinstance(record.args, dict):
                record.args = SensitiveDataFilter.filter_dict(record.args)
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    SensitiveDataFilter.filter_string(str(arg)) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        
        return True


class LoggingConfig:
    """全局日志配置类"""
    
    _initialized = False
    _memory_loggers_initialized = False
    _prompt_logger = None
    _template_logger = None
    _timing_logger = None
    _agent_loggers = {}
    
    @classmethod
    def setup_logging(cls) -> None:
        """初始化全局日志配置"""
        if cls._initialized:
            return
            
        # 创建日志目录
        log_dir = Path(settings.LOG_FILE_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # 配置根日志器
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
        
        # 清除现有处理器
        root_logger.handlers.clear()
        
        # 创建格式化器
        formatter = logging.Formatter(
            fmt=settings.LOG_FORMAT,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 创建敏感信息过滤器
        sensitive_filter = SensitiveDataLoggingFilter()
        
        # 控制台处理器
        if settings.LOG_TO_CONSOLE:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
            console_handler.addFilter(sensitive_filter)
            root_logger.addHandler(console_handler)
        
        # 文件处理器（带轮转）
        if settings.LOG_TO_FILE:
            file_handler = logging.handlers.RotatingFileHandler(
                filename=settings.LOG_FILE_PATH,
                maxBytes=settings.LOG_MAX_SIZE,
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
            file_handler.addFilter(sensitive_filter)
            root_logger.addHandler(file_handler)
        
        cls._initialized = True
        
        # Initialize memory module logging
        cls.setup_memory_logging()
        
        # 记录初始化完成
        logger = logging.getLogger(__name__)
        logger.info("全局日志系统初始化完成")
    
    @classmethod
    def setup_memory_logging(cls) -> None:
        """Initialize memory module specific loggers.
        
        Called automatically by setup_logging() or can be called independently.
        Sets up:
        - Prompt logger with timestamped files
        - Template logger with conditional file output
        - Timing logger with dual output (file + console)
        - Agent logger factory with concurrent handlers
        """
        if cls._memory_loggers_initialized:
            return
        
        # Create logs directory if it doesn't exist
        log_dir = Path("logs")
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"Warning: Could not create log directory: {e}")
            # Continue with console-only logging
        
        # Initialize memory-specific loggers
        # These will be created lazily when first requested via factory functions
        # This method just marks the system as ready for memory logging
        
        cls._memory_loggers_initialized = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取日志器实例
    
    Args:
        name: 日志器名称，默认为调用模块名
        
    Returns:
        配置好的日志器实例
    """
    return logging.getLogger(name)


def get_auth_logger() -> logging.Logger:
    """获取认证专用日志器"""
    return logging.getLogger("auth")


def get_security_logger() -> logging.Logger:
    """获取安全专用日志器"""
    return logging.getLogger("security")


def get_api_logger() -> logging.Logger:
    """获取API专用日志器"""
    return logging.getLogger("api")


def get_db_logger() -> logging.Logger:
    """获取数据库专用日志器"""
    return logging.getLogger("database")


def get_business_logger() -> logging.Logger:
    """获取业务逻辑专用日志器"""
    return logging.getLogger("business")


def get_prompt_logger() -> logging.Logger:
    """Get the prompt logger for memory module.
    
    Returns a logger configured for prompt rendering output with:
    - Logger name: memory.prompts
    - Output: logs/prompt_logs-{timestamp}.log
    - Level: Configurable via PROMPT_LOG_LEVEL setting (default: INFO)
    - Handler: FileHandler (no console output)
    
    The logger is cached after first creation for performance.
    
    Returns:
        Logger configured for prompt rendering output
        
    Example:
        >>> logger = get_prompt_logger()
        >>> logger.info("=== RENDERED EXTRACTION PROMPT ===\\n%s", prompt_content)
    """
    # Return cached logger if already initialized
    if LoggingConfig._prompt_logger is not None:
        return LoggingConfig._prompt_logger
    
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Create prompt logger
    logger = logging.getLogger("memory.prompts")
    logger.setLevel(getattr(logging, settings.PROMPT_LOG_LEVEL.upper()))
    logger.propagate = False  # Don't propagate to root logger (no console output)
    
    # Create timestamped log file
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = Path("logs/prompts/") / f"prompt_logs-{timestamp}.log"
    
    # Ensure log directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Create file handler
    file_handler = logging.FileHandler(
        filename=str(log_file),
        encoding='utf-8'
    )
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    
    # Add handler to logger
    logger.addHandler(file_handler)
    
    # Cache the logger
    LoggingConfig._prompt_logger = logger
    
    return logger


def get_template_logger() -> logging.Logger:
    """Get the template logger for memory module.
    
    Returns a logger configured for template rendering information with:
    - Logger name: memory.templates
    - Output: logs/prompt_templates.log (only when ENABLE_TEMPLATE_LOGGING is True)
    - Level: INFO
    - Handler: FileHandler when enabled, NullHandler when disabled
    
    The logger is cached after first creation for performance.
    
    Returns:
        Logger configured for template rendering info
        
    Example:
        >>> logger = get_template_logger()
        >>> logger.info("Rendering template: %s with context keys: %s", 
        ...             template_name, list(context.keys()))
    """
    # Return cached logger if already initialized
    if LoggingConfig._template_logger is not None:
        return LoggingConfig._template_logger
    
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Create template logger
    logger = logging.getLogger("memory.templates")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't propagate to root logger
    
    # Add appropriate handler based on configuration
    if settings.ENABLE_TEMPLATE_LOGGING:
        # Create log file path
        log_file = Path("logs") / "prompt_templates.log"
        
        # Ensure log directory exists
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Create file handler
        file_handler = logging.FileHandler(
            filename=str(log_file),
            encoding='utf-8'
        )
        
        # Create formatter
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        
        # Add handler to logger
        logger.addHandler(file_handler)
    else:
        # Use NullHandler when template logging is disabled
        null_handler = logging.NullHandler()
        logger.addHandler(null_handler)
    
    # Cache the logger
    LoggingConfig._template_logger = logger
    
    return logger


def log_prompt_rendering(prompt_type: str, content: str) -> None:
    """Log rendered prompt content.
    
    Logs the rendered prompt with a formatted header and separator for easy
    identification in log files. This is useful for debugging LLM interactions
    and understanding what prompts are being sent.
    
    Args:
        prompt_type: Type of prompt (e.g., 'statement_extraction', 'triplet_extraction')
        content: The rendered prompt text
        
    Example:
        >>> log_prompt_rendering("extraction", "Extract entities from: Hello world")
        # Logs:
        # === RENDERED EXTRACTION PROMPT ===
        # Extract entities from: Hello world
        # =====================================
    """
    logger = get_prompt_logger()
    
    # Format the log entry with header and separator
    separator = "=" * 50
    header = f"=== RENDERED {prompt_type.upper()} PROMPT ==="
    
    log_message = f"\n{header}\n{content}\n{separator}\n"
    
    logger.info(log_message)


def log_template_rendering(template_name: str, context: Optional[dict] = None) -> None:
    """Log template rendering information.
    
    Logs the template name and context keys for debugging template rendering.
    This function is wrapped in try-except to ensure it never breaks application
    flow, even if logging fails.
    
    Args:
        template_name: Name of the Jinja2 template being rendered
        context: Optional context dictionary with template variables
        
    Example:
        >>> log_template_rendering("extract_triplet.jinja2", {"text": "...", "ontology": "..."})
        # Logs: Rendering template: extract_triplet.jinja2 with context keys: ['text', 'ontology']
        
        >>> log_template_rendering("system.jinja2")
        # Logs: Rendering template: system.jinja2 with no context
    """
    try:
        logger = get_template_logger()
        
        if context is not None:
            context_keys = list(context.keys())
            logger.info(f"Rendering template: {template_name} with context keys: {context_keys}")
        else:
            logger.info(f"Rendering template: {template_name} with no context")
    except Exception:
        # Never break application flow due to logging issues
        # Silently ignore any logging errors
        pass



def get_timing_logger() -> logging.Logger:
    """Get the timing logger for memory module.
    
    Returns a logger configured for performance timing with:
    - Logger name: memory.timing
    - Output: Configurable via TIMING_LOG_FILE setting (default: logs/time.log)
    - Level: INFO
    - Handlers: FileHandler + optional StreamHandler for console output
    - Console output: Controlled by TIMING_LOG_TO_CONSOLE setting (default: True)
    
    The logger is cached after first creation for performance.
    
    Returns:
        Logger configured for performance timing
        
    Example:
        >>> logger = get_timing_logger()
        >>> logger.info("[2025-11-18 10:30:45] Extraction: 2.34 seconds")
    """
    # Return cached logger if already initialized
    if LoggingConfig._timing_logger is not None:
        return LoggingConfig._timing_logger
    
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Create timing logger
    logger = logging.getLogger("memory.timing")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't propagate to root logger
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Add file handler
    log_file = Path(settings.TIMING_LOG_FILE)
    
    # Ensure log directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = logging.FileHandler(
        filename=str(log_file),
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Add console handler if enabled
    if settings.TIMING_LOG_TO_CONSOLE:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Cache the logger
    LoggingConfig._timing_logger = logger
    
    return logger


def log_time(step_name: str, duration: float, log_file: str = "logs/time.log") -> None:
    """Log timing information for performance tracking.
    
    Logs timing information to both file and console (console output is always shown
    for backward compatibility). The file output includes a timestamp and full details,
    while console output shows a concise checkmark format.
    
    Args:
        step_name: Name of the operation being timed
        duration: Duration in seconds
        log_file: Optional custom log file path (default: logs/time.log)
        
    Example:
        >>> log_time("Knowledge Extraction", 2.34)
        # File logs: [2025-11-18 10:30:45] Knowledge Extraction: 2.34 seconds
        # Console prints: ✓ Knowledge Extraction: 2.34s
        
        >>> log_time("Database Query", 0.15, "logs/custom_time.log")
        # Logs to custom file and console
    """
    from datetime import datetime
    
    # Format timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Format timing entry for file
    log_entry = f"[{timestamp}] {step_name}: {duration:.2f} seconds\n"
    
    # Write to file with error handling
    try:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
    except IOError as e:
        # Fallback to console only if file write fails
        print(f"Warning: Could not write to timing log: {e}")
    
    # Always print to console (backward compatible behavior)
    print(f"✓ {step_name}: {duration:.2f}s")


def get_agent_logger(name: str = "agent_service", 
                     console_level: str = "INFO",
                     file_level: str = "DEBUG") -> logging.Logger:
    """Get an agent logger with concurrent file handling.
    
    Returns a logger configured for agent operations with:
    - Logger name: memory.agent.{name}
    - Output: Configurable via AGENT_LOG_FILE setting (default: logs/agent_service.log)
    - Console level: Configurable (default: INFO)
    - File level: Configurable (default: DEBUG)
    - Handler: ConcurrentRotatingFileHandler for multi-process support
    - Rotation: Configurable via AGENT_LOG_MAX_SIZE (default: 5MB) and 
                AGENT_LOG_BACKUP_COUNT (default: 20)
    
    The logger is cached by name after first creation for performance.
    Supports concurrent writes from multiple processes.
    
    Args:
        name: Logger name for namespacing (default: "agent_service")
        console_level: Log level for console output (default: "INFO")
        file_level: Log level for file output (default: "DEBUG")
        
    Returns:
        Logger configured for agent operations
        
    Example:
        >>> logger = get_agent_logger("my_agent")
        >>> logger.info("Agent operation started")
        >>> logger.debug("Detailed agent state information")
        
        >>> logger = get_agent_logger("custom_agent", console_level="WARNING", file_level="INFO")
        >>> logger.warning("This appears in console and file")
        >>> logger.info("This only appears in file")
    """
    # Return cached logger if already initialized
    if name in LoggingConfig._agent_loggers:
        return LoggingConfig._agent_loggers[name]
    
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Create agent logger with namespaced name
    logger_name = f"memory.agent.{name}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)  # Set to DEBUG to allow both handlers to filter
    logger.propagate = False  # Don't propagate to root logger
    
    # Create formatter
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Add console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, console_level.upper()))
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Add concurrent rotating file handler
    try:
        from concurrent_log_handler import ConcurrentRotatingFileHandler
    except ImportError:
        # Fall back to standard RotatingFileHandler if concurrent handler not available
        from logging.handlers import RotatingFileHandler as ConcurrentRotatingFileHandler
        print("Warning: concurrent-log-handler not available, using standard RotatingFileHandler")
    
    # Create log file path
    log_file = Path(settings.AGENT_LOG_FILE)
    
    # Ensure log directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Create file handler with rotation
    file_handler = ConcurrentRotatingFileHandler(
        filename=str(log_file),
        maxBytes=settings.AGENT_LOG_MAX_SIZE,
        backupCount=settings.AGENT_LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, file_level.upper()))
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Cache the logger
    LoggingConfig._agent_loggers[name] = logger
    
    return logger


def get_named_logger(name: str) -> logging.Logger:
    """Backward compatible alias for get_agent_logger.
    
    This function maintains backward compatibility with existing code that uses
    the get_named_logger pattern from the agent logger module.
    
    Args:
        name: Logger name for namespacing
        
    Returns:
        Logger configured for agent operations
        
    Example:
        >>> logger = get_named_logger("my_agent")
        >>> logger.info("Agent operation started")
    """
    return get_agent_logger(name)


def get_config_logger() -> logging.Logger:
    """Get a specialized logger for memory configuration operations.
    
    Returns a logger configured specifically for configuration loading, validation,
    and model resolution operations with:
    - Logger name: memory.config
    - Output: Inherits from root logger (console + file)
    - Level: Inherits from root logger
    - Format: Standard format with timing information
    
    This logger is optimized for configuration operations and includes
    structured logging for timing, validation steps, and error context.
    
    Returns:
        Logger configured for memory configuration operations
        
    Example:
        >>> logger = get_config_logger()
        >>> logger.info("Loading configuration", extra={
        ...     "config_id": 123,
        ...     "workspace_id": "uuid-here",
        ...     "operation": "load_config"
        ... })
    """
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Get configuration logger with memory namespace
    logger = logging.getLogger("memory.config")
    
    # The logger automatically inherits handlers, formatters, and level from root logger
    # through Python's logging hierarchy, so no additional configuration is needed
    
    return logger


def get_memory_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a standard logger for memory module components.
    
    Returns a logger configured for memory module components that inherits
    the root logger's configuration (handlers, formatters, and level). This
    provides consistent logging behavior across the memory module while
    maintaining the ability to filter and identify memory-specific logs.
    
    The logger uses the 'memory' namespace:
    - If name is provided: logger name is 'memory.{module_name}'
    - If name is None: logger name is 'memory'
    
    The logger inherits all handlers and formatters from the root logger,
    ensuring consistent output format and destinations (console, file, etc.).
    
    Args:
        name: Optional logger name, typically __name__ from the calling module.
              If provided, creates a namespaced logger under 'memory.{name}'.
              If None, returns the base 'memory' logger.
        
    Returns:
        Logger configured for memory module operations with root logger inheritance
        
    Example:
        >>> # In app/core/memory/src/search.py
        >>> logger = get_memory_logger(__name__)
        >>> logger.info("Starting search operation")
        # Logs: [timestamp] - memory.app.core.memory.src.search - INFO - Starting search operation
        
        >>> # Get base memory logger
        >>> logger = get_memory_logger()
        >>> logger.debug("Memory module initialized")
        # Logs: [timestamp] - memory - DEBUG - Memory module initialized
        
        >>> # In app/core/memory/src/knowledge_extraction/triplet_extraction.py
        >>> logger = get_memory_logger(__name__)
        >>> logger.error("Extraction failed", exc_info=True)
        # Logs error with full traceback
    """
    # Ensure memory logging is initialized
    if not LoggingConfig._memory_loggers_initialized:
        LoggingConfig.setup_memory_logging()
    
    # Construct logger name with memory namespace
    if name is not None:
        logger_name = f"memory.{name}"
    else:
        logger_name = "memory"
    
    # Get logger - it will inherit from root logger configuration
    logger = logging.getLogger(logger_name)
    
    # The logger automatically inherits handlers, formatters, and level from root logger
    # through Python's logging hierarchy, so no additional configuration is needed
    
    return logger
