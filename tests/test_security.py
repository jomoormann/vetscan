import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
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
from email_importer import RateLimiter
from fastapi.testclient import TestClient
from models import Animal, ClinicalNote


class SecurityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="vetscan-security-test-"))
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

    def _create_user(self, email="vet@example.com", password="StrongPassword1"):
        service = web_server.get_service()
        try:
            auth_service = AuthService(service.db)
            user, error = auth_service.register_user(email, password, "Dr Vet")
            self.assertIsNotNone(user, error)
            self.assertFalse(error)
            service.db.update_user(user.id, is_approved=True, is_superuser=True)
            return user
        finally:
            service.close()

    def _login(self, email="vet@example.com", password="StrongPassword1"):
        csrf_token = self._csrf_token()
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )

    def test_api_requires_auth_and_register_is_disabled(self):
        self._create_user()
        response = self.client.get("/api/search?q=fi")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Authentication required")

        register_response = self.client.get("/register")
        self.assertEqual(register_response.status_code, 404)

    def test_login_logout_and_persistent_lockout(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response.headers["location"], "/")

        service = web_server.get_service()
        try:
            active_sessions = service.db.conn.execute(
                "SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NULL"
            ).fetchone()[0]
            self.assertEqual(active_sessions, 1)
        finally:
            service.close()

        csrf_token = web_server.create_csrf_signed_token(
            self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
        )
        logout_response = self.client.post(
            "/logout",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(logout_response.status_code, 302)
        self.assertEqual(logout_response.headers["location"], "/login")

        api_response = self.client.get("/api/search?q=fi")
        self.assertEqual(api_response.status_code, 401)

        service = web_server.get_service()
        try:
            for _ in range(5):
                service.db.create_auth_event(web_server.AuthEvent(
                    event_type="login",
                    email_normalized="blocked@example.com",
                    ip_address="testclient",
                    success=False,
                ))
        finally:
            service.close()

        blocked_login = self._login(email="blocked@example.com", password="WrongPassword1")
        self.assertEqual(blocked_login.status_code, 429)
        self.assertIn("error-message", blocked_login.text)

    def test_delete_requires_csrf_and_language_redirect_is_safe(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Finn", species="Canine"))
            note_id = service.db.create_clinical_note(ClinicalNote(
                animal_id=animal_id,
                title="Test",
                content="Clinical note",
                note_date=date.today(),
                author_user_id=user.id,
                updated_by_user_id=user.id,
            ))
        finally:
            service.close()

        missing_csrf = self.client.request(
            "DELETE",
            f"/animal/{animal_id}/clinical-note/{note_id}",
        )
        self.assertEqual(missing_csrf.status_code, 403)

        csrf_token = web_server.create_csrf_signed_token(
            self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
        )
        delete_response = self.client.request(
            "DELETE",
            f"/animal/{animal_id}/clinical-note/{note_id}",
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertTrue(delete_response.json()["success"])

        redirect_response = self.client.get(
            "/set-language/en",
            headers={"referer": "https://evil.example/phish"},
            follow_redirects=False,
        )
        self.assertEqual(redirect_response.status_code, 303)
        self.assertEqual(redirect_response.headers["location"], "/")

    def test_upload_uses_temp_storage_before_persisting(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        sample_pdf = Path(__file__).resolve().parents[2] / "new reports" / "Finn.pdf"
        csrf_token = web_server.create_csrf_signed_token(
            self.client.cookies.get(web_server.CSRF_COOKIE_NAME)
        )
        with sample_pdf.open("rb") as handle:
            response = self.client.post(
                "/upload",
                data={"csrf_token": csrf_token},
                headers={"X-CSRF-Token": csrf_token},
                files={"file": ("Finn.pdf", handle, "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(sorted(path.name for path in self.uploads_dir.iterdir()), ["Finn.pdf"])
        self.assertEqual(list(self.tmp_uploads_dir.iterdir()), [])

    def test_email_import_rate_limit_reads_database_history(self):
        service = web_server.get_service()
        try:
            service.db.conn.execute("""
                INSERT INTO email_import_log (
                    email_uid, email_subject, email_from, attachment_name,
                    validation_result, import_success, error_message,
                    report_number, animal_id, session_id, import_timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "uid-1",
                "subject",
                "from@example.com",
                "report.pdf",
                "queued_manual_assignment",
                1,
                None,
                "R1",
                None,
                None,
                datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            ))
            service.db.conn.commit()
        finally:
            service.close()

        limiter = RateLimiter(1, str(web_server.DB_PATH))
        self.assertFalse(limiter.can_proceed())
        self.assertEqual(limiter.remaining, 0)


if __name__ == "__main__":
    unittest.main()
