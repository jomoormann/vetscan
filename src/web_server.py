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
from typing import Optional, List
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Database, Animal, Symptom, Observation, TestSession, ClinicalNote, DiagnosisReport, User
from pdf_parser import parse_dnatech_report
from app import VetProteinService
from i18n import get_text, get_language_from_request, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
from auth import AuthService, hash_password, verify_password, validate_password, validate_email
from email_sender import email_service

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


class CookieAuthMiddleware(BaseHTTPMiddleware):
    """
    Cookie-based authentication middleware.
    Supports both legacy single-user and multi-user database auth.
    Redirects unauthenticated users to the login page.
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
                                # User disabled
                                return RedirectResponse(url="/login?error=disabled", status_code=302)
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


# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Static files directory
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files for game assets and other static content
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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

# Register filters with Jinja2
templates.env.filters["format_date"] = format_date_filter
templates.env.filters["format_date_short"] = format_date_short_filter
templates.env.filters["format_number"] = format_number_filter
templates.env.filters["format_datetime"] = format_datetime_filter

# Register translation function as Jinja2 global
templates.env.globals["t"] = get_text


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_service() -> VetProteinService:
    """Get a configured service instance"""
    service = VetProteinService(
        db_path=str(DB_PATH),
        uploads_dir=str(UPLOADS_DIR)
    )
    service.initialize()
    return service


def format_date(d) -> str:
    """Format date for display"""
    if d is None:
        return "N/A"
    if isinstance(d, str):
        return d
    try:
        return d.strftime("%d/%m/%Y")
    except:
        return "N/A"


def json_serial(obj):
    """JSON serializer for objects not serializable by default"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


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

    response = templates.TemplateResponse("login.html", {
        "request": request,
        "lang": current_lang,
        "error": error_message,
        "multi_user_enabled": multi_user_enabled
    })
    return set_lang_cookie(response, current_lang)


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(None),
    email: str = Form(None),
    password: str = Form(...)
):
    """Handle login form submission - supports both legacy and multi-user auth"""
    lang = get_lang(request)

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
                # Successful authentication
                token = create_auth_token(str(user.id))
                if user.is_approved:
                    response = RedirectResponse(url="/", status_code=302)
                else:
                    response = RedirectResponse(url="/pending-approval", status_code=302)
                response.set_cookie(
                    key=AUTH_COOKIE_NAME,
                    value=token,
                    max_age=30 * 24 * 60 * 60,  # 30 days
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
                        max_age=30 * 24 * 60 * 60,
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
            # Create auth token and redirect to home
            token = create_auth_token(login_identifier)
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=token,
                max_age=30 * 24 * 60 * 60,  # 30 days
                httponly=True,
                samesite="lax",
                secure=True
            )
            return response

    # Invalid credentials - show error
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

    response = templates.TemplateResponse("auth/register.html", {
        "request": request,
        "lang": current_lang,
        "error": None
    })
    return set_lang_cookie(response, current_lang)


