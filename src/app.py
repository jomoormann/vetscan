"""
Veterinary Protein Analysis Application - Main Service

This module provides the high-level interface for:
- Importing PDF reports
- Storing data in the database
- Comparing results over time
- Generating reports
"""

import os
import shutil
from datetime import date, datetime
from typing import Dict, List, Optional
from dataclasses import asdict, dataclass
import json

from models import (
    Database, Animal, TestSession, ProteinResult, SessionAsset,
    Symptom, Observation, AnimalMatchDecision, UnassignedReport
)
from pdf_parser import parse_lab_report, ParsedReport


@dataclass
class ResultComparison:
    """Comparison of a marker value between two test sessions"""
    marker_name: str
    current_value: Optional[float]
    previous_value: Optional[float]
    current_absolute: Optional[float]
    previous_absolute: Optional[float]
    change_percent: Optional[float]
    change_absolute: Optional[float]
    current_flag: str
    previous_flag: str
    trend: str  # "improved", "worsened", "stable", "new"
    clinical_significance: str  # "none", "minor", "moderate", "significant"


@dataclass
class AnalysisReport:
    """Complete analysis report for a test session"""
    animal: Animal
    session: TestSession
    results: List[ProteinResult]
    comparisons: List[ResultComparison]
    symptoms: List[Symptom]
    observations: List[Observation]
    summary: str
    flags_summary: Dict[str, int]  # count of normal/high/low


@dataclass
class ImportOutcome:
    """Result of importing a PDF report."""
    status: str  # imported, pending_review
    parsed: ParsedReport
    animal_id: Optional[int] = None
    session_id: Optional[int] = None
    unassigned_report_id: Optional[int] = None
    match_decision: Optional[AnimalMatchDecision] = None

    @property
    def imported(self) -> bool:
        return self.status == "imported"


