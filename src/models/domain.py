"""
Domain Models for VetScan

Contains all dataclasses representing domain entities.
Based on DNAtech lab report format (Portuguese).
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from .enums import ResultFlag


# =============================================================================
# CORE DOMAIN MODELS
# =============================================================================

@dataclass
class Animal:
    """
    Represents an animal patient.

    Fields match DNAtech report structure:
    - ID Animal (optional in their system)
    - Animal (name)
    - Especie (species)
    - Raca (breed)
    - Microchip
    - Idade (age)
    """
    id: Optional[int] = None
    name: str = ""
    species: str = "Canídeo"  # Default to dog
    breed: str = ""
    microchip: Optional[str] = None
    owner_name: Optional[str] = None
    age_years: Optional[float] = None
    age_months: Optional[int] = None
    sex: str = "U"
    weight_kg: Optional[float] = None
    neutered: Optional[bool] = None
    medical_history: Optional[str] = None
    notes: Optional[str] = None
    responsible_vet: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def age_display(self) -> str:
        """Format age for display (e.g., '7 A' for 7 years)."""
        if self.age_years:
            if self.age_years >= 1:
                return f"{int(self.age_years)} A"
            elif self.age_months:
                return f"{self.age_months} M"
        return "Unknown"


@dataclass
class TestSession:
    """
    Represents a single test session/report.

    Maps to DNAtech report header:
    - Folha de Trabalho No (report number)
    - Data (test date)
    - Amostra (sample type)
    - Data de fecho (closing date)
    """
    id: Optional[int] = None
    animal_id: int = 0
    report_number: str = ""  # Folha de Trabalho No (e.g., "66790/1521038")
    test_date: Optional[date] = None
    closing_date: Optional[date] = None
    sample_type: str = "Soro"  # Serum
    lab_name: str = "DNAtech"
    source_system: str = "dnatech"
    report_type: str = "dnatech_proteinogram"
    external_report_id: Optional[str] = None
    report_source: Optional[str] = None
    reported_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    clinic_name: Optional[str] = None
    panel_name: Optional[str] = None
    raw_metadata_json: Optional[str] = None
    pdf_path: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class ProteinResult:
    """
    Represents a single protein measurement result.

    Maps to DNAtech result rows:
    - Analise (marker name)
    - Resultado (value - may have both % and g/dL)
    - Un. (unit)
    - Ref. (reference range)
    - Historico (historical reference - typically for g/dL)
    """
    id: Optional[int] = None
    session_id: int = 0
    marker_name: str = ""  # e.g., "Albumina", "Alfa 1", "Gama"
    marker_category: str = "PROTEINOGRAMA"

    # Primary value (percentage for fractions, absolute for totals)
    value: Optional[float] = None
    unit: str = ""  # e.g., "%", "g/dL"

    # Secondary value (g/dL absolute value for protein fractions)
    value_absolute: Optional[float] = None
    unit_absolute: str = "g/dL"

    # Reference ranges (percentage)
    reference_min: Optional[float] = None
    reference_max: Optional[float] = None

    # Reference ranges (absolute g/dL) - from "Historico" column
    reference_min_absolute: Optional[float] = None
    reference_max_absolute: Optional[float] = None

    # Computed flags
    flag: str = "normal"
    flag_absolute: str = "normal"

    def compute_flags(self):
        """Compute result flags based on reference ranges."""
        # Check percentage value
        if self.value is not None and self.reference_min is not None and self.reference_max is not None:
            if self.value < self.reference_min:
                self.flag = ResultFlag.LOW.value
            elif self.value > self.reference_max:
                self.flag = ResultFlag.HIGH.value
            else:
                self.flag = ResultFlag.NORMAL.value

        # Check absolute value
        if self.value_absolute is not None and self.reference_min_absolute is not None and self.reference_max_absolute is not None:
            if self.value_absolute < self.reference_min_absolute:
                self.flag_absolute = ResultFlag.LOW.value
            elif self.value_absolute > self.reference_max_absolute:
                self.flag_absolute = ResultFlag.HIGH.value
            else:
                self.flag_absolute = ResultFlag.NORMAL.value


@dataclass
class Symptom:
    """
    Represents a clinical symptom or observation.

    Allows veterinarian to record symptoms associated with an animal
    for correlation with test results.
    """
    id: Optional[int] = None
    animal_id: int = 0
    observed_date: Optional[date] = None
    description: str = ""
    severity: str = "mild"
    category: Optional[str] = None  # e.g., "gastrointestinal", "neurological"
    resolved_date: Optional[date] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class Observation:
    """General observations about the animal (weight, diet, medications, etc.)."""
    id: Optional[int] = None
    animal_id: int = 0
    observation_date: Optional[date] = None
    observation_type: str = ""  # weight, diet_change, medication, vaccination, etc.
    details: str = ""
    value: Optional[float] = None  # For numeric observations like weight
    unit: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class ClinicalNote:
    """
    Clinical notes for an animal.

    Detailed text records of consultations, physical exams, anamnesis,
    diagnostic findings, etc.
    """
    id: Optional[int] = None
    animal_id: int = 0
    note_date: Optional[date] = None
    title: Optional[str] = None  # e.g., "Consulta", "Anamnese", "Exame Fisico"
    content: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class DiagnosisReport:
    """
    AI-generated differential diagnosis report.

    Stores diagnosis reports generated by Claude and OpenAI APIs based on:
    - Clinical notes only (clinical_notes_only)
    - Comprehensive analysis including test data (comprehensive)
    """
    id: Optional[int] = None
    animal_id: int = 0
    report_date: Optional[date] = None
    report_type: str = ""  # "clinical_notes_only" or "comprehensive"
    input_summary: str = ""

    # Executive Summary (generated by OpenAI combining both reports)
    executive_summary: str = ""

    # Claude (Anthropic) results
    test_interpretation: str = ""  # Comprehensive only
    differential_diagnosis: str = ""  # Main diagnosis content (markdown)
    recommendations: str = ""
    references: str = ""
    model_used: str = "claude-sonnet-4-20250514"

    # OpenAI results
    openai_test_interpretation: str = ""
    openai_differential_diagnosis: str = ""
    openai_recommendations: str = ""
    openai_references: str = ""
    openai_model_used: str = "gpt-5-mini"

    # Metadata
    created_at: Optional[datetime] = None


# =============================================================================
# ADDITIONAL RESULT MODELS
# =============================================================================

@dataclass
class BiochemistryResult:
    """
    Represents biochemistry results (BIOQUIMICA section).

    Includes UPC ratio (protein/creatinine ratio) for kidney disease staging.
    """
    id: Optional[int] = None
    session_id: int = 0

    # UPC Ratio (Racio Proteinas Totais/Creatinina Urina)
    upc_ratio: Optional[float] = None
    upc_status: str = ""  # "nao_proteinurico", "suspeito", "proteinurico"

    # Individual urine values
    urine_total_protein: Optional[float] = None  # P.TOTAIS (URINA) mg/dl
    urine_creatinine: Optional[float] = None  # CREATININA (URINA) mg/dl

    # IRIS CKD staging reference
    iris_stage: Optional[str] = None

    notes: Optional[str] = None

    def compute_upc_status(self):
        """Determine proteinuria status based on IRIS guidelines."""
        if self.upc_ratio is None:
            return
        if self.upc_ratio < 0.2:
            self.upc_status = "nao_proteinurico"
        elif self.upc_ratio <= 0.5:
            self.upc_status = "suspeito"
        else:
            self.upc_status = "proteinurico"


@dataclass
class UrinalysisResult:
    """
    Represents urinalysis results (URINA TIPO II).

    Complete urine analysis including:
    - General characteristics
    - Biochemistry
    - Microscopic sediment examination
    """
    id: Optional[int] = None
    session_id: int = 0

    # General characteristics (CARACTERES GERAIS)
    color: Optional[str] = None  # Cor (e.g., "Amarela Clara")
    appearance: Optional[str] = None  # Aspecto (e.g., "Limpido", "Turvo")

    # Biochemistry (Bioquimica Urinaria)
    glucose: Optional[str] = None
    bilirubin: Optional[str] = None  # Bilirrubina
    ketones: Optional[str] = None  # Corpos cetonicos
    specific_gravity: Optional[float] = None  # Densidade (1.012-1.050)
    ph: Optional[float] = None  # pH (5.0-7.0)
    proteins: Optional[str] = None  # Proteinas (mg/dL or Negativo)
    proteins_value: Optional[float] = None
    urobilinogen: Optional[str] = None  # Urobilinogenio
    nitrites: Optional[str] = None  # Nitritos

    # Microscopic sediment (EXAME MICROSCOPICO DO SEDIMENTO)
    leukocytes: Optional[str] = None  # Leucocitos (0-5/campo)
    erythrocytes: Optional[str] = None  # Eritrocitos (0-5/campo)
    epithelial_cells: Optional[str] = None  # Celulas Epiteliais
    casts: Optional[str] = None  # Cilindros
    crystals: Optional[str] = None  # Cristais
    mucus: Optional[str] = None  # Muco
    bacteria: Optional[str] = None  # Bacterias

    # Observations
    observations: Optional[str] = None

    # Flags for abnormal values
    flags: Optional[str] = None  # JSON string of flagged parameters

    def compute_flags(self) -> List[str]:
        """Identify abnormal values."""
        flagged = []

        # Check specific gravity
        if self.specific_gravity:
            if self.specific_gravity < 1.012 or self.specific_gravity > 1.050:
                flagged.append(f"Densidade: {self.specific_gravity}")

        # Check pH
        if self.ph:
            if self.ph < 5.0 or self.ph > 7.0:
                flagged.append(f"pH: {self.ph}")

        # Check proteins
        if self.proteins and self.proteins.lower() != "negativo":
            flagged.append(f"Proteinas: {self.proteins}")

        # Check crystals
        if self.crystals and self.crystals.lower() not in ["ausentes", "ausente"]:
            flagged.append(f"Cristais: {self.crystals}")

        # Check bacteria
        if self.bacteria and self.bacteria.lower() not in ["ausentes", "ausente"]:
            flagged.append(f"Bacterias: {self.bacteria}")

        self.flags = json.dumps(flagged, ensure_ascii=False) if flagged else None
        return flagged


@dataclass
class AnimalIdentifier:
    """External identifier for an animal in a source system."""
    id: Optional[int] = None
    animal_id: int = 0
    source_system: str = ""
    identifier_type: str = ""
    identifier_value: str = ""
    created_at: Optional[datetime] = None


@dataclass
class SessionMeasurement:
    """Generic structured measurement for non-protein reports."""
    id: Optional[int] = None
    session_id: int = 0
    panel_name: Optional[str] = None
    measurement_code: str = ""
    measurement_name: str = ""
    value_numeric: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    reference_min: Optional[float] = None
    reference_max: Optional[float] = None
    reference_text: Optional[str] = None
    flag: str = "normal"
    sort_order: int = 0
    created_at: Optional[datetime] = None


@dataclass
class PathologyFinding:
    """Narrative pathology finding, optionally scoped to a specimen."""
    id: Optional[int] = None
    session_id: int = 0
    section_type: str = ""
    specimen_label: Optional[str] = None
    title: Optional[str] = None
    sample_site: Optional[str] = None
    sample_method: Optional[str] = None
    clinical_history: Optional[str] = None
    microscopic_description: Optional[str] = None
    diagnosis: Optional[str] = None
    comment: Optional[str] = None
    sort_order: int = 0
    created_at: Optional[datetime] = None


@dataclass
class SessionAsset:
    """Stored asset extracted from a PDF report."""
    id: Optional[int] = None
    session_id: int = 0
    asset_type: str = ""
    label: Optional[str] = None
    file_path: str = ""
    page_number: Optional[int] = None
    sort_order: int = 0
    metadata_json: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class AnimalMatchCandidate:
    """Possible existing-animal match for a parsed report."""
    animal_id: int = 0
    name: str = ""
    species: str = ""
    owner_name: Optional[str] = None
    microchip: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


@dataclass
class AnimalMatchDecision:
    """Result of evaluating whether a report can be assigned automatically."""
    action: str = "create_new"  # match_existing, create_new, manual_review
    animal_id: Optional[int] = None
    confidence: float = 0.0
    reason: str = ""
    candidates: List[AnimalMatchCandidate] = field(default_factory=list)


@dataclass
class UnassignedReport:
    """Parsed report waiting for manual assignment to an animal."""
    id: Optional[int] = None
    filename: str = ""
    pdf_path: str = ""
    source_system: Optional[str] = None
    report_type: Optional[str] = None
    report_number: Optional[str] = None
    external_report_id: Optional[str] = None
    report_source: Optional[str] = None
    animal_name: Optional[str] = None
    species: Optional[str] = None
    owner_name: Optional[str] = None
    clinic_name: Optional[str] = None
    report_date: Optional[date] = None
    panel_name: Optional[str] = None
    match_reason: Optional[str] = None
    parsed_summary_json: Optional[str] = None
    candidate_matches_json: Optional[str] = None
    status: str = "pending"
    assigned_animal_id: Optional[int] = None
    session_id: Optional[int] = None
    created_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None


# =============================================================================
# USER MODELS
# =============================================================================

@dataclass
class User:
    """
    Represents an authenticated user of the system.

    Users must be approved by an admin before they can access the system.
    """
    id: Optional[int] = None
    email: str = ""
    email_normalized: str = ""  # Lowercase for case-insensitive lookups
    password_hash: str = ""
    display_name: Optional[str] = None
    is_active: bool = True
    is_approved: bool = False
    is_superuser: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by_user_id: Optional[int] = None


@dataclass
class PasswordResetToken:
    """
    Represents a password reset token.

    Tokens are hashed in the database and have a 1-hour expiry.
    """
    id: Optional[int] = None
    user_id: int = 0
    token_hash: str = ""
    expires_at: Optional[datetime] = None
    used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


# =============================================================================
# REFERENCE DATA
# =============================================================================

PROTEIN_MARKERS = {
    "proteinas_totais": {
        "name_pt": "Proteinas totais",
        "name_en": "Total Proteins",
        "unit": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Total serum protein concentration"
    },
    "albumina": {
        "name_pt": "Albumina",
        "name_en": "Albumin",
        "unit": "%",
        "unit_absolute": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Main plasma protein, synthesized by liver"
    },
    "alfa_1": {
        "name_pt": "Alfa 1",
        "name_en": "Alpha-1 globulin",
        "unit": "%",
        "unit_absolute": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Alpha-1 globulin fraction"
    },
    "alfa_2": {
        "name_pt": "Alfa 2",
        "name_en": "Alpha-2 globulin",
        "unit": "%",
        "unit_absolute": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Alpha-2 globulin fraction, includes haptoglobin"
    },
    "beta": {
        "name_pt": "Beta",
        "name_en": "Beta globulin",
        "unit": "%",
        "unit_absolute": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Beta globulin fraction, includes transferrin and complement"
    },
    "gama": {
        "name_pt": "Gama",
        "name_en": "Gamma globulin",
        "unit": "%",
        "unit_absolute": "g/dL",
        "category": "PROTEINOGRAMA",
        "description": "Gamma globulin fraction, includes immunoglobulins"
    },
    "rel_albumina_globulina": {
        "name_pt": "Rel. Albumina/Globulina",
        "name_en": "Albumin/Globulin Ratio",
        "unit": "ratio",
        "category": "PROTEINOGRAMA",
        "description": "Ratio of albumin to total globulins"
    }
}
