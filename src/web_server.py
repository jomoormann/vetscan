"""
Web Server for Veterinary Protein Analysis Application

FastAPI-based web interface for:
- Uploading PDF reports
- Viewing animals and their test history
- Comparing test results
- Managing symptoms and observations
"""

import os
import sys
import json
import secrets
import base64
import hmac
import hashlib
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, List
from pathlib import Path
from urllib.parse import quote_plus, urlencode, urlparse

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
import bleach
from markupsafe import Markup

# Load environment variables
load_dotenv()

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    Database, Animal, Symptom, Observation, TestSession,
    ClinicalNote, DiagnosisReport, User, AuthEvent
)
from app import VetProteinService
from i18n import get_text, get_language_from_request, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
from auth import (
    AuthService, hash_password, verify_password, validate_password,
    validate_email, hash_token
)
from email_sender import email_service
from logging_config import get_logger, setup_logging
from pdf_validator import PDFValidator, ValidationResult

# Initialize logger for this module
logger = get_logger("web_server")




# Import diagnosis service (optional - may not be installed)
try:
    from diagnosis_service import DiagnosisService, create_diagnosis_report
    DIAGNOSIS_AVAILABLE = True
except ImportError:
    DIAGNOSIS_AVAILABLE = False

# =============================================================================
# APP CONFIGURATION
# =============================================================================

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
TEMPLATES_DIR = BASE_DIR / "templates"
TEMP_UPLOADS_DIR = DATA_DIR / ".upload_tmp"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)
TEMP_UPLOADS_DIR.mkdir(exist_ok=True)

# Database path
DB_PATH = DATA_DIR / "vet_proteins.db"
ENVIRONMENT = os.getenv("ENVIRONMENT", "production").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

# Initialize FastAPI
app = FastAPI(
    title="Vet Protein Analysis",
    description="Veterinary blood test analysis application",
    version="0.2.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)


# =============================================================================
# AUTHENTICATION & SECURITY MIDDLEWARE
# =============================================================================

# Get credentials from environment variables
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")  # Legacy fallback when DB auth is not enabled
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "")
ALLOW_SELF_REGISTRATION = os.getenv("ALLOW_SELF_REGISTRATION", "false").lower() == "true"
if IS_PRODUCTION and not AUTH_SECRET_KEY:
    raise RuntimeError("AUTH_SECRET_KEY must be configured in production")
if not AUTH_SECRET_KEY:
    AUTH_SECRET_KEY = secrets.token_hex(32)
    logger.warning("AUTH_SECRET_KEY not configured; using an ephemeral development secret")

# Session cookie name
AUTH_COOKIE_NAME = "vetscan_session"
CSRF_COOKIE_NAME = "vetscan_csrf"

# Allowed hosts for password reset links (prevents host header injection)
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,vetscan.net").split(",")

# Auth/session policy
LOGIN_EMAIL_WINDOW_LIMIT = 5
LOGIN_EMAIL_WINDOW = timedelta(minutes=15)
LOGIN_IP_WINDOW_LIMIT = 20
LOGIN_IP_WINDOW = timedelta(hours=1)
AUTH_LOCKOUT_MINUTES = 15
SESSION_IDLE_TIMEOUT = timedelta(hours=24)
SESSION_ABSOLUTE_TIMEOUT = timedelta(days=7)


def normalize_email(value: Optional[str]) -> Optional[str]:
    """Normalize an email address for auth lookup/rate limiting."""
    if not value:
        return None
    normalized = value.strip().lower()
    return normalized or None


def hash_value(value: str) -> str:
    """Hash a secret or identifier for persistent storage."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def create_session_token() -> str:
    """Create an opaque session token."""
    return secrets.token_urlsafe(48)


def hash_user_agent(user_agent: Optional[str]) -> Optional[str]:
    """Hash the current user agent for session metadata."""
    if not user_agent:
        return None
    return hash_value(user_agent[:512])


def get_client_ip(request: Request) -> str:
    """Resolve the originating client IP from a trusted reverse proxy."""
    client_host = request.client.host if request.client else ""
    if client_host in {"127.0.0.1", "::1", "localhost"}:
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
    return client_host or "unknown"


def create_auth_session(service: VetProteinService, request: Request,
                        user_id: Optional[int]) -> str:
    """Persist a server-side auth session and return its opaque token."""
    token = create_session_token()
    hashed_token = hash_value(token)
    client_ip = get_client_ip(request)
    service.db.create_user_session(
        user_id=user_id,
        session_token_hash=hashed_token,
        expires_at=datetime.utcnow() + SESSION_ABSOLUTE_TIMEOUT,
        created_ip=client_ip,
        last_seen_ip=client_ip,
        user_agent_hash=hash_user_agent(request.headers.get("user-agent")),
    )
    return token


def set_auth_cookie(response: Response, request: Request, token: str):
    """Set the auth session cookie on a response."""
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=int(SESSION_ABSOLUTE_TIMEOUT.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https" or IS_PRODUCTION,
    )


def clear_auth_cookie(response: Response):
    """Clear the auth session cookie."""
    response.delete_cookie(key=AUTH_COOKIE_NAME)


def get_safe_redirect_target(request: Request, fallback: str = "/") -> str:
    """Return a same-origin relative redirect target."""
    referer = request.headers.get("referer", "")
    if not referer:
        return fallback

    parsed = urlparse(referer)
    if parsed.scheme and parsed.hostname and parsed.hostname != request.url.hostname:
        return fallback

    path = parsed.path or "/"
    if not path.startswith("/"):
        return fallback
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def generate_csrf_token() -> str:
    """Generate a secure CSRF token"""
    return secrets.token_urlsafe(32)


def create_csrf_signed_token(token: str) -> str:
    """Sign a CSRF token for verification"""
    signature = hmac.new(
        AUTH_SECRET_KEY.encode(),
        token.encode(),
        hashlib.sha256
    ).hexdigest()[:16]
    return f"{token}:{signature}"


def verify_csrf_token(signed_token: str, cookie_token: str) -> bool:
    """Verify CSRF token matches cookie and signature is valid"""
    if not signed_token or not cookie_token:
        return False
    try:
        token, signature = signed_token.rsplit(":", 1)
        expected_signature = hmac.new(
            AUTH_SECRET_KEY.encode(),
            token.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return (hmac.compare_digest(signature, expected_signature) and
                hmac.compare_digest(token, cookie_token))
    except Exception:
        return False


def get_safe_host(request: Request) -> str:
    """Get host from request, validated against allowed hosts"""
    host = request.headers.get("host", "localhost")
    # Strip port if present
    host_without_port = host.split(":")[0]
    if host_without_port not in ALLOWED_HOSTS:
        return ALLOWED_HOSTS[0] if ALLOWED_HOSTS else "localhost"
    return host


def record_auth_event(service: VetProteinService, event_type: str, request: Request,
                      success: bool, email_normalized: Optional[str] = None,
                      user_id: Optional[int] = None,
                      metadata: Optional[Dict[str, Any]] = None) -> None:
    """Persist an auth event for audit and rate limiting."""
    service.db.create_auth_event(AuthEvent(
        event_type=event_type,
        email_normalized=email_normalized,
        ip_address=get_client_ip(request),
        user_id=user_id,
        success=success,
        metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
    ))


def auth_action_is_limited(service: VetProteinService, event_type: str, request: Request,
                           email_normalized: Optional[str] = None,
                           failures_only: bool = True) -> bool:
    """Check persistent auth-event thresholds for abuse control."""
    success_filter = False if failures_only else None
    now = datetime.utcnow()
    client_ip = get_client_ip(request)

    if email_normalized:
        recent_email_ip_failures = service.db.count_auth_events(
            event_type,
            now - LOGIN_EMAIL_WINDOW,
            success=success_filter,
            email_normalized=email_normalized,
            ip_address=client_ip,
        )
        if recent_email_ip_failures >= LOGIN_EMAIL_WINDOW_LIMIT:
            return True

    recent_ip_failures = service.db.count_auth_events(
        event_type,
        now - LOGIN_IP_WINDOW,
        success=success_filter,
        ip_address=client_ip,
    )
    return recent_ip_failures >= LOGIN_IP_WINDOW_LIMIT


def csrf_token_for_request(request: Request) -> str:
    """Return the current signed CSRF token for templates."""
    token = getattr(request.state, "csrf_raw", None) or request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = generate_csrf_token()
    return create_csrf_signed_token(token)


def build_api_auth_response(status_code: int, detail: str) -> JSONResponse:
    """JSON auth failure response for API routes."""
    return JSONResponse({"detail": detail}, status_code=status_code)


def internal_error_detail() -> str:
    """Generic user-facing error detail for unexpected failures."""
    return "An internal error occurred. Please try again later."


def internal_error_json() -> JSONResponse:
    """Standard JSON payload for unexpected failures."""
    return JSONResponse({
        "success": False,
        "message": internal_error_detail(),
    }, status_code=500)


def secure_cookie_enabled(request: Request) -> bool:
    """Determine whether cookies should be marked secure."""
    return request.url.scheme == "https" or IS_PRODUCTION


def enrich_import_audit_rows(service: VetProteinService,
                             imports: List[Dict[str, Any]]) -> None:
    """Overlay the current assignment state onto historical email import rows."""
    if not imports:
        return

    report_numbers = sorted({
        row["report_number"]
        for row in imports
        if row.get("report_number")
    })
    filenames = sorted({
        row["attachment_name"]
        for row in imports
        if row.get("attachment_name")
    })

    assigned_by_report: Dict[str, Dict[str, Any]] = {}
    assigned_by_filename: Dict[str, Dict[str, Any]] = {}

    filters = []
    params: List[object] = []
    if report_numbers:
        filters.append(f"ur.report_number IN ({','.join('?' for _ in report_numbers)})")
        params.extend(report_numbers)
    if filenames:
        filters.append(f"ur.filename IN ({','.join('?' for _ in filenames)})")
        params.extend(filenames)

    if filters:
        rows = service.db.conn.execute(f"""
            SELECT
                ur.report_number,
                ur.filename,
                ur.status,
                ur.assigned_animal_id,
                ur.session_id,
                a.name AS assigned_animal_name
            FROM unassigned_reports ur
            LEFT JOIN animals a ON a.id = ur.assigned_animal_id
            WHERE {' OR '.join(filters)}
            ORDER BY COALESCE(ur.assigned_at, ur.created_at) DESC, ur.id DESC
        """, tuple(params)).fetchall()

        for row in rows:
            item = dict(row)
            if item["status"] != "assigned":
                continue
            if item.get("report_number") and item["report_number"] not in assigned_by_report:
                assigned_by_report[item["report_number"]] = item
            if item.get("filename") and item["filename"] not in assigned_by_filename:
                assigned_by_filename[item["filename"]] = item

    for imp in imports:
        display_status = "failed"

        if imp.get("validation_result") == "queued_manual_assignment":
            assigned_entry = None
            if imp.get("session_id") or imp.get("animal_id"):
                display_status = "assigned"
            else:
                report_number = imp.get("report_number")
                filename = imp.get("attachment_name")
                if report_number and report_number in assigned_by_report:
                    assigned_entry = assigned_by_report[report_number]
                elif filename and filename in assigned_by_filename:
                    assigned_entry = assigned_by_filename[filename]

                if assigned_entry:
                    imp["animal_id"] = imp.get("animal_id") or assigned_entry.get("assigned_animal_id")
                    imp["animal_name"] = imp.get("animal_name") or assigned_entry.get("assigned_animal_name")
                    imp["session_id"] = imp.get("session_id") or assigned_entry.get("session_id")
                    display_status = "assigned"
                else:
                    display_status = "queued"
        elif imp.get("import_success"):
            display_status = "success"
        elif imp.get("validation_result") == "duplicate":
            display_status = "duplicate"
        elif imp.get("validation_result") == "rate_limited":
            display_status = "rate_limited"

        imp["display_status"] = display_status


def add_csrf_cookie(response: Response, request: Request, csrf_raw: Optional[str] = None) -> str:
    """Attach the CSRF cookie to a response and return the raw token."""
    token = csrf_raw or getattr(request.state, "csrf_raw", None) or request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        token = generate_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=secure_cookie_enabled(request),
    )
    return token


def render_template_with_csrf(template_name: str, request: Request,
                              context: Dict[str, Any],
                              status_code: int = 200) -> Response:
    """Render a template response and ensure a signed CSRF token is present."""
    response = templates.TemplateResponse(request, template_name, context, status_code=status_code)
    add_csrf_cookie(response, request)
    return response


def current_session_is_expired(session) -> bool:
    """Check absolute and idle session expiry."""
    now = datetime.utcnow()

    expires_at = session.expires_at
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at and expires_at <= now:
        return True

    last_seen_at = session.last_seen_at or session.created_at
    if isinstance(last_seen_at, str):
        last_seen_at = datetime.fromisoformat(last_seen_at)
    if last_seen_at and (now - last_seen_at) > SESSION_IDLE_TIMEOUT:
        return True

    return False


def invitation_is_invalid(invitation) -> bool:
    """Check whether an invitation token is missing, used, or expired."""
    if not invitation or invitation.used_at:
        return True

    expires_at = invitation.expires_at
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    return bool(expires_at and expires_at < datetime.now())


class CSRFCookieMiddleware(BaseHTTPMiddleware):
    """Ensure every request has a CSRF cookie/token pair available."""

    async def dispatch(self, request: Request, call_next):
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        request.state.csrf_raw = csrf_raw
        request.state.csrf_token = create_csrf_signed_token(csrf_raw)

        response = await call_next(request)
        if request.cookies.get(CSRF_COOKIE_NAME) != csrf_raw:
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=csrf_raw,
                max_age=3600,
                httponly=True,
                samesite="strict",
                secure=secure_cookie_enabled(request),
            )
        return response


class CookieAuthMiddleware(BaseHTTPMiddleware):
    """
    Cookie-based authentication middleware.
    Supports both legacy single-user and multi-user database auth.
    Redirects unauthenticated users to the login page.
    Immediately invalidates sessions for disabled users.
    """

    # Public paths that don't require authentication
    PUBLIC_PATHS = [
        "/login", "/logout", "/register", "/forgot-password", "/reset-password",
        "/accept-invite"
    ]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.user = None
        request.state.session = None
        request.state.session_token = None

        service = get_service()
        try:
            user_count = service.db.user_count()

            # Skip authentication only for local bootstrap/dev mode.
            if not AUTH_PASSWORD and user_count == 0:
                return await call_next(request)

            # Allow access to public pages and static assets
            if any(path.startswith(p) for p in self.PUBLIC_PATHS) or path.startswith("/static"):
                return await call_next(request)

            auth_cookie = request.cookies.get(AUTH_COOKIE_NAME)
            if not auth_cookie:
                return self._reject(request)

            session = service.db.get_user_session_by_hash(hash_value(auth_cookie))
            if not session or session.revoked_at or current_session_is_expired(session):
                if session and not session.revoked_at:
                    service.db.revoke_user_session(session.id)
                return self._reject(request, clear_cookie=True)

            if session.user_id is None:
                if user_count != 0:
                    service.db.revoke_user_session(session.id)
                    return self._reject(request, clear_cookie=True)
                request.state.session = session
                request.state.session_token = auth_cookie
                service.db.touch_user_session(session.id, get_client_ip(request))
                return await call_next(request)

            user = service.db.get_user(session.user_id)
            if not user:
                service.db.revoke_user_session(session.id)
                return self._reject(request, clear_cookie=True)
            if not user.is_active:
                service.db.revoke_all_user_sessions(user.id)
                return self._reject(request, clear_cookie=True, detail="Account disabled")

            request.state.user = user
            request.state.session = session
            request.state.session_token = auth_cookie
            service.db.touch_user_session(session.id, get_client_ip(request))

            if not user.is_approved:
                if path == "/pending-approval":
                    return await call_next(request)
                if path.startswith("/api/"):
                    return self._reject(
                        request,
                        status_code=403,
                        detail="Account pending approval",
                        clear_cookie=False,
                    )
                return RedirectResponse(url="/pending-approval", status_code=302)

            return await call_next(request)
        finally:
            service.close()

    def _reject(self, request: Request, status_code: int = 401,
                detail: str = "Authentication required",
                clear_cookie: bool = False):
        """Return an auth failure response suitable for the route type."""
        if request.url.path.startswith("/api/"):
            response = build_api_auth_response(status_code, detail)
        else:
            response = RedirectResponse(url="/login", status_code=302)
        if clear_cookie:
            clear_auth_cookie(response)
        return response


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """
    Redirect HTTP to HTTPS in production.
    Checks X-Forwarded-Proto header (set by reverse proxies like Railway, Render).
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


