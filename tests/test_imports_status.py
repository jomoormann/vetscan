import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
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
from models import Animal, TestSession


class ImportStatusTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="vetscan-imports-test-"))
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
        return self.client.post(
            "/login",
            data={
                "email": email,
                "password": password,
                "csrf_token": self._csrf_token(),
            },
            follow_redirects=False,
        )

    def test_imports_page_shows_assigned_after_manual_review(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Bobby", species="Canine"))
            session_id = service.db.create_test_session(TestSession(
                animal_id=animal_id,
                report_number="26000620",
                test_date=date.today(),
                source_system="vedis",
                report_type="vedis_cytology",
            ))

            report_id = service.db.conn.execute("""
                INSERT INTO unassigned_reports (
                    filename,
                    pdf_path,
                    source_system,
                    report_type,
                    report_number,
                    status
                )
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (
                "Boby (4).pdf",
                str(self.tempdir / "Boby (4).pdf"),
                "cvs",
                "cvs_analyzer",
                "26000620",
            )).lastrowid

            service.db.conn.execute("""
                INSERT INTO email_import_log (
                    email_uid,
                    email_subject,
                    email_from,
                    attachment_name,
                    validation_result,
                    import_success,
                    report_number
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "5",
                "test import",
                "reports@example.com",
                "Boby (4).pdf",
                "queued_manual_assignment",
                1,
                "26000620",
            ))

            service.db.mark_unassigned_report_assigned(report_id, animal_id, session_id)

            log_row = service.db.conn.execute("""
                SELECT animal_id, session_id
                FROM email_import_log
                WHERE email_uid = ?
            """, ("5",)).fetchone()
            self.assertEqual(log_row["animal_id"], animal_id)
            self.assertEqual(log_row["session_id"], session_id)
        finally:
            service.close()

        response = self.client.get("/imports")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Atribuído", response.text)
        self.assertIn("/animal/", response.text)
        self.assertIn("Bobby", response.text)
        self.assertNotIn("Precisa de atribuição", response.text)
        self.assertNotIn("Em fila", response.text)


if __name__ == "__main__":
    unittest.main()
