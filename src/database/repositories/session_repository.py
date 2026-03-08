"""
Session Repository for VetScan

Handles database operations for TestSession, ProteinResult,
BiochemistryResult, and UrinalysisResult entities.
"""

from typing import Dict, List, Optional, Tuple

from models.domain import (
    TestSession, ProteinResult, BiochemistryResult, UrinalysisResult,
    SessionMeasurement, PathologyFinding, SessionAsset, UnassignedReport
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
                                      source_system, report_type,
                                      external_report_id, report_source,
                                      reported_at, received_at, clinic_name,
                                      panel_name, raw_metadata_json,
                                      pdf_path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (session.animal_id, session.report_number, session.test_date,
              session.closing_date, session.sample_type, session.lab_name,
              session.source_system, session.report_type,
              session.external_report_id, session.report_source,
              session.reported_at, session.received_at, session.clinic_name,
              session.panel_name, session.raw_metadata_json,
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

    def list_reports_paginated(self, search: Optional[str] = None,
                               source_system: Optional[str] = None,
                               report_type: Optional[str] = None,
                               responsible_vet: Optional[str] = None,
                               animal_id: Optional[int] = None,
                               sort: str = "date_desc",
                               page: int = 1,
                               page_size: int = 25) -> Tuple[List[Dict], int]:
        """List imported reports with joined animal metadata."""
        filters = []
        params: List[object] = []

        if search:
            wildcard = f"%{search.strip()}%"
            filters.append("""
                (
                    ts.report_number LIKE ?
                    OR ts.external_report_id LIKE ?
                    OR ts.report_source LIKE ?
                    OR ts.clinic_name LIKE ?
                    OR a.name LIKE ?
                    OR a.owner_name LIKE ?
                    OR a.microchip LIKE ?
                )
            """)
            params.extend([wildcard] * 7)

        if source_system:
            filters.append("ts.source_system = ?")
            params.append(source_system)

        if report_type:
            filters.append("ts.report_type = ?")
            params.append(report_type)

        if responsible_vet:
            filters.append("a.responsible_vet = ?")
            params.append(responsible_vet)

        if animal_id is not None:
            filters.append("ts.animal_id = ?")
            params.append(animal_id)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        count_row = self.db.conn.execute(f"""
            SELECT COUNT(*) AS total
            FROM test_sessions ts
            JOIN animals a ON a.id = ts.animal_id
            {where_clause}
        """, tuple(params)).fetchone()
        total = count_row["total"] if count_row else 0

        order_clause = {
            "date_desc": "COALESCE(ts.test_date, DATE(ts.created_at)) DESC, ts.id DESC",
            "date_asc": "COALESCE(ts.test_date, DATE(ts.created_at)) ASC, ts.id ASC",
            "report_asc": "COALESCE(ts.report_number, ts.external_report_id, '') COLLATE NOCASE ASC, ts.id ASC",
            "report_desc": "COALESCE(ts.report_number, ts.external_report_id, '') COLLATE NOCASE DESC, ts.id DESC",
            "animal_asc": "a.name COLLATE NOCASE ASC, COALESCE(ts.test_date, DATE(ts.created_at)) DESC",
            "animal_desc": "a.name COLLATE NOCASE DESC, COALESCE(ts.test_date, DATE(ts.created_at)) DESC",
            "vet_asc": "COALESCE(a.responsible_vet, '') COLLATE NOCASE ASC, a.name COLLATE NOCASE ASC",
            "vet_desc": "COALESCE(a.responsible_vet, '') COLLATE NOCASE DESC, a.name COLLATE NOCASE ASC",
            "source_asc": "COALESCE(ts.source_system, '') COLLATE NOCASE ASC, COALESCE(ts.clinic_name, ts.lab_name, '') COLLATE NOCASE ASC",
            "source_desc": "COALESCE(ts.source_system, '') COLLATE NOCASE DESC, COALESCE(ts.clinic_name, ts.lab_name, '') COLLATE NOCASE DESC",
        }.get(sort, "COALESCE(ts.test_date, DATE(ts.created_at)) DESC, ts.id DESC")

        offset = max(page - 1, 0) * page_size
        rows = self.db.conn.execute(f"""
            SELECT
                ts.*,
                a.name AS animal_name,
                a.species AS animal_species,
                a.owner_name AS owner_name,
                a.responsible_vet AS responsible_vet,
                (
                    SELECT COUNT(*)
                    FROM protein_results pr
                    WHERE pr.session_id = ts.id
                ) AS protein_result_count,
                (
                    SELECT COUNT(*)
                    FROM session_measurements sm
                    WHERE sm.session_id = ts.id
                ) AS measurement_count,
                (
                    SELECT COUNT(*)
                    FROM pathology_findings pf
                    WHERE pf.session_id = ts.id
                ) AS pathology_finding_count,
                (
                    SELECT COUNT(*)
                    FROM session_assets sa
                    WHERE sa.session_id = ts.id
                ) AS asset_count
            FROM test_sessions ts
            JOIN animals a ON a.id = ts.animal_id
            {where_clause}
            ORDER BY {order_clause}
            LIMIT ? OFFSET ?
        """, tuple(params + [page_size, offset])).fetchall()
        return [dict(row) for row in rows], total

    def search_reports(self, search: str, limit: int = 8) -> List[Dict]:
        """Search reports for the global search dropdown."""
        if not search or not search.strip():
            return []

        wildcard = f"%{search.strip()}%"
        rows = self.db.conn.execute("""
            SELECT
                ts.id,
                ts.report_number,
                ts.external_report_id,
                ts.report_type,
                ts.source_system,
                ts.test_date,
                a.id AS animal_id,
                a.name AS animal_name,
                a.owner_name AS owner_name
            FROM test_sessions ts
            JOIN animals a ON a.id = ts.animal_id
            WHERE ts.report_number LIKE ?
               OR ts.external_report_id LIKE ?
               OR ts.report_source LIKE ?
               OR a.name LIKE ?
               OR a.owner_name LIKE ?
            ORDER BY COALESCE(ts.test_date, DATE(ts.created_at)) DESC, ts.id DESC
            LIMIT ?
        """, (wildcard, wildcard, wildcard, wildcard, wildcard, limit)).fetchall()
        return [dict(row) for row in rows]

    def session_exists(self, report_number: str) -> bool:
        """Check if a session with given report number already exists."""
        cursor = self.db.conn.execute(
            "SELECT 1 FROM test_sessions WHERE report_number = ?",
            (report_number,))
        return cursor.fetchone() is not None

    def session_exists_by_external_reference(self, source_system: str,
                                             external_report_id: str) -> bool:
        """Check if a session exists for a source-system-specific external ID."""
        cursor = self.db.conn.execute("""
            SELECT 1 FROM test_sessions
            WHERE source_system = ? AND external_report_id = ?
        """, (source_system, external_report_id))
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

    # -------------------------------------------------------------------------
    # Generic Measurements
    # -------------------------------------------------------------------------

    def create_session_measurement(self, measurement: SessionMeasurement) -> int:
        """Insert a generic session measurement."""
        cursor = self.db.conn.execute("""
            INSERT INTO session_measurements (
                session_id, panel_name, measurement_code, measurement_name,
                value_numeric, value_text, unit, reference_min, reference_max,
                reference_text, flag, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            measurement.session_id,
            measurement.panel_name,
            measurement.measurement_code,
            measurement.measurement_name,
            measurement.value_numeric,
            measurement.value_text,
            measurement.unit,
            measurement.reference_min,
            measurement.reference_max,
            measurement.reference_text,
            measurement.flag,
            measurement.sort_order,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_measurements_for_session(self, session_id: int) -> List[SessionMeasurement]:
        """Get generic measurements for a session."""
        cursor = self.db.conn.execute("""
            SELECT * FROM session_measurements
            WHERE session_id = ?
            ORDER BY sort_order ASC, id ASC
        """, (session_id,))
        return [SessionMeasurement(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Pathology Findings
    # -------------------------------------------------------------------------

    def create_pathology_finding(self, finding: PathologyFinding) -> int:
        """Insert a pathology finding."""
        cursor = self.db.conn.execute("""
            INSERT INTO pathology_findings (
                session_id, section_type, specimen_label, title, sample_site,
                sample_method, clinical_history, microscopic_description,
                diagnosis, comment, sort_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            finding.session_id,
            finding.section_type,
            finding.specimen_label,
            finding.title,
            finding.sample_site,
            finding.sample_method,
            finding.clinical_history,
            finding.microscopic_description,
            finding.diagnosis,
            finding.comment,
            finding.sort_order,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_pathology_findings_for_session(self, session_id: int) -> List[PathologyFinding]:
        """Get pathology findings for a session."""
        cursor = self.db.conn.execute("""
            SELECT * FROM pathology_findings
            WHERE session_id = ?
            ORDER BY sort_order ASC, id ASC
        """, (session_id,))
        return [PathologyFinding(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Session Assets
    # -------------------------------------------------------------------------

    def create_session_asset(self, asset: SessionAsset) -> int:
        """Insert an extracted session asset."""
        cursor = self.db.conn.execute("""
            INSERT INTO session_assets (
                session_id, asset_type, label, file_path,
                page_number, sort_order, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            asset.session_id,
            asset.asset_type,
            asset.label,
            asset.file_path,
            asset.page_number,
            asset.sort_order,
            asset.metadata_json,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_assets_for_session(self, session_id: int) -> List[SessionAsset]:
        """Get stored assets for a session."""
        cursor = self.db.conn.execute("""
            SELECT * FROM session_assets
            WHERE session_id = ?
            ORDER BY sort_order ASC, id ASC
        """, (session_id,))
        return [SessionAsset(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Unassigned Reports
    # -------------------------------------------------------------------------

    def find_open_unassigned_report(self, source_system: Optional[str],
                                    external_report_id: Optional[str],
                                    report_number: Optional[str]) -> Optional[UnassignedReport]:
        """Find an existing pending report so repeated imports do not duplicate it."""
        if source_system and external_report_id:
            cursor = self.db.conn.execute("""
                SELECT * FROM unassigned_reports
                WHERE status = 'pending'
                  AND source_system = ?
                  AND external_report_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (source_system, external_report_id))
            row = cursor.fetchone()
            if row:
                return UnassignedReport(**dict(row))

        if report_number:
            cursor = self.db.conn.execute("""
                SELECT * FROM unassigned_reports
                WHERE status = 'pending'
                  AND report_number = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (report_number,))
            row = cursor.fetchone()
            if row:
                return UnassignedReport(**dict(row))

        return None

    def create_unassigned_report(self, report: UnassignedReport) -> int:
        """Insert a report awaiting manual assignment."""
        cursor = self.db.conn.execute("""
            INSERT INTO unassigned_reports (
                filename, pdf_path, source_system, report_type, report_number,
                external_report_id, report_source, animal_name, species, owner_name,
                clinic_name, report_date, panel_name, match_reason,
                parsed_summary_json, candidate_matches_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.filename,
            report.pdf_path,
            report.source_system,
            report.report_type,
            report.report_number,
            report.external_report_id,
            report.report_source,
            report.animal_name,
            report.species,
            report.owner_name,
            report.clinic_name,
            report.report_date,
            report.panel_name,
            report.match_reason,
            report.parsed_summary_json,
            report.candidate_matches_json,
            report.status,
        ))
        self.db.conn.commit()
        return cursor.lastrowid

    def get_unassigned_report(self, report_id: int) -> Optional[UnassignedReport]:
        """Get a queued report by ID."""
        cursor = self.db.conn.execute(
            "SELECT * FROM unassigned_reports WHERE id = ?", (report_id,))
        row = cursor.fetchone()
        if row:
            return UnassignedReport(**dict(row))
        return None

    def list_unassigned_reports(self, status: str = "pending",
                                search: Optional[str] = None,
                                page: Optional[int] = None,
                                page_size: Optional[int] = None):
        """List queued reports by status, optionally filtered and paginated."""
        filters = ["status = ?"]
        params: List[object] = [status]

        if search:
            wildcard = f"%{search.strip()}%"
            filters.append("""
                (
                    filename LIKE ?
                    OR report_number LIKE ?
                    OR external_report_id LIKE ?
                    OR animal_name LIKE ?
                    OR owner_name LIKE ?
                    OR clinic_name LIKE ?
                )
            """)
            params.extend([wildcard] * 6)

        where_clause = " AND ".join(filters)
        if page is None or page_size is None:
            cursor = self.db.conn.execute(f"""
                SELECT * FROM unassigned_reports
                WHERE {where_clause}
                ORDER BY created_at DESC
            """, tuple(params))
            return [UnassignedReport(**dict(row)) for row in cursor.fetchall()]

        count_row = self.db.conn.execute(f"""
            SELECT COUNT(*) AS total
            FROM unassigned_reports
            WHERE {where_clause}
        """, tuple(params)).fetchone()
        total = count_row["total"] if count_row else 0

        offset = max(page - 1, 0) * page_size
        cursor = self.db.conn.execute(f"""
            SELECT * FROM unassigned_reports
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, tuple(params + [page_size, offset]))
        return [UnassignedReport(**dict(row)) for row in cursor.fetchall()], total

    def mark_unassigned_report_assigned(self, report_id: int, animal_id: int,
                                        session_id: int) -> bool:
        """Mark a queued report as assigned after manual action."""
        cursor = self.db.conn.execute("""
            UPDATE unassigned_reports
            SET status = 'assigned',
                assigned_animal_id = ?,
                session_id = ?,
                assigned_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        """, (animal_id, session_id, report_id))
        self.db.conn.commit()
        return cursor.rowcount > 0
