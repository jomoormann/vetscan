"""
Authentication Middleware for VetScan

Provides cookie-based authentication and HTTPS redirect middleware.
"""

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings
from logging_config import get_logger
from api.dependencies import get_service

logger = get_logger("auth_middleware")

# Cookie name
AUTH_COOKIE_NAME = "vetscan_session"


# =============================================================================
# RATE LIMITING
# =============================================================================

class LoginRateLimiter:
    """Simple in-memory rate limiter for login attempts."""

    def __init__(self):
        self._attempts = {}  # {ip: [(timestamp, success), ...]}
        self._lockouts = {}  # {ip: lockout_expires_at}

    def _cleanup_old_attempts(self, ip: str):
        """Remove attempts older than the time window."""
        if ip in self._attempts:
            cutoff = datetime.now() - timedelta(seconds=settings.auth.rate_limit_window)
            self._attempts[ip] = [
                (ts, success) for ts, success in self._attempts[ip]
                if ts > cutoff
            ]

    def is_locked_out(self, ip: str) -> bool:
        """Check if an IP is currently locked out."""
        if ip in self._lockouts:
            if datetime.now() < self._lockouts[ip]:
                return True
            else:
                del self._lockouts[ip]
        return False

    def record_attempt(self, ip: str, success: bool):
        """Record a login attempt."""
        self._cleanup_old_attempts(ip)
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append((datetime.now(), success))

        # Check if we need to lock out
        if not success:
            failed_attempts = sum(1 for _, s in self._attempts[ip] if not s)
            if failed_attempts >= settings.auth.rate_limit_attempts:
                self._lockouts[ip] = datetime.now() + timedelta(
                    seconds=settings.auth.rate_limit_lockout
                )
                logger.warning(f"Login rate limit exceeded for IP: {ip}")

    def get_remaining_lockout_seconds(self, ip: str) -> int:
        """Get remaining lockout time in seconds."""
        if ip in self._lockouts:
            remaining = (self._lockouts[ip] - datetime.now()).total_seconds()
            return max(0, int(remaining))
        return 0


# Global rate limiter instance
login_rate_limiter = LoginRateLimiter()


# =============================================================================
# TOKEN HANDLING
# =============================================================================

def create_auth_token(identifier: str) -> str:
    """
    Create a signed authentication token.

    Args:
        identifier: Either a user ID (for multi-user) or username (for legacy)

    Returns:
        Base64-encoded signed token
    """
    secret = settings.auth.secret_key
    message = f"{identifier}:{secret[:16]}"
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    token = base64.b64encode(f"{identifier}:{signature}".encode()).decode()
    return token


def verify_auth_token(token: str) -> Optional[str]:
    """
    Verify an authentication token and return the identifier if valid.

    Args:
        token: Base64-encoded signed token

    Returns:
        The identifier (user ID or username) if valid, None otherwise
    """
    try:
        decoded = base64.b64decode(token).decode()
        identifier, signature = decoded.rsplit(":", 1)

        # Recreate expected signature
        secret = settings.auth.secret_key
        message = f"{identifier}:{secret[:16]}"
        expected_signature = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        # Timing-safe comparison
        if hmac.compare_digest(signature, expected_signature):
            return identifier
    except Exception:
        pass
    return None


def get_safe_host(request: Request) -> str:
    """
    Get host from request, validated against allowed hosts.

    Args:
        request: FastAPI request

    Returns:
        Validated host string
    """
    host = request.headers.get("host", "localhost")
    # Strip port if present
    host_without_port = host.split(":")[0]
    if host_without_port not in settings.auth.allowed_hosts:
        return settings.auth.allowed_hosts[0] if settings.auth.allowed_hosts else "localhost"
    return host


# =============================================================================
# MIDDLEWARE CLASSES
# =============================================================================

class CookieAuthMiddleware(BaseHTTPMiddleware):
    """
    Cookie-based authentication middleware.

    Supports both legacy single-user and multi-user database auth.
    Redirects unauthenticated users to the login page.
    Immediately invalidates sessions for disabled users.
    """

    # Public paths that don't require authentication
    PUBLIC_PATHS = [
        "/login", "/logout", "/register", "/forgot-password", "/reset-password"
    ]

    async def dispatch(self, request: Request, call_next):
        # Skip authentication if no password is set (dev mode)
        if not settings.auth.password:
            request.state.user = None
            return await call_next(request)

        # Allow access to public pages and static assets
        path = request.url.path
        if any(path.startswith(p) for p in self.PUBLIC_PATHS) or path.startswith("/static"):
            request.state.user = None
            return await call_next(request)

        # Check for auth cookie
        auth_cookie = request.cookies.get(AUTH_COOKIE_NAME)

        if auth_cookie:
            # Try to validate the token
            token_data = verify_auth_token(auth_cookie)

            if token_data:
                # Check if it's a user ID (multi-user) or username (legacy)
                if token_data.isdigit():
                    # Multi-user mode: load user from database
                    service = get_service()
                    try:
                        user = service.db.get_user(int(token_data))
                        if user:
                            if not user.is_active:
                                # User disabled - invalidate session immediately
                                logger.info(f"Session invalidated for disabled user: {user.email}")
                                response = RedirectResponse(url="/login?error=disabled", status_code=302)
                                # Clear the auth cookie
                                response.delete_cookie(key=AUTH_COOKIE_NAME)
                                return response
                            if not user.is_approved:
                                # User pending approval
                                request.state.user = user
                                if path != "/pending-approval":
                                    return RedirectResponse(url="/pending-approval", status_code=302)
                                return await call_next(request)
                            # Valid, active, approved user
                            request.state.user = user
                            return await call_next(request)
                    finally:
                        service.close()
                else:
                    # Legacy mode: username-based auth
                    service = get_service()
                    try:
                        if service.db.user_count() == 0:
                            # Legacy mode active
                            request.state.user = None
                            return await call_next(request)
                    finally:
                        service.close()

        # Redirect to login page
        return RedirectResponse(url="/login", status_code=302)


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """
    Redirect HTTP to HTTPS in production.

    Checks X-Forwarded-Proto header (set by reverse proxies).
    Does not redirect localhost for local development.
    """

    async def dispatch(self, request: Request, call_next):
        # Get the original protocol from proxy headers
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
        host = request.headers.get("Host", "")

        # Skip redirect for localhost/development
        if "localhost" in host or "127.0.0.1" in host:
            return await call_next(request)

        # Redirect HTTP to HTTPS if behind a proxy serving HTTP
        if forwarded_proto == "http":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url=str(url), status_code=301)

        return await call_next(request)
