import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

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


class ProfilePageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="vetscan-profile-test-"))
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

    def _session_csrf_token(self) -> str:
        raw = self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
        return web_server.create_csrf_signed_token(raw)

    def _create_user(self, email="user@example.com", password="StrongPassword1", name="Staff User"):
        service = web_server.get_service()
        try:
            auth = AuthService(service.db)
            user, error = auth.create_superuser(email, password, name)
            self.assertIsNotNone(user, error)
            self.assertFalse(error)
            return user
        finally:
            service.close()

    def _login(self, email="user@example.com", password="StrongPassword1"):
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": self._csrf_token(),
            },
            follow_redirects=False,
        )

    def test_profile_page_updates_identity_and_password(self):
        user = self._create_user()
        response = self._login(email=user.email)
        self.assertEqual(response.status_code, 302)

        page = self.client.get("/profile")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Staff User", page.text)

        update_response = self.client.post(
            "/profile",
            data={
                "display_name": "Updated User",
                "email": "updated@example.com",
                "csrf_token": self._session_csrf_token(),
            },
            follow_redirects=False,
        )
        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(update_response.headers["location"], "/profile?saved=profile")

        service = web_server.get_service()
        try:
            updated = service.db.get_user(user.id)
            self.assertEqual(updated.display_name, "Updated User")
            self.assertEqual(updated.email, "updated@example.com")
            self.assertEqual(updated.email_normalized, "updated@example.com")
        finally:
            service.close()

        password_response = self.client.post(
            "/profile/password",
            data={
                "current_password": "StrongPassword1",
                "new_password": "NewStrongPassword1",
                "confirm_password": "NewStrongPassword1",
                "csrf_token": self._session_csrf_token(),
            },
            follow_redirects=False,
        )
        self.assertEqual(password_response.status_code, 302)
        self.assertEqual(password_response.headers["location"], "/profile?saved=password")

        relogin = self.client.post(
            "/login",
            data={
                "email": "updated@example.com",
                "password": "NewStrongPassword1",
                "csrf_token": self._csrf_token(),
            },
            follow_redirects=False,
        )
        self.assertEqual(relogin.status_code, 302)
        self.assertEqual(relogin.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
