"""
Logging Configuration for VetScan

Provides structured logging with:
- File rotation (keeps 7 days of logs)
- Console and file handlers
- Configurable log levels
- Sensitive data filtering (API keys, passwords)
"""

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Optional


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Default log level from environment
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Log file settings
LOG_FILE = LOGS_DIR / "vetscan.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 7  # Keep 7 rotated files


# =============================================================================
# SENSITIVE DATA FILTER
# =============================================================================

class SensitiveDataFilter(logging.Filter):
    """
    Filter that redacts sensitive data from log records.

    Patterns redacted:
    - API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
    - Passwords and secrets
    - Email credentials
    - Auth tokens
    """

    # Patterns to redact (key=value style) - with capturing groups
    KEY_PATTERNS = [
        (r'(api[_-]?key\s*[=:]\s*)[^\s,;"\'\]]+', r'\1[REDACTED]'),
        (r'(password\s*[=:]\s*)[^\s,;"\'\]]+', r'\1[REDACTED]'),
        (r'(secret\s*[=:]\s*)[^\s,;"\'\]]+', r'\1[REDACTED]'),
        (r'(token\s*[=:]\s*)[^\s,;"\'\]]+', r'\1[REDACTED]'),
        (r'(auth\s*[=:]\s*)[^\s,;"\'\]]+', r'\1[REDACTED]'),
        (r'(bearer\s+)[^\s,;"\'\]]+', r'\1[REDACTED]'),
    ]

    # Environment variable names that should be redacted (full replacement)
    ENV_VAR_PATTERNS = [
        (r'ANTHROPIC_API_KEY=\S+', 'ANTHROPIC_API_KEY=[REDACTED]'),
        (r'OPENAI_API_KEY=\S+', 'OPENAI_API_KEY=[REDACTED]'),
        (r'AUTH_SECRET_KEY=\S+', 'AUTH_SECRET_KEY=[REDACTED]'),
        (r'SMTP_PASSWORD=\S+', 'SMTP_PASSWORD=[REDACTED]'),
        (r'DB_PASSWORD=\S+', 'DB_PASSWORD=[REDACTED]'),
    ]

    # Combined regex patterns with their replacements
    _compiled_patterns = None

    @classmethod
    def _get_patterns(cls):
        if cls._compiled_patterns is None:
            all_patterns = cls.KEY_PATTERNS + cls.ENV_VAR_PATTERNS
            cls._compiled_patterns = [
                (re.compile(p, re.IGNORECASE), r) for p, r in all_patterns
            ]
        return cls._compiled_patterns

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter and redact sensitive data from log message."""
        if record.msg:
            record.msg = self._redact(str(record.msg))
        if record.args:
            record.args = tuple(
                self._redact(str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True

    def _redact(self, text: str) -> str:
        """Redact sensitive patterns from text."""
        for pattern, replacement in self._get_patterns():
            text = pattern.sub(replacement, text)
        return text


# =============================================================================
# EXCEPTION FORMATTER
# =============================================================================

class SanitizedFormatter(logging.Formatter):
    """
    Custom formatter that sanitizes exception tracebacks.

    Removes sensitive data like API keys from exception messages.
    """

    SENSITIVE_PATTERNS = [
        (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '[REDACTED_API_KEY]'),
        (re.compile(r'sk-ant-[a-zA-Z0-9-]+'), '[REDACTED_ANTHROPIC_KEY]'),
        (re.compile(r'Bearer\s+[a-zA-Z0-9._-]+'), 'Bearer [REDACTED]'),
        (re.compile(r'password["\']?\s*[=:]\s*["\']?[^"\'\s,]+'), 'password=[REDACTED]'),
    ]

    def formatException(self, ei) -> str:
        """Format and sanitize exception info."""
        result = super().formatException(ei)
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)
        return result

    def format(self, record: logging.LogRecord) -> str:
        """Format and sanitize the entire log record."""
        result = super().format(record)
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            result = pattern.sub(replacement, result)
        return result


# =============================================================================
# LOGGER SETUP
# =============================================================================

def setup_logging(
    level: Optional[str] = None,
    log_file: Optional[Path] = None,
    console: bool = True
) -> logging.Logger:
    """
    Configure and return the root logger for VetScan.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file. If None, uses default.
        console: Whether to add console handler.

    Returns:
        Configured root logger
    """
    level = level or LOG_LEVEL
    log_file = log_file or LOG_FILE

    # Create root logger for vetscan
    logger = logging.getLogger("vetscan")
    logger.setLevel(getattr(logging, level, logging.INFO))

    # Remove existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = SanitizedFormatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Create sensitive data filter
    sensitive_filter = SensitiveDataFilter()

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)  # File gets all levels
    file_handler.setFormatter(formatter)
    file_handler.addFilter(sensitive_filter)
    logger.addHandler(file_handler)

    # Console handler
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, level, logging.INFO))
        console_handler.setFormatter(formatter)
        console_handler.addFilter(sensitive_filter)
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a child logger for a specific module.

    Args:
        name: Module name (e.g., "web_server", "pdf_parser")

    Returns:
        Logger instance
    """
    return logging.getLogger(f"vetscan.{name}")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

# Initialize the root logger on import
_root_logger = setup_logging()


def debug(msg: str, *args, **kwargs):
    """Log a debug message."""
    _root_logger.debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs):
    """Log an info message."""
    _root_logger.info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs):
    """Log a warning message."""
    _root_logger.warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs):
    """Log an error message."""
    _root_logger.error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs):
    """Log a critical message."""
    _root_logger.critical(msg, *args, **kwargs)


def exception(msg: str, *args, **kwargs):
    """Log an exception with traceback."""
    _root_logger.exception(msg, *args, **kwargs)
