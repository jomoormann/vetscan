"""
Enumerations for VetScan Domain Models

Contains all enum types used throughout the application.
"""

from enum import Enum


class Species(Enum):
    """Supported animal species."""
    CANINE = "Canídeo"  # Dog
    FELINE = "Felídeo"  # Cat


class Sex(Enum):
    """Animal sex."""
    MALE = "M"
    FEMALE = "F"
    UNKNOWN = "U"


class ResultFlag(Enum):
    """Flag indicating if result is within reference range."""
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"


class SymptomSeverity(Enum):
    """Severity level for symptoms."""
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class UPCStatus(Enum):
    """UPC ratio status based on IRIS guidelines."""
    NON_PROTEINURIC = "nao_proteinurico"  # < 0.2
    BORDERLINE = "suspeito"  # 0.2 - 0.5
    PROTEINURIC = "proteinurico"  # > 0.5


class DiagnosisReportType(Enum):
    """Type of diagnosis report."""
    CLINICAL_NOTES_ONLY = "clinical_notes_only"
    COMPREHENSIVE = "comprehensive"
