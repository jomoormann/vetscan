"""
Database Models for Veterinary Protein Analysis Application

This module defines the data model for storing and analyzing
protein electrophoresis results from blood tests.

Based on DNAtech lab report format (Portuguese).
"""

import sqlite3
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import json


# =============================================================================
# ENUMS
# =============================================================================

class Species(Enum):
    """Supported animal species"""
    CANINE = "Canídeo"      # Dog
    FELINE = "Felídeo"      # Cat (for future expansion)
    
class Sex(Enum):
    """Animal sex"""
    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"

class ResultFlag(Enum):
    """Flag indicating if result is within reference range"""
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"

class SymptomSeverity(Enum):
    """Severity level for symptoms"""
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


# =============================================================================
# DATA CLASSES (Domain Models)
# =============================================================================

@dataclass
class Animal:
    """
    Represents an animal patient.
    
    Fields match DNAtech report structure:
    - ID Animal (optional in their system)
    - Animal (name)
    - Espécie (species)
    - Raça (breed)
    - Microchip
    - Idade (age)
    """
    id: Optional[int] = None
    name: str = ""
    species: str = "Canídeo"  # Default to dog as per requirements
    breed: str = ""
    microchip: Optional[str] = None
    age_years: Optional[float] = None
    age_months: Optional[int] = None
    sex: str = "U"
    weight_kg: Optional[float] = None
    neutered: Optional[bool] = None
    medical_history: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def age_display(self) -> str:
        """Format age for display (e.g., '7 A' for 7 years)"""
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
    - Folha de Trabalho Nº (report number)
    - Data (test date)
    - Amostra (sample type)
    - Data de fecho (closing date)
    """
    id: Optional[int] = None
    animal_id: int = 0
    report_number: str = ""          # Folha de Trabalho Nº (e.g., "66790/1521038")
    test_date: Optional[date] = None  # Data
    closing_date: Optional[date] = None  # Data de fecho
    sample_type: str = "Soro"        # Amostra (Serum)
    lab_name: str = "DNAtech"
    pdf_path: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class ProteinResult:
    """
    Represents a single protein measurement result.
    
    Maps to DNAtech result rows:
    - Análise (marker name)
    - Resultado (value - may have both % and g/dL)
    - Un. (unit)
    - Ref. (reference range)
    - Histórico (historical reference - typically for g/dL)
    """
    id: Optional[int] = None
    session_id: int = 0
    marker_name: str = ""            # e.g., "Albumina", "Alfa 1", "Gama"
    marker_category: str = "PROTEINOGRAMA"  # Analysis category
    
    # Primary value (percentage for fractions, absolute for totals)
    value: Optional[float] = None
    unit: str = ""                   # e.g., "%", "g/dL"
    
    # Secondary value (g/dL absolute value for protein fractions)
    value_absolute: Optional[float] = None
    unit_absolute: str = "g/dL"
    
    # Reference ranges (percentage)
    reference_min: Optional[float] = None
    reference_max: Optional[float] = None
    
    # Reference ranges (absolute g/dL) - from "Histórico" column
    reference_min_absolute: Optional[float] = None
    reference_max_absolute: Optional[float] = None
    
    # Computed flag
    flag: str = "normal"
    flag_absolute: str = "normal"
    
    def compute_flags(self):
        """Compute result flags based on reference ranges"""
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
    """
    General observations about the animal (weight, diet, medications, etc.)
    """
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
    Clinical notes for an animal - detailed text records of consultations,
    physical exams, anamnesis, diagnostic findings, etc.
    """
    id: Optional[int] = None
    animal_id: int = 0
    note_date: Optional[date] = None
    title: Optional[str] = None  # e.g., "Consulta", "Anamnese", "Exame Físico"
    content: str = ""  # Main text content
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
    input_summary: str = ""  # Summary of what was analyzed
    # Executive Summary (generated by OpenAI combining both reports)
    executive_summary: str = ""
    # Claude (Anthropic) results
    test_interpretation: str = ""  # Detailed test results interpretation (comprehensive only)
    differential_diagnosis: str = ""  # Main diagnosis content (markdown)
    recommendations: str = ""  # Suggested next steps
    references: str = ""  # Veterinary literature references
    model_used: str = "claude-sonnet-4-20250514"
    # OpenAI (GPT-5 mini) results
    openai_test_interpretation: str = ""
    openai_differential_diagnosis: str = ""
    openai_recommendations: str = ""
    openai_references: str = ""
    openai_model_used: str = "gpt-5-mini"
    # Metadata
    created_at: Optional[datetime] = None


