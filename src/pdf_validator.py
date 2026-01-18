"""
PDF Validator for Email Import

Security validation for PDF attachments before importing into the system.
Ensures PDFs are legitimate DNAtech lab reports and free of malicious content.
"""

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import pdfplumber

from email_config import get_email_config


class ValidationResult(Enum):
    """Validation result codes."""
    VALID = "valid"
    INVALID_EXTENSION = "invalid_extension"
    FILE_TOO_LARGE = "file_too_large"
    INVALID_MAGIC_BYTES = "invalid_magic_bytes"
    SUSPICIOUS_CONTENT = "suspicious_content"
    PDF_PARSE_ERROR = "pdf_parse_error"
    MISSING_DNATECH_MARKERS = "missing_dnatech_markers"
    FILE_NOT_FOUND = "file_not_found"


@dataclass
class ValidationReport:
    """Result of PDF validation."""
    is_valid: bool
    result_code: ValidationResult
    message: str
    report_number: Optional[str] = None
    details: Optional[dict] = None


class PDFValidator:
    """
    Validates PDF files for security and DNAtech report authenticity.

    Security checks performed (in order):
    1. File extension check
    2. File size check (max 10MB by default)
    3. PDF magic bytes verification
    4. Suspicious content scan (JavaScript, Launch actions, etc.)
    5. PDF structure validation (must parse with pdfplumber)
    6. DNAtech marker verification
    """

    # PDF magic bytes
    PDF_MAGIC = b'%PDF-'

    # Suspicious patterns that could indicate malicious content
    # Note: /AA and /OpenAction removed - commonly used in legitimate PDFs
    SUSPICIOUS_PATTERNS = [
        b'/JavaScript',
        b'/JS',
        b'/Launch',
        b'/EmbeddedFile',
        b'/RichMedia',
        b'/XFA',
    ]

    # Required DNAtech markers (must find report header + lab name)
    REQUIRED_MARKERS = [
        r'Folha\s+de\s+Trabalho\s+N[º°o]',  # Report header
        r'DNAtech',  # Lab name
    ]

    # Additional markers (must find at least 2)
    SUPPORTING_MARKERS = [
        r'Albumina',
        r'PROTEINOGRAMA',
        r'Esp[ée]cie',
        r'Ra[çc]a',
        r'Data\s+de\s+fecho',
        r'Amostra',
        r'Refer[êe]ncia',
        r'Resultado',
    ]

    # Pattern to extract report number
    REPORT_NUMBER_PATTERN = r'Folha\s+de\s+Trabalho\s+N[º°o]\s*[:\s]*(\d+/\d+)'

    def __init__(self, max_size_mb: Optional[int] = None):
        """
        Initialize validator.

        Args:
            max_size_mb: Maximum file size in MB. If None, uses config value.
        """
        config = get_email_config()
        self.max_size_bytes = (max_size_mb or config.pdf_max_size_mb) * 1024 * 1024

    def validate(self, file_path: str) -> ValidationReport:
        """
        Perform full validation on a PDF file.

        Args:
            file_path: Path to the PDF file to validate

        Returns:
            ValidationReport with validation result
        """
        # Check 1: File exists
        if not os.path.exists(file_path):
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.FILE_NOT_FOUND,
                message=f"File not found: {file_path}"
            )

        # Check 2: File extension
        if not file_path.lower().endswith('.pdf'):
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.INVALID_EXTENSION,
                message="File must have .pdf extension"
            )

        # Check 3: File size
        file_size = os.path.getsize(file_path)
        if file_size > self.max_size_bytes:
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.FILE_TOO_LARGE,
                message=f"File size ({file_size / 1024 / 1024:.1f}MB) exceeds maximum "
                        f"({self.max_size_bytes / 1024 / 1024:.0f}MB)"
            )

        # Check 4: PDF magic bytes
        try:
            with open(file_path, 'rb') as f:
                header = f.read(1024)  # Read first 1KB for checks

            if not header.startswith(self.PDF_MAGIC):
                return ValidationReport(
                    is_valid=False,
                    result_code=ValidationResult.INVALID_MAGIC_BYTES,
                    message="File does not have valid PDF header (magic bytes)"
                )
        except IOError as e:
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.FILE_NOT_FOUND,
                message=f"Cannot read file: {e}"
            )

        # Check 5: Suspicious content scan
        suspicious_result = self._scan_suspicious_content(file_path)
        if suspicious_result:
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.SUSPICIOUS_CONTENT,
                message=f"PDF contains suspicious content: {suspicious_result}"
            )

        # Check 6: PDF structure validation
        try:
            text_content = self._extract_text(file_path)
        except Exception as e:
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.PDF_PARSE_ERROR,
                message=f"PDF parsing failed: {e}"
            )

        # Check 7: DNAtech markers
        markers_result = self._check_dnatech_markers(text_content)
        if not markers_result['valid']:
            return ValidationReport(
                is_valid=False,
                result_code=ValidationResult.MISSING_DNATECH_MARKERS,
                message=markers_result['message'],
                details=markers_result
            )

        # All checks passed
        report_number = self._extract_report_number(text_content)

        return ValidationReport(
            is_valid=True,
            result_code=ValidationResult.VALID,
            message="PDF is a valid DNAtech report",
            report_number=report_number,
            details={
                'file_size': file_size,
                'markers_found': markers_result.get('found_markers', [])
            }
        )

    def _scan_suspicious_content(self, file_path: str) -> Optional[str]:
        """
        Scan PDF raw bytes for suspicious patterns.

        Returns:
            Name of suspicious pattern found, or None if clean
        """
        try:
            with open(file_path, 'rb') as f:
                content = f.read()

            for pattern in self.SUSPICIOUS_PATTERNS:
                if pattern in content:
                    return pattern.decode('utf-8', errors='ignore')

            return None
        except IOError:
            return "Cannot read file for security scan"

    def _extract_text(self, file_path: str) -> str:
        """
        Extract text content from PDF using pdfplumber.

        Returns:
            Concatenated text from all pages

        Raises:
            Exception if PDF cannot be parsed
        """
        text_parts = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

        return '\n'.join(text_parts)

    def _check_dnatech_markers(self, text: str) -> dict:
        """
        Check for required DNAtech report markers.

        Returns:
            Dict with 'valid' bool and details about found markers
        """
        found_required = []
        missing_required = []

        # Check required markers
        for pattern in self.REQUIRED_MARKERS:
            if re.search(pattern, text, re.IGNORECASE):
                found_required.append(pattern)
            else:
                missing_required.append(pattern)

        # If missing required markers, fail immediately
        if missing_required:
            return {
                'valid': False,
                'message': f"Missing required markers: {missing_required}",
                'found_markers': found_required,
                'missing_markers': missing_required
            }

        # Check supporting markers (need at least 2)
        found_supporting = []
        for pattern in self.SUPPORTING_MARKERS:
            if re.search(pattern, text, re.IGNORECASE):
                found_supporting.append(pattern)

        if len(found_supporting) < 2:
            return {
                'valid': False,
                'message': f"Found only {len(found_supporting)} supporting markers "
                          f"(need at least 2): {found_supporting}",
                'found_markers': found_required + found_supporting,
                'supporting_count': len(found_supporting)
            }

        return {
            'valid': True,
            'message': "All required markers found",
            'found_markers': found_required + found_supporting,
            'supporting_count': len(found_supporting)
        }

    def _extract_report_number(self, text: str) -> Optional[str]:
        """Extract report number from PDF text."""
        match = re.search(self.REPORT_NUMBER_PATTERN, text)
        if match:
            return match.group(1)
        return None


def validate_pdf(file_path: str) -> ValidationReport:
    """
    Convenience function to validate a PDF file.

    Args:
        file_path: Path to PDF file

    Returns:
        ValidationReport with result
    """
    validator = PDFValidator()
    return validator.validate(file_path)