# Add middleware (outermost added last)
app.add_middleware(CSRFCookieMiddleware)
app.add_middleware(CookieAuthMiddleware)
app.add_middleware(HTTPSRedirectMiddleware)


# =============================================================================
# EXCEPTION HANDLERS
# =============================================================================

def sanitize_error_message(message: str) -> str:
    """Remove sensitive data from error messages before returning to users."""
    import re
    patterns = [
        (r'sk-[a-zA-Z0-9]{20,}', '[REDACTED]'),
        (r'sk-ant-[a-zA-Z0-9-]+', '[REDACTED]'),
        (r'password[=:]\s*\S+', 'password=[REDACTED]'),
        (r'api[_-]?key[=:]\s*\S+', 'api_key=[REDACTED]'),
        (r'secret[=:]\s*\S+', 'secret=[REDACTED]'),
        (r'token[=:]\s*[a-zA-Z0-9._-]+', 'token=[REDACTED]'),
    ]
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message, flags=re.IGNORECASE)
    return message


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler that sanitizes error messages."""
    # Log the full exception with traceback (will be sanitized by logging_config)
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


# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Static files directory
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")


# =============================================================================
# TEMPLATE FILTERS
# =============================================================================

def parse_date_value(d):
    """Convert various date formats to a date object"""
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        try:
            # Try ISO datetime format (e.g., 2024-01-15T10:30:00)
            return datetime.fromisoformat(d.replace('Z', '+00:00')).date()
        except:
            try:
                # Try YYYY-MM-DD format (SQLite default)
                return datetime.strptime(d, "%Y-%m-%d").date()
            except:
                try:
                    # Try DD/MM/YYYY format
                    return datetime.strptime(d, "%d/%m/%Y").date()
                except:
                    try:
                        # Try YYYY-MM-DD HH:MM:SS format
                        return datetime.strptime(d[:19], "%Y-%m-%d %H:%M:%S").date()
                    except:
                        return None
    return None

def format_date_filter(d) -> str:
    """Format date for display in templates"""
    parsed = parse_date_value(d)
    if parsed is None:
        return "N/A"
    try:
        return parsed.strftime("%d/%m/%Y")
    except:
        return "N/A"

def format_date_short_filter(d) -> str:
    """Format date short (dd/mm) for display in templates"""
    parsed = parse_date_value(d)
    if parsed is None:
        return "N/A"
    try:
        return parsed.strftime("%d/%m")
    except:
        return "N/A"

def format_number_filter(n, decimals=1) -> str:
    """Format number with specified decimals"""
    if n is None:
        return "--"
    try:
        return f"{float(n):.{decimals}f}"
    except:
        return "--"

def format_datetime_filter(d) -> str:
    """Format datetime for display in templates"""
    if d is None:
        return "N/A"
    if isinstance(d, str):
        try:
            # Try ISO format
            d = datetime.fromisoformat(d.replace('Z', '+00:00'))
        except:
            return d[:16] if len(d) > 16 else d
    if isinstance(d, datetime):
        return d.strftime("%d/%m/%Y %H:%M")
    if isinstance(d, date):
        return d.strftime("%d/%m/%Y")
    return "N/A"


def short_report_label_filter(value: Optional[str], max_length: int = 22) -> str:
    """Shorten long report identifiers for dense table layouts."""
    if value is None:
        return ""

    label = str(value).strip()
    if len(label) <= max_length:
        return label

    parts = [part for part in label.split("/") if part]
    if len(parts) >= 4:
        candidate = f"{parts[0]}/{parts[1]}/.../{parts[-1]}"
        if len(candidate) <= max_length:
            return candidate

        tail_fragment = parts[-2][-4:] if len(parts[-2]) > 4 else parts[-2]
        candidate = f"{parts[0]}/{parts[1]}/...{tail_fragment}/{parts[-1]}"
        if len(candidate) <= max_length:
            return candidate

    head_length = max(8, (max_length - 3) // 2)
    tail_length = max(5, max_length - head_length - 3)
    return f"{label[:head_length]}...{label[-tail_length:]}"

# HTML sanitization for AI-generated content
ALLOWED_TAGS = [
    'p', 'br', 'strong', 'em', 'b', 'i', 'u',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'blockquote', 'code', 'pre',
    'a', 'span', 'div'
]
ALLOWED_ATTRIBUTES = {
    'a': ['href', 'title'],
    'th': ['colspan', 'rowspan'],
    'td': ['colspan', 'rowspan'],
}


def sanitize_html_filter(content: str) -> Markup:
    """Sanitize HTML content, allowing only safe tags"""
    if not content:
        return Markup("")
    cleaned = bleach.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True
    )
    return Markup(cleaned)


# Register filters with Jinja2
templates.env.filters["format_date"] = format_date_filter
templates.env.filters["format_date_short"] = format_date_short_filter
templates.env.filters["format_number"] = format_number_filter
templates.env.filters["format_datetime"] = format_datetime_filter
templates.env.filters["short_report_label"] = short_report_label_filter
templates.env.filters["sanitize_html"] = sanitize_html_filter

# Register translation function as Jinja2 global
templates.env.globals["t"] = get_text
templates.env.globals["csrf_token_for_request"] = csrf_token_for_request


def add_csrf_to_response(response: Response, request: Request) -> str:
    """Add CSRF cookie and return token for template"""
    csrf_token = getattr(request.state, "csrf_raw", None) or request.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_token:
        csrf_token = generate_csrf_token()
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_token,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https" or IS_PRODUCTION
        )
    return csrf_token


def validate_csrf(request: Request, form_token: Optional[str] = None) -> bool:
    """Validate CSRF token from form against cookie"""
    cookie_token = getattr(request.state, "csrf_raw", None) or request.cookies.get(CSRF_COOKIE_NAME)
    provided_token = form_token or request.headers.get("X-CSRF-Token")
    return verify_csrf_token(provided_token, cookie_token)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Global service instance - initialized once at startup, reused for all requests
_global_service: Optional[VetProteinService] = None
DIAGNOSIS_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2)


class ServiceProxy:
    """
    Proxy wrapper for VetProteinService that makes close() a no-op.
    This allows code to call service.close() without actually closing
    the shared global database connection.
    """

    def __init__(self, service: VetProteinService):
        self._service = service

    def __getattr__(self, name):
        return getattr(self._service, name)

    def close(self):
        """No-op: don't close the global service."""
        pass


def _init_global_service():
    """Initialize the global service instance (called once at startup)."""
    global _global_service
    if _global_service is None:
        _global_service = VetProteinService(
            db_path=str(DB_PATH),
            uploads_dir=str(UPLOADS_DIR)
        )
        _global_service.initialize()
        expired_sessions = _global_service.db.cleanup_expired_user_sessions()
        old_auth_events = _global_service.db.cleanup_old_auth_events()
        stale_jobs = _global_service.db.mark_stale_diagnosis_jobs_failed()
        if expired_sessions:
            logger.warning(f"Revoked {expired_sessions} expired sessions during startup")
        if old_auth_events:
            logger.info(f"Pruned {old_auth_events} old auth events during startup")
        if stale_jobs:
            logger.warning(f"Marked {stale_jobs} stale diagnosis jobs as failed during startup")
        logger.info("Global database service initialized")


def get_service() -> VetProteinService:
    """
    Get a service instance that wraps the global database connection.
    Returns a proxy that ignores close() calls to keep the connection alive.
    """
    global _global_service
    if _global_service is None:
        _init_global_service()
    return ServiceProxy(_global_service)


@app.on_event("startup")
async def startup_event():
    """Initialize database on app startup."""
    _init_global_service()


