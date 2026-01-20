"""
Upload Routes for VetScan

Handles: /upload, /imports
"""

import os
import re

from fastapi import APIRouter, Request, Form, File, UploadFile, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional

from config import settings
from logging_config import get_logger
from api.dependencies import get_container, get_service, ServiceContainer
from middleware.csrf import validate_csrf, get_csrf_context, set_csrf_cookie
from pdf_validator import PDFValidator, ValidationResult

logger = get_logger("routes.upload")

router = APIRouter(tags=["upload"])

# Templates will be set by main app
templates: Optional[Jinja2Templates] = None


def set_templates(t: Jinja2Templates):
    """Set the Jinja2 templates instance."""
    global templates
    templates = t


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Display PDF upload page."""
    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("upload.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response


@router.post("/upload")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(None)
):
    """Handle PDF upload with security validation."""
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
    uploads_dir = settings.paths.uploads_dir
    temp_path = uploads_dir / safe_filename
    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        # Validate PDF content using PDFValidator
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
            from utils.template_filters import format_date
            animal_id, session_id, parsed = service.import_pdf(
                str(temp_path),
                copy_to_uploads=False
            )

            logger.info(
                f"PDF imported: {parsed.animal.name}, "
                f"report={parsed.session.report_number or 'N/A'}"
            )

            return JSONResponse({
                "success": True,
                "message": f"Successfully imported report for {parsed.animal.name}",
                "animal_id": animal_id,
                "session_id": session_id,
                "animal_name": parsed.animal.name,
                "report_number": parsed.session.report_number or "N/A",
                "test_date": str(parsed.session.test_date) if parsed.session.test_date else "N/A"
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


@router.get("/imports", response_class=HTMLResponse)
async def imports_page(
    request: Request,
    container: ServiceContainer = Depends(get_container)
):
    """Display email import log page."""
    # Get import log from database
    cursor = container.db.conn.execute("""
        SELECT * FROM email_import_log
        ORDER BY import_timestamp DESC
        LIMIT 100
    """)
    import_logs = [dict(row) for row in cursor.fetchall()]

    csrf_ctx = get_csrf_context(request)
    context = {
        "request": request,
        "import_logs": import_logs,
        "user": getattr(request.state, 'user', None),
        **csrf_ctx
    }
    response = templates.TemplateResponse("imports.html", context)
    set_csrf_cookie(response, request, csrf_ctx["_csrf_raw"])
    return response
