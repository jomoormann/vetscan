"""
Sessions Routes for VetScan

Handles: /session/{id}, /compare/{id}
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from logging_config import get_logger
from api.dependencies import get_container, ServiceContainer
from middleware.csrf import get_csrf_context, set_csrf_cookie

logger = get_logger("routes.sessions")

router = APIRouter(tags=["sessions"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


@router.get("/session/{session_id}", response_class=HTMLResponse)
async def session_detail(
    request: Request,
    session_id: int,
    container: ServiceContainer = Depends(get_container)
):
    """Display session detail page."""
    session = container.session_repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    animal = container.animal_repo.get(session.animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    results = container.session_repo.get_results_for_session(session_id)
    measurements = container.session_repo.get_measurements_for_session(session_id)
    biochemistry = container.session_repo.get_biochemistry_for_session(session_id)
    urinalysis = container.session_repo.get_urinalysis_for_session(session_id)
    pathology_findings = container.session_repo.get_pathology_findings_for_session(session_id)
    session_assets = container.session_repo.get_assets_for_session(session_id)

    # Get all sessions for comparison options
    all_sessions = container.session_repo.get_sessions_for_animal(animal.id)

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "session": session,
        "animal": animal,
        "results": results,
        "measurements": measurements,
        "biochemistry": biochemistry,
        "urinalysis": urinalysis,
        "pathology_findings": pathology_findings,
        "session_assets": session_assets,
        "all_sessions": all_sessions,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("session_detail.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.get("/compare/{session_id}", response_class=HTMLResponse)
async def compare_sessions(
    request: Request,
    session_id: int,
    compare_to: Optional[int] = None,
    container: ServiceContainer = Depends(get_container)
):
    """Display session comparison page."""
    session = container.session_repo.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    animal = container.animal_repo.get(session.animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    # Get all sessions for this animal
    all_sessions = container.session_repo.get_sessions_for_animal(animal.id)

    # Get comparison session if specified
    compare_session = None
    if compare_to:
        compare_session = container.session_repo.get_session(compare_to)

    # Get results for both sessions
    results_current = container.session_repo.get_results_for_session(session_id)
    results_compare = []
    if compare_session:
        results_compare = container.session_repo.get_results_for_session(compare_to)

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "session": session,
        "compare_session": compare_session,
        "animal": animal,
        "results_current": results_current,
        "results_compare": results_compare,
        "all_sessions": all_sessions,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("compare.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response
