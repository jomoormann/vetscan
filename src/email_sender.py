"""
Email Service for VetScan

Sends emails via SMTP (configured for Hostinger).

Email types:
- Password reset links
- Account approval notifications
- Admin invitation links
- New registration alerts to admins
"""

import os
import ssl
import smtplib
import html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def escape_html(text: str) -> str:
    """Escape HTML special characters in user input"""
    if text is None:
        return ""
    return html.escape(str(text))


def escape_url_for_href(url: str) -> str:
    """
    Safely escape a URL for use in href attributes.

    This prevents XSS attacks while preserving valid URL structure.
    - Validates scheme is http/https only (blocks javascript:, data:, etc.)
    - Escapes quotes and special HTML chars that could break out of attribute
    - Preserves query string parameters (unlike html.escape which corrupts &)
    """
    if url is None:
        return ""

    url = str(url).strip()

    # Validate URL scheme to prevent javascript: and other XSS vectors
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.scheme.lower() not in ('http', 'https'):
            return ""  # Block non-http(s) URLs
    except Exception:
        return ""

    # Escape only characters that could break out of the href attribute
    # Don't escape & as it's valid in URLs
    return url.replace('"', '%22').replace("'", '%27').replace('<', '%3C').replace('>', '%3E')


# =============================================================================
# CONFIGURATION
# =============================================================================

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", "noreply@vetscan.net")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "VetScan")

# Template directory
BASE_DIR = Path(__file__).parent.parent
TEMPLATES_DIR = BASE_DIR / "templates" / "emails"


# =============================================================================
# EMAIL SERVICE
# =============================================================================

