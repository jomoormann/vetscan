import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["ENVIRONMENT"] = "development"
os.environ["AUTH_SECRET_KEY"] = "test-secret-key"
os.environ["AUTH_PASSWORD"] = ""
os.environ["ALLOW_SELF_REGISTRATION"] = "false"

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import database.base as db_base

_ORIGINAL_CONNECT = db_base.sqlite3.connect


def _threaded_connect(*args, **kwargs):
    kwargs.setdefault("check_same_thread", False)
    return _ORIGINAL_CONNECT(*args, **kwargs)


db_base.sqlite3.connect = _threaded_connect

import web_server
from auth import AuthService
from fastapi.testclient import TestClient


class InvitationFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="vetscan-invite-test-"))
        self.uploads_dir = self.tempdir / "uploads"
        self.tmp_uploads_dir = self.tempdir / "upload_tmp"
        self.data_dir = self.tempdir / "data"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_uploads_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if web_server._global_service is not None:
            web_server._global_service.close()
        web_server._global_service = None
        web_server.DB_PATH = self.tempdir / "test.db"
        web_server.UPLOADS_DIR = self.uploads_dir
        web_server.TEMP_UPLOADS_DIR = self.tmp_uploads_dir
        web_server.DATA_DIR = self.data_dir

        self.client_ctx = TestClient(web_server.app)
        self.client = self.client_ctx.__enter__()

    def tearDown(self):
        self.client_ctx.__exit__(None, None, None)
        if web_server._global_service is not None:
            web_server._global_service.close()
        web_server._global_service = None
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _csrf_token(self) -> str:
        self.client.get("/login")
        raw = self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
        return web_server.create_csrf_signed_token(raw)

    def _create_admin(self, email="admin@example.com", password="StrongPassword1"):
        service = web_server.get_service()
        try:
            auth = AuthService(service.db)
            user, error = auth.create_superuser(email, password, "Admin User")
            self.assertIsNotNone(user, error)
            self.assertFalse(error)
            return user
        finally:
            service.close()

    def _login(self, email="admin@example.com", password="StrongPassword1"):
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": self._csrf_token(),
            },
            follow_redirects=False,
        )

    def test_admin_can_invite_and_accept_admin_account(self):
        admin = self._create_admin()
        response = self._login(email=admin.email)
        self.assertEqual(response.status_code, 302)

        sent_invites = []

        with patch.object(web_server.email_service, "is_configured", return_value=True), \
             patch.object(web_server.email_service, "send_user_invitation") as send_invite:
            send_invite.side_effect = lambda to_email, invite_url, role, invited_by_name, lang: sent_invites.append(
                (to_email, invite_url, role, invited_by_name, lang)
            ) or True

            invite_response = self.client.post(
                "/admin/users/invite",
                data={
                    "email": "newadmin@example.com",
                    "role": "admin",
                    "csrf_token": web_server.create_csrf_signed_token(
                        self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
                    ),
                },
                follow_redirects=False,
            )

        self.assertEqual(invite_response.status_code, 302)
        self.assertEqual(invite_response.headers["location"], "/admin/users?invited=1")
        self.assertEqual(len(sent_invites), 1)

        service = web_server.get_service()
        try:
            invited_user = service.db.get_user_by_email("newadmin@example.com")
            self.assertIsNotNone(invited_user)
            self.assertTrue(invited_user.is_superuser)
            self.assertTrue(invited_user.is_approved)
            invites = service.db.list_active_invitations()
            self.assertEqual(len(invites), 1)
            invite = invites[0]
            self.assertEqual(invite.user_id, invited_user.id)
        finally:
            service.close()

        invite_url = sent_invites[0][1]
        token = invite_url.split("token=", 1)[1]

        accept_page = self.client.get(f"/accept-invite?token={token}")
        self.assertEqual(accept_page.status_code, 200)
        self.assertIn("newadmin@example.com", accept_page.text)

        accept_response = self.client.post(
            "/accept-invite",
            data={
                "token": token,
                "display_name": "New Admin",
                "password": "BetterPassword1",
                "password_confirm": "BetterPassword1",
                "csrf_token": web_server.create_csrf_signed_token(
                    self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
                ),
            },
        )
        self.assertEqual(accept_response.status_code, 200)
        self.assertIn("/login", accept_response.text)

        service = web_server.get_service()
        try:
            invited_user = service.db.get_user_by_email("newadmin@example.com")
            self.assertEqual(invited_user.display_name, "New Admin")
            self.assertTrue(invited_user.is_superuser)
            invite = service.db.get_invitation_token(web_server.hash_token(token))
            self.assertIsNotNone(invite.used_at)
        finally:
            service.close()

        login_response = self.client.post(
            "/login",
            data={
                "email": "newadmin@example.com",
                "password": "BetterPassword1",
                "csrf_token": self._csrf_token(),
            },
            follow_redirects=False,
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
