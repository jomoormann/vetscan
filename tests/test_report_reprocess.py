import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["ENVIRONMENT"] = "development"
os.environ["AUTH_SECRET_KEY"] = "test-secret-key"

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import VetProteinService
from models import Animal, PathologyFinding, TestSession
from pdf_parser import ParsedReport


class ReportReprocessTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = Path(tempfile.mkdtemp(prefix="vetscan-reprocess-test-"))
        self.uploads = self.tempdir / "uploads"
        self.uploads.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _pdf(self, filename: str) -> Path:
        path = self.uploads / filename
        path.write_bytes(b"%PDF-1.4\n% test\n")
        return path

    def test_reprocess_session_replaces_old_pathology_with_latest_parser_output(self):
        pdf_path = self._pdf("vedis-old.pdf")

        with VetProteinService(db_path=str(self.tempdir / "test.db"), uploads_dir=str(self.uploads)) as service:
            animal_id = service.db.create_animal(Animal(name="Kika", species="Canídeo"))
            session_id = service.db.create_test_session(TestSession(
                animal_id=animal_id,
                report_number="VEDIS/26004748",
                source_system="vedis",
                report_type="histology",
                panel_name="histology",
                pdf_path=str(pdf_path),
            ))
            service.db.create_pathology_finding(PathologyFinding(
                session_id=session_id,
                section_type="histology",
                diagnosis="Old diagnosis",
                microscopic_description="Old microscopic text",
            ))

            parsed = ParsedReport(
                animal=Animal(name="Kika", species="Canídeo"),
                session=TestSession(
                    report_number="VEDIS/26004748",
                    source_system="vedis",
                    report_type="histology",
                    panel_name="histology",
                    pdf_path=str(pdf_path),
                ),
                pathology_findings=[
                    PathologyFinding(
                        section_type="general_comment",
                        title="Comentário geral",
                        comment="Latest Portuguese general comment",
                    )
                ],
            )

            with patch("app.parse_lab_report", return_value=parsed):
                stats = service.reprocess_all_reports(include_unassigned=False)

            self.assertEqual(stats.sessions_updated, 1)
            findings = service.db.get_pathology_findings_for_session(session_id)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].section_type, "general_comment")
            self.assertEqual(findings[0].comment, "Latest Portuguese general comment")
            self.assertIsNone(findings[0].diagnosis)
            self.assertIsNone(findings[0].microscopic_description)

    def test_reprocess_unassigned_refreshes_and_auto_assigns_confident_match(self):
        pdf_path = self._pdf("simba.pdf")

        with VetProteinService(db_path=str(self.tempdir / "test.db"), uploads_dir=str(self.uploads)) as service:
            animal_id = service.db.create_animal(Animal(name="Simba", species="Canídeo"))
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
                pdf_path.name,
                str(pdf_path),
                "vedis",
                "cytology",
                "VEDIS/1",
            )).lastrowid
            service.db.conn.commit()

            parsed = ParsedReport(
                animal=Animal(name="Simba", species="Canídeo"),
                session=TestSession(
                    report_number="VEDIS/26009999",
                    source_system="vedis",
                    report_type="cytology",
                    panel_name="cytology",
                    pdf_path=str(pdf_path),
                ),
                pathology_findings=[
                    PathologyFinding(
                        section_type="general_comment",
                        title="Comentário geral",
                        comment="Updated comment",
                    )
                ],
            )

            with patch("app.parse_lab_report", return_value=parsed):
                status = service.reprocess_unassigned_report(report_id)

            self.assertEqual(status, "assigned")
            report = service.db.get_unassigned_report(report_id)
            self.assertEqual(report.status, "assigned")
            self.assertEqual(report.assigned_animal_id, animal_id)
            self.assertIsNotNone(report.session_id)
            findings = service.db.get_pathology_findings_for_session(report.session_id)
            self.assertEqual(findings[0].comment, "Updated comment")


if __name__ == "__main__":
    unittest.main()