def serialize_diagnosis_job(job: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prepare a diagnosis job dict for JSON responses and templates."""
    if not job:
        return None

    payload = dict(job)
    for field in ("created_at", "started_at", "completed_at"):
        value = payload.get(field)
        if isinstance(value, (date, datetime)):
            payload[field] = value.isoformat()
        elif value is not None:
            payload[field] = str(value)

    payload["is_active"] = payload.get("status") in {"pending", "running"}
    report_id = payload.get("report_id")
    animal_id = payload.get("animal_id")
    payload["redirect_url"] = (
        f"/animal/{animal_id}/diagnosis/{report_id}"
        if animal_id and report_id else None
    )
    return payload


def run_diagnosis_job(job_id: int, animal_id: int, report_type: str,
                      anthropic_api_key: str,
                      openai_api_key: Optional[str]) -> None:
    """Run diagnosis generation in a worker thread with its own DB connection."""
    service = VetProteinService(db_path=str(DB_PATH), uploads_dir=str(UPLOADS_DIR))
    started_at = datetime.utcnow().isoformat(timespec="seconds")
    completed_at = None

    try:
        service.initialize()
        service.db.update_diagnosis_job(
            job_id,
            status="running",
            started_at=started_at,
            error_message=None,
        )

        report = create_diagnosis_report(
            db=service.db,
            animal_id=animal_id,
            report_type=report_type,
            anthropic_api_key=anthropic_api_key,
            openai_api_key=openai_api_key,
        )

        completed_at = datetime.utcnow().isoformat(timespec="seconds")
        service.db.update_diagnosis_job(
            job_id,
            status="completed",
            report_id=report.id,
            error_message=None,
            completed_at=completed_at,
        )
        logger.info(
            f"Completed diagnosis job {job_id} for animal {animal_id} with report {report.id}"
        )
    except Exception as exc:
        completed_at = datetime.utcnow().isoformat(timespec="seconds")
        logger.exception(f"Diagnosis job {job_id} failed for animal {animal_id}")
        try:
            if service.db.conn:
                service.db.update_diagnosis_job(
                    job_id,
                    status="failed",
                    error_message=str(exc),
                    completed_at=completed_at,
                )
        except Exception:
            logger.exception(f"Failed to persist diagnosis job failure state for job {job_id}")
    finally:
        service.close()


def format_date(d) -> str:
    """Format date for display"""
    if d is None:
        return "N/A"
    if isinstance(d, str):
        candidate = d.strip()
        for parser in (
            lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')).date(),
            lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
            lambda value: datetime.strptime(value, "%Y-%m-%d %H:%M:%S").date(),
        ):
            try:
                return parser(candidate).strftime("%d/%m/%Y")
            except Exception:
                continue
        return candidate
    try:
        return d.strftime("%d/%m/%Y")
    except:
        return "N/A"


def json_serial(obj):
    """JSON serializer for objects not serializable by default"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def sanitize_pdf_filename(filename: Optional[str]) -> str:
    """Return a filesystem-safe PDF filename."""
    import re

    candidate = re.sub(r"[^\w\-.]", "_", os.path.basename(filename or "report.pdf"))
    if not candidate.lower().endswith(".pdf"):
        candidate = f"{candidate}.pdf"
    return candidate or "report.pdf"


def allocate_upload_path(filename: Optional[str]) -> Path:
    """Create a unique final uploads path for a validated PDF."""
    safe_filename = sanitize_pdf_filename(filename)
    destination = UPLOADS_DIR / safe_filename
    stem = destination.stem
    suffix = destination.suffix or ".pdf"
    counter = 1
    while destination.exists():
        destination = UPLOADS_DIR / f"{stem}_{counter}{suffix}"
        counter += 1
    return destination


def build_upload_url(file_path: Optional[str]) -> Optional[str]:
    """Convert an absolute uploads path to a static /uploads URL when possible."""
    if not file_path:
        return None

    try:
        relative_path = Path(file_path).resolve().relative_to(UPLOADS_DIR.resolve())
        return f"/uploads/{relative_path.as_posix()}"
    except Exception:
        normalized = file_path.replace("\\", "/")
        if normalized.startswith("uploads/"):
            return f"/{normalized}"
        if "/uploads/" in normalized:
            return f"/uploads/{normalized.split('/uploads/', 1)[1]}"
        return None


def parse_positive_int(raw_value: Optional[str], default: int, minimum: int = 1,
                       maximum: Optional[int] = None) -> int:
    """Parse query-string integers safely."""
    try:
        value = int(raw_value or default)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def build_page_url(request: Request, **updates) -> str:
    """Return the current path with updated query parameters."""
    query = dict(request.query_params)
    for key, value in updates.items():
        if value in (None, "", []):
            query.pop(key, None)
        else:
            query[key] = str(value)
    encoded = urlencode(query)
    return f"{request.url.path}?{encoded}" if encoded else request.url.path


def build_pagination(request: Request, total_items: int, page: int, page_size: int,
                     param_name: str = "page") -> Dict[str, Any]:
    """Pagination metadata including ready-to-use URLs for templates."""
    total_pages = max((total_items + page_size - 1) // page_size, 1)
    page = max(1, min(page, total_pages))
    window_start = max(1, page - 2)
    window_end = min(total_pages, page + 2)
    pages = []
    for page_number in range(window_start, window_end + 1):
        pages.append({
            "number": page_number,
            "current": page_number == page,
            "url": build_page_url(request, **{param_name: page_number}),
        })
    return {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_url": build_page_url(request, **{param_name: page - 1}) if page > 1 else None,
        "next_url": build_page_url(request, **{param_name: page + 1}) if page < total_pages else None,
        "pages": pages,
        "range_start": 0 if total_items == 0 else ((page - 1) * page_size) + 1,
        "range_end": min(total_items, page * page_size),
    }


def build_sort_toggle(request: Request, current_sort: str, field_key: str,
                      default_direction: str = "asc", param_name: str = "sort",
                      page_param: str = "page") -> Dict[str, Optional[str]]:
    """Build toggle metadata for clickable table header sorting."""
    asc_value = f"{field_key}_asc"
    desc_value = f"{field_key}_desc"
    state = None
    if current_sort == asc_value:
        state = "asc"
    elif current_sort == desc_value:
        state = "desc"

    if default_direction == "desc":
        next_value = asc_value if current_sort == desc_value else desc_value
    else:
        next_value = desc_value if current_sort == asc_value else asc_value

    return {
        "url": build_page_url(request, **{param_name: next_value, page_param: 1}),
        "state": state,
    }


def humanize_report_type(report_type: Optional[str], panel_name: Optional[str] = None,
                         lang: str = DEFAULT_LANGUAGE) -> str:
    """Display-friendly label for report families."""
    normalized = (report_type or "").strip().lower()
    panel = (panel_name or "").strip()
    if normalized == "dnatech_proteinogram":
        return get_text(lang, "report_types.dnatech_proteinogram")
    if normalized == "cvs_analyzer":
        return get_text(lang, "report_types.cvs_analyzer")
    if normalized == "cytology":
        return get_text(lang, "report_types.cytology")
    if normalized == "immunocytochemistry":
        return get_text(lang, "report_types.immunocytochemistry")
    if normalized == "biochemistry":
        return get_text(lang, "report_types.biochemistry")
    if normalized == "urinalysis":
        return get_text(lang, "report_types.urinalysis")
    if panel:
        return panel.replace("_", " ").title()
    if normalized:
        return normalized.replace("_", " ").title()
    return get_text(lang, "report_types.imported_report")


def summarize_report_overview(row: Dict[str, Any], lang: str = DEFAULT_LANGUAGE) -> str:
    """Compact summary used in tables and cards."""
    if row.get("protein_result_count"):
        return get_text(lang, "report_summary.protein_markers").format(
            count=row["protein_result_count"]
        )
    if row.get("measurement_count"):
        return get_text(lang, "report_summary.measurements").format(
            count=row["measurement_count"]
        )
    if row.get("pathology_finding_count"):
        if row.get("asset_count"):
            return get_text(lang, "report_summary.findings_with_images").format(
                findings=row["pathology_finding_count"],
                images=row["asset_count"],
            )
        return get_text(lang, "report_summary.findings").format(
            count=row["pathology_finding_count"]
        )
    if (row.get("report_type") or "").lower() in {"biochemistry", "urinalysis"}:
        return get_text(lang, "report_summary.renal_and_urine_markers")
    return get_text(lang, "report_summary.imported_report")


def describe_report_item(item: dict, lang: str = DEFAULT_LANGUAGE) -> str:
    """Human-readable summary for a session row in the animal page."""
    if item["results"]:
        return get_text(lang, "report_summary.protein_markers").format(
            count=len(item["results"])
        )
    if item["measurements"]:
        return get_text(lang, "report_summary.measurements").format(
            count=len(item["measurements"])
        )
    if item["pathology_findings"]:
        if item["session_assets"]:
            return get_text(lang, "report_summary.findings_with_images").format(
                findings=len(item["pathology_findings"]),
                images=len(item["session_assets"]),
            )
        return get_text(lang, "report_summary.findings").format(
            count=len(item["pathology_findings"])
        )
    if item["biochemistry"] or item["urinalysis"]:
        return get_text(lang, "report_summary.renal_and_urine_markers")
    return get_text(lang, "report_summary.imported_report")


def build_session_groups(sessions_with_results: List[dict],
                         lang: str = DEFAULT_LANGUAGE) -> List[dict]:
    """Group animal history rows by report family for the UI."""
    grouped = {}

    for item in sessions_with_results:
        session = item["session"]
        report_type = (session.report_type or "").lower()
        source_system = (session.source_system or "").lower()

        if report_type == "dnatech_proteinogram":
            key = "protein_reports"
            label = get_text(lang, "report_groups.protein_reports")
            sort_order = 1
        elif "cytology" in report_type or "immuno" in report_type or source_system == "vedis":
            key = "pathology_reports"
            label = get_text(lang, "report_groups.pathology_reports")
            sort_order = 3
        elif session.panel_name or item["measurements"]:
            key = "analyzer_reports"
            label = get_text(lang, "report_groups.analyzer_reports")
            sort_order = 2
        else:
            key = "other_reports"
            label = get_text(lang, "report_groups.other_reports")
            sort_order = 4

        if key not in grouped:
            grouped[key] = {
                "key": key,
                "label": label,
                "sort_order": sort_order,
                "rows": [],
            }

        item["report_summary"] = describe_report_item(item, lang)
        grouped[key]["rows"].append(item)

    return sorted(grouped.values(), key=lambda group: group["sort_order"])


def get_lang(request: Request) -> str:
    """Get language from request (query param > cookie > accept-language)"""
    query_lang = request.query_params.get('lang')
    cookie_lang = request.cookies.get('lang')
    accept_lang = request.headers.get('accept-language')
    return get_language_from_request(query_lang, cookie_lang, accept_lang)


def set_lang_cookie(response, lang: str):
    """Set language cookie on response"""
    response.set_cookie(
        key="lang",
        value=lang,
        max_age=365 * 24 * 60 * 60,  # 1 year
        httponly=True,
        samesite="lax"
    )
    return response


# =============================================================================
# AUTHENTICATION ROUTES
# =============================================================================

def is_multi_user_enabled(service: VetProteinService) -> bool:
    """Return whether database-backed multi-user auth is active."""
    return service.db.user_count() > 0


def render_login_template(request: Request, lang: str,
                          error: Optional[str] = None,
                          username: Optional[str] = None,
                          status_code: int = 200) -> Response:
    """Render the login screen with the current auth mode."""
    service = get_service()
    try:
        multi_user_enabled = is_multi_user_enabled(service)
    finally:
        service.close()

    response = render_template_with_csrf("login.html", request, {
        "request": request,
        "lang": lang,
        "error": error,
        "username": username or "",
        "multi_user_enabled": multi_user_enabled,
        "allow_self_registration": ALLOW_SELF_REGISTRATION,
        "csrf_token": csrf_token_for_request(request),
    }, status_code=status_code)
    return set_lang_cookie(response, lang)


def render_profile_template(
    request: Request,
    lang: str,
    current_user: User,
    *,
    profile_error: Optional[str] = None,
    profile_success: bool = False,
    password_error: Optional[str] = None,
    password_success: bool = False,
    form_values: Optional[Dict[str, str]] = None,
    status_code: int = 200,
) -> Response:
    """Render the signed-in user profile page."""
    response = render_template_with_csrf("profile.html", request, {
        "request": request,
        "lang": lang,
        "current_user": current_user,
        "profile_error": profile_error,
        "profile_success": profile_success,
        "password_error": password_error,
        "password_success": password_success,
        "form_values": form_values or {},
        "csrf_token": csrf_token_for_request(request),
    }, status_code=status_code)
    return set_lang_cookie(response, lang)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: Optional[str] = None, error: Optional[str] = None):
    """Display login page"""
    # Get language from query param or detect from request
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    # Map error codes to messages
    error_message = None
    if error == "disabled":
        error_message = get_text(current_lang, "auth.login.error_disabled")
    elif error == "invalid":
        error_message = get_text(current_lang, "login.error")
    return render_login_template(request, current_lang, error=error_message)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(None),
    email: str = Form(None),
    password: str = Form(...),
    csrf_token: str = Form(None)
):
    """Handle login form submission - supports both legacy and multi-user auth"""
    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/login?error=invalid", status_code=302)

    # Use email field if provided, otherwise use username (for backwards compatibility)
    login_identifier = (email or username or "").strip()
    email_normalized = normalize_email(login_identifier)
    service = get_service()
    try:
        user_count = service.db.user_count()

        if auth_action_is_limited(service, "login", request, email_normalized):
            error_msg = get_text(lang, "auth.login.error_rate_limit").format(
                minutes=AUTH_LOCKOUT_MINUTES
            )
            return render_login_template(
                request,
                lang,
                error=error_msg,
                username=login_identifier,
                status_code=429,
            )

        if user_count > 0:
            # Multi-user mode: authenticate against database
            auth_service = AuthService(service.db)
            user, error_code = auth_service.authenticate(login_identifier, password)

            if user:
                record_auth_event(
                    service,
                    "login",
                    request,
                    success=True,
                    email_normalized=user.email_normalized,
                    user_id=user.id,
                )
                token = create_auth_session(service, request, user.id)
                if user.is_approved:
                    response = RedirectResponse(url="/", status_code=302)
                else:
                    response = RedirectResponse(url="/pending-approval", status_code=302)
                set_auth_cookie(response, request, token)
                return response

            # Handle specific errors
            if error_code == "disabled":
                user = service.db.get_user_by_email(login_identifier)
                record_auth_event(
                    service,
                    "login",
                    request,
                    success=False,
                    email_normalized=user.email_normalized if user else email_normalized,
                    user_id=user.id if user else None,
                    metadata={"error_code": error_code},
                )
                return RedirectResponse(url="/login?error=disabled", status_code=302)
            elif error_code == "pending_approval":
                # User exists but not approved - create session anyway
                user = service.db.get_user_by_email(login_identifier)
                if user:
                    record_auth_event(
                        service,
                        "login",
                        request,
                        success=True,
                        email_normalized=user.email_normalized,
                        user_id=user.id,
                        metadata={"pending_approval": True},
                    )
                    token = create_auth_session(service, request, user.id)
                    response = RedirectResponse(url="/pending-approval", status_code=302)
                    set_auth_cookie(response, request, token)
                    return response

            record_auth_event(
                service,
                "login",
                request,
                success=False,
                email_normalized=email_normalized,
                metadata={"error_code": error_code or "invalid_credentials"},
            )
        else:
            # Legacy mode: authenticate against env vars
            username_correct = AUTH_PASSWORD and secrets.compare_digest(login_identifier, AUTH_USERNAME)
            password_correct = AUTH_PASSWORD and secrets.compare_digest(password, AUTH_PASSWORD)

            if username_correct and password_correct:
                record_auth_event(
                    service,
                    "login",
                    request,
                    success=True,
                    email_normalized=email_normalized,
                    metadata={"legacy_mode": True},
                )
                token = create_auth_session(service, request, None)
                response = RedirectResponse(url="/", status_code=302)
                set_auth_cookie(response, request, token)
                return response

            record_auth_event(
                service,
                "login",
                request,
                success=False,
                email_normalized=email_normalized,
                metadata={"legacy_mode": True, "error_code": "invalid_credentials"},
            )

        return render_login_template(
            request,
            lang,
            error=get_text(lang, "login.error"),
            username=login_identifier,
            status_code=401,
        )
    finally:
        service.close()


