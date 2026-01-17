"""
Veterinary Protein Analysis Application

A Python application for managing and analyzing protein electrophoresis
results from veterinary blood tests.
"""

from .models import (
    Animal, TestSession, ProteinResult, Symptom, Observation,
    BiochemistryResult, UrinalysisResult,
    Database, Species, Sex, ResultFlag, SymptomSeverity,
    PROTEIN_MARKERS
)
from .pdf_parser import DNAtechParser, parse_dnatech_report, ParsedReport
from .app import VetProteinService, AnalysisReport, ResultComparison

__version__ = "0.2.0"
__all__ = [
    # Models
    'Animal', 'TestSession', 'ProteinResult', 'Symptom', 'Observation',
    'BiochemistryResult', 'UrinalysisResult',
    'Database', 'Species', 'Sex', 'ResultFlag', 'SymptomSeverity',
    'PROTEIN_MARKERS',
    # Parser
    'DNAtechParser', 'parse_dnatech_report', 'ParsedReport',
    # Application
    'VetProteinService', 'AnalysisReport', 'ResultComparison',
]