# =============================================================================
# ADDITIONAL RESULT CLASSES
# =============================================================================

@dataclass
class BiochemistryResult:
    """
    Represents biochemistry results (BIOQUIMICA section).
    
    Includes UPC ratio (protein/creatinine ratio) for kidney disease staging.
    """
    id: Optional[int] = None
    session_id: int = 0
    
    # UPC Ratio (Rácio Proteínas Totais/Creatinina Urina)
    upc_ratio: Optional[float] = None
    upc_status: str = ""  # "nao_proteinurico", "suspeito", "proteinurico"
    
    # Individual urine values
    urine_total_protein: Optional[float] = None  # P.TOTAIS (URINA) mg/dl
    urine_creatinine: Optional[float] = None     # CREATININA (URINA) mg/dl
    
    # IRIS CKD staging reference
    iris_stage: Optional[str] = None
    
    notes: Optional[str] = None
    
    def compute_upc_status(self):
        """Determine proteinuria status based on IRIS guidelines"""
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
    color: Optional[str] = None           # Cor (e.g., "Amarela Clara")
    appearance: Optional[str] = None       # Aspecto (e.g., "Límpido", "Turvo")
    
    # Biochemistry (Bioquímica Urinária)
    glucose: Optional[str] = None          # Negativo or value
    bilirubin: Optional[str] = None        # Bilirrubina
    ketones: Optional[str] = None          # Corpos cetónicos
    specific_gravity: Optional[float] = None  # Densidade (1.012-1.050)
    ph: Optional[float] = None             # pH (5.0-7.0)
    proteins: Optional[str] = None         # Proteínas (mg/dL or Negativo)
    proteins_value: Optional[float] = None # Numeric value if present
    urobilinogen: Optional[str] = None     # Urobilinogénio
    nitrites: Optional[str] = None         # Nitritos
    
    # Microscopic sediment (EXAME MICROSCÓPICO DO SEDIMENTO)
    leukocytes: Optional[str] = None       # Leucócitos (0-5/campo)
    erythrocytes: Optional[str] = None     # Eritrócitos (0-5/campo)
    epithelial_cells: Optional[str] = None # Células Epiteliais
    casts: Optional[str] = None            # Cilindros
    crystals: Optional[str] = None         # Cristais
    mucus: Optional[str] = None            # Muco
    bacteria: Optional[str] = None         # Bactérias
    
    # Observations
    observations: Optional[str] = None
    
    # Flags for abnormal values
    flags: Optional[str] = None  # JSON string of flagged parameters
    
    def compute_flags(self) -> List[str]:
        """Identify abnormal values"""
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
            flagged.append(f"Proteínas: {self.proteins}")
        
        # Check crystals
        if self.crystals and self.crystals.lower() not in ["ausentes", "ausente"]:
            flagged.append(f"Cristais: {self.crystals}")
        
        # Check bacteria
        if self.bacteria and self.bacteria.lower() not in ["ausentes", "ausente"]:
            flagged.append(f"Bactérias: {self.bacteria}")
        
        self.flags = json.dumps(flagged, ensure_ascii=False) if flagged else None
        return flagged


# =============================================================================
# REFERENCE DATA - Protein Markers
# =============================================================================

# Standard protein markers from DNAtech proteinogram
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


# =============================================================================
# DATABASE SCHEMA
# =============================================================================

