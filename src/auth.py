"""
Authentication Service for VetScan

Provides:
- Password hashing with bcrypt
- User authentication and session management
- Password reset token generation
- Migration from legacy env-based auth
"""

import re
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple

from passlib.context import CryptContext

from models import Database, User, PasswordResetToken, InvitationToken


# =============================================================================
# PASSWORD HASHING
# =============================================================================

# Configure passlib with bcrypt, 12 rounds (~250ms hash time)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def validate_password(password: str, min_length: int = 8) -> Tuple[bool, str]:
    """
    Validate password meets security requirements.

    Requirements:
    - Minimum configured length
    - At least 1 uppercase letter
    - At least 1 lowercase letter
    - At least 1 number

    Returns:
        Tuple of (is_valid, error_message)
    """
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least 1 uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least 1 lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least 1 number"
    return True, ""


def validate_email(email: str) -> Tuple[bool, str]:
    """
    Validate email format.

    Returns:
        Tuple of (is_valid, error_message)
    """
    email = email.strip()
    if not email:
        return False, "Email is required"

    # Basic email regex
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format"

    return True, ""


# =============================================================================
# TOKEN GENERATION
# =============================================================================

def generate_reset_token() -> str:
    """Generate a secure random token for password reset"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a token for storage in database"""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_invitation_token() -> str:
    """Generate a secure random token for user invitations."""
    return secrets.token_urlsafe(32)


# =============================================================================
# AUTH SERVICE
# =============================================================================