@app.post("/logout")
async def logout(request: Request, csrf_token: str = Form(None)):
    """Log out user by revoking the current server-side session."""
    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid request")

    service = get_service()
    try:
        auth_cookie = request.cookies.get(AUTH_COOKIE_NAME)
        if auth_cookie:
            service.db.revoke_user_session_by_hash(hash_value(auth_cookie))
    finally:
        service.close()

    response = RedirectResponse(url="/login", status_code=302)
    clear_auth_cookie(response)
    response.delete_cookie(key=CSRF_COOKIE_NAME)
    return response


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """Display profile settings for the signed-in user."""
    lang = get_lang(request)
    current_user = getattr(request.state, "user", None)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    return render_profile_template(
        request,
        lang,
        current_user,
        profile_success=request.query_params.get("saved") == "profile",
        password_success=request.query_params.get("saved") == "password",
    )


@app.post("/profile")
async def update_profile(
    request: Request,
    display_name: str = Form(...),
    email: str = Form(...),
    csrf_token: str = Form(None),
):
    """Update the signed-in user's display name and email."""
    lang = get_lang(request)
    current_user = getattr(request.state, "user", None)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid request")

    cleaned_name = (display_name or "").strip()
    cleaned_email = (email or "").strip()
    form_values = {"display_name": cleaned_name, "email": cleaned_email}

    if not cleaned_name:
        return render_profile_template(
            request, lang, current_user,
            profile_error=get_text(lang, "profile.error_name_required"),
            form_values=form_values,
            status_code=400,
        )

    is_valid_email, email_error = validate_email(cleaned_email)
    if not is_valid_email:
        return render_profile_template(
            request, lang, current_user,
            profile_error=email_error,
            form_values=form_values,
            status_code=400,
        )

    normalized_email = cleaned_email.lower()
    service = get_service()
    try:
        existing_user = service.db.get_user_by_email(cleaned_email)
        if existing_user and existing_user.id != current_user.id:
            return render_profile_template(
                request, lang, current_user,
                profile_error=get_text(lang, "profile.error_email_exists"),
                form_values=form_values,
                status_code=400,
            )

        service.db.update_user(
            current_user.id,
            display_name=cleaned_name,
            email=cleaned_email,
            email_normalized=normalized_email,
        )
        updated_user = service.db.get_user(current_user.id)
        request.state.user = updated_user
    finally:
        service.close()

    return RedirectResponse(url="/profile?saved=profile", status_code=302)


@app.post("/profile/password")
async def update_profile_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(None),
):
    """Update the signed-in user's password."""
    lang = get_lang(request)
    current_user = getattr(request.state, "user", None)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid request")

    if new_password != confirm_password:
        return render_profile_template(
            request, lang, current_user,
            password_error=get_text(lang, "profile.error_password_mismatch"),
            status_code=400,
        )

    if not verify_password(current_password, current_user.password_hash):
        return render_profile_template(
            request, lang, current_user,
            password_error=get_text(lang, "profile.error_current_password"),
            status_code=400,
        )

    is_valid_password, password_error = validate_password(new_password, min_length=12)
    if not is_valid_password:
        return render_profile_template(
            request, lang, current_user,
            password_error=password_error,
            status_code=400,
        )

    service = get_service()
    try:
        service.db.update_user(
            current_user.id,
            password_hash=hash_password(new_password),
        )
        service.db.revoke_all_user_sessions(current_user.id)
        new_token = create_auth_session(service, request, current_user.id)
    finally:
        service.close()

    response = RedirectResponse(url="/profile?saved=password", status_code=302)
    set_auth_cookie(response, request, new_token)
    return response


# =============================================================================
# MULTI-USER AUTHENTICATION ROUTES
# =============================================================================

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: Optional[str] = None):
    """Display registration page"""
    if not ALLOW_SELF_REGISTRATION:
        raise HTTPException(status_code=404, detail="Not found")

    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    response = render_template_with_csrf("auth/register.html", request, {
        "request": request,
        "lang": current_lang,
        "error": None,
        "csrf_token": csrf_token_for_request(request),
    })
    return set_lang_cookie(response, current_lang)


@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(None),
    csrf_token: str = Form(None)
):
    """Handle registration form submission"""
    if not ALLOW_SELF_REGISTRATION:
        raise HTTPException(status_code=404, detail="Not found")

    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/register?error=invalid", status_code=302)

    # Validate passwords match
    if password != password_confirm:
        response = render_template_with_csrf("auth/register.html", request, {
            "request": request,
            "lang": lang,
            "error": get_text(lang, "auth.register.error_password_mismatch"),
            "email": email,
            "display_name": display_name,
            "csrf_token": csrf_token_for_request(request),
        }, status_code=400)
        return set_lang_cookie(response, lang)

    service = get_service()
    try:
        email_normalized = normalize_email(email)
        if auth_action_is_limited(
            service, "register", request, email_normalized, failures_only=False
        ):
            record_auth_event(
                service,
                "register",
                request,
                success=False,
                email_normalized=email_normalized,
                metadata={"limited": True},
            )
            response = render_template_with_csrf("auth/register.html", request, {
                "request": request,
                "lang": lang,
                "error": get_text(lang, "auth.common.rate_limit").format(
                    minutes=AUTH_LOCKOUT_MINUTES
                ),
                "email": email,
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=429)
            return set_lang_cookie(response, lang)

        auth_service = AuthService(service.db)
        user, error = auth_service.register_user(email, password, display_name)

        if error:
            record_auth_event(
                service,
                "register",
                request,
                success=False,
                email_normalized=email_normalized,
                metadata={"error": error},
            )
            response = render_template_with_csrf("auth/register.html", request, {
                "request": request,
                "lang": lang,
                "error": error,
                "email": email,
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=400)
            return set_lang_cookie(response, lang)

        # Send confirmation email to user
        if email_service.is_configured():
            email_service.send_signup_confirmation(email, display_name, lang)

        # Send notification to admins - use validated host
        superusers = service.db.get_superusers()
        if superusers and email_service.is_configured():
            admin_emails = [u.email for u in superusers]
            host = get_safe_host(request)
            scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
            admin_url = f"{scheme}://{host}/admin/users"
            email_service.send_new_registration_alert(
                admin_emails, email, display_name, admin_url, lang
            )

        # Create session and redirect to pending approval
        record_auth_event(
            service,
            "register",
            request,
            success=True,
            email_normalized=user.email_normalized,
            user_id=user.id,
        )
        token = create_auth_session(service, request, user.id)
        response = RedirectResponse(url="/pending-approval", status_code=302)
        set_auth_cookie(response, request, token)
        return response
    finally:
        service.close()


@app.get("/pending-approval", response_class=HTMLResponse)
async def pending_approval_page(request: Request, lang: Optional[str] = None):
    """Display pending approval page"""
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    user = getattr(request.state, 'user', None)

    response = render_template_with_csrf("auth/pending_approval.html", request, {
        "request": request,
        "lang": current_lang,
        "email": user.email if user else None,
        "csrf_token": csrf_token_for_request(request),
    })
    return set_lang_cookie(response, current_lang)


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, lang: Optional[str] = None):
    """Display forgot password page"""
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    response = render_template_with_csrf("auth/forgot_password.html", request, {
        "request": request,
        "lang": current_lang,
        "error": None,
        "success": False,
        "csrf_token": csrf_token_for_request(request),
    })
    return set_lang_cookie(response, current_lang)


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(None)
):
    """Handle forgot password form submission"""
    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/forgot-password?error=invalid", status_code=302)

    service = get_service()
    try:
        email_normalized = normalize_email(email)
        if auth_action_is_limited(
            service, "forgot_password", request, email_normalized, failures_only=False
        ):
            record_auth_event(
                service,
                "forgot_password",
                request,
                success=False,
                email_normalized=email_normalized,
                metadata={"limited": True},
            )
            response = templates.TemplateResponse(request, "auth/forgot_password.html", {
                "request": request,
                "lang": lang,
                "error": None,
                "success": True,
            })
            return set_lang_cookie(response, lang)

        auth_service = AuthService(service.db)
        token, _ = auth_service.create_password_reset_token(email)
        user = service.db.get_user_by_email(email)

        record_auth_event(
            service,
            "forgot_password",
            request,
            success=True,
            email_normalized=email_normalized,
            user_id=user.id if user else None,
        )

        if token and email_service.is_configured():
            # Send password reset email - use validated host to prevent header injection
            host = get_safe_host(request)
            scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
            reset_url = f"{scheme}://{host}/reset-password?token={token}"

            email_service.send_password_reset(
                email, reset_url,
                user.display_name if user else None,
                lang
            )
    finally:
        service.close()

    # Always show success message (don't reveal if email exists)
    response = templates.TemplateResponse(request, "auth/forgot_password.html", {
        "request": request,
        "lang": lang,
        "error": None,
        "success": True
    })
    return set_lang_cookie(response, lang)


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str,
    lang: Optional[str] = None
):
    """Display reset password page"""
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    # Validate token exists and is not expired
    from auth import hash_token
    service = get_service()
    try:
        token_hash = hash_token(token)
        reset_token = service.db.get_password_reset_token(token_hash)

        if not reset_token or reset_token.used_at:
            response = templates.TemplateResponse(request, "auth/reset_password.html", {
                "request": request,
                "lang": current_lang,
                "invalid_token": True,
                "token": token
            })
            return set_lang_cookie(response, current_lang)

        # Check expiry
        from datetime import datetime
        if isinstance(reset_token.expires_at, str):
            expires_at = datetime.fromisoformat(reset_token.expires_at)
        else:
            expires_at = reset_token.expires_at

        if expires_at < datetime.now():
            response = templates.TemplateResponse(request, "auth/reset_password.html", {
                "request": request,
                "lang": current_lang,
                "invalid_token": True,
                "token": token
            })
            return set_lang_cookie(response, current_lang)
    finally:
        service.close()

    response = render_template_with_csrf("auth/reset_password.html", request, {
        "request": request,
        "lang": current_lang,
        "token": token,
        "error": None,
        "success": False,
        "csrf_token": csrf_token_for_request(request),
    })
    return set_lang_cookie(response, current_lang)


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(None)
):
    """Handle reset password form submission"""
    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url=f"/reset-password?token={token}&error=invalid", status_code=302)

    # Validate passwords match
    if password != password_confirm:
        response = render_template_with_csrf("auth/reset_password.html", request, {
            "request": request,
            "lang": lang,
            "token": token,
            "error": get_text(lang, "auth.reset.error_password_mismatch"),
            "success": False,
            "csrf_token": csrf_token_for_request(request),
        }, status_code=400)
        return set_lang_cookie(response, lang)

    service = get_service()
    try:
        from auth import hash_token

        token_hash = hash_token(token)
        reset_token = service.db.get_password_reset_token(token_hash)
        reset_user = service.db.get_user(reset_token.user_id) if reset_token else None
        email_normalized = reset_user.email_normalized if reset_user else None

        if auth_action_is_limited(
            service, "reset_password", request, email_normalized, failures_only=False
        ):
            record_auth_event(
                service,
                "reset_password",
                request,
                success=False,
                email_normalized=email_normalized,
                user_id=reset_user.id if reset_user else None,
                metadata={"limited": True},
            )
            response = render_template_with_csrf("auth/reset_password.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": get_text(lang, "auth.common.rate_limit").format(
                    minutes=AUTH_LOCKOUT_MINUTES
                ),
                "success": False,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=429)
            return set_lang_cookie(response, lang)

        auth_service = AuthService(service.db)
        success, error = auth_service.reset_password(token, password)

        record_auth_event(
            service,
            "reset_password",
            request,
            success=success,
            email_normalized=email_normalized,
            user_id=reset_user.id if reset_user else None,
            metadata={"error": error} if error else None,
        )

        if not success:
            response = render_template_with_csrf("auth/reset_password.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": error,
                "success": False,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=400)
            return set_lang_cookie(response, lang)
    finally:
        service.close()

    # Success
    response = templates.TemplateResponse(request, "auth/reset_password.html", {
        "request": request,
        "lang": lang,
        "token": token,
        "error": None,
        "success": True
    })
    return set_lang_cookie(response, lang)