class EmailService:
    """
    Service for sending emails via SMTP.
    """

    def __init__(self):
        self.host = SMTP_HOST
        self.port = SMTP_PORT
        self.username = SMTP_USERNAME
        self.password = SMTP_PASSWORD
        self.from_email = SMTP_FROM_EMAIL
        self.from_name = SMTP_FROM_NAME

        # Set up Jinja2 for email templates
        if TEMPLATES_DIR.exists():
            self.template_env = Environment(
                loader=FileSystemLoader(str(TEMPLATES_DIR)),
                autoescape=True
            )
        else:
            self.template_env = None

    def is_configured(self) -> bool:
        """Check if SMTP is properly configured"""
        return bool(self.username and self.password)

    def _create_message(self, to_email: str, subject: str,
                        html_content: str, text_content: Optional[str] = None) -> MIMEMultipart:
        """Create a MIME message"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to_email

        # Plain text version (fallback)
        if text_content:
            msg.attach(MIMEText(text_content, "plain"))

        # HTML version
        msg.attach(MIMEText(html_content, "html"))

        return msg

    def _send(self, msg: MIMEMultipart, to_email: str) -> bool:
        """Send an email message"""
        if not self.is_configured():
            print("SMTP not configured, skipping email send")
            return False

        try:
            # Create SSL context
            context = ssl.create_default_context()

            # Connect using SSL (port 465)
            with smtplib.SMTP_SSL(self.host, self.port, context=context) as server:
                server.login(self.username, self.password)
                server.sendmail(self.from_email, to_email, msg.as_string())

            print(f"Email sent to {to_email}")
            return True

        except smtplib.SMTPAuthenticationError:
            print("SMTP authentication failed")
            return False
        except smtplib.SMTPException as e:
            print(f"SMTP error: {e}")
            return False
        except Exception as e:
            print(f"Email send error: {e}")
            return False

    def _render_template(self, template_name: str, **context) -> str:
        """Render an email template"""
        if not self.template_env:
            raise ValueError("Template directory not found")

        template = self.template_env.get_template(template_name)
        return template.render(**context)

    def send_signup_confirmation(self, to_email: str,
                                  display_name: Optional[str] = None,
                                  lang: str = "en") -> bool:
        """
        Send a signup confirmation email to a new user.

        Args:
            to_email: Recipient email address
            display_name: User's display name (optional)
            lang: Language code for the email
        """
        context = {
            "display_name": display_name or to_email,
            "email": to_email,
            "lang": lang
        }

        try:
            html_content = self._render_template("signup_confirmation.html", **context)
        except Exception as e:
            print(f"Template render error: {e}")
            html_content = self._fallback_signup_confirmation_html(to_email, display_name, lang)

        subject = "VetScan - Registration received" if lang == "en" else "VetScan - Registo recebido"
        text_content = f"Thank you for registering. Your account is pending approval."

        msg = self._create_message(to_email, subject, html_content, text_content)
        return self._send(msg, to_email)

    def send_password_reset(self, to_email: str, reset_url: str,
                            display_name: Optional[str] = None,
                            lang: str = "en") -> bool:
        """
        Send a password reset email.

        Args:
            to_email: Recipient email address
            reset_url: Full URL with reset token
            display_name: User's display name (optional)
            lang: Language code for the email
        """
        context = {
            "reset_url": reset_url,
            "display_name": display_name or to_email,
            "email": to_email,
            "lang": lang
        }

        try:
            html_content = self._render_template("password_reset.html", **context)
        except Exception as e:
            print(f"Template render error: {e}")
            # Fallback to simple HTML
            html_content = self._fallback_password_reset_html(reset_url, display_name, lang)

        subject = "Reset your VetScan password" if lang == "en" else "Repor a sua palavra-passe VetScan"
        text_content = f"Reset your password: {reset_url}"

        msg = self._create_message(to_email, subject, html_content, text_content)
        return self._send(msg, to_email)

    def send_account_approved(self, to_email: str, login_url: str,
                              display_name: Optional[str] = None,
                              lang: str = "en") -> bool:
        """
        Send an account approval notification.

        Args:
            to_email: Recipient email address
            login_url: URL to the login page
            display_name: User's display name (optional)
            lang: Language code for the email
        """
        context = {
            "login_url": login_url,
            "display_name": display_name or to_email,
            "email": to_email,
            "lang": lang
        }

        try:
            html_content = self._render_template("account_approved.html", **context)
        except Exception as e:
            print(f"Template render error: {e}")
            html_content = self._fallback_account_approved_html(login_url, display_name, lang)

        subject = "Your VetScan account has been approved" if lang == "en" else "A sua conta VetScan foi aprovada"
        text_content = f"Your account has been approved. Login at: {login_url}"

        msg = self._create_message(to_email, subject, html_content, text_content)
        return self._send(msg, to_email)

    def send_user_invitation(self, to_email: str, invite_url: str, role: str,
                             invited_by_name: Optional[str] = None,
                             lang: str = "en") -> bool:
        """Send an invitation email for a new admin- or user-level account."""
        context = {
            "invite_url": invite_url,
            "email": to_email,
            "role": role,
            "invited_by_name": invited_by_name,
            "lang": lang,
        }

        try:
            html_content = self._render_template("user_invitation.html", **context)
        except Exception as e:
            print(f"Template render error: {e}")
            html_content = self._fallback_user_invitation_html(
                invite_url, role, invited_by_name, lang
            )

        role_label = "administrator" if role == "admin" else "user"
        if lang == "pt":
            role_label = "administrador" if role == "admin" else "utilizador"
        subject = (
            "You have been invited to VetScan"
            if lang == "en"
            else "Foi convidado para o VetScan"
        )
        text_content = f"Accept your VetScan invitation as {role_label}: {invite_url}"

        msg = self._create_message(to_email, subject, html_content, text_content)
        return self._send(msg, to_email)

    def send_new_registration_alert(self, admin_emails: List[str],
                                    new_user_email: str,
                                    new_user_name: Optional[str],
                                    admin_url: str,
                                    lang: str = "en") -> int:
        """
        Send notification to admins about a new registration.

        Args:
            admin_emails: List of admin email addresses
            new_user_email: Email of the newly registered user
            new_user_name: Display name of the new user
            admin_url: URL to the admin panel
            lang: Language code for the email

        Returns:
            Number of emails successfully sent
        """
        context = {
            "new_user_email": new_user_email,
            "new_user_name": new_user_name or new_user_email,
            "admin_url": admin_url,
            "lang": lang
        }

        try:
            html_content = self._render_template("new_registration.html", **context)
        except Exception as e:
            print(f"Template render error: {e}")
            html_content = self._fallback_new_registration_html(
                new_user_email, new_user_name, admin_url, lang
            )

        subject = "New VetScan registration pending approval" if lang == "en" else "Novo registo VetScan aguarda aprovacao"
        text_content = f"New user registration: {new_user_email}. Approve at: {admin_url}"

        sent_count = 0
        for admin_email in admin_emails:
            msg = self._create_message(admin_email, subject, html_content, text_content)
            if self._send(msg, admin_email):
                sent_count += 1

        return sent_count

    # -------------------------------------------------------------------------
    # Fallback HTML (if templates fail)
    # -------------------------------------------------------------------------

    def _fallback_signup_confirmation_html(self, to_email: str,
                                            display_name: Optional[str],
                                            lang: str) -> str:
        """Fallback HTML for signup confirmation email"""
        safe_name = escape_html(display_name) if display_name else ('utilizador' if lang == 'pt' else 'user')
        safe_email = escape_html(to_email)
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Registo Recebido!</h2>
                <p>Olá {safe_name},</p>
                <p>Obrigado por se registar no VetScan! A sua conta foi criada e está a aguardar aprovação.</p>
                <p><strong>Email:</strong> {safe_email}</p>
                <p><strong>Estado:</strong> Aguarda Aprovação</p>
                <p>Um administrador irá rever o seu pedido em breve. Receberá um email quando a sua conta for aprovada.</p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Registration Received!</h2>
            <p>Hello {safe_name},</p>
            <p>Thank you for registering with VetScan! Your account has been created and is pending approval.</p>
            <p><strong>Email:</strong> {safe_email}</p>
            <p><strong>Status:</strong> Pending Approval</p>
            <p>An administrator will review your request shortly. You'll receive an email when your account is approved.</p>
        </body>
        </html>
        """

    def _fallback_password_reset_html(self, reset_url: str,
                                       display_name: Optional[str],
                                       lang: str) -> str:
        """Fallback HTML for password reset email"""
        safe_name = escape_html(display_name) if display_name else ('utilizador' if lang == 'pt' else 'user')
        safe_url = escape_url_for_href(reset_url)
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Repor Palavra-passe</h2>
                <p>Ola {safe_name},</p>
                <p>Recebemos um pedido para repor a sua palavra-passe VetScan.</p>
                <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Repor Palavra-passe</a></p>
                <p>Este link expira em 1 hora.</p>
                <p>Se nao solicitou esta alteracao, ignore este email.</p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Password Reset</h2>
            <p>Hello {safe_name},</p>
            <p>We received a request to reset your VetScan password.</p>
            <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
            <p>This link expires in 1 hour.</p>
            <p>If you didn't request this, please ignore this email.</p>
        </body>
        </html>
        """

    def _fallback_account_approved_html(self, login_url: str,
                                         display_name: Optional[str],
                                         lang: str) -> str:
        """Fallback HTML for account approved email"""
        safe_name = escape_html(display_name) if display_name else ('utilizador' if lang == 'pt' else 'user')
        safe_url = escape_url_for_href(login_url)
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Conta Aprovada</h2>
                <p>Ola {safe_name},</p>
                <p>A sua conta VetScan foi aprovada! Ja pode iniciar sessao.</p>
                <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Iniciar Sessao</a></p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Account Approved</h2>
            <p>Hello {safe_name},</p>
            <p>Your VetScan account has been approved! You can now sign in.</p>
            <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Sign In</a></p>
        </body>
        </html>
        """

    def _fallback_new_registration_html(self, new_user_email: str,
                                         new_user_name: Optional[str],
                                         admin_url: str,
                                         lang: str) -> str:
        """Fallback HTML for new registration alert"""
        safe_email = escape_html(new_user_email)
        safe_name = escape_html(new_user_name) if new_user_name else ('Nao fornecido' if lang == 'pt' else 'Not provided')
        safe_url = escape_url_for_href(admin_url)
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Novo Registo Pendente</h2>
                <p>Um novo utilizador registou-se no VetScan:</p>
                <p><strong>Email:</strong> {safe_email}</p>
                <p><strong>Nome:</strong> {safe_name}</p>
                <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Rever no Painel de Admin</a></p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>New Registration Pending</h2>
            <p>A new user has registered on VetScan:</p>
            <p><strong>Email:</strong> {safe_email}</p>
            <p><strong>Name:</strong> {safe_name}</p>
            <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Review in Admin Panel</a></p>
        </body>
        </html>
        """

    def _fallback_user_invitation_html(self, invite_url: str, role: str,
                                       invited_by_name: Optional[str],
                                       lang: str) -> str:
        """Fallback HTML for user invitation emails."""
        safe_url = escape_url_for_href(invite_url)
        safe_inviter = escape_html(invited_by_name) if invited_by_name else ("a clinic administrator" if lang == "en" else "um administrador da clínica")
        if lang == "pt":
            role_label = "administrador" if role == "admin" else "utilizador"
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Convite VetScan</h2>
                <p>Recebeu um convite de {safe_inviter} para criar uma conta VetScan como {role_label}.</p>
                <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Aceitar convite</a></p>
                <p>Este link expira em 7 dias.</p>
            </body>
            </html>
            """
        role_label = "administrator" if role == "admin" else "user"
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>VetScan Invitation</h2>
            <p>You have been invited by {safe_inviter} to create a VetScan account as {role_label}.</p>
            <p><a href="{safe_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Accept invitation</a></p>
            <p>This link expires in 7 days.</p>
        </body>
        </html>
        """


# Singleton instance
email_service = EmailService()
