"""
Centralized Configuration for VetScan

Validated settings loaded from environment variables with type safety.
Replaces scattered os.getenv() calls throughout the codebase.

Usage:
    from config import settings

    if settings.auth.password:
        # Auth enabled
        ...

    db_path = settings.paths.database
"""

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# =============================================================================
# PATH CONFIGURATION
# =============================================================================

@dataclass
class PathSettings:
    """Application paths configuration."""
    base_dir: Path = field(default_factory=lambda: Path(__file__).parent.parent)

    @property
    def data_dir(self) -> Path:
        path = self.base_dir / "data"
        path.mkdir(exist_ok=True)
        return path

    @property
    def uploads_dir(self) -> Path:
        path = self.base_dir / "uploads"
        path.mkdir(exist_ok=True)
        return path

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "templates"

    @property
    def static_dir(self) -> Path:
        path = self.base_dir / "static"
        path.mkdir(exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = self.base_dir / "logs"
        path.mkdir(exist_ok=True)
        return path

    @property
    def translations_dir(self) -> Path:
        return self.base_dir / "translations"

    @property
    def database(self) -> Path:
        return self.data_dir / "vet_proteins.db"


# =============================================================================
# AUTHENTICATION CONFIGURATION
# =============================================================================

@dataclass
class AuthSettings:
    """Authentication and security settings."""
    username: str = ""
    password: str = ""
    secret_key: str = ""
    cookie_name: str = "vetscan_session"
    csrf_cookie_name: str = "vetscan_csrf"
    allowed_hosts: List[str] = field(default_factory=list)

    # Rate limiting
    rate_limit_attempts: int = 5
    rate_limit_window: int = 300  # 5 minutes
    rate_limit_lockout: int = 900  # 15 minutes

    # Session settings
    session_max_age: int = 7 * 24 * 60 * 60  # 7 days

    @classmethod
    def from_env(cls) -> 'AuthSettings':
        """Load auth settings from environment."""
        return cls(
            username=os.getenv("AUTH_USERNAME", "admin"),
            password=os.getenv("AUTH_PASSWORD", ""),
            secret_key=os.getenv("AUTH_SECRET_KEY", "") or secrets.token_hex(32),
            allowed_hosts=os.getenv(
                "ALLOWED_HOSTS",
                "localhost,127.0.0.1,vetscan.net"
            ).split(","),
            rate_limit_attempts=int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5")),
            rate_limit_window=int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "300")),
            rate_limit_lockout=int(os.getenv("LOGIN_RATE_LIMIT_LOCKOUT", "900")),
        )

    @property
    def is_enabled(self) -> bool:
        """Check if authentication is enabled."""
        return bool(self.password)

    def validate(self) -> List[str]:
        """Validate auth settings and return list of errors."""
        errors = []
        if self.is_enabled:
            if len(self.secret_key) < 32:
                errors.append(
                    "AUTH_SECRET_KEY must be at least 32 characters for security"
                )
            if len(self.password) < 8:
                errors.append(
                    "AUTH_PASSWORD should be at least 8 characters"
                )
        return errors


# =============================================================================
# SMTP CONFIGURATION
# =============================================================================