SCHEMA_SQL = """
-- Animals table
CREATE TABLE IF NOT EXISTS animals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    species TEXT DEFAULT 'Canídeo',
    breed TEXT,
    microchip TEXT UNIQUE,
    age_years REAL,
    age_months INTEGER,
    sex TEXT DEFAULT 'U',
    weight_kg REAL,
    neutered INTEGER,
    medical_history TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Test sessions table
CREATE TABLE IF NOT EXISTS test_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    report_number TEXT UNIQUE,
    test_date DATE,
    closing_date DATE,
    sample_type TEXT DEFAULT 'Soro',
    lab_name TEXT DEFAULT 'DNAtech',
    pdf_path TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE
);

-- Protein results table
CREATE TABLE IF NOT EXISTS protein_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    marker_name TEXT NOT NULL,
    marker_category TEXT DEFAULT 'PROTEINOGRAMA',
    value REAL,
    unit TEXT,
    value_absolute REAL,
    unit_absolute TEXT DEFAULT 'g/dL',
    reference_min REAL,
    reference_max REAL,
    reference_min_absolute REAL,
    reference_max_absolute REAL,
    flag TEXT DEFAULT 'normal',
    flag_absolute TEXT DEFAULT 'normal',
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);

-- Symptoms table
CREATE TABLE IF NOT EXISTS symptoms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    observed_date DATE,
    description TEXT NOT NULL,
    severity TEXT DEFAULT 'mild',
    category TEXT,
    resolved_date DATE,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE
);

-- Observations table (weight, diet, medications, etc.)
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    observation_date DATE,
    observation_type TEXT NOT NULL,
    details TEXT,
    value REAL,
    unit TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE
);

-- Clinical notes table (detailed consultation notes, anamnesis, exam findings)
CREATE TABLE IF NOT EXISTS clinical_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    note_date DATE,
    title TEXT,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE
);

-- Research references table (for AI interpretation)
CREATE TABLE IF NOT EXISTS research_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT,
    publication_date DATE,
    species TEXT,
    marker_name TEXT,
    content TEXT,
    tags TEXT,  -- JSON array of tags
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_test_sessions_animal_id ON test_sessions(animal_id);
CREATE INDEX IF NOT EXISTS idx_test_sessions_test_date ON test_sessions(test_date);
CREATE INDEX IF NOT EXISTS idx_protein_results_session_id ON protein_results(session_id);
CREATE INDEX IF NOT EXISTS idx_protein_results_marker ON protein_results(marker_name);
CREATE INDEX IF NOT EXISTS idx_symptoms_animal_id ON symptoms(animal_id);
CREATE INDEX IF NOT EXISTS idx_observations_animal_id ON observations(animal_id);
CREATE INDEX IF NOT EXISTS idx_clinical_notes_animal_id ON clinical_notes(animal_id);

-- Biochemistry results table (UPC ratio, kidney markers)
CREATE TABLE IF NOT EXISTS biochemistry_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    upc_ratio REAL,
    upc_status TEXT,
    urine_total_protein REAL,
    urine_creatinine REAL,
    iris_stage TEXT,
    notes TEXT,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);

-- Urinalysis results table (Urina Tipo II)
CREATE TABLE IF NOT EXISTS urinalysis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    -- General characteristics
    color TEXT,
    appearance TEXT,
    -- Biochemistry
    glucose TEXT,
    bilirubin TEXT,
    ketones TEXT,
    specific_gravity REAL,
    ph REAL,
    proteins TEXT,
    proteins_value REAL,
    urobilinogen TEXT,
    nitrites TEXT,
    -- Microscopic sediment
    leukocytes TEXT,
    erythrocytes TEXT,
    epithelial_cells TEXT,
    casts TEXT,
    crystals TEXT,
    mucus TEXT,
    bacteria TEXT,
    -- Observations and flags
    observations TEXT,
    flags TEXT,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_biochemistry_session ON biochemistry_results(session_id);
CREATE INDEX IF NOT EXISTS idx_urinalysis_session ON urinalysis_results(session_id);

-- Diagnosis reports table (AI-generated differential diagnoses)
CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    report_date DATE,
    report_type TEXT NOT NULL,
    input_summary TEXT,
    -- Executive Summary (combines both AI reports)
    executive_summary TEXT,
    -- Claude (Anthropic) results
    test_interpretation TEXT,
    differential_diagnosis TEXT NOT NULL,
    recommendations TEXT,
    literature_references TEXT,
    model_used TEXT,
    -- OpenAI (GPT-5 mini) results
    openai_test_interpretation TEXT,
    openai_differential_diagnosis TEXT,
    openai_recommendations TEXT,
    openai_literature_references TEXT,
    openai_model_used TEXT,
    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_diagnosis_animal ON diagnosis_reports(animal_id);

-- Email import audit log table
CREATE TABLE IF NOT EXISTS email_import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    email_uid TEXT,
    email_subject TEXT,
    email_from TEXT,
    attachment_name TEXT,
    validation_result TEXT,
    import_success INTEGER,
    error_message TEXT,
    report_number TEXT,
    animal_id INTEGER,
    session_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_import_log_timestamp ON email_import_log(import_timestamp);

-- View for easy result querying with animal info
CREATE VIEW IF NOT EXISTS v_results_with_animal AS
SELECT 
    a.id as animal_id,
    a.name as animal_name,
    a.species,
    a.breed,
    a.age_years,
    a.sex,
    ts.id as session_id,
    ts.report_number,
    ts.test_date,
    pr.marker_name,
    pr.value,
    pr.unit,
    pr.value_absolute,
    pr.reference_min,
    pr.reference_max,
    pr.reference_min_absolute,
    pr.reference_max_absolute,
    pr.flag,
    pr.flag_absolute
FROM animals a
JOIN test_sessions ts ON a.id = ts.animal_id
JOIN protein_results pr ON ts.id = pr.session_id;
"""


