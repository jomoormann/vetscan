"""
VetScan Database Layer

Provides backwards-compatible Database class that wraps repositories.

Usage:
    from database import Database

    db = Database("path/to/db.sqlite")
    db.connect()
    db.initialize()

    # All old methods still work
    animal = db.get_animal(1)
    sessions = db.get_sessions_for_animal(1)
"""

from datetime import date, datetime
from typing import Dict, List, Optional

from .base import Database as BaseDatabase
from .repositories import (
    AnimalRepository,
    SessionRepository,
    UserRepository,
    DiagnosisRepository,
)

# Import models for type hints
from models.domain import (
    Animal, TestSession, ProteinResult, Symptom, Observation,
    ClinicalNote, DiagnosisReport, BiochemistryResult, UrinalysisResult,
    AnimalIdentifier, AnimalMatchDecision, SessionMeasurement,
    PathologyFinding, SessionAsset, UnassignedReport,
    User, PasswordResetToken
)


class Database(BaseDatabase):
    """
    Backwards-compatible Database class.

    Wraps repository classes and provides the same interface as the
    original monolithic Database class from models.py.
    """

    def __init__(self, db_path: str = "vet_proteins.db"):
        super().__init__(db_path)
        self._animal_repo: Optional[AnimalRepository] = None
        self._session_repo: Optional[SessionRepository] = None
        self._user_repo: Optional[UserRepository] = None
        self._diagnosis_repo: Optional[DiagnosisRepository] = None

    def connect(self):
        """Connect and initialize repositories."""
        result = super().connect()
        self._animal_repo = AnimalRepository(self)
        self._session_repo = SessionRepository(self)
        self._user_repo = UserRepository(self)
        self._diagnosis_repo = DiagnosisRepository(self)
        return result

    # =========================================================================
    # ANIMAL OPERATIONS
    # =========================================================================

    def create_animal(self, animal: Animal) -> int:
        """Insert a new animal and return its ID."""
        return self._animal_repo.create(animal)

    def get_animal(self, animal_id: int) -> Optional[Animal]:
        """Retrieve an animal by ID."""
        return self._animal_repo.get(animal_id)

    def find_animal_by_name(self, name: str) -> List[Animal]:
        """Find animals by name (partial match)."""
        return self._animal_repo.find_by_name(name)

    def find_or_create_animal(self, animal: Animal,
                              identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        """Find existing animal or create new one."""
        return self._animal_repo.find_or_create(animal, identifiers)

    def analyze_animal_match(self, animal: Animal,
                             identifiers: Optional[List[AnimalIdentifier]] = None) -> AnimalMatchDecision:
        """Analyze whether a report can be linked to an existing animal with confidence."""
        return self._animal_repo.analyze_match(animal, identifiers)

    def attach_report_to_animal(self, animal_id: int, animal: Animal,
                                identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        """Update and enrich an existing animal from a parsed report."""
        return self._animal_repo.attach_report_to_animal(animal_id, animal, identifiers)

    def create_animal_from_report(self, animal: Animal,
                                  identifiers: Optional[List[AnimalIdentifier]] = None) -> int:
        """Create a new animal directly from a parsed report."""
        return self._animal_repo.create_from_report(animal, identifiers)

    def list_animals(self) -> List[Animal]:
        """List all animals."""
        return self._animal_repo.list_all()

    # =========================================================================
    # SESSION OPERATIONS
    # =========================================================================

    def create_test_session(self, session: TestSession) -> int:
        """Insert a new test session and return its ID."""
        return self._session_repo.create_session(session)

    def get_sessions_for_animal(self, animal_id: int) -> List[TestSession]:
        """Get all test sessions for an animal, ordered by date."""
        return self._session_repo.get_sessions_for_animal(animal_id)

    def session_exists(self, report_number: str) -> bool:
        """Check if a session with given report number already exists."""
        return self._session_repo.session_exists(report_number)

    def session_exists_by_external_reference(self, source_system: str,
                                             external_report_id: str) -> bool:
        """Check if a session exists for a source-system-specific external ID."""
        return self._session_repo.session_exists_by_external_reference(
            source_system, external_report_id
        )

    # =========================================================================
    # RESULT OPERATIONS
    # =========================================================================

    def create_protein_result(self, result: ProteinResult) -> int:
        """Insert a protein result."""
        return self._session_repo.create_protein_result(result)

    def get_results_for_session(self, session_id: int) -> List[ProteinResult]:
        """Get all protein results for a session."""
        return self._session_repo.get_results_for_session(session_id)

    def get_marker_history(self, animal_id: int, marker_name: str) -> List[Dict]:
        """Get historical values for a specific marker for an animal."""
        return self._session_repo.get_marker_history(animal_id, marker_name)

    def create_biochemistry_result(self, result: BiochemistryResult) -> int:
        """Insert a biochemistry result."""
        return self._session_repo.create_biochemistry_result(result)

    def get_biochemistry_for_session(self, session_id: int) -> Optional[BiochemistryResult]:
        """Get biochemistry result for a session."""
        return self._session_repo.get_biochemistry_for_session(session_id)

    def create_urinalysis_result(self, result: UrinalysisResult) -> int:
        """Insert a urinalysis result."""
        return self._session_repo.create_urinalysis_result(result)

    def get_urinalysis_for_session(self, session_id: int) -> Optional[UrinalysisResult]:
        """Get urinalysis result for a session."""
        return self._session_repo.get_urinalysis_for_session(session_id)

    def create_session_measurement(self, measurement: SessionMeasurement) -> int:
        """Insert a generic session measurement."""
        return self._session_repo.create_session_measurement(measurement)

    def get_measurements_for_session(self, session_id: int) -> List[SessionMeasurement]:
        """Get generic measurements for a session."""
        return self._session_repo.get_measurements_for_session(session_id)

    def create_pathology_finding(self, finding: PathologyFinding) -> int:
        """Insert a pathology finding."""
        return self._session_repo.create_pathology_finding(finding)

    def get_pathology_findings_for_session(self, session_id: int) -> List[PathologyFinding]:
        """Get pathology findings for a session."""
        return self._session_repo.get_pathology_findings_for_session(session_id)

    def create_session_asset(self, asset: SessionAsset) -> int:
        """Insert an extracted session asset."""
        return self._session_repo.create_session_asset(asset)

    def get_assets_for_session(self, session_id: int) -> List[SessionAsset]:
        """Get stored session assets."""
        return self._session_repo.get_assets_for_session(session_id)

    def find_open_unassigned_report(self, source_system: Optional[str],
                                    external_report_id: Optional[str],
                                    report_number: Optional[str]) -> Optional[UnassignedReport]:
        """Find an already queued report by reference."""
        return self._session_repo.find_open_unassigned_report(
            source_system, external_report_id, report_number
        )

    def create_unassigned_report(self, report: UnassignedReport) -> int:
        """Create a queued unassigned report."""
        return self._session_repo.create_unassigned_report(report)

    def get_unassigned_report(self, report_id: int) -> Optional[UnassignedReport]:
        """Get a queued report."""
        return self._session_repo.get_unassigned_report(report_id)

    def list_unassigned_reports(self, status: str = "pending") -> List[UnassignedReport]:
        """List queued reports."""
        return self._session_repo.list_unassigned_reports(status)

    def mark_unassigned_report_assigned(self, report_id: int, animal_id: int,
                                        session_id: int) -> bool:
        """Mark a queued report as assigned."""
        return self._session_repo.mark_unassigned_report_assigned(
            report_id, animal_id, session_id
        )

    # =========================================================================
    # SYMPTOM OPERATIONS
    # =========================================================================

    def create_symptom(self, symptom: Symptom) -> int:
        """Insert a symptom record."""
        return self._animal_repo.create_symptom(symptom)

    def get_symptoms_for_animal(self, animal_id: int,
                                active_only: bool = False) -> List[Symptom]:
        """Get symptoms for an animal."""
        return self._animal_repo.get_symptoms(animal_id, active_only)

    # =========================================================================
    # OBSERVATION OPERATIONS
    # =========================================================================

    def create_observation(self, observation: Observation) -> int:
        """Insert an observation record."""
        return self._animal_repo.create_observation(observation)

    def get_observations_for_animal(self, animal_id: int,
                                    obs_type: Optional[str] = None) -> List[Observation]:
        """Get observations for an animal, optionally filtered by type."""
        return self._animal_repo.get_observations(animal_id, obs_type)

    # =========================================================================
    # CLINICAL NOTES OPERATIONS
    # =========================================================================

    def create_clinical_note(self, note: ClinicalNote) -> int:
        """Insert a clinical note record."""
        return self._animal_repo.create_clinical_note(note)

    def get_clinical_note(self, note_id: int) -> Optional[ClinicalNote]:
        """Get a clinical note by ID."""
        return self._animal_repo.get_clinical_note(note_id)

    def get_clinical_notes_for_animal(self, animal_id: int) -> List[ClinicalNote]:
        """Get all clinical notes for an animal, ordered by date."""
        return self._animal_repo.get_clinical_notes(animal_id)

    def update_clinical_note(self, note_id: int, title: Optional[str],
                            content: str, note_date: Optional[date] = None) -> bool:
        """Update a clinical note."""
        return self._animal_repo.update_clinical_note(note_id, title, content, note_date)

    def delete_clinical_note(self, note_id: int) -> bool:
        """Delete a clinical note."""
        return self._animal_repo.delete_clinical_note(note_id)

    # =========================================================================
    # DIAGNOSIS REPORT OPERATIONS
    # =========================================================================

    def create_diagnosis_report(self, report: DiagnosisReport) -> int:
        """Insert a diagnosis report and return its ID."""
        return self._diagnosis_repo.create(report)

    def get_diagnosis_report(self, report_id: int) -> Optional[DiagnosisReport]:
        """Get a diagnosis report by ID."""
        return self._diagnosis_repo.get(report_id)

    def get_diagnosis_reports_for_animal(self, animal_id: int) -> List[DiagnosisReport]:
        """Get all diagnosis reports for an animal, ordered by date."""
        return self._diagnosis_repo.get_for_animal(animal_id)

    def delete_diagnosis_report(self, report_id: int) -> bool:
        """Delete a diagnosis report."""
        return self._diagnosis_repo.delete(report_id)

    # =========================================================================
    # USER OPERATIONS
    # =========================================================================

    def create_user(self, user: User) -> int:
        """Insert a new user and return their ID."""
        return self._user_repo.create(user)

    def get_user(self, user_id: int) -> Optional[User]:
        """Get a user by ID."""
        return self._user_repo.get(user_id)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Get a user by email (case-insensitive)."""
        return self._user_repo.get_by_email(email)

    def list_users(self, include_inactive: bool = False) -> List[User]:
        """List all users, optionally including inactive ones."""
        return self._user_repo.list_all(include_inactive)

    def get_pending_users(self) -> List[User]:
        """Get users who are active but not yet approved."""
        return self._user_repo.get_pending()

    def get_superusers(self) -> List[User]:
        """Get all superuser accounts."""
        return self._user_repo.get_superusers()

    def update_user(self, user_id: int, **kwargs) -> bool:
        """Update user fields."""
        return self._user_repo.update(user_id, **kwargs)

    def approve_user(self, user_id: int, approved_by_user_id: int) -> bool:
        """Approve a user account."""
        return self._user_repo.approve(user_id, approved_by_user_id)

    def disable_user(self, user_id: int) -> bool:
        """Disable a user account."""
        return self._user_repo.disable(user_id)

    def enable_user(self, user_id: int) -> bool:
        """Re-enable a user account."""
        return self._user_repo.enable(user_id)

    def user_count(self) -> int:
        """Get total number of users."""
        return self._user_repo.count()

    # =========================================================================
    # PASSWORD RESET TOKEN OPERATIONS
    # =========================================================================

    def create_password_reset_token(self, user_id: int, token_hash: str,
                                    expires_at: datetime) -> int:
        """Create a password reset token."""
        return self._user_repo.create_reset_token(user_id, token_hash, expires_at)

    def get_password_reset_token(self, token_hash: str) -> Optional[PasswordResetToken]:
        """Get a password reset token by its hash."""
        return self._user_repo.get_reset_token(token_hash)

    def mark_token_used(self, token_id: int) -> bool:
        """Mark a password reset token as used."""
        return self._user_repo.mark_token_used(token_id)

    def cleanup_expired_tokens(self) -> int:
        """Remove expired password reset tokens."""
        return self._user_repo.cleanup_expired_tokens()


__all__ = [
    'Database',
    'AnimalRepository',
    'SessionRepository',
    'UserRepository',
    'DiagnosisRepository',
]
