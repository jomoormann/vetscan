"""
Custom Exceptions for VetScan

Provides a hierarchy of exceptions for better error handling and logging.
All custom exceptions inherit from VetScanException.

Usage:
    from exceptions import DuplicateReportError, AnimalNotFoundError

    try:
        service.import_pdf(path)
    except DuplicateReportError as e:
        logger.warning(f"Duplicate report: {e.report_number}")
    except AnimalNotFoundError as e:
        logger.error(f"Animal not found: {e.animal_id}")
"""

from typing import Optional


# =============================================================================
# BASE EXCEPTION
# =============================================================================

class VetScanException(Exception):
    """
    Base exception for all VetScan errors.

    Attributes:
        message: Human-readable error message
        code: Machine-readable error code
    """

    def __init__(self, message: str, code: Optional[str] = None):
        self.message = message
        self.code = code or self.__class__.__name__
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


# =============================================================================
# DATA EXCEPTIONS
# =============================================================================

class DataException(VetScanException):
    """Base exception for data-related errors."""
    pass


class DuplicateReportError(DataException):
    """Raised when attempting to import a duplicate report."""

    def __init__(self, report_number: str, message: Optional[str] = None):
        self.report_number = report_number
        super().__init__(
            message or f"Report {report_number} already exists in the database",
            code="DUPLICATE_REPORT"
        )


class AnimalNotFoundError(DataException):
    """Raised when an animal is not found in the database."""

    def __init__(self, animal_id: Optional[int] = None, name: Optional[str] = None):
        self.animal_id = animal_id
        self.animal_name = name
        identifier = f"ID {animal_id}" if animal_id else f"name '{name}'"
        super().__init__(
            f"Animal with {identifier} not found",
            code="ANIMAL_NOT_FOUND"
        )


class SessionNotFoundError(DataException):
    """Raised when a test session is not found."""

    def __init__(self, session_id: int):
        self.session_id = session_id
        super().__init__(
            f"Test session with ID {session_id} not found",
            code="SESSION_NOT_FOUND"
        )


class DiagnosisReportNotFoundError(DataException):
    """Raised when a diagnosis report is not found."""

    def __init__(self, report_id: int):
        self.report_id = report_id
        super().__init__(
            f"Diagnosis report with ID {report_id} not found",
            code="DIAGNOSIS_NOT_FOUND"
        )


class ClinicalNoteNotFoundError(DataException):
    """Raised when a clinical note is not found."""

    def __init__(self, note_id: int):
        self.note_id = note_id
        super().__init__(
            f"Clinical note with ID {note_id} not found",
            code="CLINICAL_NOTE_NOT_FOUND"
        )


# =============================================================================
# AUTHENTICATION EXCEPTIONS
# =============================================================================

class AuthException(VetScanException):
    """Base exception for authentication errors."""
    pass


class AuthenticationError(AuthException):
    """Raised when authentication fails."""

    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(message, code="AUTH_FAILED")


class SessionExpiredError(AuthException):
    """Raised when a user session has expired."""

    def __init__(self):
        super().__init__(
            "Your session has expired. Please log in again.",
            code="SESSION_EXPIRED"
        )


class UserDisabledError(AuthException):
    """Raised when a disabled user attempts to access the system."""

    def __init__(self, email: Optional[str] = None):
        self.email = email
        super().__init__(
            "Your account has been disabled. Please contact an administrator.",
            code="USER_DISABLED"
        )


class UserNotApprovedError(AuthException):
    """Raised when an unapproved user attempts to access restricted features."""

    def __init__(self, email: Optional[str] = None):
        self.email = email
        super().__init__(
            "Your account is pending approval.",
            code="USER_NOT_APPROVED"
        )


class UserNotFoundError(AuthException):
    """Raised when a user is not found."""

    def __init__(self, email: Optional[str] = None, user_id: Optional[int] = None):
        self.email = email
        self.user_id = user_id
        identifier = email or f"ID {user_id}"
        super().__init__(
            f"User {identifier} not found",
            code="USER_NOT_FOUND"
        )


