"""
Admin Routes for VetScan

Handles: /admin/users, approve, disable, enable
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from config import settings
from logging_config import get_logger
from api.dependencies import get_container, ServiceContainer
from middleware.csrf import validate_csrf, get_csrf_context, set_csrf_cookie
from email_sender import email_service

logger = get_logger("routes.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


def require_superuser(request: Request):
    """Ensure the current user is a superuser."""
    user = getattr(request.state, 'user', None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request,
    container: ServiceContainer = Depends(get_container)
):
    """Display admin users management page."""
    current_user = require_superuser(request)

    users = container.user_repo.list_all(include_inactive=True)
    pending = container.user_repo.get_pending()

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "users": users,
        "pending_users": pending,
        "current_user": current_user,
        **csrf_ctx
    }
    response = templates.TemplateResponse("admin/users.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/users/{user_id}/approve")
async def admin_approve_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Approve a user account."""
    current_user = require_superuser(request)

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed for user approval by {current_user.email}")
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    user = container.user_repo.get(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    container.user_repo.approve(user_id, current_user.id)
    logger.info(f"User {user.email} approved by {current_user.email}")

    # Send approval notification email
    host = request.headers.get("Host", "localhost")
    scheme = "https" if "localhost" not in host else "http"
    login_url = f"{scheme}://{host}/login"
    email_service.send_account_approved(user.email, login_url, user.display_name)

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/disable")
async def admin_disable_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Disable a user account and invalidate their session."""
    current_user = require_superuser(request)

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed for user disable by {current_user.email}")
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    if user_id == current_user.id:
        logger.warning(f"User {current_user.email} attempted to disable their own account")
        raise HTTPException(status_code=400, detail="Cannot disable your own account")

    user = container.user_repo.get(user_id)
    if user:
        container.user_repo.disable(user_id)
        logger.info(f"User {user.email} (ID: {user_id}) disabled by admin {current_user.email}")
    else:
        logger.warning(f"Attempted to disable non-existent user ID: {user_id}")

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/enable")
async def admin_enable_user(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Re-enable a user account."""
    current_user = require_superuser(request)

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        logger.warning(f"CSRF validation failed for user enable by {current_user.email}")
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    user = container.user_repo.get(user_id)
    if user:
        container.user_repo.enable(user_id)
        logger.info(f"User {user.email} (ID: {user_id}) re-enabled by admin {current_user.email}")
    else:
        logger.warning(f"Attempted to enable non-existent user ID: {user_id}")

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/make-admin")
async def admin_make_admin(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Grant admin privileges to a user."""
    current_user = require_superuser(request)

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    user = container.user_repo.get(user_id)
    if user:
        container.user_repo.update(user_id, is_superuser=True)
        logger.info(f"User {user.email} granted admin by {current_user.email}")

    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/users/{user_id}/remove-admin")
async def admin_remove_admin(
    request: Request,
    user_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Remove admin privileges from a user."""
    current_user = require_superuser(request)

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(url="/admin/users?error=csrf", status_code=302)

    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot remove your own admin privileges")

    user = container.user_repo.get(user_id)
    if user:
        container.user_repo.update(user_id, is_superuser=False)
        logger.info(f"Admin privileges removed from {user.email} by {current_user.email}")

    return RedirectResponse(url="/admin/users", status_code=302)