@app.get("/accept-invite", response_class=HTMLResponse)
async def accept_invite_page(
    request: Request,
    token: str,
    lang: Optional[str] = None,
):
    """Display the invitation acceptance page."""
    current_lang = lang if lang in SUPPORTED_LANGUAGES else get_lang(request)
    service = get_service()
    try:
        invitation = service.db.get_invitation_token(hash_token(token))
        invited_user = service.db.get_user(invitation.user_id) if invitation else None
        invalid_token = invitation_is_invalid(invitation) or not invited_user
    finally:
        service.close()

    response = render_template_with_csrf("auth/accept_invite.html", request, {
        "request": request,
        "lang": current_lang,
        "token": token,
        "error": None,
        "success": False,
        "invalid_token": invalid_token,
        "invited_email": invited_user.email if invited_user else None,
        "invited_role": invitation.invited_role if invitation else "user",
        "display_name": invited_user.display_name if invited_user else "",
        "csrf_token": csrf_token_for_request(request),
    })
    return set_lang_cookie(response, current_lang)


@app.post("/accept-invite", response_class=HTMLResponse)
async def accept_invite_submit(
    request: Request,
    token: str = Form(...),
    display_name: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(None),
):
    """Accept an invitation and finish account setup."""
    lang = get_lang(request)
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url=f"/accept-invite?token={quote_plus(token)}&error=invalid", status_code=302)

    service = get_service()
    try:
        invitation = service.db.get_invitation_token(hash_token(token))
        invited_user = service.db.get_user(invitation.user_id) if invitation else None
        email_normalized = invited_user.email_normalized if invited_user else None

        if invitation_is_invalid(invitation) or not invited_user:
            response = render_template_with_csrf("auth/accept_invite.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": None,
                "success": False,
                "invalid_token": True,
                "invited_email": invited_user.email if invited_user else None,
                "invited_role": invitation.invited_role if invitation else "user",
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=400)
            return set_lang_cookie(response, lang)

        if password != password_confirm:
            response = render_template_with_csrf("auth/accept_invite.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": get_text(lang, "auth.invite.error_password_mismatch"),
                "success": False,
                "invalid_token": False,
                "invited_email": invited_user.email,
                "invited_role": invitation.invited_role,
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=400)
            return set_lang_cookie(response, lang)

        if auth_action_is_limited(
            service, "accept_invite", request, email_normalized, failures_only=False
        ):
            record_auth_event(
                service,
                "accept_invite",
                request,
                success=False,
                email_normalized=email_normalized,
                user_id=invited_user.id,
                metadata={"limited": True},
            )
            response = render_template_with_csrf("auth/accept_invite.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": get_text(lang, "auth.common.rate_limit").format(
                    minutes=AUTH_LOCKOUT_MINUTES
                ),
                "success": False,
                "invalid_token": False,
                "invited_email": invited_user.email,
                "invited_role": invitation.invited_role,
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=429)
            return set_lang_cookie(response, lang)

        auth_service = AuthService(service.db)
        accepted_user, error = auth_service.accept_invitation(token, display_name, password)
        record_auth_event(
            service,
            "accept_invite",
            request,
            success=bool(accepted_user),
            email_normalized=email_normalized,
            user_id=invited_user.id,
            metadata={"error": error} if error else None,
        )

        if error or not accepted_user:
            response = render_template_with_csrf("auth/accept_invite.html", request, {
                "request": request,
                "lang": lang,
                "token": token,
                "error": error,
                "success": False,
                "invalid_token": False,
                "invited_email": invited_user.email,
                "invited_role": invitation.invited_role,
                "display_name": display_name,
                "csrf_token": csrf_token_for_request(request),
            }, status_code=400)
            return set_lang_cookie(response, lang)
    finally:
        service.close()

    response = templates.TemplateResponse(request, "auth/accept_invite.html", {
        "request": request,
        "lang": lang,
        "token": token,
        "error": None,
        "success": True,
        "invalid_token": False,
        "invited_email": accepted_user.email,
        "invited_role": invitation.invited_role,
        "display_name": accepted_user.display_name,
    })
    return set_lang_cookie(response, lang)


# =============================================================================
# ADMIN ROUTES
# =============================================================================

def require_superuser(request: Request) -> User:
    """Helper to require superuser access"""
    user = getattr(request.state, 'user', None)
    if not user or not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request):
    """Admin user management page"""
    lang = get_lang(request)
    current_user = require_superuser(request)

    service = get_service()
    try:
        users = service.db.list_users(include_inactive=True)
        pending_users = service.db.get_pending_users()
        active_invitations = service.db.list_active_invitations()
        invitation_lookup = {invite.user_id: invite for invite in active_invitations}

        # Calculate stats
        stats = {
            "total": len(users),
            "approved": sum(1 for u in users if u.is_approved and u.is_active),
            "pending": len(pending_users),
            "disabled": sum(1 for u in users if not u.is_active)
        }

        # Generate CSRF token
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)

        response = templates.TemplateResponse(request, "admin/users.html", {
            "request": request,
            "lang": lang,
            "users": users,
            "pending_users": pending_users,
            "invitation_lookup": invitation_lookup,
            "stats": stats,
            "current_user": current_user,
            "csrf_token": signed_csrf,
            "invite_success": request.query_params.get("invited") == "1",
            "invite_error": (
                get_text(lang, "admin.users.invite.error_exists")
                if request.query_params.get("error") == "invite_exists"
                else get_text(lang, "admin.users.invite.error_invalid_role")
                if request.query_params.get("error") == "invite_role"
                else get_text(lang, "admin.users.invite.error_send_failed")
                if request.query_params.get("error") == "invite_email"
                else get_text(lang, "admin.users.invite.error_invalid_email")
                if request.query_params.get("error") == "invite_invalid"
                else get_text(lang, "admin.users.invite.error_generic")
                if request.query_params.get("error") == "invite_failed"
                else None
            ),
        })
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=secure_cookie_enabled(request)
        )
        return set_lang_cookie(response, lang)
    finally:
        service.close()


@app.post("/admin/users/invite")
async def admin_invite_user(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    csrf_token: str = Form(None),
):
    """Create an invited account and email a setup link."""
    current_user = require_superuser(request)
    lang = get_lang(request)

    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    service = get_service()
    try:
        if not email_service.is_configured():
            return RedirectResponse(url="/admin/users?error=invite_email", status_code=302)

        auth_service = AuthService(service.db)
        invited_user, plain_token, error = auth_service.create_invited_user(
            email=email,
            role=role,
            invited_by_user_id=current_user.id,
        )

        if error or not invited_user or not plain_token:
            error_code = "invite_failed"
            if error == "An account with this email already exists":
                error_code = "invite_exists"
            elif error == "Invalid role":
                error_code = "invite_role"
            elif error in {"Email is required", "Invalid email format"}:
                error_code = "invite_invalid"
            return RedirectResponse(url=f"/admin/users?error={error_code}", status_code=302)

        host = get_safe_host(request)
        scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
        invite_url = f"{scheme}://{host}/accept-invite?token={quote_plus(plain_token)}"
        sent = email_service.send_user_invitation(
            invited_user.email,
            invite_url,
            role,
            current_user.display_name or current_user.email,
            lang,
        )
        if not sent:
            return RedirectResponse(url="/admin/users?error=invite_email", status_code=302)
    finally:
        service.close()

    return RedirectResponse(url="/admin/users?invited=1", status_code=302)


@app.post("/admin/users/{user_id}/approve")
async def admin_approve_user(request: Request, user_id: int, csrf_token: str = Form(None)):
    """Approve a pending user"""
    current_user = require_superuser(request)
    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    service = get_service()
    try:
        user = service.db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        service.db.approve_user(user_id, current_user.id)

        # Send approval notification email - use validated host
        if email_service.is_configured():
            host = get_safe_host(request)
            scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
            login_url = f"{scheme}://{host}/login"
            email_service.send_account_approved(
                user.email, login_url, user.display_name, lang
            )
    finally:
        service.close()

    return RedirectResponse(url="/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/disable")
async def admin_disable_user(request: Request, user_id: int, csrf_token: str = Form(None)):
    """Disable a user account and invalidate their session"""
    current_user = require_superuser(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed for user disable request by {current_user.email}")
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    if user_id == current_user.id:
        logger.warning(f"User {current_user.email} attempted to disable their own account")
        raise HTTPException(status_code=400, detail="Cannot disable your own account")

    service = get_service()
    try:
        # Get user info for logging before disabling
        user_to_disable = service.db.get_user(user_id)
        if user_to_disable:
            service.db.disable_user(user_id)
            service.db.revoke_all_user_sessions(user_id)
            logger.info(
                f"User {user_to_disable.email} (ID: {user_id}) disabled by admin {current_user.email}"
            )
        else:
            logger.warning(f"Attempted to disable non-existent user ID: {user_id}")
    finally:
        service.close()

    return RedirectResponse(url="/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/enable")
async def admin_enable_user(request: Request, user_id: int, csrf_token: str = Form(None)):
    """Re-enable a disabled user account"""
    require_superuser(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    service = get_service()
    try:
        service.db.enable_user(user_id)
    finally:
        service.close()

    return RedirectResponse(url="/admin/users", status_code=302)


# =============================================================================
# API ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Practice-wide inbox and overview."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        db = service.db
        dashboard_page_size = 10
        reports_page = parse_positive_int(
            request.query_params.get("reports_page"),
            default=1,
        )
        animals_page = parse_positive_int(
            request.query_params.get("animals_page"),
            default=1,
        )
        stats_row = db.conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM animals) AS total_animals,
                (SELECT COUNT(*) FROM test_sessions) AS total_reports,
                (SELECT COUNT(*) FROM unassigned_reports WHERE status = 'pending') AS pending_reports,
                (
                    SELECT COUNT(*)
                    FROM email_import_log
                    WHERE import_success = 0
                      AND COALESCE(validation_result, '') NOT IN (
                          'duplicate', 'rate_limited', 'queued_manual_assignment'
                      )
                ) AS failed_imports
        """).fetchone()

        recent_reports, recent_reports_total = db.list_reports_paginated(
            page=reports_page,
            page_size=dashboard_page_size,
        )
        recent_animals, recent_animals_total = db.list_animals_paginated(
            page=animals_page,
            page_size=dashboard_page_size,
        )
        pending_rows, _ = db.list_unassigned_reports(status="pending", page=1, page_size=6)
        pending_reports = []
        for report in pending_rows:
            pending_reports.append({
                "report": report,
                "summary": json.loads(report.parsed_summary_json or "{}"),
                "report_type_label": humanize_report_type(report.report_type, None, lang),
            })
        recent_failures = [
            dict(row) for row in db.conn.execute("""
                SELECT import_timestamp, email_from, attachment_name, error_message
                FROM email_import_log
                WHERE import_success = 0
                  AND COALESCE(validation_result, '') NOT IN (
                      'duplicate', 'rate_limited', 'queued_manual_assignment'
                  )
                ORDER BY import_timestamp DESC
                LIMIT 5
            """).fetchall()
        ]
        for row in recent_reports:
            row["display_type"] = humanize_report_type(
                row.get("report_type"), row.get("panel_name"), lang
            )
            row["summary"] = summarize_report_overview(row, lang)

        response = templates.TemplateResponse(request, "index.html", {
            "request": request,
            "lang": lang,
            "current_user": current_user,
            "stats": dict(stats_row) if stats_row else {},
            "recent_reports": recent_reports,
            "recent_reports_pagination": build_pagination(
                request,
                recent_reports_total,
                reports_page,
                dashboard_page_size,
                param_name="reports_page",
            ),
            "recent_animals": recent_animals,
            "recent_animals_pagination": build_pagination(
                request,
                recent_animals_total,
                animals_page,
                dashboard_page_size,
                param_name="animals_page",
            ),
            "pending_reports": pending_reports,
            "recent_failures": recent_failures,
        })
        return set_lang_cookie(response, lang)
    except Exception:
        logger.exception("Error in home")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/animals", response_class=HTMLResponse)
async def list_animals(request: Request):
    """Animals index with search, filters, and pagination."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        page = parse_positive_int(request.query_params.get("page"), default=1)
        page_size = parse_positive_int(request.query_params.get("page_size"), default=25, maximum=100)
        search = (request.query_params.get("q") or "").strip() or None
        responsible_vet = (request.query_params.get("responsible_vet") or "").strip() or None
        species = (request.query_params.get("species") or "").strip() or None
        sort = (request.query_params.get("sort") or "updated_desc").strip()

        animals, total = service.db.list_animals_paginated(
            search=search,
            responsible_vet=responsible_vet,
            species=species,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        pagination = build_pagination(request, total, page, page_size)
        species_options = [
            row["species"] for row in service.db.conn.execute("""
                SELECT DISTINCT species
                FROM animals
                WHERE species IS NOT NULL AND TRIM(species) != ''
                ORDER BY species
            """).fetchall()
        ]

        response = templates.TemplateResponse(request, "animals.html", {
            "request": request,
            "lang": lang,
            "animals": animals,
            "pagination": pagination,
            "filters": {
                "q": search or "",
                "responsible_vet": responsible_vet or "",
                "species": species or "",
                "sort": sort,
                "page_size": page_size,
            },
            "sort_links": {
                "animal": build_sort_toggle(request, sort, "name", "asc"),
                "vet": build_sort_toggle(request, sort, "vet", "asc"),
                "last_report": build_sort_toggle(request, sort, "last_report", "desc"),
                "reports": build_sort_toggle(request, sort, "reports", "desc"),
            },
            "responsible_vets": service.db.list_responsible_vets(),
            "species_options": species_options,
            "current_user": current_user,
        })
        return set_lang_cookie(response, lang)
    except Exception:
        logger.exception("Error in list_animals")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/animals/new", response_class=HTMLResponse)
async def new_animal_page(request: Request):
    """Manual animal creation form."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_raw)

    response = templates.TemplateResponse(request, "animal_form.html", {
        "request": request,
        "lang": lang,
        "current_user": current_user,
        "csrf_token": signed_csrf,
        "form_values": {},
        "error": None,
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_raw,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=secure_cookie_enabled(request)
    )
    return set_lang_cookie(response, lang)


@app.post("/animals/new", response_class=HTMLResponse)
async def create_animal_page(
    request: Request,
    name: str = Form(...),
    species: str = Form(...),
    owner_name: str = Form(None),
    breed: str = Form(None),
    age_years: float = Form(None),
    age_months: int = Form(None),
    sex: str = Form("U"),
    responsible_vet: str = Form(None),
    microchip: str = Form(None),
    patient_since: str = Form(None),
    weight_kg: float = Form(None),
    medical_history: str = Form(None),
    notes: str = Form(None),
    csrf_token: str = Form(None)
):
    """Create a new animal manually from the UI."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid request")

    service = get_service()
    try:
        animal_id = service.db.create_animal(Animal(
            name=name.strip(),
            species=species.strip(),
            owner_name=owner_name.strip() if owner_name else None,
            breed=breed.strip() if breed else "",
            age_years=age_years,
            age_months=age_months,
            sex=sex,
            responsible_vet=responsible_vet.strip() if responsible_vet else None,
            microchip=microchip.strip() if microchip else None,
            patient_since=parse_date_value(patient_since.strip()) if patient_since and patient_since.strip() else None,
            weight_kg=weight_kg,
            medical_history=medical_history.strip() if medical_history else None,
            notes=notes.strip() if notes else None,
        ))
        return RedirectResponse(url=f"/animal/{animal_id}", status_code=302)
    except Exception as e:
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)
        response = templates.TemplateResponse(request, "animal_form.html", {
            "request": request,
            "lang": lang,
            "current_user": current_user,
            "csrf_token": signed_csrf,
            "error": str(e),
            "form_values": {
                "name": name,
                "species": species,
                "owner_name": owner_name,
                "breed": breed,
                "age_years": age_years,
                "age_months": age_months,
                "sex": sex,
                "responsible_vet": responsible_vet,
                "microchip": microchip,
                "patient_since": patient_since,
                "weight_kg": weight_kg,
                "medical_history": medical_history,
                "notes": notes,
            },
        }, status_code=400)
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=secure_cookie_enabled(request)
        )
        return set_lang_cookie(response, lang)
    finally:
        service.close()


