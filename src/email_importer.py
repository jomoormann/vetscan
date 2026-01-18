"""
Email Importer for Veterinary Protein Analysis Application

Fetches emails via IMAP, validates PDF attachments, and imports
legitimate DNAtech reports using the existing import pipeline.
"""

import email
import imaplib
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from typing import List, Optional, Tuple
import traceback

from email_config import EmailConfig, get_email_config
from pdf_validator import PDFValidator, ValidationReport, ValidationResult
from app import VetProteinService
from models import Database


@dataclass
class ImportResult:
    """Result of importing a single PDF attachment."""
    success: bool
    email_uid: str
    email_subject: str
    email_from: str
    attachment_name: str
    validation_result: str
    error_message: Optional[str] = None
    report_number: Optional[str] = None
    animal_id: Optional[int] = None
    session_id: Optional[int] = None


@dataclass
class BatchResult:
    """Result of a batch import run."""
    start_time: datetime
    end_time: Optional[datetime] = None
    emails_processed: int = 0
    pdfs_found: int = 0
    imports_successful: int = 0
    imports_failed: int = 0
    imports_skipped: int = 0  # Duplicates or rate limited
    results: List[ImportResult] = field(default_factory=list)
    error: Optional[str] = None


class RateLimiter:
    """Simple rate limiter for import operations."""

    def __init__(self, max_per_hour: int):
        self.max_per_hour = max_per_hour
        self.timestamps: List[datetime] = []

    def can_proceed(self) -> bool:
        """Check if we can proceed with another import."""
        now = datetime.now()
        cutoff = now - timedelta(hours=1)

        # Remove old timestamps
        self.timestamps = [ts for ts in self.timestamps if ts > cutoff]

        return len(self.timestamps) < self.max_per_hour

    def record_import(self):
        """Record that an import was performed."""
        self.timestamps.append(datetime.now())

    @property
    def remaining(self) -> int:
        """Number of imports remaining in the current hour."""
        now = datetime.now()
        cutoff = now - timedelta(hours=1)
        self.timestamps = [ts for ts in self.timestamps if ts > cutoff]
        return max(0, self.max_per_hour - len(self.timestamps))


