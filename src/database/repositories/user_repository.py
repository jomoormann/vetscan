"""
User Repository for VetScan

Handles database operations for User and PasswordResetToken entities.
"""

from datetime import datetime
from typing import List, Optional

from models.domain import User, PasswordResetToken


class UserRepository:
    """Repository for User and authentication-related CRUD operations."""

    def __init__(self, db):
        """
        Initialize repository with database connection.

        Args:
            db: Database instance with active connection
        """
        self.db = db

    # -------------------------------------------------------------------------
    # User CRUD
    # -------------------------------------------------------------------------

    def create(self, user: User) -> int:
        """
        Insert a new user and return their ID.

        Args:
            user: User instance to create

        Returns:
            ID of the created user
        """
        cursor = self.db.conn.execute("""
            INSERT INTO users (email, email_normalized, password_hash, display_name,
                              is_active, is_approved, is_superuser)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user.email, user.email_normalized, user.password_hash,
              user.display_name, user.is_active, user.is_approved, user.is_superuser))
        self.db.conn.commit()
        return cursor.lastrowid

    def get(self, user_id: int) -> Optional[User]:
        """Get a user by ID."""
        cursor = self.db.conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return User(**dict(row))
        return None

    def get_by_email(self, email: str) -> Optional[User]:
        """Get a user by email (case-insensitive)."""
        email_normalized = email.lower().strip()
        cursor = self.db.conn.execute(
            "SELECT * FROM users WHERE email_normalized = ?", (email_normalized,))
        row = cursor.fetchone()
        if row:
            return User(**dict(row))
        return None

    def list_all(self, include_inactive: bool = False) -> List[User]:
        """List all users, optionally including inactive ones."""
        if include_inactive:
            cursor = self.db.conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC")
        else:
            cursor = self.db.conn.execute(
                "SELECT * FROM users WHERE is_active = 1 ORDER BY created_at DESC")
        return [User(**dict(row)) for row in cursor.fetchall()]

    def get_pending(self) -> List[User]:
        """Get users who are active but not yet approved."""
        cursor = self.db.conn.execute("""
            SELECT * FROM users
            WHERE is_active = 1 AND is_approved = 0
            ORDER BY created_at ASC
        """)
        return [User(**dict(row)) for row in cursor.fetchall()]

    def get_superusers(self) -> List[User]:
        """Get all active superuser accounts."""
        cursor = self.db.conn.execute("""
            SELECT * FROM users
            WHERE is_superuser = 1 AND is_active = 1
            ORDER BY created_at ASC
        """)
        return [User(**dict(row)) for row in cursor.fetchall()]

    def update(self, user_id: int, **kwargs) -> bool:
        """
        Update user fields.

        Args:
            user_id: ID of the user to update
            **kwargs: Fields to update

        Returns:
            True if update was successful
        """
        allowed_fields = {'display_name', 'is_active', 'is_approved', 'is_superuser',
                         'password_hash', 'last_login_at', 'approved_at', 'approved_by_user_id'}
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not update_fields:
            return False

        update_fields['updated_at'] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in update_fields.keys())
        values = list(update_fields.values()) + [user_id]

        cursor = self.db.conn.execute(
            f"UPDATE users SET {set_clause} WHERE id = ?", values)
        self.db.conn.commit()
        return cursor.rowcount > 0

    def approve(self, user_id: int, approved_by_user_id: int) -> bool:
        """Approve a user account."""
        return self.update(
            user_id,
            is_approved=True,
            approved_at=datetime.now().isoformat(),
            approved_by_user_id=approved_by_user_id
        )

    def disable(self, user_id: int) -> bool:
        """Disable a user account."""
        return self.update(user_id, is_active=False)

    def enable(self, user_id: int) -> bool:
        """Re-enable a user account."""
        return self.update(user_id, is_active=True)

    def count(self) -> int:
        """Get total number of users."""
        cursor = self.db.conn.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

    def delete(self, user_id: int) -> bool:
        """Delete a user."""
        cursor = self.db.conn.execute(
            "DELETE FROM users WHERE id = ?", (user_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Password Reset Tokens
    # -------------------------------------------------------------------------

    def create_reset_token(self, user_id: int, token_hash: str,
                          expires_at: datetime) -> int:
        """Create a password reset token."""
        cursor = self.db.conn.execute("""
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
            VALUES (?, ?, ?)
        """, (user_id, token_hash, expires_at.isoformat()))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_reset_token(self, token_hash: str) -> Optional[PasswordResetToken]:
        """Get a password reset token by its hash."""
        cursor = self.db.conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token_hash = ?", (token_hash,))
        row = cursor.fetchone()
        if row:
            return PasswordResetToken(**dict(row))
        return None

    def mark_token_used(self, token_id: int) -> bool:
        """Mark a password reset token as used."""
        cursor = self.db.conn.execute("""
            UPDATE password_reset_tokens
            SET used_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (token_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def cleanup_expired_tokens(self) -> int:
        """Remove expired or used password reset tokens."""
        cursor = self.db.conn.execute("""
            DELETE FROM password_reset_tokens
            WHERE expires_at < CURRENT_TIMESTAMP OR used_at IS NOT NULL
        """)
        self.db.conn.commit()
        return cursor.rowcount
