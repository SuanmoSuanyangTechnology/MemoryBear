import logging
import logging.handlers
from pathlib import Path

from src.config import settings
from src.middleware.trace.utils import get_trace_id


class Neo4jSuccessNotificationFilter(logging.Filter):
    """Neo4j 日志过滤器：过滤成功/信息性状态的通知，保留真正的警告和错误

    Neo4j 驱动会以 WARNING 级别记录所有数据库通知，包括成功的操作。
    这个过滤器会过滤掉以下 GQL 状态码的通知，只保留真正的警告和错误：
      - 00000: 成功完成 (successful completion)
      - 00N00: 无数据 (no data)
      - 00NA0: 无数据，信息性通知 (no data, informational notification)

    使用正则表达式进行更严格的匹配，避免误过滤无关的警告。
    """

    import re

    # 编译正则表达式以提高性能
    # 匹配所有"成功/信息性"的 GQL 状态码：
    # 00000 = 成功完成, 00N00 = 无数据, 00NA0 = 无数据信息性通知
    GQL_STATUS_PATTERN = re.compile(r"gql_status=['\"](00000|00N00|00NA0)['\"]")

    # 匹配 status_description 中的成功完成或信息性通知消息
    SUCCESS_DESC_PATTERN = re.compile(r"status_description=['\"]note:\s*(successful\s+completion|no\s+data)['\"]",
                                      re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤 Neo4j 成功通知

        Args:
            record: 日志记录

        Returns:
            True表示允许记录，False表示拒绝（过滤掉）
        """
        # 只处理 INFO 和 WARNING 级别的日志
        # Neo4j 驱动对 severity='INFORMATION' 的通知使用 INFO 级别，
        # 对 severity='WARNING' 的通知使用 WARNING 级别
        if record.levelno not in (logging.INFO, logging.WARNING):
            return True

        # 检查是否是 Neo4j 的成功通知
        message = str(record.msg)

        # 使用正则表达式进行更严格的匹配
        # 这样可以避免误过滤包含这些子字符串但不是 Neo4j 通知的日志
        if self.GQL_STATUS_PATTERN.search(message) or self.SUCCESS_DESC_PATTERN.search(message):
            return False  # 过滤掉这条日志

        # 保留其他所有日志（包括真正的警告和错误）
        return True


class TraceIdFilter(logging.Filter):
    """日志过滤器：将请求上下文中的 trace_id 注入 LogRecord。

    在 handler 格式化之前执行，把 ContextVar 中的 trace_id 写入
    record.trace_id，使 LOG_FORMAT 中的 %(trace_id)s 能取到值。
    无请求上下文（如后台任务）时 trace_id 为空字符串。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id()
        return True


class LoggingConfig:
    """全局日志配置类"""

    _initialized = False
    _memory_loggers_initialized = False
    _prompt_logger = None
    _template_logger = None
    _timing_logger = None

    @classmethod
    def setup_logging(cls) -> None:
        if cls._initialized:
            return

        log_dir = Path(settings.LOG_FILE_PATH).parent
        log_dir.mkdir(parents=True, exist_ok=True)

        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, settings.LOG_LEVEL))

        neo4j_filter = Neo4jSuccessNotificationFilter()
        neo4j_notifications_logger = logging.getLogger("neo4j.notifications")
        neo4j_notifications_logger.setLevel(logging.WARNING)
        for neo4j_logger_name in ["neo4j", "neo4j.io", "neo4j.pool", "neo4j.notifications"]:
            neo4j_logger = logging.getLogger(neo4j_logger_name)
            neo4j_logger.addFilter(neo4j_filter)

        noisy_model_loggers = [
            "httpx", "httpcore", "httpcore.http11", "httpcore.connection",
            "elastic_transport", "elastic_transport.transport",
        ]
        for noisy_logger in noisy_model_loggers:
            logging.getLogger(noisy_logger).setLevel(logging.WARNING)

        formatter = logging.Formatter(
            fmt=settings.LOG_FORMAT,
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        trace_id_filter = TraceIdFilter()

        # 控制台处理器
        if settings.LOG_TO_CONSOLE:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(getattr(logging, settings.LOG_LEVEL.upper()))
            console_handler.addFilter(trace_id_filter)
            console_handler.addFilter(neo4j_filter)
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
            file_handler.addFilter(trace_id_filter)
            file_handler.addFilter(neo4j_filter)
            root_logger.addHandler(file_handler)

        cls._initialized = True

        # 记录初始化完成
        logger = logging.getLogger(__name__)
        logger.info("全局日志系统初始化完成")


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(name)