class VetProteinService:
    """Main service class for the veterinary protein analysis application"""
    
    def __init__(self, db_path: str = "vet_proteins.db", 
                 uploads_dir: str = "uploads"):
        self.db = Database(db_path)
        self.uploads_dir = uploads_dir
        os.makedirs(uploads_dir, exist_ok=True)
    
    def initialize(self):
        """Initialize the database"""
        self.db.connect()
        self.db.initialize()
    
    def close(self):
        """Close database connection"""
        self.db.close()
    
    def __enter__(self):
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # =========================================================================
    # PDF Import
    # =========================================================================
    
    @staticmethod
    def _json_default(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"Type {type(value)} is not JSON serializable")

    def _ensure_unique_session(self, parsed: ParsedReport):
        if parsed.session.external_report_id and parsed.session.source_system:
            if self.db.session_exists_by_external_reference(
                parsed.session.source_system,
                parsed.session.external_report_id,
            ):
                raise ValueError(
                    f"Report {parsed.session.external_report_id} already exists in database"
                )

        if parsed.session.report_number and self.db.session_exists(parsed.session.report_number):
            raise ValueError(
                f"Report {parsed.session.report_number} already exists in database"
            )

    def _persist_session(self, parsed: ParsedReport, animal_id: int) -> int:
        parsed.session.animal_id = animal_id
        session_id = self.db.create_test_session(parsed.session)

        for result in parsed.results:
            result.session_id = session_id
            self.db.create_protein_result(result)

        if parsed.biochemistry:
            parsed.biochemistry.session_id = session_id
            self.db.create_biochemistry_result(parsed.biochemistry)

        if parsed.urinalysis:
            parsed.urinalysis.session_id = session_id
            self.db.create_urinalysis_result(parsed.urinalysis)

        for measurement in parsed.measurements:
            measurement.session_id = session_id
            measurement.panel_name = measurement.panel_name or parsed.session.panel_name
            self.db.create_session_measurement(measurement)

        for finding in parsed.pathology_findings:
            finding.session_id = session_id
            self.db.create_pathology_finding(finding)

        if parsed.assets:
            assets_dir = os.path.join(self.uploads_dir, "report_assets", str(session_id))
            os.makedirs(assets_dir, exist_ok=True)
            for asset in parsed.assets:
                asset_path = os.path.join(assets_dir, asset.filename)
                with open(asset_path, "wb") as f:
                    f.write(asset.content)
                self.db.create_session_asset(SessionAsset(
                    session_id=session_id,
                    asset_type=asset.asset_type,
                    label=asset.label,
                    file_path=asset_path,
                    page_number=asset.page_number,
                    sort_order=asset.sort_order,
                    metadata_json=json.dumps(asset.metadata, ensure_ascii=False),
                ))

        return session_id

    def _queue_unassigned_report(self, parsed: ParsedReport,
                                 match_decision: AnimalMatchDecision) -> int:
        existing = self.db.find_open_unassigned_report(
            parsed.session.source_system,
            parsed.session.external_report_id,
            parsed.session.report_number,
        )
        if existing:
            return existing.id

        summary = {
            "animal_name": parsed.animal.name,
            "species": parsed.animal.species,
            "breed": parsed.animal.breed,
            "owner_name": parsed.animal.owner_name,
            "microchip": parsed.animal.microchip,
            "age_years": parsed.animal.age_years,
            "sex": parsed.animal.sex,
            "report_number": parsed.session.report_number,
            "external_report_id": parsed.session.external_report_id,
            "report_type": parsed.session.report_type,
            "source_system": parsed.session.source_system,
            "report_source": parsed.session.report_source,
            "test_date": parsed.session.test_date,
            "sample_type": parsed.session.sample_type,
            "lab_name": parsed.session.lab_name,
            "clinic_name": parsed.session.clinic_name,
            "panel_name": parsed.session.panel_name,
            "measurement_count": len(parsed.measurements),
            "protein_result_count": len(parsed.results),
            "pathology_finding_count": len(parsed.pathology_findings),
            "asset_count": len(parsed.assets),
        }

        queued = UnassignedReport(
            filename=os.path.basename(parsed.session.pdf_path or ""),
            pdf_path=parsed.session.pdf_path or "",
            source_system=parsed.session.source_system,
            report_type=parsed.session.report_type,
            report_number=parsed.session.report_number or None,
            external_report_id=parsed.session.external_report_id,
            report_source=parsed.session.report_source,
            animal_name=parsed.animal.name or None,
            species=parsed.animal.species or None,
            owner_name=parsed.animal.owner_name,
            clinic_name=parsed.session.clinic_name,
            report_date=parsed.session.test_date,
            panel_name=parsed.session.panel_name,
            match_reason=match_decision.reason,
            parsed_summary_json=json.dumps(
                summary, ensure_ascii=False, default=self._json_default
            ),
            candidate_matches_json=json.dumps(
                [asdict(candidate) for candidate in match_decision.candidates],
                ensure_ascii=False,
                default=self._json_default,
            ),
        )
        return self.db.create_unassigned_report(queued)

    def _get_pending_report(self, report_id: int) -> UnassignedReport:
        report = self.db.get_unassigned_report(report_id)
        if not report:
            raise ValueError(f"Queued report {report_id} not found")
        if report.status != "pending":
            raise ValueError(f"Queued report {report_id} is already assigned")
        return report

    def import_pdf(self, pdf_path: str,
                   copy_to_uploads: bool = True,
                   report_source: Optional[str] = None,
                   force_animal_id: Optional[int] = None,
                   force_create_animal: bool = False,
                   allow_pending_assignment: bool = True) -> ImportOutcome:
        """
        Import a PDF report into the database.
        """
        if force_animal_id is not None and force_create_animal:
            raise ValueError("force_animal_id and force_create_animal cannot be used together")

        # Parse the PDF
        parsed = parse_lab_report(pdf_path)

        # Check if report already exists
        self._ensure_unique_session(parsed)
        
        # Copy PDF to uploads directory if requested
        stored_path = pdf_path
        if copy_to_uploads:
            filename = os.path.basename(pdf_path)
            stored_path = os.path.join(self.uploads_dir, filename)
            if pdf_path != stored_path:
                shutil.copy2(pdf_path, stored_path)
            parsed.session.pdf_path = stored_path
        else:
            parsed.session.pdf_path = stored_path

        parsed.session.report_source = report_source or parsed.session.report_source or stored_path

        if force_animal_id is not None:
            animal_id = self.db.attach_report_to_animal(
                force_animal_id, parsed.animal, parsed.animal_identifiers
            )
            session_id = self._persist_session(parsed, animal_id)
            return ImportOutcome(
                status="imported",
                animal_id=animal_id,
                session_id=session_id,
                parsed=parsed,
            )

        if force_create_animal:
            animal_id = self.db.create_animal_from_report(
                parsed.animal, parsed.animal_identifiers
            )
            session_id = self._persist_session(parsed, animal_id)
            return ImportOutcome(
                status="imported",
                animal_id=animal_id,
                session_id=session_id,
                parsed=parsed,
            )

        match_decision = self.db.analyze_animal_match(
            parsed.animal, parsed.animal_identifiers
        )

        if match_decision.action == "match_existing" and match_decision.animal_id:
            animal_id = self.db.attach_report_to_animal(
                match_decision.animal_id,
                parsed.animal,
                parsed.animal_identifiers,
            )
            session_id = self._persist_session(parsed, animal_id)
            return ImportOutcome(
                status="imported",
                animal_id=animal_id,
                session_id=session_id,
                parsed=parsed,
                match_decision=match_decision,
            )

        if match_decision.action == "manual_review" and allow_pending_assignment:
            unassigned_report_id = self._queue_unassigned_report(parsed, match_decision)
            return ImportOutcome(
                status="pending_review",
                parsed=parsed,
                unassigned_report_id=unassigned_report_id,
                match_decision=match_decision,
            )

        animal_id = self.db.create_animal_from_report(
            parsed.animal, parsed.animal_identifiers
        )
        session_id = self._persist_session(parsed, animal_id)
        return ImportOutcome(
            status="imported",
            animal_id=animal_id,
            session_id=session_id,
            parsed=parsed,
            match_decision=match_decision,
        )

    def get_unassigned_reports(self, status: str = "pending") -> List[UnassignedReport]:
        """List queued reports awaiting manual assignment."""
        return self.db.list_unassigned_reports(status)

    def assign_unassigned_report_to_animal(self, report_id: int,
                                           animal_id: int) -> ImportOutcome:
        """Assign a queued report to an existing animal."""
        report = self._get_pending_report(report_id)
        if not self.db.get_animal(animal_id):
            raise ValueError(f"Animal {animal_id} not found")

        outcome = self.import_pdf(
            report.pdf_path,
            copy_to_uploads=False,
            report_source=report.report_source,
            force_animal_id=animal_id,
            allow_pending_assignment=False,
        )
        self.db.mark_unassigned_report_assigned(report_id, animal_id, outcome.session_id)
        return outcome

    def create_animal_from_unassigned_report(self, report_id: int) -> ImportOutcome:
        """Assign a queued report by creating a new animal entry."""
        report = self._get_pending_report(report_id)
        outcome = self.import_pdf(
            report.pdf_path,
            copy_to_uploads=False,
            report_source=report.report_source,
            force_create_animal=True,
            allow_pending_assignment=False,
        )
        self.db.mark_unassigned_report_assigned(report_id, outcome.animal_id, outcome.session_id)
        return outcome
    
    # =========================================================================
    # Data Retrieval
    # =========================================================================
    
    def get_animal_history(self, animal_id: int) -> Dict:
        """
        Get complete history for an animal including all tests, symptoms, observations.
        
        Returns:
            Dict with animal info, all sessions, and statistics
        """
        animal = self.db.get_animal(animal_id)
        if not animal:
            raise ValueError(f"Animal with ID {animal_id} not found")
        
        sessions = self.db.get_sessions_for_animal(animal_id)
        symptoms = self.db.get_symptoms_for_animal(animal_id)
        observations = self.db.get_observations_for_animal(animal_id)
        
        # Get all results for all sessions
        all_results = {}
        for session in sessions:
            all_results[session.id] = self.db.get_results_for_session(session.id)
        
        return {
            'animal': animal,
            'sessions': sessions,
            'results': all_results,
            'symptoms': symptoms,
            'observations': observations,
            'total_tests': len(sessions),
            'date_range': (
                sessions[-1].test_date if sessions else None,
                sessions[0].test_date if sessions else None
            )
        }
    
    def get_marker_trend(self, animal_id: int, marker_name: str) -> List[Dict]:
        """
        Get the trend of a specific marker over time for an animal.
        
        Returns:
            List of dicts with date, value, flag for each test
        """
        return self.db.get_marker_history(animal_id, marker_name)
    
    # =========================================================================
    # Comparison & Analysis
    # =========================================================================
    
    def compare_sessions(self, current_session_id: int, 
                        previous_session_id: int) -> List[ResultComparison]:
        """
        Compare results between two test sessions.
        
        Args:
            current_session_id: ID of the more recent session
            previous_session_id: ID of the older session
            
        Returns:
            List of ResultComparison objects
        """
        current_results = self.db.get_results_for_session(current_session_id)
        previous_results = self.db.get_results_for_session(previous_session_id)
        
        # Index previous results by marker name
        prev_by_marker = {r.marker_name: r for r in previous_results}
        
        comparisons = []
        for current in current_results:
            previous = prev_by_marker.get(current.marker_name)
            
            comparison = self._compare_single_marker(current, previous)
            comparisons.append(comparison)
        
        return comparisons
    
    def _compare_single_marker(self, current: ProteinResult, 
                               previous: Optional[ProteinResult]) -> ResultComparison:
        """Compare a single marker between two results"""
        if previous is None:
            return ResultComparison(
                marker_name=current.marker_name,
                current_value=current.value,
                previous_value=None,
                current_absolute=current.value_absolute,
                previous_absolute=None,
                change_percent=None,
                change_absolute=None,
                current_flag=current.flag,
                previous_flag="",
                trend="new",
                clinical_significance="none"
            )
        
        # Calculate changes
        change_percent = None
        change_absolute = None
        
        if current.value is not None and previous.value is not None:
            if previous.value != 0:
                change_percent = ((current.value - previous.value) / previous.value) * 100
        
        if current.value_absolute is not None and previous.value_absolute is not None:
            change_absolute = current.value_absolute - previous.value_absolute
        
        # Determine trend
        trend = self._determine_trend(current, previous, change_percent)
        
        # Determine clinical significance
        significance = self._determine_significance(
            current, previous, change_percent, change_absolute
        )
        
        return ResultComparison(
            marker_name=current.marker_name,
            current_value=current.value,
            previous_value=previous.value,
            current_absolute=current.value_absolute,
            previous_absolute=previous.value_absolute,
            change_percent=change_percent,
            change_absolute=change_absolute,
            current_flag=current.flag,
            previous_flag=previous.flag,
            trend=trend,
            clinical_significance=significance
        )
    
    def _determine_trend(self, current: ProteinResult, 
                        previous: ProteinResult, 
                        change_percent: Optional[float]) -> str:
        """Determine if the trend is improving, worsening, or stable"""
        if change_percent is None:
            return "stable"
        
        # Consider anything < 5% change as stable
        if abs(change_percent) < 5:
            return "stable"
        
        # If current is normal but previous wasn't, it's improved
        if current.flag == "normal" and previous.flag != "normal":
            return "improved"
        
        # If current is abnormal but previous was normal, it's worsened
        if current.flag != "normal" and previous.flag == "normal":
            return "worsened"
        
        # If both abnormal, check if moving toward or away from normal
        if current.flag == previous.flag:
            # Still in the same abnormal state - check direction
            if change_percent > 5:
                return "worsened" if current.flag == "high" else "improved"
            elif change_percent < -5:
                return "improved" if current.flag == "high" else "worsened"
        
        return "stable"
    
    def _determine_significance(self, current: ProteinResult,
                               previous: ProteinResult,
                               change_percent: Optional[float],
                               change_absolute: Optional[float]) -> str:
        """Determine clinical significance of the change"""
        # Flag change is always significant
        if current.flag != previous.flag:
            if current.flag == "normal" or previous.flag == "normal":
                return "significant"
            return "moderate"
        
        if change_percent is None:
            return "none"
        
        # Threshold-based significance
        if abs(change_percent) > 20:
            return "significant"
        elif abs(change_percent) > 10:
            return "moderate"
        elif abs(change_percent) > 5:
            return "minor"
        
        return "none"
    
    def generate_analysis_report(self, session_id: int) -> AnalysisReport:
        """
        Generate a complete analysis report for a test session.
        
        Includes comparison with the most recent previous test if available.
        """
        # Get session and results
        cursor = self.db.conn.execute(
            "SELECT * FROM test_sessions WHERE id = ?", (session_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Session {session_id} not found")
        
        session = TestSession(**dict(row))
        animal = self.db.get_animal(session.animal_id)
        results = self.db.get_results_for_session(session_id)
        symptoms = self.db.get_symptoms_for_animal(session.animal_id, active_only=True)
        observations = self.db.get_observations_for_animal(session.animal_id)
        
        # Get previous session for comparison
        all_sessions = self.db.get_sessions_for_animal(session.animal_id)
        comparisons = []
        
        # Find the previous session (the one just before this one)
        prev_session = None
        for i, s in enumerate(all_sessions):
            if s.id == session_id and i + 1 < len(all_sessions):
                prev_session = all_sessions[i + 1]
                break
        
        if prev_session:
            comparisons = self.compare_sessions(session_id, prev_session.id)
        
        # Count flags
        flags_summary = {'normal': 0, 'high': 0, 'low': 0}
        for r in results:
            if r.flag in flags_summary:
                flags_summary[r.flag] += 1
        
        # Generate summary
        summary = self._generate_summary(animal, results, comparisons, symptoms)
        
        return AnalysisReport(
            animal=animal,
            session=session,
            results=results,
            comparisons=comparisons,
            symptoms=symptoms,
            observations=observations[:5],  # Last 5 observations
            summary=summary,
            flags_summary=flags_summary
        )
    
    def _generate_summary(self, animal: Animal, results: List[ProteinResult],
                         comparisons: List[ResultComparison],
                         symptoms: List[Symptom]) -> str:
        """Generate a text summary of the analysis"""
        lines = []
        
        # Abnormal values
        abnormal = [r for r in results if r.flag != "normal"]
        if abnormal:
            lines.append("⚠️ Valores fora do intervalo de referência:")
            for r in abnormal:
                direction = "elevado" if r.flag == "high" else "baixo"
                lines.append(f"  • {r.marker_name}: {r.value}{r.unit} ({direction})")
        else:
            lines.append("✓ Todos os valores dentro do intervalo de referência.")
        
        # Significant changes
        if comparisons:
            significant_changes = [c for c in comparisons 
                                  if c.clinical_significance in ('moderate', 'significant')]
            if significant_changes:
                lines.append("\n📊 Alterações significativas desde o último teste:")
                for c in significant_changes:
                    if c.change_percent is not None:
                        direction = "↑" if c.change_percent > 0 else "↓"
                        lines.append(
                            f"  • {c.marker_name}: {direction} {abs(c.change_percent):.1f}% "
                            f"({c.previous_value} → {c.current_value})"
                        )
        
        # Current symptoms context
        if symptoms:
            lines.append(f"\n📝 Sintomas ativos registados: {len(symptoms)}")
            for s in symptoms[:3]:
                lines.append(f"  • {s.description} ({s.severity})")
        
        return "\n".join(lines)
    
    # =========================================================================
    # Symptom & Observation Management
    # =========================================================================
    
    def add_symptom(self, animal_id: int, description: str, 
                   severity: str = "mild", category: str = None,
                   observed_date: date = None) -> int:
        """Add a symptom record for an animal"""
        symptom = Symptom(
            animal_id=animal_id,
            description=description,
            severity=severity,
            category=category,
            observed_date=observed_date or date.today()
        )
        return self.db.create_symptom(symptom)
    
    def resolve_symptom(self, symptom_id: int, resolved_date: date = None):
        """Mark a symptom as resolved"""
        resolved = resolved_date or date.today()
        self.db.conn.execute(
            "UPDATE symptoms SET resolved_date = ? WHERE id = ?",
            (resolved, symptom_id)
        )
        self.db.conn.commit()
    
    def add_observation(self, animal_id: int, obs_type: str, 
                       details: str, value: float = None,
                       unit: str = None, obs_date: date = None) -> int:
        """Add an observation (weight, medication, etc.) for an animal"""
        observation = Observation(
            animal_id=animal_id,
            observation_type=obs_type,
            details=details,
            value=value,
            unit=unit,
            observation_date=obs_date or date.today()
        )
        return self.db.create_observation(observation)


# =============================================================================
# CLI DEMO
# =============================================================================

def demo():
    """Demonstrate the application with multiple PDF files"""
    
    print("=" * 70)
    print("🐕 Veterinary Protein Analysis Application - Demo")
    print("=" * 70)
    
    # Use in-memory database for demo
    with VetProteinService(db_path=":memory:", uploads_dir="/tmp/vet_uploads") as service:
        
        # Import all available PDFs
        pdf_files = [
            "/mnt/user-data/uploads/bolt58630_1500951.pdf",
            "/mnt/user-data/uploads/bolt65401_1517628__1_.pdf",
            "/mnt/user-data/uploads/bolt66790_1521038_copy.pdf",
        ]
        
        imported = []
        for pdf_path in pdf_files:
            print(f"\n📄 Importing: {pdf_path.split('/')[-1]}")
            try:
                outcome = service.import_pdf(
                    pdf_path, copy_to_uploads=False
                )
                parsed = outcome.parsed
                if outcome.imported:
                    imported.append((outcome.animal_id, outcome.session_id, parsed))
                    print(f"   ✓ {parsed.animal.name} - {parsed.session.report_number}")
                    print(f"     Date: {parsed.session.test_date}")
                    print(f"     Sample: {parsed.session.sample_type}")
                    if parsed.biochemistry:
                        print(f"     UPC Ratio: {parsed.biochemistry.upc_ratio} ({parsed.biochemistry.upc_status})")
                    if parsed.urinalysis:
                        print(f"     Urinalysis: pH {parsed.urinalysis.ph}, Density {parsed.urinalysis.specific_gravity}")
                else:
                    print(f"   ? Queued for manual review: {parsed.animal.name}")
                    
            except Exception as e:
                print(f"   ✗ Error: {e}")
        
        # Show Bobby's history (has 2 tests)
        print("\n" + "=" * 70)
        print("📊 BOBBY'S TEST HISTORY (2 tests)")
        print("=" * 70)
        
        # Get Bobby's animal ID (should be 1)
        bobby_sessions = [i for i in imported if i[2].animal.name == "Bobby"]
        
        if len(bobby_sessions) >= 2:
            # Sort by date
            bobby_sessions.sort(key=lambda x: x[2].session.test_date or date.min)
            
            first = bobby_sessions[0]
            second = bobby_sessions[1]
            
            print(f"\n📅 Test 1: {first[2].session.test_date} (Report: {first[2].session.report_number})")
            print(f"📅 Test 2: {second[2].session.test_date} (Report: {second[2].session.report_number})")
            
            # Compare UPC ratios
            if first[2].biochemistry and second[2].biochemistry:
                upc1 = first[2].biochemistry.upc_ratio
                upc2 = second[2].biochemistry.upc_ratio
                change = ((upc2 - upc1) / upc1 * 100) if upc1 else 0
                print(f"\n🧪 UPC Ratio Trend:")
                print(f"   {first[2].session.test_date}: {upc1} ({first[2].biochemistry.upc_status})")
                print(f"   {second[2].session.test_date}: {upc2} ({second[2].biochemistry.upc_status})")
                print(f"   Change: {change:+.1f}%")
                if upc2 < upc1:
                    print(f"   ✓ Improvement - proteinuria decreased")
                else:
                    print(f"   ⚠ Proteinuria increased")
            
            # Compare protein results
            print(f"\n🔬 Protein Comparison:")
            print("-" * 70)
            print(f"{'Marker':<20} {'Oct 25':<12} {'Nov 28':<12} {'Change':<12} {'Trend'}")
            print("-" * 70)
            
            # Index results by marker name
            results1 = {r.marker_name: r for r in first[2].results}
            results2 = {r.marker_name: r for r in second[2].results}
            
            for marker in results1:
                if marker in results2:
                    r1, r2 = results1[marker], results2[marker]
                    v1 = f"{r1.value:.1f}" if r1.value else "N/A"
                    v2 = f"{r2.value:.1f}" if r2.value else "N/A"
                    
                    if r1.value and r2.value:
                        change = ((r2.value - r1.value) / r1.value * 100)
                        change_str = f"{change:+.1f}%"
                        # Determine trend based on flags
                        if r2.flag == "normal" and r1.flag != "normal":
                            trend = "✓ Improved"
                        elif r2.flag != "normal" and r1.flag == "normal":
                            trend = "⚠ Worsened"
                        elif abs(change) < 5:
                            trend = "→ Stable"
                        else:
                            trend = "↑" if change > 0 else "↓"
                    else:
                        change_str = "N/A"
                        trend = "?"
                    
                    print(f"  {marker:<18} {v1:<12} {v2:<12} {change_str:<12} {trend}")
            
            # Show urinalysis comparison
            if first[2].urinalysis and second[2].urinalysis:
                ua1, ua2 = first[2].urinalysis, second[2].urinalysis
                print(f"\n💧 Urinalysis Comparison:")
                print(f"  {'Parameter':<20} {'Oct 25':<15} {'Nov 28':<15}")
                print(f"  {'-'*50}")
                print(f"  {'pH':<20} {ua1.ph:<15} {ua2.ph:<15}")
                print(f"  {'Density':<20} {ua1.specific_gravity:<15} {ua2.specific_gravity:<15}")
                print(f"  {'Proteins':<20} {ua1.proteins:<15} {ua2.proteins:<15}")
                print(f"  {'Crystals':<20} {ua1.crystals:<15} {ua2.crystals:<15}")
                print(f"  {'Appearance':<20} {ua1.appearance:<15} {ua2.appearance:<15}")
        
        # Show all animals in database
        print("\n" + "=" * 70)
        print("📋 ALL ANIMALS IN DATABASE")
        print("=" * 70)
        
        animals = service.db.list_animals()
        for animal in animals:
            sessions = service.db.get_sessions_for_animal(animal.id)
            print(f"\n  {animal.name} ({animal.species} - {animal.breed})")
            print(f"    Age: {animal.age_display}, Sex: {animal.sex}")
            print(f"    Tests: {len(sessions)}")
            for s in sessions:
                print(f"      - {s.test_date}: {s.report_number}")
        
        print("\n" + "=" * 70)
        print("Demo complete!")


if __name__ == "__main__":
    demo()