@app.get("/reports", response_class=HTMLResponse)
async def list_reports(request: Request):
    """Reports index with filters, search, and pagination."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        page = parse_positive_int(request.query_params.get("page"), default=1)
        page_size = parse_positive_int(request.query_params.get("page_size"), default=25, maximum=100)
        search = (request.query_params.get("q") or "").strip() or None
        source_system = (request.query_params.get("source_system") or "").strip() or None
        report_type = (request.query_params.get("report_type") or "").strip() or None
        responsible_vet = (request.query_params.get("responsible_vet") or "").strip() or None
        sort = (request.query_params.get("sort") or "date_desc").strip()

        rows, total = service.db.list_reports_paginated(
            search=search,
            source_system=source_system,
            report_type=report_type,
            responsible_vet=responsible_vet,
            sort=sort,
            page=page,
            page_size=page_size,
        )
        for row in rows:
            row["display_type"] = humanize_report_type(
                row.get("report_type"), row.get("panel_name"), lang
            )
            row["summary"] = summarize_report_overview(row, lang)

        pagination = build_pagination(request, total, page, page_size)
        report_types = [{
            "value": row["report_type"],
            "label": humanize_report_type(row["report_type"], None, lang),
        } for row in service.db.conn.execute("""
                SELECT DISTINCT report_type
                FROM test_sessions
                WHERE report_type IS NOT NULL AND TRIM(report_type) != ''
                ORDER BY report_type
            """).fetchall()]
        source_systems = [
            row["source_system"] for row in service.db.conn.execute("""
                SELECT DISTINCT source_system
                FROM test_sessions
                WHERE source_system IS NOT NULL AND TRIM(source_system) != ''
                ORDER BY source_system
            """).fetchall()
        ]
        response = templates.TemplateResponse(request, "reports.html", {
            "request": request,
            "lang": lang,
            "current_user": current_user,
            "reports": rows,
            "pagination": pagination,
            "filters": {
                "q": search or "",
                "source_system": source_system or "",
                "report_type": report_type or "",
                "responsible_vet": responsible_vet or "",
                "sort": sort,
                "page_size": page_size,
            },
            "sort_links": {
                "date": build_sort_toggle(request, sort, "date", "desc"),
                "report": build_sort_toggle(request, sort, "report", "asc"),
                "animal": build_sort_toggle(request, sort, "animal", "asc"),
                "vet": build_sort_toggle(request, sort, "vet", "asc"),
                "source": build_sort_toggle(request, sort, "source", "asc"),
            },
            "source_systems": source_systems,
            "report_types": report_types,
            "responsible_vets": service.db.list_responsible_vets(),
            "total_reports": total,
        })
        return set_lang_cookie(response, lang)
    except Exception:
        logger.exception("Error in list_reports")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/animal/{animal_id}", response_class=HTMLResponse)
async def view_animal(request: Request, animal_id: int):
    """Animal workspace with tabs for overview, reports, notes, diagnostics, and profile."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        db = service.db
        animal = db.get_animal(animal_id)
        if not animal:
            raise HTTPException(status_code=404, detail="Animal not found")

        active_tab = (request.query_params.get("tab") or "overview").strip().lower()
        if active_tab not in {"overview", "reports", "notes", "diagnostics", "profile"}:
            active_tab = "overview"

        report_page = parse_positive_int(request.query_params.get("report_page"), default=1)
        report_page_size = 10
        report_type_filter = (request.query_params.get("report_type") or "").strip() or None
        report_sort = (request.query_params.get("report_sort") or "date_desc").strip()

        report_rows, report_total = db.list_reports_paginated(
            animal_id=animal_id,
            report_type=report_type_filter,
            sort=report_sort,
            page=report_page,
            page_size=report_page_size,
        )
        for row in report_rows:
            row["display_type"] = humanize_report_type(
                row.get("report_type"), row.get("panel_name"), lang
            )
            row["summary"] = summarize_report_overview(row, lang)

        overview_reports, _ = db.list_reports_paginated(
            animal_id=animal_id,
            page=1,
            page_size=5,
        )
        for row in overview_reports:
            row["display_type"] = humanize_report_type(
                row.get("report_type"), row.get("panel_name"), lang
            )
            row["summary"] = summarize_report_overview(row, lang)

        clinical_notes = db.get_clinical_notes_for_animal(animal_id)
        diagnosis_reports = db.get_diagnosis_reports_for_animal(animal_id)
        active_diagnosis_job = db.get_active_diagnosis_job_for_animal(animal_id)
        vet_history = db.get_vet_assignment_history(animal_id)
        symptoms = db.get_symptoms_for_animal(animal_id, active_only=True)
        observations = db.get_observations_for_animal(animal_id)
        report_type_options = []
        for row in db.conn.execute("""
            SELECT DISTINCT report_type, panel_name
            FROM test_sessions
            WHERE animal_id = ?
            ORDER BY report_type
        """, (animal_id,)).fetchall():
            value = row["report_type"]
            if not value:
                continue
            report_type_options.append({
                "value": value,
                "label": humanize_report_type(row["report_type"], row["panel_name"], lang),
            })

        # Generate CSRF token
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)

        response = templates.TemplateResponse(request, "animal_detail.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "active_tab": active_tab,
            "overview_reports": overview_reports,
            "reports": report_rows,
            "report_pagination": build_pagination(
                request, report_total, report_page, report_page_size, param_name="report_page"
            ),
            "report_total": report_total,
            "report_type_filter": report_type_filter or "",
            "report_sort": report_sort,
            "report_sort_links": {
                "date": build_sort_toggle(request, report_sort, "date", "desc", param_name="report_sort", page_param="report_page"),
                "report": build_sort_toggle(request, report_sort, "report", "asc", param_name="report_sort", page_param="report_page"),
                "source": build_sort_toggle(request, report_sort, "source", "asc", param_name="report_sort", page_param="report_page"),
            },
            "report_type_options": report_type_options,
            "symptoms": symptoms,
            "observations": observations,
            "clinical_notes": clinical_notes,
            "diagnosis_reports": diagnosis_reports,
            "latest_diagnosis": diagnosis_reports[0] if diagnosis_reports else None,
            "active_diagnosis_job": serialize_diagnosis_job(active_diagnosis_job),
            "vet_history": vet_history,
            "diagnosis_available": DIAGNOSIS_AVAILABLE,
            "current_user": current_user,
            "csrf_token": signed_csrf,
            "today": date.today().isoformat(),
            "merge_notice": request.query_params.get("merged_from"),
        })
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=secure_cookie_enabled(request)
        )
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error in view_animal for animal {animal_id}")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.post("/animal/{animal_id}/update")
async def update_animal(
    request: Request,
    animal_id: int,
    name: str = Form(...),
    species: str = Form(...),
    owner_name: str = Form(None),
    breed: str = Form(None),
    age_years: float = Form(None),
    age_months: int = Form(None),
    sex: str = Form("U"),
    responsible_vet: str = Form(None),
    microchip: str = Form(None),
    patient_since: str = Form(None),
    weight_kg: float = Form(None),
    medical_history: str = Form(None),
    notes: str = Form(None),
    assignment_reason: str = Form(None),
    csrf_token: str = Form(None)
):
    """Update animal profile information"""
    current_user = getattr(request.state, 'user', None)
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        # Verify animal exists
        animal = service.db.get_animal(animal_id)
        if not animal:
            return JSONResponse({"success": False, "message": "Animal not found"}, status_code=404)

        # Build update fields (only include non-None values)
        update_fields = {
            "name": name,
            "species": species,
            "sex": sex,
        }
        if owner_name is not None:
            update_fields["owner_name"] = owner_name if owner_name.strip() else None
        if breed is not None:
            update_fields["breed"] = breed
        if age_years is not None:
            update_fields["age_years"] = age_years
        if age_months is not None:
            update_fields["age_months"] = age_months
        if responsible_vet is not None:
            update_fields["responsible_vet"] = responsible_vet if responsible_vet.strip() else None
        if microchip is not None:
            update_fields["microchip"] = microchip if microchip.strip() else None
        if patient_since is not None:
            update_fields["patient_since"] = (
                parse_date_value(patient_since.strip()) if patient_since.strip() else None
            )
        if weight_kg is not None:
            update_fields["weight_kg"] = weight_kg
        if medical_history is not None:
            update_fields["medical_history"] = medical_history if medical_history.strip() else None
        if notes is not None:
            update_fields["notes"] = notes if notes.strip() else None

        success = service.db.update_animal(
            animal_id,
            changed_by_user_id=current_user.id if current_user else None,
            assignment_reason=assignment_reason.strip() if assignment_reason else None,
            **update_fields,
        )

        updated_animal = service.db.get_animal(animal_id)
        return JSONResponse({
            "success": success,
            "animal": {
                "id": updated_animal.id if updated_animal else animal_id,
                "name": updated_animal.name if updated_animal else name,
                "owner_name": updated_animal.owner_name if updated_animal else owner_name,
                "responsible_vet": updated_animal.responsible_vet if updated_animal else responsible_vet,
            }
        })
    except Exception as e:
        logger.exception(f"Error updating animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.post("/animal/{animal_id}/merge")
async def merge_animal(
    request: Request,
    animal_id: int,
    target_animal_id: int = Form(...),
    csrf_token: str = Form(None),
):
    """Merge a duplicate animal into an existing animal record."""
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        if animal_id == target_animal_id:
            return JSONResponse({"success": False, "message": "Choose a different target animal."}, status_code=400)

        if not service.db.get_animal(animal_id):
            return JSONResponse({"success": False, "message": "Source animal not found."}, status_code=404)
        if not service.db.get_animal(target_animal_id):
            return JSONResponse({"success": False, "message": "Target animal not found."}, status_code=404)

        success = service.db.merge_animals(animal_id, target_animal_id)
        if not success:
            return JSONResponse({"success": False, "message": "Could not merge the selected animals."}, status_code=400)

        return JSONResponse({
            "success": True,
            "redirect_url": f"/animal/{target_animal_id}?tab=profile&merged_from={animal_id}",
        })
    except Exception as e:
        logger.exception("Error merging animals")
        return internal_error_json()
    finally:
        service.close()


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def view_session(request: Request, session_id: int):
    """View detailed test session results"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        session = service.db.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        animal = service.db.get_animal(session.animal_id)

        # Get all results
        results = service.db.get_results_for_session(session_id)
        measurements = service.db.get_measurements_for_session(session_id)
        biochem = service.db.get_biochemistry_for_session(session_id)
        urinalysis = service.db.get_urinalysis_for_session(session_id)
        pathology_findings = service.db.get_pathology_findings_for_session(session_id)
        session_assets = service.db.get_assets_for_session(session_id)
        for asset in session_assets:
            asset.url = build_upload_url(asset.file_path)

        # Get previous session for comparison
        all_sessions = service.db.get_sessions_for_animal(session.animal_id)
        previous_session = None
        comparison = None

        for i, s in enumerate(all_sessions):
            if s.id == session_id and i + 1 < len(all_sessions):
                previous_session = all_sessions[i + 1]
                try:
                    comparison = service.compare_sessions(session_id, previous_session.id)
                except Exception as comp_error:
                    print(f"Comparison error: {comp_error}")
                    comparison = None
                break

        response = templates.TemplateResponse(request, "session_detail.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "session": session,
            "results": results,
            "measurements": measurements,
            "biochemistry": biochem,
            "urinalysis": urinalysis,
            "pathology_findings": pathology_findings,
            "session_assets": session_assets,
            "previous_session": previous_session,
            "comparison": comparison,
            "current_user": current_user,
            "pdf_url": build_upload_url(session.pdf_path),
            "report_type_label": humanize_report_type(session.report_type, session.panel_name, lang),
        })
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error in view_session for session {session_id}")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """PDF upload page"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)

    # Generate CSRF token
    csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_raw)

    response = templates.TemplateResponse(request, "upload.html", {
        "request": request,
        "lang": lang,
        "current_user": current_user,
        "csrf_token": signed_csrf
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_raw,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=secure_cookie_enabled(request)
    )
    return set_lang_cookie(response, lang)


@app.get("/imports", response_class=HTMLResponse)
async def view_imports(request: Request):
    """View automatic email imports"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        # Get email address from config
        email_address = os.getenv("EMAIL_ADDRESS", "reports@vetscan.net")

        # Query import log with animal names
        cursor = service.db.conn.execute("""
            SELECT
                eil.*,
                a.name as animal_name
            FROM email_import_log eil
            LEFT JOIN animals a ON eil.animal_id = a.id
            ORDER BY eil.import_timestamp DESC
            LIMIT 100
        """)

        imports = []
        now = datetime.now()

        for row in cursor.fetchall():
            imp = dict(row)

            # Calculate human-readable time ago
            if imp.get('import_timestamp'):
                try:
                    if isinstance(imp['import_timestamp'], str):
                        ts = datetime.fromisoformat(imp['import_timestamp'].replace('Z', '+00:00'))
                    else:
                        ts = imp['import_timestamp']

                    delta = now - ts

                    if delta.total_seconds() < 60:
                        imp['time_ago'] = get_text(lang, 'imports.time.just_now')
                    elif delta.total_seconds() < 3600:
                        mins = int(delta.total_seconds() / 60)
                        imp['time_ago'] = f"{mins} {get_text(lang, 'imports.time.minutes_ago')}"
                    elif delta.total_seconds() < 86400:
                        hours = int(delta.total_seconds() / 3600)
                        imp['time_ago'] = f"{hours} {get_text(lang, 'imports.time.hours_ago')}"
                    elif delta.days == 1:
                        imp['time_ago'] = get_text(lang, 'imports.time.yesterday')
                    else:
                        imp['time_ago'] = f"{delta.days} {get_text(lang, 'imports.time.days_ago')}"
                except:
                    imp['time_ago'] = str(imp['import_timestamp'])[:16]
            else:
                imp['time_ago'] = '--'

            imports.append(imp)

        enrich_import_audit_rows(service, imports)

        # Calculate stats
        stats = {
            'total': len(imports),
            'successful': sum(1 for i in imports if i.get('import_success')),
            'failed': sum(1 for i in imports if not i.get('import_success') and i.get('validation_result') not in ('duplicate', 'rate_limited')),
            'skipped': sum(1 for i in imports if i.get('validation_result') in ('duplicate', 'rate_limited'))
        }

        response = templates.TemplateResponse(request, "imports.html", {
            "request": request,
            "lang": lang,
            "email_address": email_address,
            "imports": imports,
            "stats": stats,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except Exception:
        logger.exception("Error in view_imports")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/unassigned-reports", response_class=HTMLResponse)
async def view_unassigned_reports(request: Request, error: Optional[str] = None):
    """Review queue for reports that need manual assignment."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        page = parse_positive_int(request.query_params.get("page"), default=1)
        page_size = parse_positive_int(request.query_params.get("page_size"), default=12, maximum=50)
        search = (request.query_params.get("q") or "").strip() or None
        pending_reports, total = service.get_unassigned_reports(
            search=search,
            page=page,
            page_size=page_size,
        )

        report_items = []
        for report in pending_reports:
            summary = json.loads(report.parsed_summary_json or "{}")
            candidates = json.loads(report.candidate_matches_json or "[]")
            report_items.append({
                "report": report,
                "summary": summary,
                "candidates": candidates,
                "pdf_url": build_upload_url(report.pdf_path),
                "report_type_label": humanize_report_type(report.report_type, None, lang),
            })

        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)

        response = templates.TemplateResponse(request, "unassigned_reports.html", {
            "request": request,
            "lang": lang,
            "current_user": current_user,
            "pending_reports": report_items,
            "pagination": build_pagination(request, total, page, page_size),
            "filters": {
                "q": search or "",
                "page_size": page_size,
            },
            "error": error,
            "csrf_token": signed_csrf,
        })
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=secure_cookie_enabled(request)
        )
        return set_lang_cookie(response, lang)
    except Exception:
        logger.exception("Error in view_unassigned_reports")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.post("/unassigned-reports/{report_id}/assign-existing")