class EmailImporter:
    """
    Imports DNAtech lab reports from email attachments.

    Workflow:
    1. Connect to IMAP server
    2. Fetch unread emails from INBOX
    3. Extract PDF attachments
    4. Validate each PDF (security + DNAtech markers)
    5. Import valid PDFs using VetProteinService
    6. Move processed emails to appropriate folders
    7. Log all operations to database
    """

    def __init__(self, config: Optional[EmailConfig] = None,
                 db_path: str = "data/vet_proteins.db",
                 uploads_dir: str = "uploads"):
        """
        Initialize the email importer.

        Args:
            config: Email configuration. If None, loads from environment.
            db_path: Path to the SQLite database
            uploads_dir: Directory to store imported PDFs
        """
        self.config = config or get_email_config()
        self.db_path = db_path
        self.uploads_dir = uploads_dir
        self.validator = PDFValidator()
        self.rate_limiter = RateLimiter(self.config.import_rate_limit)
        self.imap: Optional[imaplib.IMAP4_SSL] = None
        self._log_func = print  # Default to print, can be replaced with logger

    def set_logger(self, log_func):
        """Set custom logging function."""
        self._log_func = log_func

    def _log(self, message: str):
        """Log a message."""
        self._log_func(f"[EmailImporter] {message}")

    def connect(self) -> bool:
        """
        Establish IMAP connection.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            self._log(f"Connecting to {self.config.imap_host}:{self.config.imap_port}")
            self.imap = imaplib.IMAP4_SSL(
                self.config.imap_host,
                self.config.imap_port
            )
            self.imap.login(self.config.email_address, self.config.email_password)
            self._log("IMAP connection established")
            return True
        except Exception as e:
            self._log(f"IMAP connection failed: {e}")
            return False

    def disconnect(self):
        """Close IMAP connection."""
        if self.imap:
            try:
                self.imap.close()
                self.imap.logout()
            except Exception:
                pass
            self.imap = None

    def _ensure_folder_exists(self, folder_name: str) -> bool:
        """Create IMAP folder if it doesn't exist."""
        try:
            status, _ = self.imap.select(folder_name)
            if status == 'OK':
                self.imap.select('INBOX')  # Go back to inbox
                return True
        except Exception:
            pass

        # Try to create the folder
        try:
            self.imap.create(folder_name)
            self._log(f"Created folder: {folder_name}")
            return True
        except Exception as e:
            self._log(f"Could not create folder {folder_name}: {e}")
            return False

    def get_unprocessed_emails(self) -> List[Tuple[str, Message]]:
        """
        Fetch unread emails from INBOX.

        Returns:
            List of (uid, email_message) tuples
        """
        if not self.imap:
            return []

        try:
            self.imap.select('INBOX')
            status, data = self.imap.uid('search', None, 'UNSEEN')

            if status != 'OK':
                self._log("Failed to search for unread emails")
                return []

            uids = data[0].split()
            self._log(f"Found {len(uids)} unread emails")

            emails = []
            for uid in uids:
                uid_str = uid.decode('utf-8')
                status, msg_data = self.imap.uid('fetch', uid, '(RFC822)')
                if status == 'OK' and msg_data[0]:
                    raw_email = msg_data[0][1]
                    email_message = email.message_from_bytes(raw_email)
                    emails.append((uid_str, email_message))

            return emails

        except Exception as e:
            self._log(f"Error fetching emails: {e}")
            return []

    def _decode_header_value(self, header_value) -> str:
        """Decode email header value to string."""
        if header_value is None:
            return ""

        decoded_parts = decode_header(header_value)
        result = []
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(encoding or 'utf-8', errors='replace'))
            else:
                result.append(part)
        return ''.join(result)

    def extract_pdf_attachments(self, msg: Message) -> List[Tuple[str, bytes]]:
        """
        Extract PDF attachments from an email message.

        Returns:
            List of (filename, content_bytes) tuples
        """
        attachments = []

        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Check for PDF attachments
            if "attachment" in content_disposition or content_type == "application/pdf":
                filename = part.get_filename()
                if filename:
                    filename = self._decode_header_value(filename)
                    if filename.lower().endswith('.pdf'):
                        content = part.get_payload(decode=True)
                        if content:
                            attachments.append((filename, content))

        return attachments

    def _move_email(self, uid: str, folder: str) -> bool:
        """Move email to specified folder."""
        try:
            self._ensure_folder_exists(folder)
            # Copy to destination folder
            status, _ = self.imap.uid('copy', uid, folder)
            if status == 'OK':
                # Mark original as deleted
                self.imap.uid('store', uid, '+FLAGS', '\\Deleted')
                self.imap.expunge()
                return True
            return False
        except Exception as e:
            self._log(f"Error moving email {uid} to {folder}: {e}")
            return False

    def _mark_as_read(self, uid: str):
        """Mark email as read."""
        try:
            self.imap.uid('store', uid, '+FLAGS', '\\Seen')
        except Exception:
            pass

    def process_pdf(self, filename: str, content: bytes,
                    email_uid: str, email_subject: str,
                    email_from: str) -> ImportResult:
        """
        Validate and import a single PDF attachment.

        Args:
            filename: Name of the PDF file
            content: PDF file content
            email_uid: UID of the source email
            email_subject: Subject of the source email
            email_from: Sender of the source email

        Returns:
            ImportResult with outcome details
        """
        # Check rate limit
        if not self.rate_limiter.can_proceed():
            return ImportResult(
                success=False,
                email_uid=email_uid,
                email_subject=email_subject,
                email_from=email_from,
                attachment_name=filename,
                validation_result="rate_limited",
                error_message=f"Rate limit exceeded ({self.config.import_rate_limit}/hour)"
            )

        # Write to temp file for validation
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # Validate PDF
            validation = self.validator.validate(tmp_path)

            if not validation.is_valid:
                return ImportResult(
                    success=False,
                    email_uid=email_uid,
                    email_subject=email_subject,
                    email_from=email_from,
                    attachment_name=filename,
                    validation_result=validation.result_code.value,
                    error_message=validation.message,
                    report_number=validation.report_number
                )

            # Import using VetProteinService
            with VetProteinService(
                db_path=self.db_path,
                uploads_dir=self.uploads_dir
            ) as service:
                try:
                    # Copy temp file to uploads with original filename
                    upload_path = os.path.join(self.uploads_dir, filename)

                    # Handle duplicate filenames
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(upload_path):
                        upload_path = os.path.join(
                            self.uploads_dir,
                            f"{base}_{counter}{ext}"
                        )
                        counter += 1

                    # Copy to final location
                    with open(upload_path, 'wb') as f:
                        f.write(content)

                    # Import the PDF
                    animal_id, session_id, parsed = service.import_pdf(
                        upload_path,
                        copy_to_uploads=False  # Already in uploads
                    )

                    self.rate_limiter.record_import()

                    return ImportResult(
                        success=True,
                        email_uid=email_uid,
                        email_subject=email_subject,
                        email_from=email_from,
                        attachment_name=filename,
                        validation_result=ValidationResult.VALID.value,
                        report_number=parsed.session.report_number,
                        animal_id=animal_id,
                        session_id=session_id
                    )

                except ValueError as e:
                    # Duplicate report
                    error_msg = str(e)
                    if "already exists" in error_msg:
                        return ImportResult(
                            success=False,
                            email_uid=email_uid,
                            email_subject=email_subject,
                            email_from=email_from,
                            attachment_name=filename,
                            validation_result="duplicate",
                            error_message=error_msg,
                            report_number=validation.report_number
                        )
                    raise

        except Exception as e:
            self._log(f"Error processing PDF {filename}: {e}")
            traceback.print_exc()
            return ImportResult(
                success=False,
                email_uid=email_uid,
                email_subject=email_subject,
                email_from=email_from,
                attachment_name=filename,
                validation_result="import_error",
                error_message=str(e)
            )

        finally:
            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _log_import_result(self, result: ImportResult):
        """Log import result to database."""
        try:
            db = Database(self.db_path)
            db.connect()
            db.conn.execute("""
                INSERT INTO email_import_log (
                    email_uid, email_subject, email_from, attachment_name,
                    validation_result, import_success, error_message,
                    report_number, animal_id, session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.email_uid,
                result.email_subject[:500] if result.email_subject else None,
                result.email_from[:200] if result.email_from else None,
                result.attachment_name,
                result.validation_result,
                1 if result.success else 0,
                result.error_message,
                result.report_number,
                result.animal_id,
                result.session_id
            ))
            db.conn.commit()
            db.close()
        except Exception as e:
            self._log(f"Error logging import result: {e}")

    def run_import_batch(self) -> BatchResult:
        """
        Run a batch import of all unread emails.

        Returns:
            BatchResult with summary and details
        """
        batch = BatchResult(start_time=datetime.now())

        # Validate configuration
        valid, error_msg = self.config.validate()
        if not valid:
            batch.error = error_msg
            batch.end_time = datetime.now()
            return batch

        # Connect to IMAP
        if not self.connect():
            batch.error = "Failed to connect to IMAP server"
            batch.end_time = datetime.now()
            return batch

        try:
            # Ensure folders exist
            self._ensure_folder_exists(self.config.processed_folder)
            self._ensure_folder_exists(self.config.failed_folder)

            # Fetch unread emails
            emails = self.get_unprocessed_emails()
            batch.emails_processed = len(emails)

            for uid, msg in emails:
                subject = self._decode_header_value(msg.get('Subject', ''))
                sender = self._decode_header_value(msg.get('From', ''))

                self._log(f"Processing email: {subject[:50]}...")

                # Extract PDF attachments
                attachments = self.extract_pdf_attachments(msg)

                if not attachments:
                    self._log(f"  No PDF attachments found")
                    self._mark_as_read(uid)
                    continue

                batch.pdfs_found += len(attachments)

                # Process each attachment
                email_success = True
                for filename, content in attachments:
                    self._log(f"  Processing attachment: {filename}")

                    result = self.process_pdf(
                        filename, content, uid, subject, sender
                    )

                    # Log to database
                    self._log_import_result(result)
                    batch.results.append(result)

                    if result.success:
                        batch.imports_successful += 1
                        self._log(f"    Imported successfully: {result.report_number}")
                    elif result.validation_result == "duplicate":
                        batch.imports_skipped += 1
                        self._log(f"    Skipped (duplicate): {result.report_number}")
                    elif result.validation_result == "rate_limited":
                        batch.imports_skipped += 1
                        self._log(f"    Skipped (rate limited)")
                        email_success = False  # Don't move, process later
                    else:
                        batch.imports_failed += 1
                        email_success = False
                        self._log(f"    Failed: {result.error_message}")

                # Move email to appropriate folder
                if email_success or all(
                    r.validation_result in ("duplicate", ValidationResult.VALID.value)
                    for r in batch.results if r.email_uid == uid
                ):
                    self._move_email(uid, self.config.processed_folder)
                elif any(r.validation_result == "rate_limited"
                        for r in batch.results if r.email_uid == uid):
                    # Leave rate-limited emails for next run
                    pass
                else:
                    self._move_email(uid, self.config.failed_folder)

        except Exception as e:
            batch.error = str(e)
            self._log(f"Batch import error: {e}")
            traceback.print_exc()

        finally:
            self.disconnect()
            batch.end_time = datetime.now()

        return batch


def run_email_import(db_path: str = "data/vet_proteins.db",
                     uploads_dir: str = "uploads") -> BatchResult:
    """
    Convenience function to run email import.

    Args:
        db_path: Path to SQLite database
        uploads_dir: Directory for uploaded PDFs

    Returns:
        BatchResult with import summary
    """
    importer = EmailImporter(db_path=db_path, uploads_dir=uploads_dir)
    return importer.run_import_batch()
