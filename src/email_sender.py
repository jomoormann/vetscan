"""
Email Service for VetScan

Sends emails via SMTP (configured for Hostinger).

Email types:
- Password reset links
- Account approval notifications
- New registration alerts to admins
"""

import os
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


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

    def _fallback_password_reset_html(self, reset_url: str,
                                       display_name: Optional[str],
                                       lang: str) -> str:
        """Fallback HTML for password reset email"""
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Repor Palavra-passe</h2>
                <p>Ola {display_name or 'utilizador'},</p>
                <p>Recebemos um pedido para repor a sua palavra-passe VetScan.</p>
                <p><a href="{reset_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Repor Palavra-passe</a></p>
                <p>Este link expira em 1 hora.</p>
                <p>Se nao solicitou esta alteracao, ignore este email.</p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Password Reset</h2>
            <p>Hello {display_name or 'user'},</p>
            <p>We received a request to reset your VetScan password.</p>
            <p><a href="{reset_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Reset Password</a></p>
            <p>This link expires in 1 hour.</p>
            <p>If you didn't request this, please ignore this email.</p>
        </body>
        </html>
        """

    def _fallback_account_approved_html(self, login_url: str,
                                         display_name: Optional[str],
                                         lang: str) -> str:
        """Fallback HTML for account approved email"""
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Conta Aprovada</h2>
                <p>Ola {display_name or 'utilizador'},</p>
                <p>A sua conta VetScan foi aprovada! Ja pode iniciar sessao.</p>
                <p><a href="{login_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Iniciar Sessao</a></p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>Account Approved</h2>
            <p>Hello {display_name or 'user'},</p>
            <p>Your VetScan account has been approved! You can now sign in.</p>
            <p><a href="{login_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Sign In</a></p>
        </body>
        </html>
        """

    def _fallback_new_registration_html(self, new_user_email: str,
                                         new_user_name: Optional[str],
                                         admin_url: str,
                                         lang: str) -> str:
        """Fallback HTML for new registration alert"""
        if lang == "pt":
            return f"""
            <html>
            <body style="font-family: sans-serif; padding: 20px;">
                <h2>Novo Registo Pendente</h2>
                <p>Um novo utilizador registou-se no VetScan:</p>
                <p><strong>Email:</strong> {new_user_email}</p>
                <p><strong>Nome:</strong> {new_user_name or 'Nao fornecido'}</p>
                <p><a href="{admin_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Rever no Painel de Admin</a></p>
            </body>
            </html>
            """
        return f"""
        <html>
        <body style="font-family: sans-serif; padding: 20px;">
            <h2>New Registration Pending</h2>
            <p>A new user has registered on VetScan:</p>
            <p><strong>Email:</strong> {new_user_email}</p>
            <p><strong>Name:</strong> {new_user_name or 'Not provided'}</p>
            <p><a href="{admin_url}" style="background: #135E4B; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">Review in Admin Panel</a></p>
        </body>
        </html>
        """


# Singleton instance
email_service = EmailService()
