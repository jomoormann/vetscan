"""
Diagnosis Repository for VetScan

Handles database operations for DiagnosisReport entities and background jobs.
"""

from typing import Any, Dict, List, Optional

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

    @staticmethod
    def _row_to_report(row) -> DiagnosisReport:
        """Map a sqlite row into a DiagnosisReport dataclass."""
        data = dict(row)
        if 'literature_references' in data:
            data['references'] = data.pop('literature_references')
        if 'openai_literature_references' in data:
            data['openai_references'] = data.pop('openai_literature_references')
        return DiagnosisReport(**data)

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
            return self._row_to_report(row)
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
            reports.append(self._row_to_report(row))
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
            return self._row_to_report(row)
        return None

    def count_for_animal(self, animal_id: int) -> int:
        """Get the number of diagnosis reports for an animal."""
        cursor = self.db.conn.execute(
            "SELECT COUNT(*) FROM diagnosis_reports WHERE animal_id = ?",
            (animal_id,))
        return cursor.fetchone()[0]

    def create_job(self, animal_id: int, report_type: str,
                   requested_by_user_id: Optional[int] = None) -> int:
        """Create a background diagnosis job and return its ID."""
        cursor = self.db.conn.execute("""
            INSERT INTO diagnosis_jobs (
                animal_id, requested_by_user_id, report_type, status
            ) VALUES (?, ?, ?, 'pending')
        """, (animal_id, requested_by_user_id, report_type))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Fetch a diagnosis background job by ID."""
        cursor = self.db.conn.execute(
            "SELECT * FROM diagnosis_jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_active_job_for_animal(self, animal_id: int) -> Optional[Dict[str, Any]]:
        """Fetch the newest pending or running job for an animal."""
        cursor = self.db.conn.execute("""
            SELECT *
            FROM diagnosis_jobs
            WHERE animal_id = ?
              AND status IN ('pending', 'running')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
        """, (animal_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_job(self, job_id: int, **fields) -> bool:
        """Update mutable diagnosis job fields."""
        allowed_fields = {
            "status",
            "report_id",
            "error_message",
            "started_at",
            "completed_at",
        }
        assignments = []
        params = []
        for key, value in fields.items():
            if key not in allowed_fields:
                continue
            assignments.append(f"{key} = ?")
            params.append(value)

        if not assignments:
            return False

        params.append(job_id)
        cursor = self.db.conn.execute(
            f"UPDATE diagnosis_jobs SET {', '.join(assignments)} WHERE id = ?",
            tuple(params),
        )
        self.db.conn.commit()
        return cursor.rowcount > 0

    def mark_stale_jobs_failed(self, max_age_minutes: int = 30) -> int:
        """Mark abandoned pending/running jobs as failed."""
        threshold = f"-{max_age_minutes} minutes"
        cursor = self.db.conn.execute("""
            UPDATE diagnosis_jobs
            SET status = 'failed',
                error_message = COALESCE(
                    error_message,
                    'Diagnosis generation timed out before completion.'
                ),
                completed_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'running')
              AND datetime(COALESCE(started_at, created_at)) <= datetime('now', ?)
        """, (threshold,))
        self.db.conn.commit()
        return cursor.rowcount