async def assign_unassigned_report_existing(
    request: Request,
    report_id: int,
    animal_id: int = Form(...),
    csrf_token: str = Form(None)
):
    """Assign a queued report to an existing animal."""
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        outcome = service.assign_unassigned_report_to_animal(report_id, animal_id)
        return RedirectResponse(url=f"/animal/{outcome.animal_id}", status_code=302)
    except Exception as e:
        logger.exception(f"Error assigning queued report {report_id} to animal {animal_id}")
        return RedirectResponse(
            url=f"/unassigned-reports?error={quote_plus(internal_error_detail())}",
            status_code=302
        )
    finally:
        service.close()


@app.post("/unassigned-reports/{report_id}/create-animal")
async def assign_unassigned_report_new_animal(
    request: Request,
    report_id: int,
    csrf_token: str = Form(None)
):
    """Assign a queued report by creating a new animal entry."""
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        outcome = service.create_animal_from_unassigned_report(report_id)
        return RedirectResponse(url=f"/animal/{outcome.animal_id}", status_code=302)
    except Exception as e:
        logger.exception(f"Error creating animal from queued report {report_id}")
        return RedirectResponse(
            url=f"/unassigned-reports?error={quote_plus(internal_error_detail())}",
            status_code=302
        )
    finally:
        service.close()


