"""
Diagnosis Repository for VetScan

Handles database operations for DiagnosisReport entities.
"""

from typing import List, Optional

from models.domain import DiagnosisReport


class DiagnosisRepository:
    """Repository for DiagnosisReport CRUD operations."""

    def __init__(self, db):
        """
        Initialize repository with database connection.

        Args:
            db: Database instance with active connection
        """
        self.db = db

    def create(self, report: DiagnosisReport) -> int:
        """
        Insert a diagnosis report and return its ID.

        Args:
            report: DiagnosisReport instance to create

        Returns:
            ID of the created report
        """
        cursor = self.db.conn.execute("""
            INSERT INTO diagnosis_reports (animal_id, report_date, report_type,
                                          input_summary, executive_summary,
                                          test_interpretation, differential_diagnosis,
                                          recommendations, literature_references, model_used,
                                          openai_test_interpretation, openai_differential_diagnosis,
                                          openai_recommendations, openai_literature_references,
                                          openai_model_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (report.animal_id, report.report_date, report.report_type,
              report.input_summary, report.executive_summary,
              report.test_interpretation, report.differential_diagnosis,
              report.recommendations, report.references, report.model_used,
              report.openai_test_interpretation, report.openai_differential_diagnosis,
              report.openai_recommendations, report.openai_references,
              report.openai_model_used))
        self.db.conn.commit()
        return cursor.lastrowid

    def get(self, report_id: int) -> Optional[DiagnosisReport]:
        """
        Get a diagnosis report by ID.

        Args:
            report_id: ID of the report

        Returns:
            DiagnosisReport instance or None if not found
        """
        cursor = self.db.conn.execute(
            "SELECT * FROM diagnosis_reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            # Map column names to dataclass fields
            if 'literature_references' in data:
                data['references'] = data.pop('literature_references')
            if 'openai_literature_references' in data:
                data['openai_references'] = data.pop('openai_literature_references')
            return DiagnosisReport(**data)
        return None

    def get_for_animal(self, animal_id: int) -> List[DiagnosisReport]:
        """
        Get all diagnosis reports for an animal, ordered by date descending.

        Args:
            animal_id: ID of the animal

        Returns:
            List of diagnosis reports
        """
        cursor = self.db.conn.execute("""
            SELECT * FROM diagnosis_reports WHERE animal_id = ?
            ORDER BY created_at DESC
        """, (animal_id,))

        reports = []
        for row in cursor.fetchall():
            data = dict(row)
            # Map column names to dataclass fields
            if 'literature_references' in data:
                data['references'] = data.pop('literature_references')
            if 'openai_literature_references' in data:
                data['openai_references'] = data.pop('openai_literature_references')
            reports.append(DiagnosisReport(**data))
        return reports

    def delete(self, report_id: int) -> bool:
        """
        Delete a diagnosis report.

        Args:
            report_id: ID of the report to delete

        Returns:
            True if deletion was successful
        """
        cursor = self.db.conn.execute(
            "DELETE FROM diagnosis_reports WHERE id = ?", (report_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    def get_latest_for_animal(self, animal_id: int) -> Optional[DiagnosisReport]:
        """
        Get the most recent diagnosis report for an animal.

        Args:
            animal_id: ID of the animal

        Returns:
            Most recent DiagnosisReport or None
        """
        cursor = self.db.conn.execute("""
            SELECT * FROM diagnosis_reports
            WHERE animal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (animal_id,))
        row = cursor.fetchone()
        if row:
            data = dict(row)
            if 'literature_references' in data:
                data['references'] = data.pop('literature_references')
            if 'openai_literature_references' in data:
                data['openai_references'] = data.pop('openai_literature_references')
            return DiagnosisReport(**data)
        return None

    def count_for_animal(self, animal_id: int) -> int:
        """Get the number of diagnosis reports for an animal."""
        cursor = self.db.conn.execute(
            "SELECT COUNT(*) FROM diagnosis_reports WHERE animal_id = ?",
            (animal_id,))
        return cursor.fetchone()[0]