# =============================================================================
# DATABASE CLASS
# =============================================================================

class Database:
    """Database manager for the veterinary protein analysis application"""
    
    def __init__(self, db_path: str = "vet_proteins.db"):
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
    
    def initialize(self):
        """Create database schema"""
        if not self.conn:
            self.connect()
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        print(f"Database initialized: {self.db_path}")
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # -------------------------------------------------------------------------
    # Animal CRUD
    # -------------------------------------------------------------------------
    
    def create_animal(self, animal: Animal) -> int:
        """Insert a new animal and return its ID"""
        cursor = self.conn.execute("""
            INSERT INTO animals (name, species, breed, microchip, age_years, 
                                age_months, sex, weight_kg, neutered, 
                                medical_history, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (animal.name, animal.species, animal.breed, animal.microchip,
              animal.age_years, animal.age_months, animal.sex, animal.weight_kg,
              animal.neutered, animal.medical_history, animal.notes))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_animal(self, animal_id: int) -> Optional[Animal]:
        """Retrieve an animal by ID"""
        cursor = self.conn.execute(
            "SELECT * FROM animals WHERE id = ?", (animal_id,))
        row = cursor.fetchone()
        if row:
            return Animal(**dict(row))
        return None
    
    def find_animal_by_name(self, name: str) -> List[Animal]:
        """Find animals by name (partial match)"""
        cursor = self.conn.execute(
            "SELECT * FROM animals WHERE name LIKE ?", (f"%{name}%",))
        return [Animal(**dict(row)) for row in cursor.fetchall()]
    
    def find_or_create_animal(self, animal: Animal) -> int:
        """Find existing animal or create new one"""
        # Try to find by microchip first (most reliable)
        if animal.microchip:
            cursor = self.conn.execute(
                "SELECT id FROM animals WHERE microchip = ?", (animal.microchip,))
            row = cursor.fetchone()
            if row:
                return row['id']
        
        # Try to find by name + species (breed can vary in reports)
        cursor = self.conn.execute("""
            SELECT id FROM animals 
            WHERE LOWER(name) = LOWER(?) AND LOWER(species) = LOWER(?)
        """, (animal.name, animal.species))
        row = cursor.fetchone()
        if row:
            # Update breed if it was "Indeterminado" or empty
            self.conn.execute("""
                UPDATE animals SET breed = ? 
                WHERE id = ? AND (breed IS NULL OR breed = '' OR breed = 'Indeterminado')
            """, (animal.breed, row['id']))
            self.conn.commit()
            return row['id']
        
        # Create new animal
        return self.create_animal(animal)
    
    def list_animals(self) -> List[Animal]:
        """List all animals"""
        cursor = self.conn.execute("SELECT * FROM animals ORDER BY name")
        return [Animal(**dict(row)) for row in cursor.fetchall()]
    
    # -------------------------------------------------------------------------
    # Test Session CRUD
    # -------------------------------------------------------------------------
    
    def create_test_session(self, session: TestSession) -> int:
        """Insert a new test session and return its ID"""
        cursor = self.conn.execute("""
            INSERT INTO test_sessions (animal_id, report_number, test_date,
                                      closing_date, sample_type, lab_name,
                                      pdf_path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session.animal_id, session.report_number, session.test_date,
              session.closing_date, session.sample_type, session.lab_name,
              session.pdf_path, session.notes))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_sessions_for_animal(self, animal_id: int) -> List[TestSession]:
        """Get all test sessions for an animal, ordered by date"""
        cursor = self.conn.execute("""
            SELECT * FROM test_sessions 
            WHERE animal_id = ? 
            ORDER BY test_date DESC
        """, (animal_id,))
        return [TestSession(**dict(row)) for row in cursor.fetchall()]
    
    def session_exists(self, report_number: str) -> bool:
        """Check if a session with given report number already exists"""
        cursor = self.conn.execute(
            "SELECT 1 FROM test_sessions WHERE report_number = ?", 
            (report_number,))
        return cursor.fetchone() is not None
    
    # -------------------------------------------------------------------------
    # Protein Results CRUD
    # -------------------------------------------------------------------------
    
    def create_protein_result(self, result: ProteinResult) -> int:
        """Insert a protein result"""
        result.compute_flags()
        cursor = self.conn.execute("""
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
        self.conn.commit()
        return cursor.lastrowid
    
    def get_results_for_session(self, session_id: int) -> List[ProteinResult]:
        """Get all protein results for a session"""
        cursor = self.conn.execute("""
            SELECT * FROM protein_results WHERE session_id = ?
        """, (session_id,))
        return [ProteinResult(**dict(row)) for row in cursor.fetchall()]
    
    def get_marker_history(self, animal_id: int, marker_name: str) -> List[Dict]:
        """Get historical values for a specific marker for an animal"""
        cursor = self.conn.execute("""
            SELECT ts.test_date, pr.value, pr.value_absolute, pr.flag, pr.flag_absolute
            FROM protein_results pr
            JOIN test_sessions ts ON pr.session_id = ts.id
            WHERE ts.animal_id = ? AND pr.marker_name = ?
            ORDER BY ts.test_date ASC
        """, (animal_id, marker_name))
        return [dict(row) for row in cursor.fetchall()]
    
    # -------------------------------------------------------------------------
    # Symptoms CRUD
    # -------------------------------------------------------------------------
    
    def create_symptom(self, symptom: Symptom) -> int:
        """Insert a symptom record"""
        cursor = self.conn.execute("""
            INSERT INTO symptoms (animal_id, observed_date, description,
                                 severity, category, resolved_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (symptom.animal_id, symptom.observed_date, symptom.description,
              symptom.severity, symptom.category, symptom.resolved_date,
              symptom.notes))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_symptoms_for_animal(self, animal_id: int, 
                                active_only: bool = False) -> List[Symptom]:
        """Get symptoms for an animal"""
        if active_only:
            cursor = self.conn.execute("""
                SELECT * FROM symptoms 
                WHERE animal_id = ? AND resolved_date IS NULL
                ORDER BY observed_date DESC
            """, (animal_id,))
        else:
            cursor = self.conn.execute("""
                SELECT * FROM symptoms WHERE animal_id = ?
                ORDER BY observed_date DESC
            """, (animal_id,))
        return [Symptom(**dict(row)) for row in cursor.fetchall()]
    
    # -------------------------------------------------------------------------
    # Biochemistry Results CRUD
    # -------------------------------------------------------------------------
    
    def create_biochemistry_result(self, result: 'BiochemistryResult') -> int:
        """Insert a biochemistry result"""
        result.compute_upc_status()
        cursor = self.conn.execute("""
            INSERT INTO biochemistry_results (session_id, upc_ratio, upc_status,
                                             urine_total_protein, urine_creatinine,
                                             iris_stage, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (result.session_id, result.upc_ratio, result.upc_status,
              result.urine_total_protein, result.urine_creatinine,
              result.iris_stage, result.notes))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_biochemistry_for_session(self, session_id: int) -> Optional['BiochemistryResult']:
        """Get biochemistry result for a session"""
        cursor = self.conn.execute(
            "SELECT * FROM biochemistry_results WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return BiochemistryResult(**dict(row))
        return None
    
    # -------------------------------------------------------------------------
    # Urinalysis Results CRUD
    # -------------------------------------------------------------------------
    
    def create_urinalysis_result(self, result: 'UrinalysisResult') -> int:
        """Insert a urinalysis result"""
        result.compute_flags()
        cursor = self.conn.execute("""
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
        self.conn.commit()
        return cursor.lastrowid
    
    def get_urinalysis_for_session(self, session_id: int) -> Optional['UrinalysisResult']:
        """Get urinalysis result for a session"""
        cursor = self.conn.execute(
            "SELECT * FROM urinalysis_results WHERE session_id = ?", (session_id,))
        row = cursor.fetchone()
        if row:
            return UrinalysisResult(**dict(row))
        return None
    
    # -------------------------------------------------------------------------
    # Observations CRUD
    # -------------------------------------------------------------------------
    
    def create_observation(self, observation: Observation) -> int:
        """Insert an observation record"""
        cursor = self.conn.execute("""
            INSERT INTO observations (animal_id, observation_date, 
                                     observation_type, details, value, unit)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (observation.animal_id, observation.observation_date,
              observation.observation_type, observation.details,
              observation.value, observation.unit))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_observations_for_animal(self, animal_id: int,
                                    obs_type: Optional[str] = None) -> List[Observation]:
        """Get observations for an animal, optionally filtered by type"""
        if obs_type:
            cursor = self.conn.execute("""
                SELECT * FROM observations
                WHERE animal_id = ? AND observation_type = ?
                ORDER BY observation_date DESC
            """, (animal_id, obs_type))
        else:
            cursor = self.conn.execute("""
                SELECT * FROM observations WHERE animal_id = ?
                ORDER BY observation_date DESC
            """, (animal_id,))
        return [Observation(**dict(row)) for row in cursor.fetchall()]

    # -------------------------------------------------------------------------
    # Clinical Notes CRUD
    # -------------------------------------------------------------------------

    def create_clinical_note(self, note: 'ClinicalNote') -> int:
        """Insert a clinical note record"""
        cursor = self.conn.execute("""
            INSERT INTO clinical_notes (animal_id, note_date, title, content)
            VALUES (?, ?, ?, ?)
        """, (note.animal_id, note.note_date, note.title, note.content))
        self.conn.commit()
        return cursor.lastrowid

    def get_clinical_note(self, note_id: int) -> Optional['ClinicalNote']:
        """Get a clinical note by ID"""
        cursor = self.conn.execute(
            "SELECT * FROM clinical_notes WHERE id = ?", (note_id,))
        row = cursor.fetchone()
        if row:
            return ClinicalNote(**dict(row))
        return None

    def get_clinical_notes_for_animal(self, animal_id: int) -> List['ClinicalNote']:
        """Get all clinical notes for an animal, ordered by date"""
        cursor = self.conn.execute("""
            SELECT * FROM clinical_notes WHERE animal_id = ?
            ORDER BY note_date DESC, created_at DESC
        """, (animal_id,))
        return [ClinicalNote(**dict(row)) for row in cursor.fetchall()]

    def update_clinical_note(self, note_id: int, title: Optional[str],
                            content: str, note_date: Optional[date] = None) -> bool:
        """Update a clinical note"""
        cursor = self.conn.execute("""
            UPDATE clinical_notes
            SET title = ?, content = ?, note_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (title, content, note_date, note_id))
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_clinical_note(self, note_id: int) -> bool:
        """Delete a clinical note"""
        cursor = self.conn.execute(
            "DELETE FROM clinical_notes WHERE id = ?", (note_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    # -------------------------------------------------------------------------
    # Diagnosis Reports CRUD
    # -------------------------------------------------------------------------

    def create_diagnosis_report(self, report: 'DiagnosisReport') -> int:
        """Insert a diagnosis report and return its ID"""
        cursor = self.conn.execute("""
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
        self.conn.commit()
        return cursor.lastrowid

    def get_diagnosis_report(self, report_id: int) -> Optional['DiagnosisReport']:
        """Get a diagnosis report by ID"""
        cursor = self.conn.execute(
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

    def get_diagnosis_reports_for_animal(self, animal_id: int) -> List['DiagnosisReport']:
        """Get all diagnosis reports for an animal, ordered by date"""
        cursor = self.conn.execute("""
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

    def delete_diagnosis_report(self, report_id: int) -> bool:
        """Delete a diagnosis report"""
        cursor = self.conn.execute(
            "DELETE FROM diagnosis_reports WHERE id = ?", (report_id,))
        self.conn.commit()
        return cursor.rowcount > 0


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def parse_portuguese_date(date_str: str) -> Optional[date]:
    """Parse date in Portuguese format (DD/MM/YYYY, DD MM YYYY, or DDMMYYYY)"""
    if not date_str:
        return None
    try:
        date_str = date_str.strip()
        
        # Handle DDMMYYYY format (no separators)
        if len(date_str) == 8 and date_str.isdigit():
            day = int(date_str[0:2])
            month = int(date_str[2:4])
            year = int(date_str[4:8])
            return date(year, month, day)
        
        # Normalize separators - replace spaces with /
        normalized = date_str.replace(' ', '/')
        parts = normalized.split('/')
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return date(year, month, day)
    except (ValueError, IndexError):
        pass
    return None


def parse_age(age_str: str) -> tuple:
    """
    Parse age string like '7 A (F)' -> (7.0 years, 'F' sex)
    or '3 M (M)' -> (0.25 years, 'M' sex)
    """
    age_years = None
    age_months = None
    sex = 'U'
    
    if not age_str:
        return age_years, age_months, sex
    
    # Extract sex from parentheses
    if '(F)' in age_str.upper():
        sex = 'F'
    elif '(M)' in age_str.upper():
        sex = 'M'
    
    # Extract age
    import re
    age_match = re.search(r'(\d+)\s*([AM])', age_str.upper())
    if age_match:
        value = int(age_match.group(1))
        unit = age_match.group(2)
        if unit == 'A':  # Anos (years)
            age_years = float(value)
        elif unit == 'M':  # Meses (months)
            age_months = value
            age_years = value / 12.0
    
    return age_years, age_months, sex


if __name__ == "__main__":
    # Quick test
    db = Database(":memory:")
    db.connect()
    db.initialize()
    
    # Create a test animal
    animal = Animal(
        name="Júlia",
        species="Felídeo",
        breed="Sphynx",
        age_years=7.0,
        sex="F"
    )
    animal_id = db.create_animal(animal)
    print(f"Created animal with ID: {animal_id}")
    
    # Retrieve and display
    retrieved = db.get_animal(animal_id)
    print(f"Retrieved: {retrieved.name}, {retrieved.breed}, {retrieved.age_display}")
    
    db.close()
    print("Model test complete!")