@app.post("/upload")
async def upload_pdf(request: Request, file: UploadFile = File(...), csrf_token: str = Form(None)):
    """Handle PDF upload with security validation"""
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        logger.warning("PDF upload failed: invalid CSRF token")
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    # Basic filename check
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        logger.warning(f"PDF upload rejected: invalid extension for {file.filename}")
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    temp_path = None
    final_path = None
    keep_uploaded_file = False
    try:
        content = await file.read()
        with tempfile.NamedTemporaryFile(
            dir=str(TEMP_UPLOADS_DIR),
            suffix=".pdf",
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            temp_path = Path(tmp_file.name)

        # Validate PDF content using PDFValidator (magic bytes, malicious content, structure)
        validator = PDFValidator()
        validation_result = validator.validate(str(temp_path))

        if not validation_result.is_valid:
            # Clean up invalid file
            if temp_path.exists():
                temp_path.unlink()

            logger.warning(
                f"PDF validation failed for {file.filename}: "
                f"{validation_result.result_code.value} - {validation_result.message}"
            )

            # Map validation errors to user-friendly messages
            error_messages = {
                ValidationResult.INVALID_MAGIC_BYTES: "File is not a valid PDF",
                ValidationResult.SUSPICIOUS_CONTENT: "PDF contains suspicious content and was rejected",
                ValidationResult.FILE_TOO_LARGE: validation_result.message,
                ValidationResult.PDF_PARSE_ERROR: "PDF file is corrupted or unreadable",
                ValidationResult.MISSING_DNATECH_MARKERS: "PDF does not appear to be a valid lab report",
            }
            user_message = error_messages.get(
                validation_result.result_code,
                "PDF validation failed"
            )

            return JSONResponse({
                "success": False,
                "message": user_message
            }, status_code=400)

        logger.info(f"PDF validated successfully: {file.filename}")

        final_path = allocate_upload_path(file.filename)
        os.replace(temp_path, final_path)
        temp_path = None

        # Import the validated PDF
        service = get_service()
        try:
            outcome = service.import_pdf(
                str(final_path),
                copy_to_uploads=False,
                report_source=f"manual upload | filename {file.filename}",
            )
            keep_uploaded_file = True

            parsed = outcome.parsed

            if outcome.status == "pending_review":
                logger.info(
                    f"PDF queued for manual assignment: {parsed.animal.name}, "
                    f"report={parsed.session.report_number or 'N/A'}"
                )
                return JSONResponse({
                    "success": True,
                    "queued": True,
                    "message": "Report queued for manual assignment",
                    "animal_name": parsed.animal.name,
                    "report_number": parsed.session.report_number or "N/A",
                    "test_date": format_date(parsed.session.test_date),
                    "unassigned_report_id": outcome.unassigned_report_id,
                })

            logger.info(
                f"PDF imported: {parsed.animal.name}, "
                f"report={parsed.session.report_number or 'N/A'}"
            )

            return JSONResponse({
                "success": True,
                "message": f"Successfully imported report for {parsed.animal.name}",
                "animal_id": outcome.animal_id,
                "session_id": outcome.session_id,
                "animal_name": parsed.animal.name,
                "report_number": parsed.session.report_number or "N/A",
                "test_date": format_date(parsed.session.test_date)
            })
        except ValueError as e:
            logger.warning(f"PDF import failed: {e}")
            if final_path and final_path.exists():
                final_path.unlink()
            return JSONResponse({
                "success": False,
                "message": str(e)
            }, status_code=400)
        finally:
            service.close()

    except Exception:
        logger.exception(f"Upload error for {file.filename}")
        if temp_path and temp_path.exists():
            temp_path.unlink()
        if final_path and final_path.exists() and not keep_uploaded_file:
            final_path.unlink()
        raise HTTPException(status_code=500, detail=internal_error_detail())


@app.post("/animal/{animal_id}/symptom")
async def add_symptom(
    request: Request,
    animal_id: int,
    description: str = Form(...),
    severity: str = Form("mild"),
    category: str = Form(None),
    csrf_token: str = Form(None)
):
    """Add a symptom for an animal"""
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            return JSONResponse({"success": False, "message": "Animal not found"}, status_code=404)
        symptom_id = service.add_symptom(
            animal_id, description, severity, category
        )
        return JSONResponse({
            "success": True,
            "symptom_id": symptom_id
        })
    except Exception:
        logger.exception(f"Error adding symptom for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.post("/animal/{animal_id}/observation")
async def add_observation(
    request: Request,
    animal_id: int,
    obs_type: str = Form(...),
    details: str = Form(...),
    value: float = Form(None),
    unit: str = Form(None),
    csrf_token: str = Form(None)
):
    """Add an observation for an animal"""
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            return JSONResponse({"success": False, "message": "Animal not found"}, status_code=404)
        obs_id = service.add_observation(
            animal_id, obs_type, details, value, unit
        )
        return JSONResponse({
            "success": True,
            "observation_id": obs_id
        })
    except Exception:
        logger.exception(f"Error adding observation for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.post("/animal/{animal_id}/clinical-note")
async def add_clinical_note(
    request: Request,
    animal_id: int,
    title: str = Form(None),
    content: str = Form(...),
    note_date: str = Form(None),
    csrf_token: str = Form(None)
):
    """Add a clinical note for an animal"""
    current_user = getattr(request.state, 'user', None)
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            return JSONResponse({"success": False, "message": "Animal not found"}, status_code=404)
        # Parse date if provided
        parsed_date = None
        if note_date:
            try:
                parsed_date = datetime.strptime(note_date, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = date.today()
        else:
            parsed_date = date.today()

        note = ClinicalNote(
            animal_id=animal_id,
            title=title,
            content=content,
            note_date=parsed_date,
            author_user_id=current_user.id if current_user else None,
            updated_by_user_id=current_user.id if current_user else None,
        )
        note_id = service.db.create_clinical_note(note)
        return JSONResponse({
            "success": True,
            "note_id": note_id
        })
    except Exception:
        logger.exception(f"Error adding clinical note for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.post("/animal/{animal_id}/clinical-note/{note_id}")
async def update_clinical_note(
    request: Request,
    animal_id: int,
    note_id: int,
    title: str = Form(None),
    content: str = Form(...),
    note_date: str = Form(None),
    csrf_token: str = Form(None)
):
    """Update a clinical note"""
    current_user = getattr(request.state, 'user', None)
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        note = service.db.get_clinical_note(note_id)
        if not note or note.animal_id != animal_id:
            return JSONResponse({"success": False, "message": "Note not found"}, status_code=404)

        # Parse date if provided
        parsed_date = None
        if note_date:
            try:
                parsed_date = datetime.strptime(note_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        success = service.db.update_clinical_note(
            note_id,
            title,
            content,
            parsed_date,
            updated_by_user_id=current_user.id if current_user else None,
        )
        return JSONResponse({
            "success": success
        })
    except Exception:
        logger.exception(f"Error updating clinical note {note_id} for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.delete("/animal/{animal_id}/clinical-note/{note_id}")
async def delete_clinical_note(request: Request, animal_id: int, note_id: int):
    """Delete a clinical note"""
    if not validate_csrf(request):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        note = service.db.get_clinical_note(note_id)
        if not note or note.animal_id != animal_id:
            return JSONResponse({"success": False, "message": "Note not found"}, status_code=404)

        success = service.db.delete_clinical_note(note_id)
        return JSONResponse({
            "success": success
        })
    except Exception:
        logger.exception(f"Error deleting clinical note {note_id} for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.get("/api/clinical-note/{note_id}")
async def get_clinical_note(note_id: int):
    """Get a clinical note by ID (for editing)"""
    service = get_service()
    try:
        note = service.db.get_clinical_note(note_id)
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")
        return JSONResponse({
            "success": True,
            "note": {
                "id": note.id,
                "animal_id": note.animal_id,
                "title": note.title,
                "content": note.content,
                "note_date": str(note.note_date) if note.note_date else None
            }
        })
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error getting clinical note {note_id}")
        return internal_error_json()
    finally:
        service.close()


# =============================================================================
# DIAGNOSIS ROUTES
# =============================================================================

@app.post("/animal/{animal_id}/diagnosis")
async def generate_diagnosis(
    request: Request,
    animal_id: int,
    report_type: str = Form("clinical_notes_only"),
    csrf_token: str = Form(None)
):
    """Queue a new AI diagnosis report for background generation."""
    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    if not DIAGNOSIS_AVAILABLE:
        return JSONResponse({
            "success": False,
            "message": "Diagnosis service not available. Please install: pip install anthropic openai python-dotenv"
        }, status_code=503)

    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            return JSONResponse({
                "success": False,
                "message": "Animal not found"
            }, status_code=404)

        # Check for API keys
        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        openai_api_key = os.getenv("OPENAI_API_KEY")

        if not anthropic_api_key:
            return JSONResponse({
                "success": False,
                "message": "ANTHROPIC_API_KEY not configured"
            }, status_code=400)

        # Validate report type
        if report_type not in ["clinical_notes_only", "comprehensive"]:
            report_type = "clinical_notes_only"

        service.db.mark_stale_diagnosis_jobs_failed()
        active_job = service.db.get_active_diagnosis_job_for_animal(animal_id)
        if active_job:
            job_payload = serialize_diagnosis_job(active_job)
            return JSONResponse({
                "success": True,
                "queued": True,
                "already_running": True,
                "job": job_payload,
                "message": "Diagnosis generation is already in progress"
            }, status_code=202)

        current_user = getattr(request.state, "user", None)
        job_id = service.db.create_diagnosis_job(
            animal_id=animal_id,
            report_type=report_type,
            requested_by_user_id=current_user.id if current_user else None,
        )
        DIAGNOSIS_JOB_EXECUTOR.submit(
            run_diagnosis_job,
            job_id,
            animal_id,
            report_type,
            anthropic_api_key,
            openai_api_key,
        )

        return JSONResponse({
            "success": True,
            "queued": True,
            "job": serialize_diagnosis_job(service.db.get_diagnosis_job(job_id)),
            "message": "Diagnosis generation started"
        }, status_code=202)
    except Exception:
        logger.exception(f"Error generating diagnosis for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.get("/api/diagnosis-jobs/{job_id}")
async def get_diagnosis_job_status(job_id: int):
    """Return the latest status for a diagnosis background job."""
    service = get_service()
    try:
        service.db.mark_stale_diagnosis_jobs_failed()
        job = service.db.get_diagnosis_job(job_id)
        if not job:
            return JSONResponse({
                "success": False,
                "message": "Diagnosis job not found"
            }, status_code=404)

        return JSONResponse({
            "success": True,
            "job": serialize_diagnosis_job(job),
        })
    finally:
        service.close()


@app.get("/animal/{animal_id}/diagnosis/{report_id}", response_class=HTMLResponse)
async def view_diagnosis_report(request: Request, animal_id: int, report_id: int):
    """View a diagnosis report"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            raise HTTPException(status_code=404, detail="Animal not found")

        report = service.db.get_diagnosis_report(report_id)
        if not report or report.animal_id != animal_id:
            raise HTTPException(status_code=404, detail="Report not found")

        response = templates.TemplateResponse(request, "diagnosis_report.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "report": report,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error viewing diagnosis report {report_id} for animal {animal_id}")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.delete("/animal/{animal_id}/diagnosis/{report_id}")
async def delete_diagnosis_report(request: Request, animal_id: int, report_id: int):
    """Delete a diagnosis report"""
    if not validate_csrf(request):
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    service = get_service()
    try:
        # Verify report belongs to animal
        report = service.db.get_diagnosis_report(report_id)
        if not report or report.animal_id != animal_id:
            return JSONResponse({
                "success": False,
                "message": "Report not found"
            }, status_code=404)

        success = service.db.delete_diagnosis_report(report_id)
        return JSONResponse({
            "success": success
        })
    except Exception:
        logger.exception(f"Error deleting diagnosis report {report_id} for animal {animal_id}")
        return internal_error_json()
    finally:
        service.close()


@app.get("/api/diagnosis/{report_id}")
async def api_get_diagnosis_report(report_id: int):
    """API: Get diagnosis report data (JSON)"""
    service = get_service()
    try:
        report = service.db.get_diagnosis_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        return JSONResponse({
            "success": True,
            "report": {
                "id": report.id,
                "animal_id": report.animal_id,
                "report_date": str(report.report_date) if report.report_date else None,
                "report_type": report.report_type,
                "input_summary": report.input_summary,
                "differential_diagnosis": report.differential_diagnosis,
                "recommendations": report.recommendations,
                "references": report.references,
                "model_used": report.model_used,
                "created_at": str(report.created_at) if report.created_at else None
            }
        })
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error getting diagnosis report {report_id}")
        return internal_error_json()
    finally:
        service.close()


@app.get("/compare/{animal_id}", response_class=HTMLResponse)
async def compare_sessions_page(request: Request, animal_id: int):
    """Compare multiple sessions for an animal"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            raise HTTPException(status_code=404, detail="Animal not found")

        sessions = service.db.get_sessions_for_animal(animal_id)

        if len(sessions) < 2:
            response = templates.TemplateResponse(request, "compare.html", {
                "request": request,
                "lang": lang,
                "animal": animal,
                "sessions": sessions,
                "comparison_data": [],
                "urinalysis_data": [],
                "has_urinalysis": False,
                "error": get_text(lang, "compare.need_two_tests"),
                "current_user": current_user
            })
            return set_lang_cookie(response, lang)

        # Get all results for comparison
        all_results = {}
        markers = set()

        for session in sessions:
            results = service.db.get_results_for_session(session.id)
            all_results[session.id] = {r.marker_name: r for r in results}
            markers.update(r.marker_name for r in results)

        # Build comparison table
        comparison_data = []
        for marker in sorted(markers):
            row = {'marker': marker, 'values': []}
            for session in sessions:
                if marker in all_results[session.id]:
                    r = all_results[session.id][marker]
                    row['values'].append({
                        'value': r.value,
                        'flag': r.flag,
                        'date': session.test_date
                    })
                else:
                    row['values'].append(None)
            comparison_data.append(row)

        # Get urinalysis data for each session
        urinalysis_data = []
        has_urinalysis = False
        for session in sessions:
            urin = service.db.get_urinalysis_for_session(session.id)
            urinalysis_data.append(urin)
            if urin:
                has_urinalysis = True

        response = templates.TemplateResponse(request, "compare.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "sessions": sessions,
            "comparison_data": comparison_data,
            "urinalysis_data": urinalysis_data,
            "has_urinalysis": has_urinalysis,
            "error": None,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error in compare for animal {animal_id}")
        raise HTTPException(status_code=500, detail=internal_error_detail())
    finally:
        service.close()


@app.get("/set-language/{lang}")
async def set_language(request: Request, lang: str):
    """Set the language preference and redirect back"""
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    response = RedirectResponse(
        url=get_safe_redirect_target(request, fallback="/"),
        status_code=303,
    )
    set_lang_cookie(response, lang)
    return response


# =============================================================================
# API ENDPOINTS (JSON)
# =============================================================================

@app.get("/api/animals")
async def api_list_animals(q: Optional[str] = None, limit: int = 50):
    """API: List animals or search by query."""
    service = get_service()
    try:
        if q:
            rows = service.db.search_animals(q, limit=min(max(limit, 1), 20))
            return [{
                "id": row["id"],
                "name": row["name"],
                "species": row["species"],
                "breed": row["breed"],
                "owner_name": row.get("owner_name"),
                "responsible_vet": row.get("responsible_vet"),
                "latest_report_at": row.get("latest_report_at"),
                "test_count": row.get("test_count", 0),
            } for row in rows]

        animals = service.db.list_animals()
        return [{
            "id": a.id,
            "name": a.name,
            "species": a.species,
            "breed": a.breed,
            "age": a.age_display,
            "sex": a.sex
        } for a in animals]
    finally:
        service.close()


@app.get("/api/animal-lookup")
async def api_animal_lookup(q: str, limit: int = 8, exclude_id: Optional[int] = None):
    """API: Lightweight animal search for assignment workflows."""
    service = get_service()
    try:
        rows = service.db.search_animals(
            q,
            limit=min(max(limit, 1), 12),
            exclude_id=exclude_id,
        )
        return [{
            "id": row["id"],
            "name": row["name"],
            "species": row["species"],
            "owner_name": row.get("owner_name"),
            "responsible_vet": row.get("responsible_vet"),
            "microchip": row.get("microchip"),
            "test_count": row.get("test_count", 0),
            "latest_report_at": row.get("latest_report_at"),
        } for row in rows]
    finally:
        service.close()


@app.get("/api/search")
async def api_global_search(request: Request, q: str):
    """API: Header search across animals, imported reports, and pending reports."""
    search = (q or "").strip()
    if len(search) < 2:
        return {"animals": [], "reports": [], "pending_reports": []}

    lang = get_lang(request)
    service = get_service()
    try:
        animals = service.db.search_animals(search, limit=6)
        reports = service.db.search_reports(search, limit=6)
        pending_reports, _ = service.db.list_unassigned_reports(
            status="pending",
            search=search,
            page=1,
            page_size=6,
        )
        return {
            "animals": [{
                "id": row["id"],
                "name": row["name"],
                "species": row["species"],
                "owner_name": row.get("owner_name"),
                "responsible_vet": row.get("responsible_vet"),
                "href": f"/animal/{row['id']}",
            } for row in animals],
            "reports": [{
                "id": row["id"],
                "report_number": (
                    row.get("report_number")
                    or row.get("external_report_id")
                    or f"{get_text(lang, 'common.report')} {row['id']}"
                ),
                "animal_name": row.get("animal_name"),
                "report_type": humanize_report_type(row.get("report_type"), row.get("panel_name"), lang),
                "test_date": row.get("test_date"),
                "href": f"/session/{row['id']}",
            } for row in reports],
            "pending_reports": [{
                "id": report.id,
                "report_number": report.report_number or report.external_report_id or report.filename,
                "animal_name": report.animal_name,
                "report_type": humanize_report_type(report.report_type, None, lang),
                "href": "/unassigned-reports",
            } for report in pending_reports],
        }
    finally:
        service.close()


@app.get("/api/animal/{animal_id}/history")
async def api_animal_history(animal_id: int):
    """API: Get animal test history"""
    service = get_service()
    try:
        history = service.get_animal_history(animal_id)
        return json.loads(json.dumps(history, default=json_serial))
    finally:
        service.close()


@app.get("/api/animal/{animal_id}/marker/{marker_name}")
async def api_marker_trend(animal_id: int, marker_name: str):
    """API: Get marker trend data for charts"""
    service = get_service()
    try:
        trend = service.get_marker_trend(animal_id, marker_name)
        return trend
    finally:
        service.close()


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    print("Starting Vet Protein Analysis Web Server...")
    print(f"Database: {DB_PATH}")
    print(f"Uploads: {UPLOADS_DIR}")
    print("\nOpen http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
