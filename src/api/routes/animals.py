"""
Animals Routes for VetScan

Handles: /, /animals, /animal/{id}
"""

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from logging_config import get_logger
from api.dependencies import get_container, ServiceContainer
from middleware.csrf import get_csrf_context, set_csrf_cookie

logger = get_logger("routes.animals")

router = APIRouter(tags=["animals"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    container: ServiceContainer = Depends(get_container)
):
    """Display main dashboard with animals list."""
    animals = container.animal_repo.list_all()

    # Get session counts for each animal
    animal_data = []
    for animal in animals:
        sessions = container.session_repo.get_sessions_for_animal(animal.id)
        animal_data.append({
            "animal": animal,
            "session_count": len(sessions),
            "latest_session": sessions[0] if sessions else None
        })

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "animals": animal_data,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("index.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.get("/animals", response_class=HTMLResponse)
async def animals_list(
    request: Request,
    container: ServiceContainer = Depends(get_container)
):
    """Display animals list page."""
    animals = container.animal_repo.list_all()

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "animals": animals,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("animals.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.get("/animal/{animal_id}", response_class=HTMLResponse)
async def animal_detail(
    request: Request,
    animal_id: int,
    container: ServiceContainer = Depends(get_container)
):
    """Display animal detail page."""
    animal = container.animal_repo.get(animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    sessions = container.session_repo.get_sessions_for_animal(animal_id)
    symptoms = container.animal_repo.get_symptoms(animal_id)
    observations = container.animal_repo.get_observations(animal_id)
    clinical_notes = container.animal_repo.get_clinical_notes(animal_id)
    diagnosis_reports = container.diagnosis_repo.get_for_animal(animal_id)

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "animal": animal,
        "sessions": sessions,
        "symptoms": symptoms,
        "observations": observations,
        "clinical_notes": clinical_notes,
        "diagnosis_reports": diagnosis_reports,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("animal_detail.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response
