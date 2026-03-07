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
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, List
from pathlib import Path
from urllib.parse import quote_plus, urlencode

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

from models import Database, Animal, Symptom, Observation, TestSession, ClinicalNote, DiagnosisReport, User
from app import VetProteinService
from i18n import get_text, get_language_from_request, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
from auth import AuthService, hash_password, verify_password, validate_password, validate_email
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

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
TEMPLATES_DIR.mkdir(exist_ok=True)

# Database path
DB_PATH = DATA_DIR / "vet_proteins.db"

# Initialize FastAPI
app = FastAPI(
    title="Vet Protein Analysis",
    description="Veterinary blood test analysis application",
    version="0.2.0"
)


# =============================================================================
# AUTHENTICATION & SECURITY MIDDLEWARE
# =============================================================================

# Get credentials from environment variables
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "admin")
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "")  # Empty = no auth required
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY", secrets.token_hex(32))  # For signing cookies

# Session cookie name
AUTH_COOKIE_NAME = "vetscan_session"
CSRF_COOKIE_NAME = "vetscan_csrf"

# Allowed hosts for password reset links (prevents host header injection)
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,vetscan.net").split(",")

# Rate limiting for login attempts
LOGIN_RATE_LIMIT_ATTEMPTS = 5  # Max attempts before lockout
LOGIN_RATE_LIMIT_WINDOW = 300  # Time window in seconds (5 minutes)
LOGIN_RATE_LIMIT_LOCKOUT = 900  # Lockout duration in seconds (15 minutes)


class LoginRateLimiter:
    """Simple in-memory rate limiter for login attempts"""

    def __init__(self):
        self._attempts = {}  # {ip: [(timestamp, success), ...]}
        self._lockouts = {}  # {ip: lockout_expires_at}

    def _cleanup_old_attempts(self, ip: str):
        """Remove attempts older than the time window"""
        if ip in self._attempts:
            cutoff = datetime.now() - timedelta(seconds=LOGIN_RATE_LIMIT_WINDOW)
            self._attempts[ip] = [
                (ts, success) for ts, success in self._attempts[ip]
                if ts > cutoff
            ]

    def is_locked_out(self, ip: str) -> bool:
        """Check if an IP is currently locked out"""
        if ip in self._lockouts:
            if datetime.now() < self._lockouts[ip]:
                return True
            else:
                del self._lockouts[ip]
        return False

    def record_attempt(self, ip: str, success: bool):
        """Record a login attempt"""
        self._cleanup_old_attempts(ip)
        if ip not in self._attempts:
            self._attempts[ip] = []
        self._attempts[ip].append((datetime.now(), success))

        # Check if we need to lock out
        if not success:
            failed_attempts = sum(1 for _, s in self._attempts[ip] if not s)
            if failed_attempts >= LOGIN_RATE_LIMIT_ATTEMPTS:
                self._lockouts[ip] = datetime.now() + timedelta(seconds=LOGIN_RATE_LIMIT_LOCKOUT)

    def get_remaining_lockout_seconds(self, ip: str) -> int:
        """Get remaining lockout time in seconds"""
        if ip in self._lockouts:
            remaining = (self._lockouts[ip] - datetime.now()).total_seconds()
            return max(0, int(remaining))
        return 0


# Global rate limiter instance
login_rate_limiter = LoginRateLimiter()


