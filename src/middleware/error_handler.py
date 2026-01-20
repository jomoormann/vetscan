"""
Error Handler Middleware for VetScan

Provides global exception handling with sensitive data sanitization.
"""

import re
import os

from fastapi import Request
from fastapi.responses import JSONResponse

from logging_config import get_logger

logger = get_logger("error_handler")


def sanitize_error_message(message: str) -> str:
    """
    Remove sensitive data from error messages before returning to users.

    Args:
        message: Raw error message

    Returns:
        Sanitized error message with sensitive data redacted
    """
    patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED]'),
        (r'sk-ant-[a-zA-Z0-9-]+', '[REDACTED]'),
        (r'password[=:]\s*\S+', 'password=[REDACTED]'),
        (r'api[_-]?key[=:]\s*\S+', 'api_key=[REDACTED]'),
        (r'secret[=:]\s*\S+', 'secret=[REDACTED]'),
        (r'token[=:]\s*[a-zA-Z0-9._-]+', 'token=[REDACTED]'),
        (r'Bearer\s+[a-zA-Z0-9._-]+', 'Bearer [REDACTED]'),
    ]
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)
    return message


async def global_exception_handler(request: Request, exc: Exception):
    """
    Global exception handler that sanitizes error messages.

    Args:
        request: FastAPI request object
        exc: Exception that was raised

    Returns:
        JSONResponse with sanitized error message
    """
    # Log the full exception with traceback (sanitized by logging_config)
    logger.exception(f"Unhandled exception for {request.method} {request.url.path}")

    # Sanitize the error message before returning to user
    error_message = sanitize_error_message(str(exc))

    # Don't expose internal details in production
    if os.getenv("ENVIRONMENT", "production") != "development":
        error_message = "An internal error occurred. Please try again later."

    return JSONResponse(
        status_code=500,
        content={"detail": error_message}
    )
