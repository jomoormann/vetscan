"""
Diagnosis Routes for VetScan

Handles: /animal/{id}/diagnosis, /diagnosis/{id}
"""

from fastapi import APIRouter, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from logging_config import get_logger
from api.dependencies import get_container, ServiceContainer
from middleware.csrf import validate_csrf, get_csrf_context, set_csrf_cookie

logger = get_logger("routes.diagnosis")

router = APIRouter(tags=["diagnosis"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


@router.get("/animal/{animal_id}/diagnosis", response_class=HTMLResponse)
async def diagnosis_page(
    request: Request,
    animal_id: int,
    container: ServiceContainer = Depends(get_container)
):
    """Display diagnosis generation options for an animal."""
    animal = container.animal_repo.get(animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    clinical_notes = container.animal_repo.get_clinical_notes(animal_id)
    sessions = container.session_repo.get_sessions_for_animal(animal_id)
    existing_reports = container.diagnosis_repo.get_for_animal(animal_id)

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "animal": animal,
        "clinical_notes": clinical_notes,
        "sessions": sessions,
        "existing_reports": existing_reports,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("diagnosis.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/animal/{animal_id}/diagnosis/generate")
async def generate_diagnosis(
    request: Request,
    animal_id: int,
    report_type: str = Form(...),
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Generate a new AI diagnosis report."""
    from diagnosis_service import DiagnosisService
    from config import settings

    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        return RedirectResponse(
            url=f"/animal/{animal_id}/diagnosis?error=csrf",
            status_code=302
        )

    animal = container.animal_repo.get(animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    # Check if AI is configured
    if not settings.ai.anthropic_available and not settings.ai.openai_available:
        return RedirectResponse(
            url=f"/animal/{animal_id}/diagnosis?error=ai_not_configured",
            status_code=302
        )

    try:
        # Get clinical data
        clinical_notes = container.animal_repo.get_clinical_notes(animal_id)
        sessions = container.session_repo.get_sessions_for_animal(animal_id)

        # Initialize diagnosis service
        diagnosis_service = DiagnosisService(
            anthropic_api_key=settings.ai.anthropic_api_key,
            openai_api_key=settings.ai.openai_api_key
        )

        # Generate diagnosis based on type
        if report_type == "clinical_notes_only":
            if not clinical_notes:
                return RedirectResponse(
                    url=f"/animal/{animal_id}/diagnosis?error=no_clinical_notes",
                    status_code=302
                )
            report = diagnosis_service.generate_from_clinical_notes(animal, clinical_notes)
        else:  # comprehensive
            if not sessions:
                return RedirectResponse(
                    url=f"/animal/{animal_id}/diagnosis?error=no_sessions",
                    status_code=302
                )

            # Get results for each session
            session_data = []
            for session in sessions:
                results = container.session_repo.get_results_for_session(session.id)
                biochemistry = container.session_repo.get_biochemistry_for_session(session.id)
                urinalysis = container.session_repo.get_urinalysis_for_session(session.id)
                session_data.append({
                    "session": session,
                    "results": results,
                    "biochemistry": biochemistry,
                    "urinalysis": urinalysis
                })

            report = diagnosis_service.generate_comprehensive(
                animal, clinical_notes, session_data
            )

        # Save report to database
        report.animal_id = animal_id
        report_id = container.diagnosis_repo.create(report)

        logger.info(f"Generated {report_type} diagnosis report for animal {animal_id}")

        return RedirectResponse(
            url=f"/diagnosis/{report_id}",
            status_code=302
        )

    except Exception as e:
        logger.exception(f"Failed to generate diagnosis for animal {animal_id}")
        return RedirectResponse(
            url=f"/animal/{animal_id}/diagnosis?error=generation_failed",
            status_code=302
        )


@router.get("/diagnosis/{report_id}", response_class=HTMLResponse)
async def view_diagnosis(
    request: Request,
    report_id: int,
    container: ServiceContainer = Depends(get_container)
):
    """Display a diagnosis report."""
    report = container.diagnosis_repo.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    animal = container.animal_repo.get(report.animal_id)
    if not animal:
        raise HTTPException(status_code=404, detail="Animal not found")

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "report": report,
        "animal": animal,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("diagnosis_report.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/diagnosis/{report_id}/delete")
async def delete_diagnosis(
    request: Request,
    report_id: int,
    csrf_token: str = Form(None),
    container: ServiceContainer = Depends(get_container)
):
    """Delete a diagnosis report."""
    # Validate CSRF
    if not validate_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid request")

    report = container.diagnosis_repo.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    animal_id = report.animal_id
    container.diagnosis_repo.delete(report_id)
    logger.info(f"Deleted diagnosis report {report_id}")

    return RedirectResponse(
        url=f"/animal/{animal_id}/diagnosis",
        status_code=302
    )
