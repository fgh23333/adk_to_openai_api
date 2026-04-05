# -*- coding: utf-8 -*-
"""
结构化日志配置模块

支持:
- JSON 格式日志输出
- 请求上下文追踪 (request_id, user_id)
- 性能追踪 (耗时统计)
- 环境变量控制格式
"""
import logging
import sys
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional
from contextvars import ContextVar
from pythonjsonlogger import jsonlogger

# Context variables for request tracking
request_context: ContextVar[Dict[str, Any]] = ContextVar('request_context', default={})


def get_request_context() -> Dict[str, Any]:
    """Get current request context."""
    try:
        return request_context.get()
    except LookupError:
        return {}


def set_request_context(**kwargs):
    """Set request context values."""
    current = get_request_context().copy()
    current.update(kwargs)
    request_context.set(current)


def clear_request_context():
    """Clear request context."""
    request_context.set({})


class RequestContextFilter(logging.Filter):
    """Add request context to log records."""

    def filter(self, record):
        context = get_request_context()
        record.request_id = context.get('request_id', '-')
        record.user_id = context.get('user_id', '-')
        record.session_id = context.get('session_id', '-')
        record.model = context.get('model', '-')
        record.backend = context.get('backend', '-')
        return True


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""

    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]):
        super().add_fields(log_record, record, message_dict)

        # Add timestamp
        log_record['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        # Add level
        log_record['level'] = record.levelname.lower()

        # Add logger name
        log_record['logger'] = record.name

        # Add request context
        context = get_request_context()
        if context:
            log_record['context'] = context

        # Add exception info if present
        if record.exc_info:
            log_record['exception'] = self.formatException(record.exc_info)

        # Add source location
        log_record['source'] = {
            'file': record.filename,
            'line': record.lineno,
            'function': record.funcName
        }


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""

    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        # Add color to level name
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"

        # Add request context if available
        context = get_request_context()
        context_str = ""
        if context.get('request_id'):
            context_str = f"[{context['request_id'][:8]}] "

        # Format the message
        formatted = super().format(record)

        # Prepend context
        if context_str:
            formatted = f"{context_str}{formatted}"

        return formatted


def setup_logging(json_format: bool = None, log_level: str = "INFO"):
    """
    Setup logging configuration.

    Args:
        json_format: Use JSON format for logs. If None, read from LOG_FORMAT env var.
        log_level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    # Determine format from environment or parameter
    if json_format is None:
        json_format = os.getenv('LOG_FORMAT', 'console').lower() == 'json'

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # Add request context filter
    console_handler.addFilter(RequestContextFilter())

    if json_format:
        # JSON format
        formatter = CustomJsonFormatter(
            '%(timestamp)s %(level)s %(name)s %(message)s',
            rename_fields={'levelname': 'level', 'name': 'logger'}
        )
    else:
        # Colored console format
        formatter = ColoredFormatter(
            '%(levelname)s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Set third-party library log levels
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)

    return root_logger


class PerformanceLogger:
    """Context manager for performance logging."""

    def __init__(self, operation: str, **extra):
        self.operation = operation
        self.extra = extra
        self.start_time = None
        self.logger = logging.getLogger(__name__)

    def __enter__(self):
        self.start_time = datetime.now()
        self.logger.debug(f"Starting {self.operation}", extra=self.extra)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = (datetime.now() - self.start_time).total_seconds() * 1000

        if exc_type:
            self.logger.error(
                f"{self.operation} failed after {elapsed:.2f}ms",
                extra={**self.extra, 'elapsed_ms': elapsed, 'error': str(exc_val)}
            )
        else:
            self.logger.info(
                f"{self.operation} completed in {elapsed:.2f}ms",
                extra={**self.extra, 'elapsed_ms': elapsed}
            )

        return False  # Don't suppress exceptions


def log_request(method: str, path: str, **kwargs):
    """Log incoming request."""
    logger = logging.getLogger('request')
    context = get_request_context()
    logger.info(f"--> {method} {path}", extra={
        'request_method': method,
        'request_path': path,
        **kwargs
    })


def log_response(status_code: int, elapsed_ms: float, **kwargs):
    """Log outgoing response."""
    logger = logging.getLogger('request')
    level = logging.INFO if status_code < 400 else logging.WARNING
    logger.log(level, f"<-- {status_code} ({elapsed_ms:.2f}ms)", extra={
        'response_status': status_code,
        'elapsed_ms': elapsed_ms,
        **kwargs
    })


def log_adk_request(backend: str, endpoint: str, **kwargs):
    """Log ADK backend request."""
    logger = logging.getLogger('adk')
    set_request_context(backend=backend)
    logger.info(f"ADK request: {endpoint}", extra={
        'adk_backend': backend,
        'adk_endpoint': endpoint,
        **kwargs
    })


def log_adk_response(status: int, elapsed_ms: float, **kwargs):
    """Log ADK backend response."""
    logger = logging.getLogger('adk')
    level = logging.INFO if status < 400 else logging.ERROR
    logger.log(level, f"ADK response: {status} ({elapsed_ms:.2f}ms)", extra={
        'adk_status': status,
        'elapsed_ms': elapsed_ms,
        **kwargs
    })
