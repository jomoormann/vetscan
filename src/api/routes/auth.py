"""
Authentication Routes for VetScan

Handles: /login, /logout, /register, /forgot-password, /reset-password
"""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from config import settings
from logging_config import get_logger
from api.dependencies import get_container, ServiceContainer
from middleware.csrf import validate_csrf, get_csrf_context, set_csrf_cookie
from middleware.auth import (
    create_auth_token, login_rate_limiter,
    AUTH_COOKIE_NAME
)

logger = get_logger("routes.auth")

router = APIRouter(tags=["auth"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    """Display login page."""
    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "error": error,
        **csrf_ctx
    }
    response = templates.TemplateResponse("login.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Handle login form submission."""
    from auth import verify_password

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/login?error=csrf", status_code=302)

    # Check rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if login_rate_limiter.is_locked_out(client_ip):
        remaining = login_rate_limiter.get_remaining_lockout_seconds(client_ip)
        logger.warning(f"Rate-limited login attempt from {client_ip}")
        return RedirectResponse(
            url=f"/login?error=rate_limited&seconds={remaining}",
            status_code=302
        )

    # Check if multi-user mode (users exist in DB)
    user_count = container.user_repo.count()

    if user_count > 0:
        # Multi-user mode
        user = container.user_repo.get_by_email(username)
        if user and verify_password(password, user.password_hash):
            if not user.is_active:
                login_rate_limiter.record_attempt(client_ip, False)
                return RedirectResponse(url="/login?error=disabled", status_code=302)
            if not user.is_approved:
                # Create token but redirect to pending
                token = create_auth_token(str(user.id))
                response = RedirectResponse(url="/pending-approval", status_code=302)
                response.set_cookie(
                    key=AUTH_COOKIE_NAME,
                    value=token,
                    max_age=settings.auth.session_max_age,
                    httponly=True,
                    samesite="strict",
                    secure=request.url.scheme == "https"
                )
                login_rate_limiter.record_attempt(client_ip, True)
                logger.info(f"User {user.email} logged in (pending approval)")
                return response

            # Successful login
            token = create_auth_token(str(user.id))
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=token,
                max_age=settings.auth.session_max_age,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https"
            )
            container.user_repo.update(user.id, last_login_at=None)  # Will use CURRENT_TIMESTAMP
            login_rate_limiter.record_attempt(client_ip, True)
            logger.info(f"User {user.email} logged in")
            return response

    else:
        # Legacy single-user mode
        if username == settings.auth.username and password == settings.auth.password:
            token = create_auth_token(username)
            response = RedirectResponse(url="/", status_code=302)
            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=token,
                max_age=settings.auth.session_max_age,
                httponly=True,
                samesite="strict",
                secure=request.url.scheme == "https"
            )
            login_rate_limiter.record_attempt(client_ip, True)
            logger.info("Legacy user logged in")
            return response

    # Login failed
    login_rate_limiter.record_attempt(client_ip, False)
    logger.warning(f"Failed login attempt for {username} from {client_ip}")
    return RedirectResponse(url="/login?error=invalid", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    """Handle logout."""
    user = getattr(request.state, 'user', None)
    if user:
        logger.info(f"User {user.email} logged out")
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(key=AUTH_COOKIE_NAME)
    return response


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, error: Optional[str] = None):
    """Display registration page."""
    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "error": error,
        **csrf_ctx
    }
    response = templates.TemplateResponse("auth/register.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    display_name: str = Form(None),
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Handle registration form submission."""
    from auth import validate_email, validate_password, hash_password
    from email_sender import email_service
    from models.domain import User

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/register?error=csrf", status_code=302)

    # Validate email
    email_valid, email_error = validate_email(email)
    if not email_valid:
        return RedirectResponse(url=f"/register?error={email_error}", status_code=302)

    # Validate password
    pwd_valid, pwd_error = validate_password(password)
    if not pwd_valid:
        return RedirectResponse(url=f"/register?error={pwd_error}", status_code=302)

    if password != password_confirm:
        return RedirectResponse(url="/register?error=password_mismatch", status_code=302)

    # Check if email already exists
    existing = container.user_repo.get_by_email(email)
    if existing:
        return RedirectResponse(url="/register?error=email_exists", status_code=302)

    # Create user
    user = User(
        email=email,
        email_normalized=email.lower().strip(),
        password_hash=hash_password(password),
        display_name=display_name or email.split("@")[0],
        is_active=True,
        is_approved=False,
        is_superuser=False
    )

    # First user becomes superuser and auto-approved
    if container.user_repo.count() == 0:
        user.is_superuser = True
        user.is_approved = True

    user_id = container.user_repo.create(user)
    logger.info(f"New user registered: {email} (ID: {user_id})")

    # Send emails
    email_service.send_signup_confirmation(email, display_name)

    # Notify admins if not auto-approved
    if not user.is_approved:
        superusers = container.user_repo.get_superusers()
        admin_emails = [u.email for u in superusers]
        host = request.headers.get("Host", "localhost")
        scheme = "https" if "localhost" not in host else "http"
        admin_url = f"{scheme}://{host}/admin/users"
        email_service.send_new_registration_alert(
            admin_emails, email, display_name, admin_url
        )
        return RedirectResponse(url="/pending-approval", status_code=302)

    # Auto-approved (first user) - log them in
    token = create_auth_token(str(user_id))
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=settings.auth.session_max_age,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https"
    )
    return response


@router.get("/pending-approval", response_class=HTMLResponse)
async def pending_approval_page(request: Request):
    """Display pending approval page."""
    csrf_ctx = get_csrf_context(request)
    context = {"request": request, **csrf_ctx}
    response = templates.TemplateResponse("auth/pending_approval.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, success: bool = False):
    """Display forgot password page."""
    csrf_ctx = get_csrf_context(request)
    context = {"request": request, "success": success, **csrf_ctx}
    response = templates.TemplateResponse("auth/forgot_password.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/forgot-password")
async def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Handle forgot password form submission."""
    from auth import AuthService
    from email_sender import email_service

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/forgot-password", status_code=302)

    # Find user
    user = container.user_repo.get_by_email(email)
    if user and user.is_active:
        # Generate reset token
        auth_service = AuthService(container.db)
        token = auth_service.create_password_reset_token(user.id)

        # Send email
        host = request.headers.get("Host", "localhost")
        scheme = "https" if "localhost" not in host else "http"
        reset_url = f"{scheme}://{host}/reset-password?token={token}"
        email_service.send_password_reset(email, reset_url, user.display_name)
        logger.info(f"Password reset requested for {email}")

    # Always show success (don't reveal if email exists)
    return RedirectResponse(url="/forgot-password?success=true", status_code=302)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str, error: Optional[str] = None):
    """Display reset password page."""
    csrf_ctx = get_csrf_context(request)
    context = {"request": request, "token": token, "error": error, **csrf_ctx}
    response = templates.TemplateResponse("auth/reset_password.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Handle reset password form submission."""
    from auth import AuthService, validate_password, hash_password

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url=f"/reset-password?token={token}&error=csrf", status_code=302)

    # Validate passwords match
    if password != password_confirm:
        return RedirectResponse(
            url=f"/reset-password?token={token}&error=password_mismatch",
            status_code=302
        )

    # Validate password strength
    pwd_valid, pwd_error = validate_password(password)
    if not pwd_valid:
        return RedirectResponse(
            url=f"/reset-password?token={token}&error={pwd_error}",
            status_code=302
        )

    # Verify token and get user
    auth_service = AuthService(container.db)
    user_id = auth_service.verify_password_reset_token(token)
    if not user_id:
        return RedirectResponse(
            url=f"/reset-password?token={token}&error=invalid_token",
            status_code=302
        )

    # Update password
    container.user_repo.update(user_id, password_hash=hash_password(password))
    logger.info(f"Password reset completed for user ID: {user_id}")

    return RedirectResponse(url="/login?success=password_reset", status_code=302)
