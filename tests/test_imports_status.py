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
from models import Animal, PathologyFinding, TestSession


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

    def test_admin_can_acknowledge_failed_import_without_dashboard_noise(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            failed_id = service.db.conn.execute("""
                INSERT INTO email_import_log (
                    email_uid,
                    email_subject,
                    email_from,
                    attachment_name,
                    validation_result,
                    import_success,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "failed-1",
                "failed import",
                "reports@example.com",
                "failed.pdf",
                "missing_dnatech_markers",
                0,
                "PDF does not match any supported report type",
            )).lastrowid
            service.db.conn.commit()
        finally:
            service.close()

        dashboard_before = self.client.get("/")
        self.assertEqual(dashboard_before.status_code, 200)
        self.assertNotIn("failed.pdf", dashboard_before.text)

        imports_before = self.client.get("/imports")
        self.assertEqual(imports_before.status_code, 200)
        self.assertIn("failed.pdf", imports_before.text)
        self.assertIn("Reconhecer", imports_before.text)

        response = self.client.post(
            f"/imports/{failed_id}/acknowledge",
            data={"csrf_token": self._csrf_token()},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/imports")

        service = web_server.get_service()
        try:
            row = service.db.conn.execute("""
                SELECT acknowledged_at, acknowledged_by_user_id
                FROM email_import_log
                WHERE id = ?
            """, (failed_id,)).fetchone()
            self.assertIsNotNone(row["acknowledged_at"])
            self.assertEqual(row["acknowledged_by_user_id"], user.id)
        finally:
            service.close()

        dashboard_after = self.client.get("/")
        self.assertEqual(dashboard_after.status_code, 200)
        self.assertNotIn("failed.pdf", dashboard_after.text)

        imports_after = self.client.get("/imports")
        self.assertEqual(imports_after.status_code, 200)
        self.assertIn("failed.pdf", imports_after.text)
        self.assertIn("Reconhecido", imports_after.text)

    def test_dashboard_shows_recent_reports_and_sidebar_unassigned_badge(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Simba", species="Canídeo"))
            service.db.create_test_session(TestSession(
                animal_id=animal_id,
                report_number="31370/1611430",
                test_date=date.today(),
                lab_name="DNAtech",
                source_system="dnatech",
                report_type="biochemistry",
                panel_name="biochemistry",
                clinic_name="Clínica Veterinária CVS SOS Animal",
                ordering_vet="Dr. Maria Santos",
            ))
            service.db.conn.execute("""
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
                "unassigned.pdf",
                str(self.tempdir / "unassigned.pdf"),
                "dnatech",
                "biochemistry",
                "pending-1",
            ))
            service.db.conn.execute("""
                INSERT INTO email_import_log (
                    email_uid,
                    email_subject,
                    email_from,
                    attachment_name,
                    validation_result,
                    import_success,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                "failed-dashboard",
                "failed import",
                "reports@example.com",
                "dashboard-failure.pdf",
                "missing_markers",
                0,
                "Unsupported",
            ))
            service.db.conn.commit()
        finally:
            service.close()

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("31370/1611430", response.text)
        self.assertIn("Maria Santos", response.text)
        self.assertNotIn("Dr. Maria Santos", response.text)
        self.assertIn("Veterinário responsável", response.text)
        self.assertNotIn("Comece pelo que precisa de ação", response.text)
        self.assertNotIn("<th>Resumo</th>", response.text)
        self.assertIn('class="nav-badge"', response.text)
        self.assertIn(">1</span>", response.text)
        self.assertNotIn('class="kpi-card"', response.text)
        self.assertNotIn("dashboard-failure.pdf", response.text)
        self.assertNotIn("unassigned.pdf", response.text)

        reports_response = self.client.get("/reports")
        self.assertEqual(reports_response.status_code, 200)
        self.assertIn("Maria Santos", reports_response.text)
        self.assertNotIn("Dr. Maria Santos", reports_response.text)
        self.assertIn("Pesquise relatórios importados de toda a clínica de acordo com o animal, laboratório, tipo de relatório ou nome do veterinário responsável.", reports_response.text)
        self.assertIn("<td>DNATech</td>", reports_response.text)
        self.assertNotIn("Clínica Veterinária CVS SOS Animal", reports_response.text)
        self.assertNotIn("<th>Resumo</th>", reports_response.text)

        animal_response = self.client.get(f"/animal/{animal_id}")
        self.assertEqual(animal_response.status_code, 200)
        self.assertIn("Maria Santos", animal_response.text)
        self.assertNotIn("Dr. Maria Santos", animal_response.text)
        self.assertIn("DNATech", animal_response.text)
        self.assertNotIn("<th>Resumo</th>", animal_response.text)

        animals_response = self.client.get("/animals")
        self.assertEqual(animals_response.status_code, 200)
        self.assertIn("Pesquise, filtre e abra os animais relevantes.", animals_response.text)
        self.assertNotIn("sem carregar a lista completa de uma vez", animals_response.text)

    def test_reports_ordering_vet_filter_merges_title_variants_for_all_vets(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_one = service.db.create_animal(Animal(name="Luna", species="Canídeo"))
            animal_two = service.db.create_animal(Animal(name="Milo", species="Felino"))
            service.db.create_test_session(TestSession(
                animal_id=animal_one,
                report_number="SOFIA-1",
                test_date=date.today(),
                source_system="vedis",
                report_type="cytology",
                ordering_vet="Dra. Sofia Castro",
            ))
            service.db.create_test_session(TestSession(
                animal_id=animal_two,
                report_number="SOFIA-2",
                test_date=date.today(),
                source_system="dnatech",
                report_type="biochemistry",
                ordering_vet="Sofia Castro",
            ))
            service.db.create_test_session(TestSession(
                animal_id=animal_two,
                report_number="JOAO-1",
                test_date=date.today(),
                source_system="vedis",
                report_type="histology",
                ordering_vet="Dr. João Costa",
            ))
            service.db.create_test_session(TestSession(
                animal_id=animal_one,
                report_number="JOAO-2",
                test_date=date.today(),
                source_system="dnatech",
                report_type="biochemistry",
                ordering_vet="João Costa",
            ))
        finally:
            service.close()

        reports_response = self.client.get("/reports")
        self.assertEqual(reports_response.status_code, 200)
        self.assertEqual(reports_response.text.count('<option value="Sofia Castro"'), 1)
        self.assertEqual(reports_response.text.count('<option value="João Costa"'), 1)
        self.assertNotIn('<option value="Dra. Sofia Castro"', reports_response.text)
        self.assertNotIn('<option value="Dr. João Costa"', reports_response.text)

        filtered_response = self.client.get("/reports?responsible_vet=Sofia%20Castro")
        self.assertEqual(filtered_response.status_code, 200)
        self.assertIn("SOFIA-1", filtered_response.text)
        self.assertIn("SOFIA-2", filtered_response.text)

        joao_response = self.client.get("/reports?responsible_vet=Jo%C3%A3o%20Costa")
        self.assertEqual(joao_response.status_code, 200)
        self.assertIn("JOAO-1", joao_response.text)
        self.assertIn("JOAO-2", joao_response.text)

    def test_animal_profile_updates_and_displays_neutered_status(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Luna", species="Canídeo"))
        finally:
            service.close()

        response = self.client.post(
            f"/animal/{animal_id}/update",
            data={
                "name": "Luna",
                "species": "Canídeo",
                "sex": "F",
                "neutered": "true",
                "csrf_token": self._csrf_token(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        page = self.client.get(f"/animal/{animal_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Esterilizado", page.text)
        self.assertIn("Sim", page.text)
        self.assertNotIn("Paciente desde</div>", page.text)

    def test_animal_detail_has_no_ai_diagnostics_surface(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Loki", species="Canídeo"))
        finally:
            service.close()

        response = self.client.get(f"/animal/{animal_id}?tab=diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("tab=diagnostics", response.text)
        self.assertNotIn("data-diagnosis-trigger", response.text)
        self.assertNotIn("triggerDiagnosis", response.text)
        self.assertNotIn("diagnosisJobNotice", response.text)
        self.assertNotIn("Sinais para revisão", response.text)
        self.assertNotIn("Diagnóstico diferencial", response.text)

        diagnosis_post = self.client.post(
            f"/animal/{animal_id}/diagnosis",
            data={"csrf_token": self._csrf_token()},
        )
        self.assertEqual(diagnosis_post.status_code, 404)
        self.assertEqual(self.client.get("/api/diagnosis-jobs/1").status_code, 404)
        self.assertEqual(self.client.get(f"/animal/{animal_id}/diagnosis/1").status_code, 404)

    def test_session_detail_formats_dense_report_text_without_embedded_pdf(self):
        user = self._create_user()
        login_response = self._login(email=user.email)
        self.assertEqual(login_response.status_code, 302)

        pdf_path = self.uploads_dir / "dense-report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n% test pdf\n")
        artifact_text = (
            "As lâminas apresentam moderada celularidade. "
            "E s t á p r e s e n te u m a p o p u la ç ão i n f la m a t ó r i a "
            "c o n s ti tu íd a p o r n e u tr ó f il o s d e g e n e r a d o s "
            "( c ar io li se ) c o m o c a s io n a l f a g o c i to s e "
            "b a ct e ri a n a (c o c o s ) . Ocasionais macrófagos."
        )

        service = web_server.get_service()
        try:
            animal_id = service.db.create_animal(Animal(name="Loki", species="Canine"))
            session_id = service.db.create_test_session(TestSession(
                animal_id=animal_id,
                report_number="VEDIS/26005750",
                test_date=date.today(),
                source_system="vedis",
                report_type="cytology",
                panel_name="cytology",
                pdf_path=str(pdf_path),
            ))
            service.db.create_pathology_finding(PathologyFinding(
                session_id=session_id,
                section_type="cytology",
                title="Cytology",
                microscopic_description=artifact_text,
                diagnosis="Inflamação séptica",
            ))
        finally:
            service.close()

        response = self.client.get(f"/session/{session_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="report-finding"', response.text)
        self.assertIn(f'href="/reports/{session_id}/raw-pdf"', response.text)
        self.assertNotIn('class="pdf-preview-frame"', response.text)
        self.assertIn("Está presente uma população inflamatória", response.text)
        self.assertNotIn("E s t á p r e s e n", response.text)


if __name__ == "__main__":
    unittest.main()
