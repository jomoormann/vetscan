"""
CSRF Protection Middleware for VetScan

Provides CSRF token generation and validation.
Consolidates the 65+ occurrences of CSRF logic in the codebase.
"""

import hmac
import hashlib
import secrets

from fastapi import Request, Response

from config import settings
from logging_config import get_logger

logger = get_logger("csrf")

# Cookie names
CSRF_COOKIE_NAME = "vetscan_csrf"


def generate_csrf_token() -> str:
    """Generate a secure CSRF token."""
    return secrets.token_urlsafe(32)


def create_csrf_signed_token(token: str) -> str:
    """
    Sign a CSRF token for verification.

    Args:
        token: Raw CSRF token

    Returns:
        Signed token in format "token:signature"
    """
    signature = hmac.new(
        settings.auth.secret_key.encode(),
        token.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{token}:{signature}"


def verify_csrf_token(signed_token: str, cookie_token: str) -> bool:
    """
    Verify CSRF token matches cookie and signature is valid.

    Args:
        signed_token: Signed token from form (format: "token:signature")
        cookie_token: Raw token from cookie

    Returns:
        True if token is valid
    """
    if not signed_token or not cookie_token:
        return False
    try:
        token, signature = signed_token.rsplit(":", 1)
        expected_signature = hmac.new(
            settings.auth.secret_key.encode(),
            token.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return (hmac.compare_digest(signature, expected_signature) and
                hmac.compare_digest(token, cookie_token))
    except Exception:
        return False


def validate_csrf(request: Request, form_token: str) -> bool:
    """
    Validate CSRF token from form against cookie.

    Args:
        request: FastAPI request object
        form_token: Token submitted in form

    Returns:
        True if validation passes
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    result = verify_csrf_token(form_token, cookie_token)
    if not result:
        logger.warning(f"CSRF validation failed for {request.url.path}")
    return result


def add_csrf_to_response(response: Response, request: Request) -> str:
    """
    Add CSRF cookie and return token for template.

    Args:
        response: FastAPI response to add cookie to
        request: FastAPI request for reading existing cookie

    Returns:
        CSRF token (raw, not signed) for template use
    """
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_token:
        csrf_token = generate_csrf_token()
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_token,
            max_age=3600,  # 1 hour
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https"
        )
    return csrf_token


def get_csrf_context(request: Request) -> dict:
    """
    Get CSRF token context for templates.

    Args:
        request: FastAPI request

    Returns:
        Dict with csrf_token key containing signed token
    """
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_token)
    return {"csrf_token": signed_csrf, "_csrf_raw": csrf_token}


def set_csrf_cookie(response: Response, request: Request, csrf_raw: str):
    """
    Set CSRF cookie on response.

    Args:
        response: FastAPI response
        request: FastAPI request
        csrf_raw: Raw (unsigned) CSRF token
    """
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_raw,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