class PasswordResetTokenError(AuthException):
    """Raised when a password reset token is invalid or expired."""

    def __init__(self, reason: str = "invalid"):
        self.reason = reason
        messages = {
            "invalid": "The password reset link is invalid.",
            "expired": "The password reset link has expired.",
            "used": "The password reset link has already been used.",
        }
        super().__init__(
            messages.get(reason, messages["invalid"]),
            code="RESET_TOKEN_ERROR"
        )


class InsufficientPermissionsError(AuthException):
    """Raised when a user lacks required permissions."""

    def __init__(self, required: str = "admin"):
        self.required = required
        super().__init__(
            f"You need {required} permissions to perform this action.",
            code="INSUFFICIENT_PERMISSIONS"
        )


# =============================================================================
# PDF VALIDATION EXCEPTIONS
# =============================================================================

class PDFException(VetScanException):
    """Base exception for PDF-related errors."""
    pass


class PDFValidationError(PDFException):
    """Raised when PDF validation fails."""

    def __init__(self, message: str, validation_code: Optional[str] = None):
        self.validation_code = validation_code
        super().__init__(message, code=validation_code or "PDF_VALIDATION_ERROR")


class PDFParseError(PDFException):
    """Raised when PDF parsing fails."""

    def __init__(self, message: str = "Failed to parse PDF file"):
        super().__init__(message, code="PDF_PARSE_ERROR")


class InvalidPDFFormatError(PDFException):
    """Raised when PDF is not a valid DNAtech report."""

    def __init__(self, message: str = "PDF is not a valid DNAtech lab report"):
        super().__init__(message, code="INVALID_PDF_FORMAT")


class SuspiciousPDFError(PDFException):
    """Raised when PDF contains suspicious content."""

    def __init__(self, pattern: str):
        self.pattern = pattern
        super().__init__(
            f"PDF contains suspicious content: {pattern}",
            code="SUSPICIOUS_PDF"
        )


# =============================================================================
# EMAIL EXCEPTIONS
# =============================================================================

class EmailException(VetScanException):
    """Base exception for email-related errors."""
    pass


class EmailSendError(EmailException):
    """Raised when email sending fails."""

    def __init__(self, recipient: str, reason: str = "unknown"):
        self.recipient = recipient
        self.reason = reason
        super().__init__(
            f"Failed to send email to {recipient}: {reason}",
            code="EMAIL_SEND_FAILED"
        )


class EmailConfigurationError(EmailException):
    """Raised when email is not properly configured."""

    def __init__(self, missing: str = "SMTP credentials"):
        self.missing = missing
        super().__init__(
            f"Email not configured: {missing}",
            code="EMAIL_NOT_CONFIGURED"
        )


# =============================================================================
# AI SERVICE EXCEPTIONS
# =============================================================================

class AIException(VetScanException):
    """Base exception for AI service errors."""
    pass


class AIServiceUnavailableError(AIException):
    """Raised when AI service is not available."""

    def __init__(self, service: str = "AI"):
        self.service = service
        super().__init__(
            f"{service} service is not available. Please check API configuration.",
            code="AI_UNAVAILABLE"
        )


class AIRequestError(AIException):
    """Raised when AI API request fails."""

    def __init__(self, service: str, message: str):
        self.service = service
        super().__init__(
            f"{service} API error: {message}",
            code="AI_REQUEST_ERROR"
        )


class InsufficientDataError(AIException):
    """Raised when there's not enough data to generate AI diagnosis."""

    def __init__(self, message: str = "Insufficient data for diagnosis"):
        super().__init__(message, code="INSUFFICIENT_DATA")


# =============================================================================
# DATABASE EXCEPTIONS
# =============================================================================

class DatabaseException(VetScanException):
    """Base exception for database errors."""
    pass


class DatabaseConnectionError(DatabaseException):
    """Raised when database connection fails."""

    def __init__(self, path: str, reason: str = "unknown"):
        self.path = path
        self.reason = reason
        super().__init__(
            f"Failed to connect to database at {path}: {reason}",
            code="DB_CONNECTION_ERROR"
        )


class DatabaseIntegrityError(DatabaseException):
    """Raised when a database integrity constraint is violated."""

    def __init__(self, message: str):
        super().__init__(message, code="DB_INTEGRITY_ERROR")
