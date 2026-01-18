"""
Email Import Configuration for Veterinary Protein Analysis Application

Manages IMAP credentials and import settings loaded from environment variables.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class EmailConfig:
    """Configuration for email import functionality."""

    # IMAP Server Settings
    imap_host: str
    imap_port: int
    email_address: str
    email_password: str

    # Feature Toggle
    import_enabled: bool

    # IMAP Folders
    processed_folder: str
    failed_folder: str

    # Security Settings
    pdf_max_size_mb: int
    import_rate_limit: int  # Max imports per hour

    @classmethod
    def from_env(cls) -> 'EmailConfig':
        """Load configuration from environment variables."""
        return cls(
            imap_host=os.getenv('EMAIL_IMAP_HOST', 'imap.hostinger.com'),
            imap_port=int(os.getenv('EMAIL_IMAP_PORT', '993')),
            email_address=os.getenv('EMAIL_ADDRESS', ''),
            email_password=os.getenv('EMAIL_PASSWORD', ''),
            import_enabled=os.getenv('EMAIL_IMPORT_ENABLED', 'false').lower() == 'true',
            processed_folder=os.getenv('EMAIL_PROCESSED_FOLDER', 'Processed'),
            failed_folder=os.getenv('EMAIL_FAILED_FOLDER', 'Failed'),
            pdf_max_size_mb=int(os.getenv('PDF_MAX_SIZE_MB', '10')),
            import_rate_limit=int(os.getenv('PDF_IMPORT_RATE_LIMIT', '20')),
        )

    def validate(self) -> tuple[bool, str]:
        """
        Validate that required configuration is present.

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self.import_enabled:
            return False, "Email import is disabled (EMAIL_IMPORT_ENABLED=false)"

        if not self.email_address:
            return False, "EMAIL_ADDRESS is not configured"

        if not self.email_password:
            return False, "EMAIL_PASSWORD is not configured"

        if not self.imap_host:
            return False, "EMAIL_IMAP_HOST is not configured"

        return True, ""

    @property
    def pdf_max_size_bytes(self) -> int:
        """Maximum PDF size in bytes."""
        return self.pdf_max_size_mb * 1024 * 1024


# Global config instance (loaded lazily)
_config: Optional[EmailConfig] = None


def get_email_config() -> EmailConfig:
    """Get the global email configuration instance."""
    global _config
    if _config is None:
        _config = EmailConfig.from_env()
    return _config
