import sys
import unittest


sys.path.insert(0, "/Users/jo/Documents/Claude coding proteins/vet_protein_app/src")

from email_sender import EmailService


class EmailTemplateRenderTests(unittest.TestCase):
    def test_all_email_templates_render(self):
        service = EmailService()
        cases = [
            (
                "signup_confirmation.html",
                {
                    "display_name": "Test User",
                    "email": "user@example.com",
                    "lang": "en",
                },
            ),
            (
                "signup_confirmation.html",
                {
                    "display_name": "Utilizador",
                    "email": "user@example.com",
                    "lang": "pt",
                },
            ),
            (
                "password_reset.html",
                {
                    "display_name": "Test User",
                    "email": "user@example.com",
                    "reset_url": "https://vetscan.net/reset-password?token=abc",
                    "lang": "en",
                },
            ),
            (
                "password_reset.html",
                {
                    "display_name": "Utilizador",
                    "email": "user@example.com",
                    "reset_url": "https://vetscan.net/reset-password?token=abc",
                    "lang": "pt",
                },
            ),
            (
                "account_approved.html",
                {
                    "display_name": "Test User",
                    "email": "user@example.com",
                    "login_url": "https://vetscan.net/login",
                    "lang": "en",
                },
            ),
            (
                "account_approved.html",
                {
                    "display_name": "Utilizador",
                    "email": "user@example.com",
                    "login_url": "https://vetscan.net/login",
                    "lang": "pt",
                },
            ),
            (
                "new_registration.html",
                {
                    "new_user_email": "new@example.com",
                    "new_user_name": "New User",
                    "admin_url": "https://vetscan.net/admin/users",
                    "lang": "en",
                },
            ),
            (
                "new_registration.html",
                {
                    "new_user_email": "new@example.com",
                    "new_user_name": "Novo Utilizador",
                    "admin_url": "https://vetscan.net/admin/users",
                    "lang": "pt",
                },
            ),
            (
                "user_invitation.html",
                {
                    "email": "invitee@example.com",
                    "invite_url": "https://vetscan.net/accept-invite?token=abc",
                    "role": "admin",
                    "invited_by_name": "Admin User",
                    "lang": "en",
                },
            ),
            (
                "user_invitation.html",
                {
                    "email": "invitee@example.com",
                    "invite_url": "https://vetscan.net/accept-invite?token=abc",
                    "role": "user",
                    "invited_by_name": "Administrador",
                    "lang": "pt",
                },
            ),
        ]

        for template_name, context in cases:
            with self.subTest(template_name=template_name, lang=context["lang"]):
                rendered = service._render_template(template_name, **context)
                self.assertIn("<html", rendered.lower())
                self.assertTrue(rendered.strip())


if __name__ == "__main__":
    unittest.main()