class AuthService:
    """
    Authentication service for managing users and sessions.
    """

    def __init__(self, db: Database):
        self.db = db

    def register_user(self, email: str, password: str,
                      display_name: Optional[str] = None) -> Tuple[Optional[User], str]:
        """
        Register a new user.

        New users are created with is_approved=False and must be
        approved by an admin before they can log in.

        Returns:
            Tuple of (user, error_message)
        """
        # Validate email
        is_valid, error = validate_email(email)
        if not is_valid:
            return None, error

        # Validate password
        is_valid, error = validate_password(password, min_length=12)
        if not is_valid:
            return None, error

        # Check if email already exists
        email_normalized = email.lower().strip()
        existing = self.db.get_user_by_email(email)
        if existing:
            return None, "An account with this email already exists"

        # Create user
        user = User(
            email=email.strip(),
            email_normalized=email_normalized,
            password_hash=hash_password(password),
            display_name=display_name.strip() if display_name else None,
            is_active=True,
            is_approved=False,
            is_superuser=False
        )

        user.id = self.db.create_user(user)
        return user, ""

    def authenticate(self, email: str, password: str) -> Tuple[Optional[User], str]:
        """
        Authenticate a user by email and password.

        Returns:
            Tuple of (user, error_code)
            error_code can be: '', 'invalid_credentials', 'disabled', 'pending_approval'
        """
        user = self.db.get_user_by_email(email)

        if not user:
            return None, "invalid_credentials"

        if not verify_password(password, user.password_hash):
            return None, "invalid_credentials"

        if not user.is_active:
            return None, "disabled"

        if not user.is_approved:
            return None, "pending_approval"

        # Update last login
        self.db.update_user(user.id, last_login_at=datetime.now().isoformat())

        return user, ""

    def create_password_reset_token(self, email: str) -> Tuple[Optional[str], str]:
        """
        Create a password reset token for a user.

        Returns:
            Tuple of (plain_token, error_message)
            The plain token should be sent to the user via email.
        """
        user = self.db.get_user_by_email(email)

        if not user:
            # Don't reveal if email exists
            return None, ""

        if not user.is_active:
            return None, ""

        # Generate token
        plain_token = generate_reset_token()
        token_hash = hash_token(plain_token)
        expires_at = datetime.now() + timedelta(hours=1)

        # Store in database
        self.db.create_password_reset_token(user.id, token_hash, expires_at)

        return plain_token, ""

    def create_invited_user(self, email: str, role: str,
                            invited_by_user_id: int) -> Tuple[Optional[User], Optional[str], str]:
        """Create a pre-approved invited user and return a plain invitation token."""
        is_valid, error = validate_email(email)
        if not is_valid:
            return None, None, error

        normalized_role = (role or "user").strip().lower()
        if normalized_role not in {"user", "admin"}:
            return None, None, "Invalid role"

        email_normalized = email.lower().strip()
        existing = self.db.get_user_by_email(email_normalized)
        if existing:
            active_invitation = next(
                (
                    invite
                    for invite in self.db.list_active_invitations()
                    if invite.user_id == existing.id
                ),
                None,
            )
            if not active_invitation or existing.last_login_at or existing.display_name:
                return None, None, "An account with this email already exists"

            self.db.update_user(
                existing.id,
                is_active=True,
                is_approved=True,
                is_superuser=(normalized_role == "admin"),
                approved_at=datetime.now().isoformat(),
                approved_by_user_id=invited_by_user_id,
            )
            user = self.db.get_user(existing.id)
        else:
            temporary_password_hash = hash_password(secrets.token_urlsafe(24))
            user = User(
                email=email.strip(),
                email_normalized=email_normalized,
                password_hash=temporary_password_hash,
                display_name=None,
                is_active=True,
                is_approved=True,
                is_superuser=(normalized_role == "admin"),
                approved_at=datetime.now().isoformat(),
                approved_by_user_id=invited_by_user_id,
            )
            user.id = self.db.create_user(user)

        if not user:
            return None, None, "Could not create invited user"

        plain_token = generate_invitation_token()
        self.db.create_invitation_token(
            user_id=user.id,
            invited_email=user.email,
            invited_role=normalized_role,
            token_hash=hash_token(plain_token),
            expires_at=datetime.now() + timedelta(days=7),
            invited_by_user_id=invited_by_user_id,
        )
        return user, plain_token, ""

    def accept_invitation(self, token: str, display_name: str,
                          password: str) -> Tuple[Optional[User], str]:
        """Accept an invitation by setting the user's name and password."""
        cleaned_name = (display_name or "").strip()
        if not cleaned_name:
            return None, "Name is required"

        is_valid, error = validate_password(password, min_length=12)
        if not is_valid:
            return None, error

        invitation = self.db.get_invitation_token(hash_token(token))
        if not invitation:
            return None, "Invalid or expired invitation link"
        if invitation.used_at:
            return None, "Invitation link has already been used"

        expires_at = invitation.expires_at
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at and expires_at < datetime.now():
            return None, "Invitation link has expired"

        user = self.db.get_user(invitation.user_id)
        if not user or not user.is_active:
            return None, "Invitation is no longer active"

        self.db.update_user(
            user.id,
            display_name=cleaned_name,
            password_hash=hash_password(password),
            is_approved=True,
            is_superuser=(invitation.invited_role == "admin"),
        )
        self.db.mark_invitation_used(invitation.id)
        self.db.revoke_all_user_sessions(user.id)
        self.db.cleanup_expired_invitations()
        return self.db.get_user(user.id), ""

    def reset_password(self, token: str, new_password: str) -> Tuple[bool, str]:
        """
        Reset a user's password using a reset token.

        Returns:
            Tuple of (success, error_message)
        """
        # Validate new password
        is_valid, error = validate_password(new_password, min_length=12)
        if not is_valid:
            return False, error

        # Find token
        token_hash = hash_token(token)
        reset_token = self.db.get_password_reset_token(token_hash)

        if not reset_token:
            return False, "Invalid or expired reset link"

        # Check if token is expired
        if isinstance(reset_token.expires_at, str):
            expires_at = datetime.fromisoformat(reset_token.expires_at)
        else:
            expires_at = reset_token.expires_at

        if expires_at < datetime.now():
            return False, "Reset link has expired"

        # Check if already used
        if reset_token.used_at:
            return False, "Reset link has already been used"

        # Update password
        user = self.db.get_user(reset_token.user_id)
        if not user:
            return False, "User not found"

        self.db.update_user(user.id, password_hash=hash_password(new_password))
        self.db.mark_token_used(reset_token.id)
        self.db.revoke_all_user_sessions(user.id)

        # Cleanup old tokens
        self.db.cleanup_expired_tokens()

        return True, ""

    def change_password(self, user_id: int, current_password: str,
                        new_password: str) -> Tuple[bool, str]:
        """
        Change a user's password (requires current password).

        Returns:
            Tuple of (success, error_message)
        """
        user = self.db.get_user(user_id)
        if not user:
            return False, "User not found"

        if not verify_password(current_password, user.password_hash):
            return False, "Current password is incorrect"

        is_valid, error = validate_password(new_password, min_length=12)
        if not is_valid:
            return False, error

        self.db.update_user(user_id, password_hash=hash_password(new_password))
        self.db.revoke_all_user_sessions(user_id)
        return True, ""

    def create_superuser(self, email: str, password: str,
                         display_name: Optional[str] = None) -> Tuple[Optional[User], str]:
        """
        Create a superuser account (already approved).

        This is used for initial setup or admin-created accounts.
        """
        # Validate inputs
        is_valid, error = validate_email(email)
        if not is_valid:
            return None, error

        is_valid, error = validate_password(password, min_length=12)
        if not is_valid:
            return None, error

        # Check if email already exists
        existing = self.db.get_user_by_email(email)
        if existing:
            return None, "An account with this email already exists"

        email_normalized = email.lower().strip()

        user = User(
            email=email.strip(),
            email_normalized=email_normalized,
            password_hash=hash_password(password),
            display_name=display_name.strip() if display_name else None,
            is_active=True,
            is_approved=True,
            is_superuser=True
        )

        user.id = self.db.create_user(user)
        return user, ""

    def approve_user(self, user_id: int, approved_by_id: int) -> bool:
        """Approve a pending user account"""
        return self.db.approve_user(user_id, approved_by_id)

    def disable_user(self, user_id: int) -> bool:
        """Disable a user account"""
        return self.db.disable_user(user_id)

    def enable_user(self, user_id: int) -> bool:
        """Re-enable a disabled user account"""
        return self.db.enable_user(user_id)


# =============================================================================
# LEGACY MIGRATION
# =============================================================================

def migrate_legacy_auth(db: Database, legacy_username: str, legacy_password: str,
                        admin_email: str) -> Optional[User]:
    """
    Migrate from legacy single-user env var auth to multi-user database auth.

    This is called during startup if:
    - No users exist in the database
    - AUTH_USERNAME and AUTH_PASSWORD env vars are set
    - An admin email is provided

    Creates a superuser with the legacy password and the provided email.

    Returns:
        The created superuser, or None if migration failed
    """
    # Check if any users already exist
    if db.user_count() > 0:
        return None

    if not legacy_password:
        return None

    if not admin_email:
        return None

    auth_service = AuthService(db)

    # Create superuser with legacy credentials
    user, error = auth_service.create_superuser(
        email=admin_email,
        password=legacy_password,
        display_name=legacy_username
    )

    if error:
        print(f"Migration error: {error}")
        return None

    print(f"Created superuser: {admin_email}")
    return user
