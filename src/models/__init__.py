"""
VetScan Domain Models

Re-exports all models for backwards compatibility.
Import from here for existing code to continue working.

New code should import from specific submodules:
    from models.domain import Animal, TestSession
    from models.enums import Species, ResultFlag
    from models.schema import SCHEMA_SQL
"""

# Enums
from .enums import (
    Species,
    Sex,
    ResultFlag,
    SymptomSeverity,
    UPCStatus,
    DiagnosisReportType,
)

# Domain models
from .domain import (
    Animal,
    TestSession,
    ProteinResult,
    Symptom,
    Observation,
    ClinicalNote,
    DiagnosisReport,
    BiochemistryResult,
    UrinalysisResult,
    AnimalIdentifier,
    AnimalMatchCandidate,
    AnimalMatchDecision,
    SessionMeasurement,
    PathologyFinding,
    SessionAsset,
    UnassignedReport,
    User,
    UserSession,
    AuthEvent,
    PasswordResetToken,
    InvitationToken,
    PROTEIN_MARKERS,
)

# Schema
from .schema import SCHEMA_SQL


# Helper functions
def parse_portuguese_date(date_str: str):
    """Parse date in Portuguese format (DD/MM/YYYY, DD MM YYYY, or DDMMYYYY)."""
    from datetime import date as date_type

    if not date_str:
        return None
    try:
        date_str = date_str.strip()

        # Handle DDMMYYYY format (no separators)
        if len(date_str) == 8 and date_str.isdigit():
            day = int(date_str[0:2])
            month = int(date_str[2:4])
            year = int(date_str[4:8])
            return date_type(year, month, day)

        # Normalize separators - replace spaces with /
        normalized = date_str.replace(' ', '/')
        parts = normalized.split('/')
        if len(parts) == 3:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return date_type(year, month, day)
    except (ValueError, IndexError):
        pass
    return None


def parse_age(age_str: str) -> tuple:
    """
    Parse age string like '7 A (F)' -> (7.0 years, 'F' sex)
    or '3 M (M)' -> (0.25 years, 'M' sex)
    """
    import re

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


# Lazy import for Database to avoid circular imports
# Users can still do: from models import Database
def __getattr__(name):
    if name == 'Database':
        from database import Database
        return Database
    raise AttributeError(f"module 'models' has no attribute '{name}'")


__all__ = [
    # Enums
    'Species',
    'Sex',
    'ResultFlag',
    'SymptomSeverity',
    'UPCStatus',
    'DiagnosisReportType',
    # Domain models
    'Animal',
    'TestSession',
    'ProteinResult',
    'Symptom',
    'Observation',
    'ClinicalNote',
    'DiagnosisReport',
    'BiochemistryResult',
    'UrinalysisResult',
    'AnimalIdentifier',
    'AnimalMatchCandidate',
    'AnimalMatchDecision',
    'SessionMeasurement',
    'PathologyFinding',
    'SessionAsset',
    'UnassignedReport',
    'User',
    'UserSession',
    'AuthEvent',
    'PasswordResetToken',
    'InvitationToken',
    'PROTEIN_MARKERS',
    # Schema
    'SCHEMA_SQL',
    # Database (backwards compatibility via __getattr__)
    'Database',
    # Helper functions
    'parse_portuguese_date',
    'parse_age',
]
