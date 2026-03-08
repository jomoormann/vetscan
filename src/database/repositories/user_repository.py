"""
User Repository for VetScan

Handles database operations for users, password reset tokens,
server-side sessions, and auth-event logging.
"""

from datetime import datetime, timedelta
from typing import List, Optional

from models.domain import (
    User, PasswordResetToken, InvitationToken, UserSession, AuthEvent
)


def _sqlite_timestamp(value: datetime) -> str:
    """Format datetimes consistently for SQLite comparisons."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


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
        allowed_fields = {
            'email', 'email_normalized', 'display_name', 'is_active', 'is_approved',
            'is_superuser', 'password_hash', 'last_login_at', 'approved_at',
            'approved_by_user_id'
        }
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
        """, (user_id, token_hash, _sqlite_timestamp(expires_at)))
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
            WHERE datetime(expires_at) < datetime('now') OR used_at IS NOT NULL
        """)
        self.db.conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Invitation Tokens
    # -------------------------------------------------------------------------

    def create_invitation_token(self, user_id: int, invited_email: str,
                                invited_role: str, token_hash: str,
                                expires_at: datetime,
                                invited_by_user_id: Optional[int] = None) -> int:
        """Create an invitation token for an admin-created account."""
        cursor = self.db.conn.execute("""
            INSERT INTO invitation_tokens (
                user_id, invited_email, invited_role, invited_by_user_id,
                token_hash, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            invited_email,
            invited_role,
            invited_by_user_id,
            token_hash,
            _sqlite_timestamp(expires_at),
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_invitation_token(self, token_hash: str) -> Optional[InvitationToken]:
        """Get an invitation token by its hash."""
        cursor = self.db.conn.execute(
            "SELECT * FROM invitation_tokens WHERE token_hash = ?",
            (token_hash,),
        )
        row = cursor.fetchone()
        if row:
            return InvitationToken(**dict(row))
        return None

    def list_active_invitations(self) -> List[InvitationToken]:
        """List invitation tokens that are not yet used or expired."""
        cursor = self.db.conn.execute("""
            SELECT *
            FROM invitation_tokens
            WHERE used_at IS NULL
              AND datetime(expires_at) >= datetime('now')
            ORDER BY created_at DESC
        """)
        return [InvitationToken(**dict(row)) for row in cursor.fetchall()]

    def mark_invitation_used(self, invitation_id: int) -> bool:
        """Mark an invitation as used."""
        cursor = self.db.conn.execute("""
            UPDATE invitation_tokens
            SET used_at = CURRENT_TIMESTAMP
            WHERE id = ? AND used_at IS NULL
        """, (invitation_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def cleanup_expired_invitations(self) -> int:
        """Remove expired invitation tokens while keeping used ones for audit."""
        cursor = self.db.conn.execute("""
            DELETE FROM invitation_tokens
            WHERE datetime(expires_at) < datetime('now')
        """)
        self.db.conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Server-side Sessions
    # -------------------------------------------------------------------------

    def create_session(self, user_id: Optional[int], session_token_hash: str,
                       expires_at: datetime, created_ip: Optional[str] = None,
                       last_seen_ip: Optional[str] = None,
                       user_agent_hash: Optional[str] = None) -> int:
        """Create a server-side session."""
        cursor = self.db.conn.execute("""
            INSERT INTO user_sessions (
                user_id, session_token_hash, created_ip, last_seen_ip,
                user_agent_hash, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            session_token_hash,
            created_ip,
            last_seen_ip,
            user_agent_hash,
            _sqlite_timestamp(expires_at),
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_session_by_hash(self, session_token_hash: str) -> Optional[UserSession]:
        """Fetch a session by its hashed token."""
        cursor = self.db.conn.execute(
            "SELECT * FROM user_sessions WHERE session_token_hash = ?",
            (session_token_hash,),
        )
        row = cursor.fetchone()
        if row:
            return UserSession(**dict(row))
        return None

    def touch_session(self, session_id: int, last_seen_ip: Optional[str] = None) -> bool:
        """Update last-seen metadata for an active session."""
        cursor = self.db.conn.execute("""
            UPDATE user_sessions
            SET last_seen_at = CURRENT_TIMESTAMP,
                last_seen_ip = COALESCE(?, last_seen_ip)
            WHERE id = ? AND revoked_at IS NULL
        """, (last_seen_ip, session_id))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def revoke_session(self, session_id: int) -> bool:
        """Revoke a single session."""
        cursor = self.db.conn.execute("""
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE id = ? AND revoked_at IS NULL
        """, (session_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def revoke_session_by_hash(self, session_token_hash: str) -> bool:
        """Revoke a session by its hashed token."""
        cursor = self.db.conn.execute("""
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE session_token_hash = ? AND revoked_at IS NULL
        """, (session_token_hash,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def revoke_user_sessions(self, user_id: int) -> int:
        """Revoke all active sessions for a user."""
        cursor = self.db.conn.execute("""
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND revoked_at IS NULL
        """, (user_id,))
        self.db.conn.commit()
        return cursor.rowcount

    def cleanup_expired_sessions(self, idle_timeout_hours: int = 24) -> int:
        """Revoke sessions that are expired or idle for too long."""
        idle_cutoff = _sqlite_timestamp(
            datetime.utcnow() - timedelta(hours=idle_timeout_hours)
        )
        cursor = self.db.conn.execute("""
            UPDATE user_sessions
            SET revoked_at = CURRENT_TIMESTAMP
            WHERE revoked_at IS NULL
              AND (
                    datetime(expires_at) <= datetime('now')
                    OR datetime(COALESCE(last_seen_at, created_at)) <= datetime(?)
              )
        """, (idle_cutoff,))
        self.db.conn.commit()
        return cursor.rowcount

    # -------------------------------------------------------------------------
    # Auth Events
    # -------------------------------------------------------------------------

    def create_auth_event(self, event: AuthEvent) -> int:
        """Insert an authentication event for audit and rate limiting."""
        cursor = self.db.conn.execute("""
            INSERT INTO auth_events (
                event_type, email_normalized, ip_address, user_id, success, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (
            event.event_type,
            event.email_normalized,
            event.ip_address,
            event.user_id,
            1 if event.success else 0,
            event.metadata_json,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def count_auth_events(self, event_type: str, since: datetime,
                          success: Optional[bool] = None,
                          email_normalized: Optional[str] = None,
                          ip_address: Optional[str] = None) -> int:
        """Count auth events matching the supplied filters."""
        clauses = ["event_type = ?", "created_at >= ?"]
        params = [event_type, _sqlite_timestamp(since)]

        if success is not None:
            clauses.append("success = ?")
            params.append(1 if success else 0)
        if email_normalized is not None:
            clauses.append("email_normalized = ?")
            params.append(email_normalized)
        if ip_address is not None:
            clauses.append("ip_address = ?")
            params.append(ip_address)

        normalized_clauses = [
            f"datetime({clause.split(' >= ')[0]}) >= datetime(?)"
            if clause == "created_at >= ?" else clause
            for clause in clauses
        ]
        cursor = self.db.conn.execute(
            f"SELECT COUNT(*) FROM auth_events WHERE {' AND '.join(normalized_clauses)}",
            tuple(params),
        )
        return cursor.fetchone()[0]

    def cleanup_old_auth_events(self, retention_days: int = 90) -> int:
        """Delete old auth audit rows outside the retention window."""
        cutoff = _sqlite_timestamp(datetime.utcnow() - timedelta(days=retention_days))
        cursor = self.db.conn.execute(
            "DELETE FROM auth_events WHERE datetime(created_at) < datetime(?)",
            (cutoff,),
        )
        self.db.conn.commit()
        return cursor.rowcount
