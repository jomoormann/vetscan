"""
Session Repository for VetScan

Handles database operations for TestSession, ProteinResult,
BiochemistryResult, and UrinalysisResult entities.
"""

from typing import Dict, List, Optional

from models.domain import (
    TestSession, ProteinResult, BiochemistryResult, UrinalysisResult
)


class SessionRepository:
    """Repository for TestSession and related result CRUD operations."""

    def __init__(self, db):
        """
        Initialize repository with database connection.

        Args:
            db: Database instance with active connection
        """
        self.db = db

    # -------------------------------------------------------------------------
    # Test Sessions
    # -------------------------------------------------------------------------

    def create_session(self, session: TestSession) -> int:
        """
        Insert a new test session and return its ID.

        Args:
            session: TestSession instance to create

        Returns:
            ID of the created session
        """
        cursor = self.db.conn.execute("""
            INSERT INTO test_sessions (animal_id, report_number, test_date,
                                      closing_date, sample_type, lab_name,
                                      pdf_path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session.animal_id, session.report_number, session.test_date,
              session.closing_date, session.sample_type, session.lab_name,
              session.pdf_path, session.notes))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_session(self, session_id: int) -> Optional[TestSession]:
        """Get a test session by ID."""
        cursor = self.db.conn.execute(
            "SELECT * FROM test_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return TestSession(**dict(row))
        return None

    def get_sessions_for_animal(self, animal_id: int) -> List[TestSession]:
        """Get all test sessions for an animal, ordered by date descending."""
        cursor = self.db.conn.execute("""
            SELECT * FROM test_sessions
            WHERE animal_id = ?
            ORDER BY test_date DESC
        """, (animal_id,))
        return [TestSession(**dict(row)) for row in cursor.fetchall()]

    def session_exists(self, report_number: str) -> bool:
        """Check if a session with given report number already exists."""
        cursor = self.db.conn.execute(
            "SELECT 1 FROM test_sessions WHERE report_number = ?",
            (report_number,))
        return cursor.fetchone() is not None

    def delete_session(self, session_id: int) -> bool:
        """Delete a test session (cascades to results)."""
        cursor = self.db.conn.execute(
            "DELETE FROM test_sessions WHERE id = ?", (session_id,))
        self.db.conn.commit()
        return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Protein Results
    # -------------------------------------------------------------------------

    def create_protein_result(self, result: ProteinResult) -> int:
        """Insert a protein result."""
        result.compute_flags()
        cursor = self.db.conn.execute("""
            INSERT INTO protein_results (session_id, marker_name, marker_category,
                                        value, unit, value_absolute, unit_absolute,
                                        reference_min, reference_max,
                                        reference_min_absolute, reference_max_absolute,
                                        flag, flag_absolute)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (result.session_id, result.marker_name, result.marker_category,
              result.value, result.unit, result.value_absolute, result.unit_absolute,
              result.reference_min, result.reference_max,
              result.reference_min_absolute, result.reference_max_absolute,
              result.flag, result.flag_absolute))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_results_for_session(self, session_id: int) -> List[ProteinResult]:
        """Get all protein results for a session."""
        cursor = self.db.conn.execute(
            "SELECT * FROM protein_results WHERE session_id = ?", (session_id,))
        return [ProteinResult(**dict(row)) for row in cursor.fetchall()]

    def get_marker_history(self, animal_id: int, marker_name: str) -> List[Dict]:
        """Get historical values for a specific marker for an animal."""
        cursor = self.db.conn.execute("""
            SELECT ts.test_date, pr.value, pr.value_absolute, pr.flag, pr.flag_absolute
            FROM protein_results pr
            JOIN test_sessions ts ON pr.session_id = ts.id
            WHERE ts.animal_id = ? AND pr.marker_name = ?
            ORDER BY ts.test_date ASC
        """, (animal_id, marker_name))
        return [dict(row) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Biochemistry Results
    # -------------------------------------------------------------------------

    def create_biochemistry_result(self, result: BiochemistryResult) -> int:
        """Insert a biochemistry result."""
        result.compute_upc_status()
        cursor = self.db.conn.execute("""
            INSERT INTO biochemistry_results (session_id, upc_ratio, upc_status,
                                             urine_total_protein, urine_creatinine,
                                             iris_stage, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (result.session_id, result.upc_ratio, result.upc_status,
              result.urine_total_protein, result.urine_creatinine,
              result.iris_stage, result.notes))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_biochemistry_for_session(self, session_id: int) -> Optional[BiochemistryResult]:
        """Get biochemistry result for a session."""
        cursor = self.db.conn.execute(
            "SELECT * FROM biochemistry_results WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return BiochemistryResult(**dict(row))
        return None

    # -------------------------------------------------------------------------
    # Urinalysis Results
    # -------------------------------------------------------------------------

    def create_urinalysis_result(self, result: UrinalysisResult) -> int:
        """Insert a urinalysis result."""
        result.compute_flags()
        cursor = self.db.conn.execute("""
            INSERT INTO urinalysis_results (session_id, color, appearance,
                                           glucose, bilirubin, ketones,
                                           specific_gravity, ph, proteins,
                                           proteins_value, urobilinogen, nitrites,
                                           leukocytes, erythrocytes, epithelial_cells,
                                           casts, crystals, mucus, bacteria,
                                           observations, flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (result.session_id, result.color, result.appearance,
              result.glucose, result.bilirubin, result.ketones,
              result.specific_gravity, result.ph, result.proteins,
              result.proteins_value, result.urobilinogen, result.nitrites,
              result.leukocytes, result.erythrocytes, result.epithelial_cells,
              result.casts, result.crystals, result.mucus, result.bacteria,
              result.observations, result.flags))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_urinalysis_for_session(self, session_id: int) -> Optional[UrinalysisResult]:
        """Get urinalysis result for a session."""
        cursor = self.db.conn.execute(
            "SELECT * FROM urinalysis_results WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return UrinalysisResult(**dict(row))
        return None