@dataclass
class SMTPSettings:
    """SMTP email settings."""
    host: str = ""
    port: int = 465
    username: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = "VetScan"
    use_ssl: bool = True

    @classmethod
    def from_env(cls) -> 'SMTPSettings':
        """Load SMTP settings from environment."""
        return cls(
            host=os.getenv("SMTP_HOST", "smtp.hostinger.com"),
            port=int(os.getenv("SMTP_PORT", "465")),
            username=os.getenv("SMTP_USERNAME", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            from_email=os.getenv("SMTP_FROM_EMAIL", "noreply@vetscan.net"),
            from_name=os.getenv("SMTP_FROM_NAME", "VetScan"),
        )

    @property
    def is_configured(self) -> bool:
        """Check if SMTP is properly configured."""
        return bool(self.username and self.password)


# =============================================================================
# EMAIL IMPORT CONFIGURATION
# =============================================================================

@dataclass
class EmailImportSettings:
    """Email import (IMAP) settings."""
    imap_host: str = ""
    imap_port: int = 993
    email_address: str = ""
    email_password: str = ""
    enabled: bool = False
    processed_folder: str = "Processed"
    failed_folder: str = "Failed"
    pdf_max_size_mb: int = 10
    rate_limit: int = 20  # Max imports per hour

    @classmethod
    def from_env(cls) -> 'EmailImportSettings':
        """Load email import settings from environment."""
        return cls(
            imap_host=os.getenv("EMAIL_IMAP_HOST", "imap.hostinger.com"),
            imap_port=int(os.getenv("EMAIL_IMAP_PORT", "993")),
            email_address=os.getenv("EMAIL_ADDRESS", ""),
            email_password=os.getenv("EMAIL_PASSWORD", ""),
            enabled=os.getenv("EMAIL_IMPORT_ENABLED", "false").lower() == "true",
            processed_folder=os.getenv("EMAIL_PROCESSED_FOLDER", "Processed"),
            failed_folder=os.getenv("EMAIL_FAILED_FOLDER", "Failed"),
            pdf_max_size_mb=int(os.getenv("PDF_MAX_SIZE_MB", "10")),
            rate_limit=int(os.getenv("PDF_IMPORT_RATE_LIMIT", "20")),
        )

    @property
    def pdf_max_size_bytes(self) -> int:
        """Maximum PDF size in bytes."""
        return self.pdf_max_size_mb * 1024 * 1024

    @property
    def is_configured(self) -> bool:
        """Check if email import is properly configured."""
        return bool(self.enabled and self.email_address and self.email_password)


# =============================================================================
# AI API CONFIGURATION
# =============================================================================

@dataclass
class AISettings:
    """AI service API settings."""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    openai_model: str = "gpt-5-mini"

    @classmethod
    def from_env(cls) -> 'AISettings':
        """Load AI settings from environment."""
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        )

    @property
    def anthropic_available(self) -> bool:
        """Check if Anthropic API is available."""
        return bool(self.anthropic_api_key)

    @property
    def openai_available(self) -> bool:
        """Check if OpenAI API is available."""
        return bool(self.openai_api_key)


# =============================================================================
# APPLICATION SETTINGS
# =============================================================================

@dataclass
class AppSettings:
    """General application settings."""
    environment: str = "production"
    debug: bool = False
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    @classmethod
    def from_env(cls) -> 'AppSettings':
        """Load app settings from environment."""
        env = os.getenv("ENVIRONMENT", "production")
        return cls(
            environment=env,
            debug=env == "development",
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            host=os.getenv("HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "8000")),
        )

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == "production"


# =============================================================================
# MAIN SETTINGS CLASS
# =============================================================================

@dataclass
class Settings:
    """
    Central settings container for VetScan.

    Aggregates all configuration sections and provides validation.
    """
    paths: PathSettings = field(default_factory=PathSettings)
    auth: AuthSettings = field(default_factory=AuthSettings.from_env)
    smtp: SMTPSettings = field(default_factory=SMTPSettings.from_env)
    email_import: EmailImportSettings = field(default_factory=EmailImportSettings.from_env)
    ai: AISettings = field(default_factory=AISettings.from_env)
    app: AppSettings = field(default_factory=AppSettings.from_env)

    def validate(self) -> List[str]:
        """
        Validate all settings and return list of errors.

        Returns:
            List of validation error messages (empty if all valid)
        """
        errors = []

        # Auth validation
        errors.extend(self.auth.validate())

        # Check paths exist
        if not self.paths.templates_dir.exists():
            errors.append(f"Templates directory not found: {self.paths.templates_dir}")

        return errors

    def validate_or_raise(self):
        """
        Validate settings and raise exception if invalid.

        Raises:
            ValueError: If any settings are invalid
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))


# =============================================================================
# GLOBAL SETTINGS INSTANCE
# =============================================================================

# Singleton settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """
    Get the global settings instance.

    Returns:
        Settings instance with all configuration loaded
    """
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# Convenience alias
settings = get_settings()