def create_auth_token(identifier: str) -> str:
    """
    Create a signed authentication token.

    Args:
        identifier: Either a user ID (for multi-user) or username (for legacy)
    """
    message = f"{identifier}:{AUTH_SECRET_KEY[:16]}"
    signature = hmac.new(
        AUTH_SECRET_KEY.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    token = base64.b64encode(f"{identifier}:{signature}".encode()).decode()
    return token


def verify_auth_token(token: str) -> Optional[str]:
    """
    Verify an authentication token and return the identifier if valid.

    Returns:
        The identifier (user ID or username) if valid, None otherwise
    """
    try:
        decoded = base64.b64decode(token).decode()
        identifier, signature = decoded.rsplit(":", 1)

        # Recreate expected signature
        message = f"{identifier}:{AUTH_SECRET_KEY[:16]}"
        expected_signature = hmac.new(
            AUTH_SECRET_KEY.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        # Timing-safe comparison
        if hmac.compare_digest(signature, expected_signature):
            return identifier
    except Exception:
        pass
    return None


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
        if not AUTH_PASSWORD:
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
                                # Clear the auth cookie to fully invalidate the session
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
                    # Check if multi-user is enabled (users exist in DB)
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


# Add middleware (order matters: HTTPS redirect first, then auth)
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

# Mount static files for game assets and other static content
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
templates.env.filters["sanitize_html"] = sanitize_html_filter

# Register translation function as Jinja2 global
templates.env.globals["t"] = get_text


def add_csrf_to_response(response: Response, request: Request) -> str:
    """Add CSRF cookie and return token for template"""
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


def validate_csrf(request: Request, form_token: str) -> bool:
    """Validate CSRF token from form against cookie"""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    return verify_csrf_token(form_token, cookie_token)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

# Global service instance - initialized once at startup, reused for all requests
_global_service: Optional[VetProteinService] = None


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


def build_upload_url(file_path: Optional[str]) -> Optional[str]:
    """Convert an absolute uploads path to a static /uploads URL when possible."""
    if not file_path:
        return None

    try:
        relative_path = Path(file_path).resolve().relative_to(UPLOADS_DIR.resolve())
        return f"/uploads/{relative_path.as_posix()}"
    except Exception:
        normalized = file_path.replace("\\", "/")
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


def humanize_report_type(report_type: Optional[str], panel_name: Optional[str] = None) -> str:
    """Display-friendly label for report families."""
    normalized = (report_type or "").strip().lower()
    panel = (panel_name or "").strip()
    if normalized == "dnatech_proteinogram":
        return "Proteinogram"
    if normalized == "cytology":
        return "Cytology"
    if normalized == "immunocytochemistry":
        return "Immunocytochemistry"
    if panel:
        return panel.replace("_", " ").title()
    if normalized:
        return normalized.replace("_", " ").title()
    return "Imported report"


def summarize_report_overview(row: Dict[str, Any]) -> str:
    """Compact summary used in tables and cards."""
    if row.get("protein_result_count"):
        return f"{row['protein_result_count']} protein markers"
    if row.get("measurement_count"):
        return f"{row['measurement_count']} measurements"
    if row.get("pathology_finding_count"):
        detail = f"{row['pathology_finding_count']} findings"
        if row.get("asset_count"):
            detail += f" | {row['asset_count']} images"
        return detail
    if (row.get("report_type") or "").lower() in {"biochemistry", "urinalysis"}:
        return "Renal and urine markers"
    return "Imported report"


def describe_report_item(item: dict) -> str:
    """Human-readable summary for a session row in the animal page."""
    if item["results"]:
        return f"{len(item['results'])} protein markers"
    if item["measurements"]:
        return f"{len(item['measurements'])} measurements"
    if item["pathology_findings"]:
        detail = f"{len(item['pathology_findings'])} findings"
        if item["session_assets"]:
            detail += f" | {len(item['session_assets'])} images"
        return detail
    if item["biochemistry"] or item["urinalysis"]:
        return "Renal and urine markers"
    return "Imported report"


def build_session_groups(sessions_with_results: List[dict]) -> List[dict]:
    """Group animal history rows by report family for the UI."""
    grouped = {}

    for item in sessions_with_results:
        session = item["session"]
        report_type = (session.report_type or "").lower()
        source_system = (session.source_system or "").lower()

        if report_type == "dnatech_proteinogram":
            key = "protein_reports"
            label = "Protein Reports"
            sort_order = 1
        elif "cytology" in report_type or "immuno" in report_type or source_system == "vedis":
            key = "pathology_reports"
            label = "Pathology Reports"
            sort_order = 3
        elif session.panel_name or item["measurements"]:
            key = "analyzer_reports"
            label = "Analyzer Panels"
            sort_order = 2
        else:
            key = "other_reports"
            label = "Other Reports"
            sort_order = 4

        if key not in grouped:
            grouped[key] = {
                "key": key,
                "label": label,
                "sort_order": sort_order,
                "rows": [],
            }

        item["report_summary"] = describe_report_item(item)
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

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, lang: Optional[str] = None, error: Optional[str] = None):
    """Display login page"""
    # Get language from query param or detect from request
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    # Check if multi-user mode is enabled
    service = get_service()
    try:
        multi_user_enabled = service.db.user_count() > 0
    finally:
        service.close()

    # Map error codes to messages
    error_message = None
    if error == "disabled":
        error_message = get_text(current_lang, "auth.login.error_disabled")
    elif error == "invalid":
        error_message = get_text(current_lang, "login.error")

    # Generate CSRF token
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_token)

    response = templates.TemplateResponse("login.html", {
        "request": request,
        "lang": current_lang,
        "error": error_message,
        "multi_user_enabled": multi_user_enabled,
        "csrf_token": signed_csrf
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
    return set_lang_cookie(response, current_lang)


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

    # Get client IP for rate limiting
    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    # Check rate limiting
    if login_rate_limiter.is_locked_out(client_ip):
        remaining = login_rate_limiter.get_remaining_lockout_seconds(client_ip)
        error_msg = get_text(lang, "auth.login.error_rate_limit").format(minutes=remaining // 60 + 1)
        service = get_service()
        try:
            multi_user_enabled = service.db.user_count() > 0
        finally:
            service.close()
        response = templates.TemplateResponse("login.html", {
            "request": request,
            "lang": lang,
            "error": error_msg,
            "multi_user_enabled": multi_user_enabled
        })
        return set_lang_cookie(response, lang)

    # Use email field if provided, otherwise use username (for backwards compatibility)
    login_identifier = email or username

    # Check if multi-user mode is enabled
    service = get_service()
    try:
        user_count = service.db.user_count()
    finally:
        service.close()

    if user_count > 0:
        # Multi-user mode: authenticate against database
        service = get_service()
        try:
            auth_service = AuthService(service.db)
            user, error_code = auth_service.authenticate(login_identifier, password)

            if user:
                # Successful authentication - record success and clear lockout
                login_rate_limiter.record_attempt(client_ip, success=True)
                token = create_auth_token(str(user.id))
                if user.is_approved:
                    response = RedirectResponse(url="/", status_code=302)
                else:
                    response = RedirectResponse(url="/pending-approval", status_code=302)
                response.set_cookie(
                    key=AUTH_COOKIE_NAME,
                    value=token,
                    max_age=7 * 24 * 60 * 60,  # 7 days
                    httponly=True,
                    samesite="lax",
                    secure=True
                )
                return response

            # Handle specific errors
            if error_code == "disabled":
                return RedirectResponse(url="/login?error=disabled", status_code=302)
            elif error_code == "pending_approval":
                # User exists but not approved - create session anyway
                user = service.db.get_user_by_email(login_identifier)
                if user:
                    token = create_auth_token(str(user.id))
                    response = RedirectResponse(url="/pending-approval", status_code=302)
                    response.set_cookie(
                        key=AUTH_COOKIE_NAME,
                        value=token,
                        max_age=7 * 24 * 60 * 60,  # 7 days
                        httponly=True,
                        samesite="lax",
                        secure=True
                    )
                    return response
        finally:
            service.close()
    else:
        # Legacy mode: authenticate against env vars
        username_correct = secrets.compare_digest(login_identifier, AUTH_USERNAME)
        password_correct = secrets.compare_digest(password, AUTH_PASSWORD)

        if username_correct and password_correct:
            # Successful authentication - record success
            login_rate_limiter.record_attempt(client_ip, success=True)
            # Create auth token and redirect to home
            token = create_auth_token(login_identifier)
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=token,
                max_age=7 * 24 * 60 * 60,  # 7 days
                httponly=True,
                samesite="lax",
                secure=True
            )
            return response

    # Invalid credentials - record failed attempt
    login_rate_limiter.record_attempt(client_ip, success=False)

    service = get_service()
    try:
        multi_user_enabled = service.db.user_count() > 0
    finally:
        service.close()

    response = templates.TemplateResponse("login.html", {
        "request": request,
        "lang": lang,
        "error": get_text(lang, "login.error"),
        "username": login_identifier,
        "multi_user_enabled": multi_user_enabled
    })
    return set_lang_cookie(response, lang)


@app.get("/logout")
async def logout():
    """Log out user by clearing the auth cookie"""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=AUTH_COOKIE_NAME)
    return response


# =============================================================================
# MULTI-USER AUTHENTICATION ROUTES
# =============================================================================

@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, lang: Optional[str] = None):
    """Display registration page"""
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    # Generate CSRF token
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_token)

    response = templates.TemplateResponse("auth/register.html", {
        "request": request,
        "lang": current_lang,
        "error": None,
        "csrf_token": signed_csrf
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
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
    lang = get_lang(request)

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/register?error=invalid", status_code=302)

    # Validate passwords match
    if password != password_confirm:
        response = templates.TemplateResponse("auth/register.html", {
            "request": request,
            "lang": lang,
            "error": get_text(lang, "auth.register.error_password_mismatch"),
            "email": email,
            "display_name": display_name
        })
        return set_lang_cookie(response, lang)

    service = get_service()
    try:
        auth_service = AuthService(service.db)
        user, error = auth_service.register_user(email, password, display_name)

        if error:
            response = templates.TemplateResponse("auth/register.html", {
                "request": request,
                "lang": lang,
                "error": error,
                "email": email,
                "display_name": display_name
            })
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
        token = create_auth_token(str(user.id))
        response = RedirectResponse(url="/pending-approval", status_code=302)
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            max_age=7 * 24 * 60 * 60,  # 7 days
            httponly=True,
            samesite="lax",
            secure=True
        )
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

    response = templates.TemplateResponse("auth/pending_approval.html", {
        "request": request,
        "lang": current_lang,
        "email": user.email if user else None
    })
    return set_lang_cookie(response, current_lang)


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, lang: Optional[str] = None):
    """Display forgot password page"""
    if lang and lang in SUPPORTED_LANGUAGES:
        current_lang = lang
    else:
        current_lang = get_lang(request)

    # Generate CSRF token
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_token)

    response = templates.TemplateResponse("auth/forgot_password.html", {
        "request": request,
        "lang": current_lang,
        "error": None,
        "success": False,
        "csrf_token": signed_csrf
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
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
        auth_service = AuthService(service.db)
        token, _ = auth_service.create_password_reset_token(email)

        if token and email_service.is_configured():
            # Send password reset email - use validated host to prevent header injection
            host = get_safe_host(request)
            scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
            reset_url = f"{scheme}://{host}/reset-password?token={token}"

            user = service.db.get_user_by_email(email)
            email_service.send_password_reset(
                email, reset_url,
                user.display_name if user else None,
                lang
            )
    finally:
        service.close()

    # Always show success message (don't reveal if email exists)
    response = templates.TemplateResponse("auth/forgot_password.html", {
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
            response = templates.TemplateResponse("auth/reset_password.html", {
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
            response = templates.TemplateResponse("auth/reset_password.html", {
                "request": request,
                "lang": current_lang,
                "invalid_token": True,
                "token": token
            })
            return set_lang_cookie(response, current_lang)
    finally:
        service.close()

    # Generate CSRF token
    csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_raw)

    response = templates.TemplateResponse("auth/reset_password.html", {
        "request": request,
        "lang": current_lang,
        "token": token,
        "error": None,
        "success": False,
        "csrf_token": signed_csrf
    })
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_raw,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
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
        response = templates.TemplateResponse("auth/reset_password.html", {
            "request": request,
            "lang": lang,
            "token": token,
            "error": get_text(lang, "auth.reset.error_password_mismatch"),
            "success": False
        })
        return set_lang_cookie(response, lang)

    service = get_service()
    try:
        auth_service = AuthService(service.db)
        success, error = auth_service.reset_password(token, password)

        if not success:
            response = templates.TemplateResponse("auth/reset_password.html", {
                "request": request,
                "lang": lang,
                "token": token,
                "error": error,
                "success": False
            })
            return set_lang_cookie(response, lang)
    finally:
        service.close()

    # Success
    response = templates.TemplateResponse("auth/reset_password.html", {
        "request": request,
        "lang": lang,
        "token": token,
        "error": None,
        "success": True
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

        response = templates.TemplateResponse("admin/users.html", {
            "request": request,
            "lang": lang,
            "users": users,
            "pending_users": pending_users,
            "stats": stats,
            "current_user": current_user,
            "csrf_token": signed_csrf
        })
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https"
        )
        return set_lang_cookie(response, lang)
    finally:
        service.close()


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
            logger.info(
                f"User {user_to_disable.email} (ID: {user_id}) disabled by admin {current_user.email}"
            )
            # Note: The user's session will be invalidated on their next request
            # because CookieAuthMiddleware checks is_active on every request
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

        recent_reports, _ = db.list_reports_paginated(page=1, page_size=8)
        recent_animals, _ = db.list_animals_paginated(page=1, page_size=8)
        pending_rows, _ = db.list_unassigned_reports(status="pending", page=1, page_size=6)
        pending_reports = []
        for report in pending_rows:
            pending_reports.append({
                "report": report,
                "summary": json.loads(report.parsed_summary_json or "{}"),
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
        recent_handovers = [
            dict(row) for row in db.conn.execute("""
                SELECT
                    ava.*,
                    a.name AS animal_name,
                    COALESCE(u.display_name, u.email) AS changed_by_name
                FROM animal_vet_assignments ava
                JOIN animals a ON a.id = ava.animal_id
                LEFT JOIN users u ON u.id = ava.changed_by_user_id
                WHERE ava.change_reason IS NOT NULL
                   OR EXISTS (
                        SELECT 1
                        FROM animal_vet_assignments other
                        WHERE other.animal_id = ava.animal_id
                          AND other.id != ava.id
                   )
                ORDER BY ava.created_at DESC
                LIMIT 6
            """).fetchall()
        ]

        for row in recent_reports:
            row["display_type"] = humanize_report_type(row.get("report_type"), row.get("panel_name"))
            row["summary"] = summarize_report_overview(row)

        response = templates.TemplateResponse("index.html", {
            "request": request,
            "lang": lang,
            "current_user": current_user,
            "stats": dict(stats_row) if stats_row else {},
            "recent_reports": recent_reports,
            "recent_animals": recent_animals,
            "pending_reports": pending_reports,
            "recent_failures": recent_failures,
            "recent_handovers": recent_handovers,
        })
        return set_lang_cookie(response, lang)
    except Exception as e:
        print(f"Error in home: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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

        response = templates.TemplateResponse("animals.html", {
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
            "responsible_vets": service.db.list_responsible_vets(),
            "species_options": species_options,
            "current_user": current_user,
        })
        return set_lang_cookie(response, lang)
    except Exception as e:
        print(f"Error in list_animals: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        service.close()


@app.get("/animals/new", response_class=HTMLResponse)
async def new_animal_page(request: Request):
    """Manual animal creation form."""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    signed_csrf = create_csrf_signed_token(csrf_raw)

    response = templates.TemplateResponse("animal_form.html", {
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
        secure=request.url.scheme == "https"
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
            weight_kg=weight_kg,
            medical_history=medical_history.strip() if medical_history else None,
            notes=notes.strip() if notes else None,
        ))
        return RedirectResponse(url=f"/animal/{animal_id}", status_code=302)
    except Exception as e:
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)
        response = templates.TemplateResponse("animal_form.html", {
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
            secure=request.url.scheme == "https"
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

        rows, total = service.db.list_reports_paginated(
            search=search,
            source_system=source_system,
            report_type=report_type,
            responsible_vet=responsible_vet,
            page=page,
            page_size=page_size,
        )
        for row in rows:
            row["display_type"] = humanize_report_type(row.get("report_type"), row.get("panel_name"))
            row["summary"] = summarize_report_overview(row)

        pagination = build_pagination(request, total, page, page_size)
        report_types = [
            row["report_type"] for row in service.db.conn.execute("""
                SELECT DISTINCT report_type
                FROM test_sessions
                WHERE report_type IS NOT NULL AND TRIM(report_type) != ''
                ORDER BY report_type
            """).fetchall()
        ]
        source_systems = [
            row["source_system"] for row in service.db.conn.execute("""
                SELECT DISTINCT source_system
                FROM test_sessions
                WHERE source_system IS NOT NULL AND TRIM(source_system) != ''
                ORDER BY source_system
            """).fetchall()
        ]
        pending_count_row = service.db.conn.execute(
            "SELECT COUNT(*) AS total FROM unassigned_reports WHERE status = 'pending'"
        ).fetchone()

        response = templates.TemplateResponse("reports.html", {
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
                "page_size": page_size,
            },
            "source_systems": source_systems,
            "report_types": report_types,
            "responsible_vets": service.db.list_responsible_vets(),
            "pending_count": pending_count_row["total"] if pending_count_row else 0,
            "total_reports": total,
        })
        return set_lang_cookie(response, lang)
    except Exception as e:
        logger.exception("Error in list_reports")
        raise HTTPException(status_code=500, detail=str(e))
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

        report_rows, report_total = db.list_reports_paginated(
            animal_id=animal_id,
            report_type=report_type_filter,
            page=report_page,
            page_size=report_page_size,
        )
        for row in report_rows:
            row["display_type"] = humanize_report_type(row.get("report_type"), row.get("panel_name"))
            row["summary"] = summarize_report_overview(row)

        overview_reports, _ = db.list_reports_paginated(
            animal_id=animal_id,
            page=1,
            page_size=5,
        )
        for row in overview_reports:
            row["display_type"] = humanize_report_type(row.get("report_type"), row.get("panel_name"))
            row["summary"] = summarize_report_overview(row)

        clinical_notes = db.get_clinical_notes_for_animal(animal_id)
        diagnosis_reports = db.get_diagnosis_reports_for_animal(animal_id)
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
                "label": humanize_report_type(row["report_type"], row["panel_name"]),
            })

        # Generate CSRF token
        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)

        response = templates.TemplateResponse("animal_detail.html", {
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
            "report_type_options": report_type_options,
            "symptoms": symptoms,
            "observations": observations,
            "clinical_notes": clinical_notes,
            "diagnosis_reports": diagnosis_reports,
            "latest_diagnosis": diagnosis_reports[0] if diagnosis_reports else None,
            "vet_history": vet_history,
            "diagnosis_available": DIAGNOSIS_AVAILABLE,
            "current_user": current_user,
            "csrf_token": signed_csrf,
            "today": date.today().isoformat()
        })
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_raw,
            max_age=3600,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https"
        )
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in view_animal: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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

        response = templates.TemplateResponse("session_detail.html", {
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
            "report_type_label": humanize_report_type(session.report_type, session.panel_name),
        })
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in view_session: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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

    response = templates.TemplateResponse("upload.html", {
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
        secure=request.url.scheme == "https"
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

        # Calculate stats
        stats = {
            'total': len(imports),
            'successful': sum(1 for i in imports if i.get('import_success')),
            'failed': sum(1 for i in imports if not i.get('import_success') and i.get('validation_result') not in ('duplicate', 'rate_limited')),
            'skipped': sum(1 for i in imports if i.get('validation_result') in ('duplicate', 'rate_limited'))
        }

        response = templates.TemplateResponse("imports.html", {
            "request": request,
            "lang": lang,
            "email_address": email_address,
            "imports": imports,
            "stats": stats,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except Exception as e:
        print(f"Error in view_imports: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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
            })

        csrf_raw = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
        signed_csrf = create_csrf_signed_token(csrf_raw)

        response = templates.TemplateResponse("unassigned_reports.html", {
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
            secure=request.url.scheme == "https"
        )
        return set_lang_cookie(response, lang)
    except Exception as e:
        logger.exception("Error in view_unassigned_reports")
        raise HTTPException(status_code=500, detail=str(e))
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
            url=f"/unassigned-reports?error={quote_plus(str(e))}",
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
            url=f"/unassigned-reports?error={quote_plus(str(e))}",
            status_code=302
        )
    finally:
        service.close()


@app.post("/upload")
async def upload_pdf(request: Request, file: UploadFile = File(...), csrf_token: str = Form(None)):
    """Handle PDF upload with security validation"""
    import re

    # Validate CSRF token
    if not validate_csrf(request, csrf_token):
        logger.warning("PDF upload failed: invalid CSRF token")
        return JSONResponse({"success": False, "message": "Invalid request"}, status_code=403)

    # Basic filename check
    if not file.filename.lower().endswith('.pdf'):
        logger.warning(f"PDF upload rejected: invalid extension for {file.filename}")
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    # Sanitize filename to prevent path traversal attacks
    safe_filename = re.sub(r'[^\w\-_\.]', '_', os.path.basename(file.filename))
    if not safe_filename.lower().endswith('.pdf'):
        safe_filename = f"{safe_filename}.pdf"

    # Save uploaded file temporarily for validation
    temp_path = UPLOADS_DIR / safe_filename
    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

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

        # Import the validated PDF
        service = get_service()
        try:
            outcome = service.import_pdf(
                str(temp_path),
                copy_to_uploads=False,
                report_source=f"manual upload | filename {file.filename}",
            )

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
            return JSONResponse({
                "success": False,
                "message": str(e)
            }, status_code=400)
        finally:
            service.close()

    except Exception as e:
        logger.exception(f"Upload error for {file.filename}")
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


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
        symptom_id = service.add_symptom(
            animal_id, description, severity, category
        )
        return JSONResponse({
            "success": True,
            "symptom_id": symptom_id
        })
    except Exception as e:
        print(f"Error adding symptom: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
        obs_id = service.add_observation(
            animal_id, obs_type, details, value, unit
        )
        return JSONResponse({
            "success": True,
            "observation_id": obs_id
        })
    except Exception as e:
        print(f"Error adding observation: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
    except Exception as e:
        print(f"Error adding clinical note: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
    except Exception as e:
        print(f"Error updating clinical note: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
    finally:
        service.close()


@app.delete("/animal/{animal_id}/clinical-note/{note_id}")
async def delete_clinical_note(animal_id: int, note_id: int):
    """Delete a clinical note"""
    service = get_service()
    try:
        success = service.db.delete_clinical_note(note_id)
        return JSONResponse({
            "success": success
        })
    except Exception as e:
        print(f"Error deleting clinical note: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
    except Exception as e:
        print(f"Error getting clinical note: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
    """Generate a new AI diagnosis report for an animal using both Claude and OpenAI"""
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

        # Generate diagnosis with both AI services
        report = create_diagnosis_report(
            db=service.db,
            animal_id=animal_id,
            report_type=report_type,
            anthropic_api_key=anthropic_api_key,
            openai_api_key=openai_api_key
        )

        return JSONResponse({
            "success": True,
            "report_id": report.id,
            "message": "Diagnosis report generated successfully"
        })
    except ValueError as e:
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=400)
    except Exception as e:
        print(f"Error generating diagnosis: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({
            "success": False,
            "message": f"Error generating diagnosis: {str(e)}"
        }, status_code=500)
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

        response = templates.TemplateResponse("diagnosis_report.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "report": report,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error viewing diagnosis report: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        service.close()


@app.delete("/animal/{animal_id}/diagnosis/{report_id}")
async def delete_diagnosis_report(animal_id: int, report_id: int):
    """Delete a diagnosis report"""
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
    except Exception as e:
        print(f"Error deleting diagnosis report: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
    except Exception as e:
        print(f"Error getting diagnosis report: {e}")
        return JSONResponse({
            "success": False,
            "message": str(e)
        }, status_code=500)
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
            response = templates.TemplateResponse("compare.html", {
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

        response = templates.TemplateResponse("compare.html", {
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
    except Exception as e:
        print(f"Error in compare: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        service.close()


@app.get("/set-language/{lang}")
async def set_language(request: Request, lang: str):
    """Set the language preference and redirect back"""
    if lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE

    # Get referer or default to home
    referer = request.headers.get('referer', '/')

    response = RedirectResponse(url=referer, status_code=303)
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
async def api_animal_lookup(q: str, limit: int = 8):
    """API: Lightweight animal search for assignment workflows."""
    service = get_service()
    try:
        rows = service.db.search_animals(q, limit=min(max(limit, 1), 12))
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
async def api_global_search(q: str):
    """API: Header search across animals, imported reports, and pending reports."""
    search = (q or "").strip()
    if len(search) < 2:
        return {"animals": [], "reports": [], "pending_reports": []}

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
                "report_number": row.get("report_number") or row.get("external_report_id") or f"Report {row['id']}",
                "animal_name": row.get("animal_name"),
                "report_type": humanize_report_type(row.get("report_type")),
                "test_date": row.get("test_date"),
                "href": f"/session/{row['id']}",
            } for row in reports],
            "pending_reports": [{
                "id": report.id,
                "report_number": report.report_number or report.external_report_id or report.filename,
                "animal_name": report.animal_name,
                "report_type": report.report_type,
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