@app.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(None)
):
    """Handle registration form submission"""
    lang = get_lang(request)

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

        # Send notification to admins
        superusers = service.db.get_superusers()
        if superusers and email_service.is_configured():
            admin_emails = [u.email for u in superusers]
            host = request.headers.get("host", "localhost")
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
            max_age=30 * 24 * 60 * 60,
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

    response = templates.TemplateResponse("auth/forgot_password.html", {
        "request": request,
        "lang": current_lang,
        "error": None,
        "success": False
    })
    return set_lang_cookie(response, current_lang)


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(...)
):
    """Handle forgot password form submission"""
    lang = get_lang(request)

    service = get_service()
    try:
        auth_service = AuthService(service.db)
        token, _ = auth_service.create_password_reset_token(email)

        if token and email_service.is_configured():
            # Send password reset email
            host = request.headers.get("host", "localhost")
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

    response = templates.TemplateResponse("auth/reset_password.html", {
        "request": request,
        "lang": current_lang,
        "token": token,
        "error": None,
        "success": False
    })
    return set_lang_cookie(response, current_lang)


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...)
):
    """Handle reset password form submission"""
    lang = get_lang(request)

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

        response = templates.TemplateResponse("admin/users.html", {
            "request": request,
            "lang": lang,
            "users": users,
            "pending_users": pending_users,
            "stats": stats,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    finally:
        service.close()


@app.post("/admin/users/{user_id}/approve")
async def admin_approve_user(request: Request, user_id: int):
    """Approve a pending user"""
    current_user = require_superuser(request)
    lang = get_lang(request)

    service = get_service()
    try:
        user = service.db.get_user(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        service.db.approve_user(user_id, current_user.id)

        # Send approval notification email
        if email_service.is_configured():
            host = request.headers.get("host", "localhost")
            scheme = "https" if request.headers.get("x-forwarded-proto") == "https" else "http"
            login_url = f"{scheme}://{host}/login"
            email_service.send_account_approved(
                user.email, login_url, user.display_name, lang
            )
    finally:
        service.close()

    return RedirectResponse(url="/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/disable")
async def admin_disable_user(request: Request, user_id: int):
    """Disable a user account"""
    current_user = require_superuser(request)

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot disable your own account")

    service = get_service()
    try:
        service.db.disable_user(user_id)
    finally:
        service.close()

    return RedirectResponse(url="/admin/users", status_code=302)


@app.post("/admin/users/{user_id}/enable")
async def admin_enable_user(request: Request, user_id: int):
    """Re-enable a disabled user account"""
    require_superuser(request)

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
    """Home page - dashboard with overview"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        animals = service.db.list_animals()

        # Calculate current week boundaries (Monday to Sunday)
        today = date.today()
        monday = today - timedelta(days=today.weekday())  # Monday of current week
        sunday = monday + timedelta(days=6)  # Sunday of current week

        # Helper to parse datetime (for created_at / import date)
        def parse_created_at(d):
            if d is None:
                return None
            if isinstance(d, datetime):
                return d.date()
            if isinstance(d, date):
                return d
            if isinstance(d, str):
                try:
                    # Try ISO format with time
                    return datetime.fromisoformat(d.replace('Z', '+00:00')).date()
                except:
                    try:
                        return datetime.strptime(d, "%Y-%m-%d").date()
                    except:
                        return None
            return None

        # Get sessions imported/received in current week (by created_at, not test_date)
        weekly_sessions = []
        for animal in animals:
            sessions = service.db.get_sessions_for_animal(animal.id)
            for session in sessions:
                # Use created_at (import date) instead of test_date
                import_date = parse_created_at(session.created_at)
                if import_date and monday <= import_date <= sunday:
                    weekly_sessions.append({
                        'animal': animal,
                        'session': session
                    })

        # Sort by import date, most recent first
        def get_sort_date(item):
            d = parse_created_at(item['session'].created_at)
            return d if d else date.min

        weekly_sessions.sort(key=get_sort_date, reverse=True)

        # Calculate total tests
        total_tests = 0
        for animal in animals:
            sessions = service.db.get_sessions_for_animal(animal.id)
            total_tests += len(sessions)

        response = templates.TemplateResponse("index.html", {
            "request": request,
            "lang": lang,
            "animals": animals,
            "weekly_sessions": weekly_sessions,
            "week_start": monday,
            "week_end": sunday,
            "total_animals": len(animals),
            "total_tests": total_tests,
            "current_user": current_user
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
    """List all animals"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        animals = service.db.list_animals()
        animals_with_counts = []
        for animal in animals:
            sessions = service.db.get_sessions_for_animal(animal.id)
            animals_with_counts.append({
                'animal': animal,
                'test_count': len(sessions),
                'last_test': sessions[0].test_date if sessions else None
            })

        response = templates.TemplateResponse("animals.html", {
            "request": request,
            "lang": lang,
            "animals": animals_with_counts,
            "current_user": current_user
        })
        return set_lang_cookie(response, lang)
    except Exception as e:
        print(f"Error in list_animals: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        service.close()


@app.get("/animal/{animal_id}", response_class=HTMLResponse)
async def view_animal(request: Request, animal_id: int):
    """View animal details and test history"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        animal = service.db.get_animal(animal_id)
        if not animal:
            raise HTTPException(status_code=404, detail="Animal not found")

        sessions = service.db.get_sessions_for_animal(animal_id)
        symptoms = service.db.get_symptoms_for_animal(animal_id)
        observations = service.db.get_observations_for_animal(animal_id)
        clinical_notes = service.db.get_clinical_notes_for_animal(animal_id)
        diagnosis_reports = service.db.get_diagnosis_reports_for_animal(animal_id)

        # Get results for each session
        sessions_with_results = []
        for session in sessions:
            results = service.db.get_results_for_session(session.id)
            biochem = service.db.get_biochemistry_for_session(session.id)
            urinalysis = service.db.get_urinalysis_for_session(session.id)
            sessions_with_results.append({
                'session': session,
                'results': results,
                'biochemistry': biochem,
                'urinalysis': urinalysis,
                'abnormal_count': sum(1 for r in results if r.flag != 'normal')
            })

        response = templates.TemplateResponse("animal_detail.html", {
            "request": request,
            "lang": lang,
            "animal": animal,
            "sessions": sessions_with_results,
            "symptoms": symptoms,
            "observations": observations,
            "clinical_notes": clinical_notes,
            "diagnosis_reports": diagnosis_reports,
            "diagnosis_available": DIAGNOSIS_AVAILABLE,
            "current_user": current_user
        })
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


@app.get("/session/{session_id}", response_class=HTMLResponse)
async def view_session(request: Request, session_id: int):
    """View detailed test session results"""
    lang = get_lang(request)
    current_user = getattr(request.state, 'user', None)
    service = get_service()
    try:
        # Get session
        cursor = service.db.conn.execute(
            "SELECT * FROM test_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")

        session = TestSession(**dict(row))
        animal = service.db.get_animal(session.animal_id)

        # Get all results
        results = service.db.get_results_for_session(session_id)
        biochem = service.db.get_biochemistry_for_session(session_id)
        urinalysis = service.db.get_urinalysis_for_session(session_id)

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
            "biochemistry": biochem,
            "urinalysis": urinalysis,
            "previous_session": previous_session,
            "comparison": comparison,
            "current_user": current_user
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
    response = templates.TemplateResponse("upload.html", {
        "request": request,
        "lang": lang,
        "current_user": current_user
    })
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


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Handle PDF upload"""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    # Save uploaded file temporarily
    temp_path = UPLOADS_DIR / file.filename
    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)
        
        # Import the PDF
        service = get_service()
        try:
            animal_id, session_id, parsed = service.import_pdf(
                str(temp_path), 
                copy_to_uploads=False
            )
            
            return JSONResponse({
                "success": True,
                "message": f"Successfully imported report for {parsed.animal.name}",
                "animal_id": animal_id,
                "session_id": session_id,
                "animal_name": parsed.animal.name,
                "report_number": parsed.session.report_number or "N/A",
                "test_date": format_date(parsed.session.test_date)
            })
        except ValueError as e:
            return JSONResponse({
                "success": False,
                "message": str(e)
            }, status_code=400)
        finally:
            service.close()
            
    except Exception as e:
        print(f"Upload error: {e}")
        import traceback
        traceback.print_exc()
        # Clean up temp file on error
        if temp_path.exists():
            temp_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/animal/{animal_id}/symptom")
async def add_symptom(
    animal_id: int,
    description: str = Form(...),
    severity: str = Form("mild"),
    category: str = Form(None)
):
    """Add a symptom for an animal"""
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
    animal_id: int,
    obs_type: str = Form(...),
    details: str = Form(...),
    value: float = Form(None),
    unit: str = Form(None)
):
    """Add an observation for an animal"""
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
    animal_id: int,
    title: str = Form(None),
    content: str = Form(...),
    note_date: str = Form(None)
):
    """Add a clinical note for an animal"""
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
            note_date=parsed_date
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
    animal_id: int,
    note_id: int,
    title: str = Form(None),
    content: str = Form(...),
    note_date: str = Form(None)
):
    """Update a clinical note"""
    service = get_service()
    try:
        # Parse date if provided
        parsed_date = None
        if note_date:
            try:
                parsed_date = datetime.strptime(note_date, "%Y-%m-%d").date()
            except ValueError:
                pass

        success = service.db.update_clinical_note(note_id, title, content, parsed_date)
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
    animal_id: int,
    report_type: str = Form("clinical_notes_only")
):
    """Generate a new AI diagnosis report for an animal using both Claude and OpenAI"""
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
async def api_list_animals():
    """API: List all animals"""
    service = get_service()
    try:
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
