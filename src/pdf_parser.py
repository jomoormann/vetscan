"""
PDF Parser for DNAtech Veterinary Lab Reports

Specifically designed to parse protein electrophoresis reports
in the DNAtech format (Portuguese).

Report structure:
- Header: Lab logo, report number, date
- Animal data: Name, species, breed, microchip, age, sample type
- BIOQUIMICA section: UPC ratio, kidney markers
- PROTEINOGRAMA section: Protein electrophoresis results
- URINAS section: Complete urinalysis (Urina Tipo II)
- Electrophoresis graph image
- Footer: Closing date
"""

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import fitz
import pdfplumber

# Import our models
from models import (
    Animal, TestSession, ProteinResult, BiochemistryResult, UrinalysisResult,
    AnimalIdentifier, SessionMeasurement, PathologyFinding,
    parse_portuguese_date, parse_age
)


@dataclass
class ParsedReport:
    """Container for all data extracted from a PDF report"""
    animal: Animal
    session: TestSession
    results: List[ProteinResult] = field(default_factory=list)
    biochemistry: Optional[BiochemistryResult] = None
    urinalysis: Optional[UrinalysisResult] = None
    measurements: List[SessionMeasurement] = field(default_factory=list)
    pathology_findings: List[PathologyFinding] = field(default_factory=list)
    animal_identifiers: List[AnimalIdentifier] = field(default_factory=list)
    assets: List["ParsedAsset"] = field(default_factory=list)
    raw_text: str = ""
    parse_warnings: List[str] = field(default_factory=list)


@dataclass
class ParsedAsset:
    """Binary asset extracted from a report before it is stored on disk."""
    asset_type: str
    label: Optional[str]
    filename: str
    content: bytes
    page_number: Optional[int] = None
    sort_order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


def _normalize_space(value: str) -> str:
    return " ".join((value or "").split())


def _parse_decimal(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    cleaned = value.replace(",", ".").replace("<", "").replace(">", "").strip()
    cleaned = cleaned.replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_species(value: Optional[str]) -> str:
    normalized = (value or "").strip()
    lowered = normalized.lower()
    if "can" in lowered or "dog" in lowered:
        return "Canídeo"
    if "fel" in lowered or "cat" in lowered:
        return "Felídeo"
    return normalized or "Canídeo"


def _extract_pdf_text(pdf_path: str) -> str:
    text_parts: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _extract_layout_text(pdf_path: str) -> str:
    pdftotext_path = shutil.which("pdftotext")
    if pdftotext_path:
        try:
            result = subprocess.run(
                [pdftotext_path, "-layout", pdf_path, "-"],
                check=True,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                return result.stdout
        except Exception:
            pass
    return _extract_pdf_text(pdf_path)


def _extract_cytology_assets(pdf_path: str) -> List[ParsedAsset]:
    assets: List[ParsedAsset] = []
    doc = fitz.open(pdf_path)
    seen_xrefs = set()

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        for image_index, image_info in enumerate(page.get_images(full=True)):
            xref = image_info[0]
            if xref in seen_xrefs:
                continue
            info = doc.extract_image(xref)
            rects = page.get_image_rects(xref)
            width = info.get("width") or 0
            height = info.get("height") or 0
            if width < 300 or height < 300:
                continue

            display_rect = rects[0] if rects else None
            if display_rect:
                display_area = display_rect.width * display_rect.height
                lower_page_start = display_rect.y0 / max(page.rect.height, 1)
                if display_area < 15000:
                    continue
                if lower_page_start > 0.75 and display_rect.height < (page.rect.height * 0.15):
                    continue

            seen_xrefs.add(xref)
            ext = info.get("ext", "bin")
            assets.append(ParsedAsset(
                asset_type="cytology_image",
                label=f"Cytology image {len(assets) + 1}",
                filename=f"cytology_page_{page_index + 1}_{image_index + 1}.{ext}",
                content=info.get("image", b""),
                page_number=page_index + 1,
                sort_order=len(assets),
                metadata={
                    "xref": xref,
                    "width": width,
                    "height": height,
                    "ext": ext,
                    "bbox": [
                        round(display_rect.x0, 2),
                        round(display_rect.y0, 2),
                        round(display_rect.x1, 2),
                        round(display_rect.y1, 2),
                    ] if display_rect else None,
                },
            ))

    doc.close()
    return assets


def _clean_vedis_text(value: Optional[str]) -> str:
    if not value:
        return ""
    ignored_prefixes = (
        "Exam ID",
        "ID exame",
        "Vedis .",
        "Page ",
        "Página ",
        "PATIENT",
        "PACIENTE",
        "Owner:",
        "Tutor:",
        "Specie:",
        "Espécie:",
        "Breed:",
        "Raça:",
        "Gender:",
        "Sexo:",
        "DOB/Age:",
        "DN/Idade:",
        "Date of receipt",
        "Date of report",
        "Data de receção",
        "Data de relatório",
        "CLIENT",
        "ENTIDADE",
        "Clínica Veterinária CVS",
        "SOS Animal",
        "Attending Vet",
        "Veterinário/a",
        "Pathologist",
        "Technical Director",
        "Em caso de dúvidas",
        "In case of doubt",
        "reports@vedis.pt",
        "Andrea Renzi",
        "Nazaré Cunha",
    )
    cleaned_lines = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in ignored_prefixes):
            continue
        cleaned_lines.append(line)
    return _normalize_space("\n".join(cleaned_lines))


def _is_vedis_noise_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    ignored_prefixes = (
        "Vedis .",
        "Page ",
        "Página ",
        "PATIENT",
        "PACIENTE",
        "Owner:",
        "Tutor:",
        "Specie:",
        "Espécie:",
        "Breed:",
        "Raça:",
        "Gender:",
        "Sexo:",
        "DOB/Age:",
        "DN/Idade:",
        "Date of receipt",
        "Date of report",
        "Data de receção",
        "Data de relatório",
        "CLIENT",
        "ENTIDADE",
        "Clínica Veterinária CVS",
        "SOS Animal",
        "Attending Vet",
        "Veterinário/a",
        "Pathologist",
        "Technical Director",
        "Em caso de dúvidas",
        "In case of doubt",
        "reports@vedis.pt",
        "Andrea Renzi",
        "Nazaré Cunha",
    )
    return any(stripped.startswith(prefix) for prefix in ignored_prefixes)


def detect_report_type(text: str) -> Optional[str]:
    upper_text = text.upper()
    if "PROTEINOGRAMA" in upper_text or "ELECTROFORESE DE PROTEINAS" in upper_text:
        return "dnatech_proteinogram"
    if "CYTOLOGY REPORT" in upper_text:
        return "vedis_cytology"
    if "RELATÓRIO IMUNOCITOQUÍMICA" in upper_text or "IMUNOCITOQUÍMICA" in upper_text:
        return "vedis_immunocytochemistry"
    if "ID PACIENTE" in upper_text and "RESULTADO" in upper_text:
        return "cvs_analyzer"
    return None


class DNAtechParser:
    """
    Parser for DNAtech protein electrophoresis reports.
    
    Expected format (Portuguese):
    - Folha de Trabalho Nº XXXXX/XXXXXXX
    - Data DD/MM/YYYY
    - Dados do Animal section
    - PROTEINOGRAMA section with results table
    """
    
    # Regex patterns for extraction
    # Note: DNAtech format has fields on separate lines or with specific structure
    PATTERNS = {
        # Report number can appear as "Folha de Trabalho Nº" or just in header
        'report_number': r'(?:Folha de Trabalho N[º°]\s*|^)(\d{5,6}/\d{6,7})',
        # Date can be "Data DD/MM/YYYY" or "Data DD MM YYYY" or "Data DDMMYYYY"
        'date': r'Data\s+(\d{2}[/\s]?\d{2}[/\s]?\d{4})',
        'closing_date': r'Data de fecho\s+(\d{2}/\d{2}/\d{4})',
        # Animal name appears after "Animal " (with space, not "ID Animal")
        'animal_name': r'^Animal\s+([A-Za-zÀ-ÿ]+)',
        'species': r'Esp[ée]cie\s+([A-Za-zÀ-ÿ]+)',
        'breed': r'Ra[çc]a\s+([A-Za-zÀ-ÿ\s]+?)(?=\n|Microchip|$)',
        'microchip': r'Microchip(?:\s+No Cliente:)?\s+([A-Za-z0-9\-]+)',
        'owner_name': r'Propriet[áa]rio\s+([A-Za-zÀ-ÿ\s]+?)(?=\n|Amostra|$)',
        'client_ref': r'No Cliente:\s*([A-Za-z0-9\-]+)',
        'age': r'Idade\s+(.+?)(?=\n|Amostra)',
        'sample': r'Amostra\s+([A-Za-zÀ-ÿ\s\|]+?)(?=\n|VrokGuur)',
    }
    
    # Known protein markers with their parsing patterns
    # Format: marker_name -> (regex pattern, has_percentage, has_absolute)
    MARKER_PATTERNS = {
        'Proteinas totais': (r'Prote[íi]nas?\s+totais?\s+([\d,\.]+)', False, True),
        'Albumina': (r'Albumina\s+([\d,\.]+)\s*%?\s*([\d,\.]+)?', True, True),
        'Alfa 1': (r'Alfa\s*1\s+([\d,\.]+)\s*%?\s*([\d,\.]+)?', True, True),
        'Alfa 2': (r'Alfa\s*2\s+([\d,\.]+)\s*%?\s*([\d,\.]+)?', True, True),
        'Beta': (r'Beta\s+([\d,\.]+)\s*%?\s*([\d,\.]+)?', True, True),
        'Gama': (r'Gama\s+([\d,\.]+)\s*%?\s*([\d,\.]+)?', True, True),
        'Rel. Albumina/Globulina': (r'Rel\.\s*Albumina/Globulina\s+([\d,\.]+)', False, False),
    }
    
    # Reference ranges for canine protein electrophoresis (DNAtech format)
    # These are defaults; actual values are extracted from PDF when available
    CANINE_REFERENCE_RANGES = {
        'Proteinas totais': {'min': 5.7, 'max': 7.9, 'unit': 'g/dL'},
        'Albumina': {
            'min_pct': 36.8, 'max_pct': 50.6,
            'min_abs': 2.10, 'max_abs': 4.00
        },
        'Alfa 1': {
            'min_pct': 3.5, 'max_pct': 13.9,
            'min_abs': 0.20, 'max_abs': 1.10
        },
        'Alfa 2': {
            'min_pct': 7.0, 'max_pct': 11.4,
            'min_abs': 0.40, 'max_abs': 0.90
        },
        'Beta': {
            'min_pct': 15.8, 'max_pct': 24.1,
            'min_abs': 0.90, 'max_abs': 1.90
        },
        'Gama': {
            'min_pct': 22.8, 'max_pct': 27.8,
            'min_abs': 1.30, 'max_abs': 2.20
        },
        'Rel. Albumina/Globulina': {'min': 0.45, 'max': 1.30, 'unit': 'ratio'},
    }
    
    def __init__(self):
        self.warnings = []
    
    def parse_pdf(self, pdf_path: str) -> ParsedReport:
        """
        Parse a DNAtech PDF report and extract all data.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            ParsedReport containing animal, session, and results data
        """
        self.warnings = []
        
        with pdfplumber.open(pdf_path) as pdf:
            # Extract text from all pages
            full_text = ""
            tables = []
            
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                full_text += page_text + "\n"
                
                # Try to extract tables
                page_tables = page.extract_tables()
                if page_tables:
                    tables.extend(page_tables)
        
        # Parse the extracted text
        animal = self._parse_animal_data(full_text)
        session = self._parse_session_data(full_text, pdf_path)
        results = self._parse_results(full_text, tables)
        animal_identifiers = self._parse_animal_identifiers(full_text)

        # Parse additional sections
        biochemistry = self._parse_biochemistry(full_text)
        urinalysis = self._parse_urinalysis(full_text)
        
        return ParsedReport(
            animal=animal,
            session=session,
            results=results,
            biochemistry=biochemistry,
            urinalysis=urinalysis,
            animal_identifiers=animal_identifiers,
            raw_text=full_text,
            parse_warnings=self.warnings
        )
    
    def _extract_pattern(self, text: str, pattern_name: str) -> Optional[str]:
        """Extract a value using a named pattern"""
        pattern = self.PATTERNS.get(pattern_name, pattern_name)
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
        return None
    
    def _parse_number(self, value_str: str) -> Optional[float]:
        """Parse a number, handling Portuguese decimal format (comma)"""
        if not value_str:
            return None
        try:
            # Replace comma with dot for decimal
            cleaned = value_str.replace(',', '.').strip()
            return float(cleaned)
        except ValueError:
            return None
    
    def _parse_reference_range(self, ref_str: str) -> Tuple[Optional[float], Optional[float]]:
        """Parse a reference range string like '5,7 - 7,9' -> (5.7, 7.9)"""
        if not ref_str:
            return None, None
        
        # Pattern for range: number - number (with Portuguese comma decimals)
        match = re.search(r'([\d,\.]+)\s*-\s*([\d,\.]+)', ref_str)
        if match:
            min_val = self._parse_number(match.group(1))
            max_val = self._parse_number(match.group(2))
            return min_val, max_val
        
        return None, None
    
    def _parse_animal_data(self, text: str) -> Animal:
        """Extract animal information from the report text"""
        name = self._extract_pattern(text, 'animal_name') or "Unknown"
        species = self._extract_pattern(text, 'species') or "Canídeo"
        breed = self._extract_pattern(text, 'breed') or ""
        microchip = self._extract_pattern(text, 'microchip')
        owner_name = self._extract_pattern(text, 'owner_name')
        age_str = self._extract_pattern(text, 'age') or ""
        
        # Parse age and extract sex
        age_years, age_months, sex = parse_age(age_str)
        
        # Clean up breed (remove trailing whitespace/newlines)
        breed = breed.strip()
        
        animal = Animal(
            name=name,
            species=_parse_species(species),
            breed=breed,
            microchip=microchip,
            owner_name=_normalize_space(owner_name),
            age_years=age_years,
            age_months=age_months,
            sex=sex
        )
        
        return animal
    
    def _parse_session_data(self, text: str, pdf_path: str) -> TestSession:
        """Extract test session information from the report text"""
        report_number = self._extract_pattern(text, 'report_number') or ""
        
        # If report number not found in text, try to extract from filename
        # Pattern: bolt58630_1500951.pdf -> 58630/1500951
        if not report_number:
            import os
            filename = os.path.basename(pdf_path)
            fn_match = re.search(r'bolt(\d+)_(\d+)', filename, re.IGNORECASE)
            if fn_match:
                report_number = f"{fn_match.group(1)}/{fn_match.group(2)}"
        
        date_str = self._extract_pattern(text, 'date')
        closing_date_str = self._extract_pattern(text, 'closing_date')
        sample_type = self._extract_pattern(text, 'sample') or "Soro"
        
        test_date = parse_portuguese_date(date_str)
        closing_date = parse_portuguese_date(closing_date_str)
        
        if not report_number:
            self.warnings.append("Could not extract report number")
        
        session = TestSession(
            report_number=report_number,
            test_date=test_date,
            closing_date=closing_date,
            sample_type=sample_type.strip(),
            lab_name="DNAtech",
            source_system="dnatech",
            report_type="dnatech_proteinogram",
            external_report_id=report_number or None,
            report_source=pdf_path,
            pdf_path=pdf_path
        )
        
        return session

    def _parse_animal_identifiers(self, text: str) -> List[AnimalIdentifier]:
        identifiers: List[AnimalIdentifier] = []

        client_ref = self._extract_pattern(text, 'client_ref')
        if client_ref:
            identifiers.append(AnimalIdentifier(
                source_system="dnatech",
                identifier_type="client_ref",
                identifier_value=client_ref,
            ))

        microchip = self._extract_pattern(text, 'microchip')
        if microchip and microchip.isdigit():
            identifiers.append(AnimalIdentifier(
                source_system="microchip",
                identifier_type="microchip",
                identifier_value=microchip,
            ))

        return identifiers
    
    def _parse_results(self, text: str, tables: List) -> List[ProteinResult]:
        """
        Extract protein results from the report.
        
        Uses a combination of regex patterns and table extraction.
        """
        results = []
        
        # Try table-based extraction first (more reliable)
        if tables:
            results = self._parse_results_from_tables(tables)
        
        # If table extraction didn't work well, fall back to text parsing
        if len(results) < 5:  # We expect at least 7 markers
            self.warnings.append("Table extraction incomplete, using text parsing")
            results = self._parse_results_from_text(text)
        
        return results
    
    def _parse_results_from_tables(self, tables: List) -> List[ProteinResult]:
        """Extract results from PDF tables"""
        results = []
        
        for table in tables:
            if not table:
                continue
            
            for row in table:
                if not row or len(row) < 2:
                    continue
                
                # Skip header rows
                first_cell = str(row[0] or "").strip().lower()
                if first_cell in ['análise', 'analise', '', 'proteinograma']:
                    continue
                
                result = self._parse_table_row(row)
                if result:
                    results.append(result)
        
        return results
    
    def _parse_table_row(self, row: List) -> Optional[ProteinResult]:
        """Parse a single table row into a ProteinResult"""
        if not row or len(row) < 2:
            return None
        
        # Clean up row values
        row = [str(cell or "").strip() for cell in row]
        
        marker_name = row[0]
        
        # Skip if not a valid marker name
        if not marker_name or marker_name.lower() in ['análise', 'analise', '']:
            return None
        
        # Handle special case for "ELECTROFORESE DE PROTEINAS" header
        if 'electroforese' in marker_name.lower():
            return None
        
        result = ProteinResult(
            marker_name=marker_name,
            marker_category="PROTEINOGRAMA"
        )
        
        # Parse result values (column 1-2 typically has value + possibly absolute)
        if len(row) > 1:
            result_str = row[1]
            
            # Check if it contains both percentage and absolute value
            # Format: "53,7 % 3,4" or just "6,4"
            pct_match = re.search(r'([\d,\.]+)\s*%\s*([\d,\.]+)?', result_str)
            if pct_match:
                result.value = self._parse_number(pct_match.group(1))
                result.unit = "%"
                if pct_match.group(2):
                    result.value_absolute = self._parse_number(pct_match.group(2))
            else:
                # Just a single value (like total proteins or A/G ratio)
                result.value = self._parse_number(result_str)
        
        # Parse unit (column 2 or 3)
        for i in range(2, min(4, len(row))):
            if row[i] and 'g/dL' in row[i]:
                result.unit_absolute = 'g/dL'
                if not result.unit:
                    result.unit = 'g/dL'
                break
        
        # Parse reference ranges (usually columns 3-4)
        for i in range(2, len(row)):
            cell = row[i]
            if not cell:
                continue
            
            ref_min, ref_max = self._parse_reference_range(cell)
            if ref_min is not None and ref_max is not None:
                if result.reference_min is None:
                    # First reference range found (percentage for fractions)
                    result.reference_min = ref_min
                    result.reference_max = ref_max
                else:
                    # Second reference range (absolute g/dL)
                    result.reference_min_absolute = ref_min
                    result.reference_max_absolute = ref_max
        
        # Apply default reference ranges if not found
        self._apply_default_ranges(result)
        
        # Compute flags
        result.compute_flags()
        
        return result
    
    def _parse_results_from_text(self, text: str) -> List[ProteinResult]:
        """Fallback: extract results using text patterns"""
        results = []
        lines = text.split('\n')
        
        for line in lines:
            for marker_name, (pattern, has_pct, has_abs) in self.MARKER_PATTERNS.items():
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    result = ProteinResult(
                        marker_name=marker_name,
                        marker_category="PROTEINOGRAMA"
                    )
                    
                    if has_pct:
                        result.value = self._parse_number(match.group(1))
                        result.unit = "%"
                        if has_abs and len(match.groups()) > 1 and match.group(2):
                            result.value_absolute = self._parse_number(match.group(2))
                            result.unit_absolute = "g/dL"
                    else:
                        result.value = self._parse_number(match.group(1))
                        if has_abs:
                            result.unit = "g/dL"
                        else:
                            result.unit = "ratio" if "Rel." in marker_name else ""
                    
                    # Extract reference ranges from the same line
                    refs = re.findall(r'([\d,\.]+)\s*-\s*([\d,\.]+)', line)
                    if refs:
                        result.reference_min, result.reference_max = \
                            self._parse_number(refs[0][0]), self._parse_number(refs[0][1])
                        if len(refs) > 1:
                            result.reference_min_absolute, result.reference_max_absolute = \
                                self._parse_number(refs[1][0]), self._parse_number(refs[1][1])
                    
                    self._apply_default_ranges(result)
                    result.compute_flags()
                    results.append(result)
                    break
        
        return results
    
    def _apply_default_ranges(self, result: ProteinResult):
        """Apply default reference ranges if not extracted from PDF"""
        defaults = self.CANINE_REFERENCE_RANGES.get(result.marker_name)
        if not defaults:
            return
        
        if 'min_pct' in defaults:
            # Marker with both percentage and absolute ranges
            if result.reference_min is None:
                result.reference_min = defaults['min_pct']
                result.reference_max = defaults['max_pct']
            if result.reference_min_absolute is None:
                result.reference_min_absolute = defaults['min_abs']
                result.reference_max_absolute = defaults['max_abs']
        else:
            # Simple marker with single range
            if result.reference_min is None:
                result.reference_min = defaults['min']
                result.reference_max = defaults['max']
    
    # =========================================================================
    # BIOCHEMISTRY PARSING (UPC Ratio, Kidney markers)
    # =========================================================================
    
    def _parse_biochemistry(self, text: str) -> Optional[BiochemistryResult]:
        """
        Parse the BIOQUIMICA section for UPC ratio and kidney markers.
        
        Looks for:
        - RACIO (P.TOTAIS/CREATININA) URINA
        - P.TOTAIS (URINA)
        - CREATININA (URINA)
        """
        # Check if BIOQUIMICA section exists
        if 'BIOQUIMICA' not in text.upper():
            return None
        
        result = BiochemistryResult()
        
        # Parse UPC ratio
        # Pattern: "RACIO (P.TOTAIS/CREATININA) URINA 1,39 (A)"
        upc_match = re.search(
            r'RACIO\s*\(P\.?TOTAIS/CREATININA\)\s*URINA\s+([\d,\.]+)',
            text, re.IGNORECASE
        )
        if upc_match:
            result.upc_ratio = self._parse_number(upc_match.group(1))
        
        # Parse urine total protein
        # Pattern: "P.TOTAIS (URINA) 51,6 mg/dl"
        protein_match = re.search(
            r'P\.?TOTAIS\s*\(URINA\)\s+([\d,\.]+)',
            text, re.IGNORECASE
        )
        if protein_match:
            result.urine_total_protein = self._parse_number(protein_match.group(1))
        
        # Parse urine creatinine
        # Pattern: "CREATININA (URINA) 37,11 mg/dl"
        creat_match = re.search(
            r'CREATININA\s*\(URINA\)\s+([\d,\.]+)',
            text, re.IGNORECASE
        )
        if creat_match:
            result.urine_creatinine = self._parse_number(creat_match.group(1))
        
        # Only return if we found at least one value
        if result.upc_ratio is not None or result.urine_total_protein is not None:
            result.compute_upc_status()
            return result
        
        return None
    
    # =========================================================================
    # URINALYSIS PARSING (Urina Tipo II)
    # =========================================================================
    
    def _parse_urinalysis(self, text: str) -> Optional[UrinalysisResult]:
        """
        Parse the URINAS section for complete urinalysis.
        
        Looks for:
        - CARACTERES GERAIS (color, appearance)
        - Bioquímica Urinária (glucose, bilirubin, pH, etc.)
        - EXAME MICROSCÓPICO DO SEDIMENTO
        - OBSERVAÇÕES
        """
        # Check if URINAS section exists
        if 'URINA TIPO II' not in text.upper() and 'URINAS' not in text.upper():
            return None
        
        result = UrinalysisResult()
        
        # === General Characteristics ===
        
        # Color: "Cor Amarela Clara"
        color_match = re.search(r'Cor\s+([A-Za-zÀ-ÿ\s]+?)(?=\n|Aspecto)', text)
        if color_match:
            result.color = color_match.group(1).strip()
        
        # Appearance: "Aspecto Límpido" or "Aspecto Ligeiramente Turvo"
        aspect_match = re.search(r'Aspecto\s+([A-Za-zÀ-ÿ\s]+?)(?=\n|Bioqu)', text)
        if aspect_match:
            result.appearance = aspect_match.group(1).strip()
        
        # === Biochemistry ===
        
        # Glucose
        glucose_match = re.search(r'Glucose\s+(Negativo|Positivo|[\d,\.]+)', text, re.IGNORECASE)
        if glucose_match:
            result.glucose = glucose_match.group(1).strip()
        
        # Bilirubin
        bili_match = re.search(r'Bilirrubina\s+(Negativo|Positivo|[\d,\.]+)', text, re.IGNORECASE)
        if bili_match:
            result.bilirubin = bili_match.group(1).strip()
        
        # Ketones
        ketone_match = re.search(r'Corpos\s+cet[óo]nicos\s+(Negativo|Positivo|[\d,\.]+)', text, re.IGNORECASE)
        if ketone_match:
            result.ketones = ketone_match.group(1).strip()
        
        # Specific gravity (Densidade)
        density_match = re.search(r'Densidade\s+([\d,\.]+)', text)
        if density_match:
            result.specific_gravity = self._parse_number(density_match.group(1))
        
        # pH
        ph_match = re.search(r'pH\s+([\d,\.]+)', text)
        if ph_match:
            result.ph = self._parse_number(ph_match.group(1))
        
        # Proteins (can be "Negativo" or "20 mg/dL")
        prot_match = re.search(r'Prote[íi]nas\s+(Negativo|[\d,\.]+\s*mg/dL|[\d,\.]+)', text, re.IGNORECASE)
        if prot_match:
            val = prot_match.group(1).strip()
            result.proteins = val
            # Try to extract numeric value
            num_match = re.search(r'([\d,\.]+)', val)
            if num_match and 'negativo' not in val.lower():
                result.proteins_value = self._parse_number(num_match.group(1))
        
        # Urobilinogen
        uro_match = re.search(r'Urobilinог[éе]nio\s+(Negativo|Normal|[\d,\.]+)', text, re.IGNORECASE)
        if uro_match:
            result.urobilinogen = uro_match.group(1).strip()
        
        # Nitrites
        nitrite_match = re.search(r'Nitritos\s+(Negativo|Positivo)', text, re.IGNORECASE)
        if nitrite_match:
            result.nitrites = nitrite_match.group(1).strip()
        
        # === Microscopic Sediment ===
        
        # Leukocytes
        leuko_match = re.search(r'Leucocitos\s+(<?\d+|Raros|Ausentes)', text, re.IGNORECASE)
        if leuko_match:
            result.leukocytes = leuko_match.group(1).strip()
        
        # Erythrocytes
        eryth_match = re.search(r'Eritr[óo]citos\s+(<?\d+|Raros|Ausentes)', text, re.IGNORECASE)
        if eryth_match:
            result.erythrocytes = eryth_match.group(1).strip()
        
        # Epithelial cells
        epith_match = re.search(r'Cel\.?\s*Epiteliais\s+(Raras|Ausentes|Presentes|\d+)', text, re.IGNORECASE)
        if epith_match:
            result.epithelial_cells = epith_match.group(1).strip()
        
        # Casts (Cilindros)
        casts_match = re.search(r'Cilindros\s+(Ausentes|Presentes|Raros|\d+)', text, re.IGNORECASE)
        if casts_match:
            result.casts = casts_match.group(1).strip()
        
        # Crystals
        crystal_match = re.search(r'Cristais\s+(Ausentes|Presente|Presentes|Raros)', text, re.IGNORECASE)
        if crystal_match:
            result.crystals = crystal_match.group(1).strip()
        
        # Mucus
        mucus_match = re.search(r'Muco\s+(Ausentes?|Presente|Raros?)', text, re.IGNORECASE)
        if mucus_match:
            result.mucus = mucus_match.group(1).strip()
        
        # Bacteria - improved pattern
        bact_match = re.search(r'Bact[ée]rias\s+([A-Za-zÀ-ÿ\s\-<>]+?)(?=\n[A-Z]|\nOBSERV|\nData|\nGot[íi]culas|$)', text, re.IGNORECASE)
        if bact_match:
            bacteria_text = bact_match.group(1).strip()
            # Clean up duplicates and extra whitespace
            bacteria_text = ' '.join(bacteria_text.split())
            # Remove duplicate words like "Ausentes Ausentes"
            words = bacteria_text.split()
            seen = []
            for w in words:
                if w.lower() not in [x.lower() for x in seen[-2:]] if seen else True:
                    seen.append(w)
            result.bacteria = ' '.join(seen)
        
        # === Observations ===
        # Look for OBSERVAÇÕES: in the URINAS section specifically
        # Pattern: after sediment section, before "Data de fecho"
        obs_patterns = [
            # Direct observations after OBSERVAÇÕES:
            r'OBSERVA[ÇC][ÕO]ES:\s*([A-Za-zÀ-ÿ\s,\(\)]+?)(?=\nData de fecho)',
            # Observations that start with specific content (crystals, cells, etc.)
            r'OBSERVA[ÇC][ÕO]ES:\s*((?:C[ée]lulas|Got[íi]culas|Espermatoz|Discretas)[^\n]+(?:\n[^\n]+)*?)(?=\nData de fecho)',
        ]
        
        for pattern in obs_patterns:
            obs_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if obs_match:
                obs_text = obs_match.group(1).strip()
                # Clean up
                obs_text = ' '.join(obs_text.split())
                # Filter out non-observation text
                if obs_text and len(obs_text) > 5 and 'resultado confirmado' not in obs_text.lower():
                    result.observations = obs_text
                    break
        
        # Compute flags
        result.compute_flags()
        
        # Only return if we found meaningful data
        if result.color or result.specific_gravity or result.ph:
            return result
        
        return None


class CVSAnalyzerParser:
    """Parser for structured CVS analyzer result PDFs."""

    def parse_pdf(self, pdf_path: str) -> ParsedReport:
        text = _extract_layout_text(pdf_path)
        name_line = self._line_starting(text, "Nome animal:")
        line_match = re.match(r'Nome animal:(.*?)\s{2,}ID paciente:([^\s]+)\s{2,}Tutor:(.*)$', name_line or "")
        animal_name = _normalize_space(line_match.group(1)) if line_match else None
        patient_id = _normalize_space(line_match.group(2)) if line_match else None
        owner_name = _normalize_space(line_match.group(3)) if line_match else None

        sample_line = self._line_starting(text, "Amostra:")
        sample_match = re.match(r'Amostra:([^\s]+)\s{2,}Lab\.:\s*(.*?)\s{2,}Vers[ãa]o:(.*)$', sample_line or "")
        sample_slot = _normalize_space(sample_match.group(1)) if sample_match else None
        version = _normalize_space(sample_match.group(3)) if sample_match else None

        species_line = self._line_starting(text, "Espécies:")
        species_match = re.match(r'Esp[ée]cies:(.*?)\s{2,}Operador:([^\s]+)\s{2,}M[áa]quina:([^\s]+)$', species_line or "")
        species = _normalize_space(species_match.group(1)) if species_match else None
        operator = _normalize_space(species_match.group(2)) if species_match else None
        machine_id = _normalize_space(species_match.group(3)) if species_match else None

        age_line = self._line_starting(text, "Idade:")
        age_match = re.match(r'Idade:(.*?)\s{2,}Identifica[çc][ãa]o:([^\s]+)\s{2,}Amostra:([^\s]+)$', age_line or "")
        identification = _normalize_space(age_match.group(2)) if age_match else None
        sample_type = _normalize_space(age_match.group(3)) if age_match else None
        test_datetime = self._parse_datetime(text)
        test_date = test_datetime.date() if test_datetime else None
        clinic_name = self._extract_first_nonempty_line(text)

        measurements = self._parse_measurements(text)
        panel_name = self._infer_panel_name([m.measurement_code for m in measurements])

        report_token_parts = [
            patient_id or "unknown",
            test_datetime.strftime("%Y%m%d%H%M%S") if test_datetime else "unknown",
            sample_slot or "report",
        ]
        external_report_id = "/".join(report_token_parts)
        report_number = f"CVS/{external_report_id}"

        anomalies = self._extract(text, r'\*Anomalias na amostra:\s*([^\n]+)')
        metadata = {
            "version": version,
            "operator": operator,
            "machine_id": machine_id,
            "identification": identification,
            "sample_slot": sample_slot,
            "sample_anomalies": anomalies,
        }

        animal = Animal(
            name=(animal_name or "Unknown").strip(),
            species=_parse_species(species),
            owner_name=(owner_name or "").strip() or None,
        )
        session = TestSession(
            report_number=report_number,
            test_date=test_date,
            closing_date=test_date,
            sample_type=(sample_type or "Plasma").strip(),
            lab_name=clinic_name or "CVS SOS Animal",
            source_system="cvs_analyzer",
            report_type="cvs_analyzer",
            external_report_id=external_report_id,
            report_source=pdf_path,
            reported_at=test_datetime,
            clinic_name=clinic_name or "CVS SOS Animal",
            panel_name=panel_name,
            raw_metadata_json=json.dumps(metadata, ensure_ascii=False),
            pdf_path=pdf_path,
        )
        animal_identifiers = []
        if patient_id:
            animal_identifiers.append(AnimalIdentifier(
                source_system="cvs_analyzer",
                identifier_type="patient_id",
                identifier_value=patient_id,
            ))

        return ParsedReport(
            animal=animal,
            session=session,
            measurements=measurements,
            animal_identifiers=animal_identifiers,
            raw_text=text,
        )

    def _extract(self, text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if not match:
            return None
        return _normalize_space(match.group(1))

    def _line_starting(self, text: str, prefix: str) -> Optional[str]:
        for line in text.splitlines():
            if line.strip().startswith(prefix):
                return line.strip()
        return None

    def _extract_first_nonempty_line(self, text: str) -> Optional[str]:
        for line in text.splitlines():
            cleaned = line.strip()
            if cleaned:
                return cleaned
        return None

    def _parse_datetime(self, text: str) -> Optional[datetime]:
        match = re.search(
            r'Hor[áa]rio teste:\s*(\d{4}\.\d{2}\.\d{2})\s+No\.:.*?\s+(\d{2}:\d{2}:\d{2})',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        combined = f"{match.group(1)} {match.group(2)}"
        try:
            return datetime.strptime(combined, "%Y.%m.%d %H:%M:%S")
        except ValueError:
            return None

    def _parse_measurements(self, text: str) -> List[SessionMeasurement]:
        measurements: List[SessionMeasurement] = []
        capture = False
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("Ensaio") and "Resultado" in stripped:
                capture = True
                continue
            if stripped.startswith("Interpretação relatório"):
                break
            if not capture:
                continue
            if stripped.startswith("Ensaio") and "Significado Clínico" in stripped:
                continue

            parts = re.split(r'\s{2,}', stripped)
            if len(parts) < 2:
                continue
            code = parts[0].strip()
            if not code:
                continue
            value_part = parts[1].strip()
            ref_text = parts[2].strip() if len(parts) > 2 else None
            flag = "normal"
            flag_match = re.search(r'\s([HL])$', value_part)
            if flag_match:
                flag = "high" if flag_match.group(1) == "H" else "low"
                value_part = value_part[:flag_match.start()].strip()

            value_match = re.match(r'(?P<value>(?:[<>]\s*)?\d+(?:[.,]\d+)?)\s*(?P<unit>.*)', value_part)
            if value_match:
                value_text = value_match.group("value").strip()
                unit = value_match.group("unit").strip() or None
            else:
                value_text = value_part
                unit = None

            reference_min = None
            reference_max = None
            if ref_text and "-" in ref_text:
                ref_parts = ref_text.split("-", 1)
                reference_min = _parse_decimal(ref_parts[0])
                reference_max = _parse_decimal(ref_parts[1])

            measurements.append(SessionMeasurement(
                measurement_code=code,
                measurement_name=code,
                value_numeric=_parse_decimal(value_text),
                value_text=value_text,
                unit=unit,
                reference_min=reference_min,
                reference_max=reference_max,
                reference_text=ref_text,
                flag=flag,
                sort_order=len(measurements),
            ))

        return measurements

    def _infer_panel_name(self, codes: List[str]) -> str:
        code_set = {code.upper() for code in codes}
        if {"CREA", "BUN", "GLU", "PHOS", "NA", "K"} & code_set:
            return "renal_electrolyte_panel"
        if {"ALB", "TP", "GLOB", "AST", "ALT"} & code_set:
            return "hepatic_protein_panel"
        return "analyzer_panel"


class VedisCytologyParser:
    """Parser for Vedis cytology pathology reports."""

    def parse_pdf(self, pdf_path: str) -> ParsedReport:
        text = _extract_layout_text(pdf_path)
        patient = self._parse_patient(text)
        clinic_name = "Clínica Veterinária CVS SOS Animal" if "Clínica Veterinária CVS" in text else None
        attending_vet = self._next_first_column_after_label(text, "Attending Vet")
        exam_id = self._extract(text, r'Exam ID\s+(\d+)')
        receipt_date = parse_portuguese_date(self._value_after_label_line(text, "Date of receipt"))
        report_date = parse_portuguese_date(self._value_after_label_line(text, "Date of report"))
        clinical_history = _clean_vedis_text(self._extract_block(text, "CLINICAL HISTORY", "DIAGNOSIS"))
        specimens = self._parse_specimens(text, clinical_history)
        general_comment = _clean_vedis_text(self._extract_block(text, "GENERAL COMMENT", "TRADUÇÃO"))
        assets = _extract_cytology_assets(pdf_path)

        animal = Animal(
            name=patient["name"] or "Unknown",
            species=_parse_species(patient["species"]),
            breed=patient["breed"] or "",
            owner_name=patient["owner"],
            sex=patient["sex"],
            neutered=patient["neutered"],
            age_years=patient["age_years"],
            age_months=patient["age_months"],
            responsible_vet=attending_vet,
        )
        session = TestSession(
            report_number=f"VEDIS/{exam_id}" if exam_id else f"VEDIS/{os.path.basename(pdf_path)}",
            test_date=report_date,
            closing_date=report_date,
            sample_type="Cytology",
            lab_name="Vedis",
            source_system="vedis",
            report_type="cytology",
            external_report_id=exam_id,
            report_source=pdf_path,
            reported_at=datetime.combine(report_date, datetime.min.time()) if report_date else None,
            received_at=datetime.combine(receipt_date, datetime.min.time()) if receipt_date else None,
            clinic_name=clinic_name or "Clínica Veterinária CVS SOS Animal",
            panel_name="cytology",
            raw_metadata_json=json.dumps({
                "attending_vet": attending_vet,
                "general_comment": general_comment,
            }, ensure_ascii=False),
            pdf_path=pdf_path,
        )

        findings = specimens
        if general_comment:
            findings.append(PathologyFinding(
                section_type="general_comment",
                title="General comment",
                comment=general_comment,
                sort_order=len(findings),
            ))

        return ParsedReport(
            animal=animal,
            session=session,
            pathology_findings=findings,
            assets=assets,
            raw_text=text,
        )

    def _extract(self, text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            return None
        return _normalize_space(match.group(1))

    def _extract_line(self, text: str, label: str) -> Optional[str]:
        match = re.search(rf'^{label}\s*(.*?)\s*$', text, re.IGNORECASE | re.MULTILINE)
        if not match:
            return None
        value = _normalize_space(match.group(1))
        return value or None

    def _value_after_label_line(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped.startswith(label):
                continue
            inline_value = stripped[len(label):].strip(" :")
            if inline_value:
                return _normalize_space(re.split(r'\s{2,}', inline_value)[0])
            for followup in lines[index + 1:]:
                candidate = followup.strip()
                if candidate:
                    return _normalize_space(re.split(r'\s{2,}', candidate)[0])
        return None

    def _next_first_column_after_label(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(label):
                for followup in lines[index + 1:]:
                    candidate = followup.strip()
                    if candidate:
                        return _normalize_space(re.split(r'\s{2,}', candidate)[0])
        return None

    def _right_column_between_markers(self, text: str, anchor: str,
                                      start_label: str, end_label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        anchored = False
        collecting = False
        parts: List[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if not anchored:
                if anchor in stripped:
                    anchored = True
                continue
            if not collecting:
                if start_label in stripped:
                    collecting = True
                continue
            if end_label in stripped:
                break
            columns = re.split(r'\s{2,}', stripped)
            if len(columns) >= 2:
                parts.append(columns[-1].strip())
            elif not _is_vedis_noise_line(stripped):
                parts.append(stripped)
        return _normalize_space(" ".join(parts)) or None

    def _extract_name_before(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(label):
                for candidate_line in reversed(lines[:index]):
                    candidate = candidate_line.strip()
                    if not candidate:
                        continue
                    candidate = re.split(r'\s{2,}', candidate)[0].strip()
                    if not candidate or candidate in {"PATIENT", "PACIENTE"}:
                        continue
                    if ":" in candidate or candidate.startswith("Vedis"):
                        continue
                    return candidate
        return None

    def _extract_name_after_heading(self, text: str, heading: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        heading_index = None
        for index, raw_line in enumerate(lines):
            if raw_line.strip() == heading:
                heading_index = index
                break
        if heading_index is None:
            return None
        for candidate_line in lines[heading_index + 1:]:
            candidate = candidate_line.strip()
            if not candidate:
                continue
            if candidate.startswith("Vedis"):
                continue
            first_column = re.split(r'\s{2,}', candidate)[0].strip()
            if not first_column or ":" in first_column or first_column in {"CYTOLOGY REPORT", "RELATÓRIO IMUNOCITOQUÍMICA"}:
                continue
            return first_column
        return None

    def _extract_block(self, text: str, start: str, end: str) -> str:
        match = re.search(
            rf'{re.escape(start)}\s+(.*?)\s+{re.escape(end)}',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        return match.group(1) if match else ""

    def _parse_patient(self, text: str) -> Dict[str, Any]:
        name = self._extract_name_after_heading(text, "PATIENT")
        owner = self._value_after_label_line(text, "Owner:")
        species = self._value_after_label_line(text, "Specie:")
        breed = self._value_after_label_line(text, "Breed:")
        gender = self._value_after_label_line(text, "Gender:")
        age_str = self._value_after_label_line(text, "DOB/Age:")
        sex = "U"
        neutered = None
        if gender:
            lowered = gender.lower()
            sex = "M" if "male" in lowered else ("F" if "female" in lowered else "U")
            neutered = "neuter" in lowered
        age_years = _parse_decimal(re.search(r'(\d+)', age_str or "") and re.search(r'(\d+)', age_str or "").group(1))
        return {
            "name": name,
            "owner": owner,
            "species": species,
            "breed": breed,
            "sex": sex,
            "neutered": neutered,
            "age_years": age_years,
            "age_months": None,
        }

    def _parse_specimens(self, text: str, clinical_history: str) -> List[PathologyFinding]:
        history_by_label = {}
        for label, body in re.findall(r'([AB])-\s*(.*?)(?=(?:[AB]-\s)|$)', clinical_history or ""):
            history_by_label[label] = body.strip()

        findings: List[PathologyFinding] = []
        specimen_patterns = {
            "A": r'A-\s*([^\n]+)\s+DIAGNOSIS\s+(.*?)\s+SAMPLE\s+(.*?)\s+MICROSCOPIC DESCRIPTION\s+(.*?)(?=\s+B-\s*[^\n]+|GENERAL COMMENT)',
            "B": r'B-\s*([^\n]+)\s+DIAGNOSIS\s+(.*?)\s+SAMPLE\s+(.*?)\s+MICROSCOPIC DESCRIPTION\s+(.*?)(?=GENERAL COMMENT)',
        }
        for label, pattern in specimen_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if not match:
                continue
            title = _clean_vedis_text(match.group(1))
            diagnosis = _clean_vedis_text(match.group(2))
            sample_method = _clean_vedis_text(match.group(3))
            microscopic_description = _clean_vedis_text(match.group(4))
            findings.append(PathologyFinding(
                section_type="cytology_specimen",
                specimen_label=label,
                title=title,
                sample_site=title,
                sample_method=sample_method,
                clinical_history=history_by_label.get(label),
                microscopic_description=microscopic_description,
                diagnosis=diagnosis,
                sort_order=len(findings),
            ))

        if not any(finding.specimen_label == "A" for finding in findings):
            title = self._extract(text, r'A-\s*([^\n]+)')
            diagnosis = self._right_column_between_markers(text, "A- Left kidney", "DIAGNOSIS", "SAMPLE")
            sample_method = self._right_column_between_markers(text, "A- Left kidney", "SAMPLE", "MICROSCOPIC DESCRIPTION")
            microscopic_description = self._right_column_between_markers(text, "A- Left kidney", "MICROSCOPIC DESCRIPTION", "B- Retroperitoneum")
            if diagnosis or sample_method or microscopic_description:
                findings.insert(0, PathologyFinding(
                    section_type="cytology_specimen",
                    specimen_label="A",
                    title=_clean_vedis_text(title) or "Specimen A",
                    sample_site=_clean_vedis_text(title) or "Specimen A",
                    sample_method=sample_method,
                    clinical_history=history_by_label.get("A"),
                    microscopic_description=microscopic_description,
                    diagnosis=diagnosis,
                    sort_order=0,
                ))
                for index, finding in enumerate(findings):
                    finding.sort_order = index
        return findings


class VedisImmunocytochemistryParser:
    """Parser for Vedis immunocytochemistry reports."""

    def parse_pdf(self, pdf_path: str) -> ParsedReport:
        text = _extract_layout_text(pdf_path)
        patient = self._parse_patient(text)
        clinic_name = "Clínica Veterinária CVS SOS Animal" if "Clínica Veterinária CVS" in text else None
        attending_vet = self._next_first_column_after_label(text, "Veterinário/a")
        exam_id = self._extract(text, r'ID exame\s+(\d+)')
        receipt_date = parse_portuguese_date(self._value_after_label_line(text, "Data de receção"))
        report_date = parse_portuguese_date(self._value_after_label_line(text, "Data de relatório"))
        product_sent = _clean_vedis_text(self._extract_block(text, "PRODUTO ENVIADO", "DESCRIÇÃO MICROSCÓPICA"))
        microscopic_description = _clean_vedis_text(self._extract_block(text, "DESCRIÇÃO MICROSCÓPICA", "DIAGNÓSTICO"))
        diagnosis = self._right_column_between_labels(text, "DIAGNÓSTICO", "COMENTÁRIO GERAL") or _clean_vedis_text(self._extract_block(text, "DIAGNÓSTICO", "COMENTÁRIO GERAL"))
        comment = self._right_column_between_labels(text, "COMENTÁRIO GERAL", "Data de receção") or _clean_vedis_text(self._extract_block(text, "COMENTÁRIO GERAL", "Pathologist"))
        if not comment:
            comment = _clean_vedis_text(self._extract_block(text, "COMENTÁRIO GERAL", "Em caso de dúvidas"))

        measurements = self._parse_markers(text)
        assets = _extract_cytology_assets(pdf_path)

        animal = Animal(
            name=patient["name"] or "Unknown",
            species=_parse_species(patient["species"]),
            breed=patient["breed"] or "",
            owner_name=patient["owner"],
            sex=patient["sex"],
            neutered=patient["neutered"],
            age_years=patient["age_years"],
            age_months=patient["age_months"],
            responsible_vet=attending_vet,
        )
        session = TestSession(
            report_number=f"VEDIS/{exam_id}" if exam_id else f"VEDIS/{os.path.basename(pdf_path)}",
            test_date=report_date,
            closing_date=report_date,
            sample_type="Immunocytochemistry",
            lab_name="Vedis",
            source_system="vedis",
            report_type="immunocytochemistry",
            external_report_id=exam_id,
            report_source=pdf_path,
            reported_at=datetime.combine(report_date, datetime.min.time()) if report_date else None,
            received_at=datetime.combine(receipt_date, datetime.min.time()) if receipt_date else None,
            clinic_name=clinic_name or "Clínica Veterinária CVS SOS Animal",
            panel_name="immunocytochemistry",
            raw_metadata_json=json.dumps({
                "product_sent": product_sent,
            }, ensure_ascii=False),
            pdf_path=pdf_path,
        )
        findings = [PathologyFinding(
            section_type="immunocytochemistry",
            title="Immunocytochemistry",
            sample_method=product_sent,
            microscopic_description=microscopic_description,
            diagnosis=diagnosis,
            comment=comment,
            sort_order=0,
        )]

        return ParsedReport(
            animal=animal,
            session=session,
            measurements=measurements,
            pathology_findings=findings,
            assets=assets,
            raw_text=text,
        )

    def _extract(self, text: str, pattern: str) -> Optional[str]:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
        if not match:
            return None
        return _normalize_space(match.group(1))

    def _extract_line(self, text: str, label: str) -> Optional[str]:
        match = re.search(rf'^{label}\s*(.*?)\s*$', text, re.IGNORECASE | re.MULTILINE)
        if not match:
            return None
        value = _normalize_space(match.group(1))
        return value or None

    def _value_after_label_line(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped.startswith(label):
                continue
            inline_value = stripped[len(label):].strip(" :")
            if inline_value:
                return _normalize_space(re.split(r'\s{2,}', inline_value)[0])
            for followup in lines[index + 1:]:
                candidate = followup.strip()
                if candidate:
                    return _normalize_space(re.split(r'\s{2,}', candidate)[0])
        return None

    def _next_first_column_after_label(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(label):
                for followup in lines[index + 1:]:
                    candidate = followup.strip()
                    if candidate:
                        return _normalize_space(re.split(r'\s{2,}', candidate)[0])
        return None

    def _right_column_between_labels(self, text: str, start_label: str,
                                     end_label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        collecting = False
        parts: List[str] = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if not collecting:
                if start_label in stripped:
                    collecting = True
                continue
            if end_label in stripped:
                break
            columns = re.split(r'\s{2,}', stripped)
            if len(columns) >= 2:
                parts.append(columns[-1].strip())
            elif not _is_vedis_noise_line(stripped):
                parts.append(stripped)
        return _normalize_space(" ".join(parts)) or None

    def _extract_name_before(self, text: str, label: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        for index, raw_line in enumerate(lines):
            if raw_line.strip().startswith(label):
                for candidate_line in reversed(lines[:index]):
                    candidate = candidate_line.strip()
                    if not candidate:
                        continue
                    candidate = re.split(r'\s{2,}', candidate)[0].strip()
                    if not candidate or candidate in {"PATIENT", "PACIENTE"}:
                        continue
                    if ":" in candidate or candidate.startswith("Vedis"):
                        continue
                    return candidate
        return None

    def _extract_name_after_heading(self, text: str, heading: str) -> Optional[str]:
        lines = [line.rstrip() for line in text.splitlines()]
        heading_index = None
        for index, raw_line in enumerate(lines):
            if raw_line.strip() == heading:
                heading_index = index
                break
        if heading_index is None:
            return None
        for candidate_line in lines[heading_index + 1:]:
            candidate = candidate_line.strip()
            if not candidate:
                continue
            if candidate.startswith("Vedis"):
                continue
            first_column = re.split(r'\s{2,}', candidate)[0].strip()
            if not first_column or ":" in first_column or first_column in {"RELATÓRIO IMUNOCITOQUÍMICA"}:
                continue
            return first_column
        return None

    def _extract_block(self, text: str, start: str, end: str) -> str:
        match = re.search(
            rf'{re.escape(start)}\s+(.*?)\s+{re.escape(end)}',
            text,
            re.IGNORECASE | re.DOTALL,
        )
        return match.group(1) if match else ""

    def _parse_patient(self, text: str) -> Dict[str, Any]:
        name = self._extract_name_after_heading(text, "PACIENTE")
        owner = self._value_after_label_line(text, "Tutor:")
        species = self._value_after_label_line(text, "Espécie:")
        breed = self._value_after_label_line(text, "Raça:")
        gender = self._value_after_label_line(text, "Sexo:")
        age_str = self._value_after_label_line(text, "DN/Idade:")
        sex = "U"
        neutered = None
        if gender:
            lowered = gender.lower()
            sex = "M" if "male" in lowered else ("F" if "female" in lowered else "U")
            neutered = "neuter" in lowered
        age_years = _parse_decimal(re.search(r'(\d+)', age_str or "") and re.search(r'(\d+)', age_str or "").group(1))
        return {
            "name": name,
            "owner": owner,
            "species": species,
            "breed": breed,
            "sex": sex,
            "neutered": neutered,
            "age_years": age_years,
            "age_months": None,
        }

    def _parse_markers(self, text: str) -> List[SessionMeasurement]:
        measurements: List[SessionMeasurement] = []
        if re.search(r'CD3.*?>\s*85-90%', text, re.IGNORECASE | re.DOTALL):
            measurements.append(SessionMeasurement(
                panel_name="immunocytochemistry",
                measurement_code="CD3",
                measurement_name="CD3",
                value_text="positive (>85-90%)",
                flag="high",
                sort_order=len(measurements),
            ))
        if re.search(r'PAX-?5.*?N[ãa]o se observam', text, re.IGNORECASE | re.DOTALL) or re.search(r'CD3 \+ / PAX5 -', text, re.IGNORECASE):
            measurements.append(SessionMeasurement(
                panel_name="immunocytochemistry",
                measurement_code="PAX5",
                measurement_name="PAX5",
                value_text="negative",
                flag="normal",
                sort_order=len(measurements),
            ))
        return measurements


def parse_lab_report(pdf_path: str) -> ParsedReport:
    """Parse any supported report type."""
    text = _extract_pdf_text(pdf_path)
    report_type = detect_report_type(text)

    if report_type == "dnatech_proteinogram":
        return DNAtechParser().parse_pdf(pdf_path)
    if report_type == "cvs_analyzer":
        return CVSAnalyzerParser().parse_pdf(pdf_path)
    if report_type == "vedis_cytology":
        return VedisCytologyParser().parse_pdf(pdf_path)
    if report_type == "vedis_immunocytochemistry":
        return VedisImmunocytochemistryParser().parse_pdf(pdf_path)

    raise ValueError(f"Unsupported report format for {os.path.basename(pdf_path)}")


def parse_dnatech_report(pdf_path: str) -> ParsedReport:
    """Backwards-compatible alias for the generic report parser."""
    return parse_lab_report(pdf_path)


# =============================================================================
# TESTING / DEMO
# =============================================================================

if __name__ == "__main__":
    import sys
    
    # Test with provided PDF if available
    test_pdfs = [
        "/mnt/user-data/uploads/bolt58630_1500951.pdf",
        "/mnt/user-data/uploads/bolt65401_1517628__1_.pdf",
        "/mnt/user-data/uploads/bolt66790_1521038_copy.pdf",
    ]
    
    print("=" * 70)
    print("DNAtech PDF Parser Test - Extended Format")
    print("=" * 70)
    
    for test_pdf in test_pdfs:
        try:
            print(f"\n{'='*70}")
            print(f"📄 Parsing: {test_pdf.split('/')[-1]}")
            print("=" * 70)
            
            result = parse_dnatech_report(test_pdf)
            
            print("\n📋 ANIMAL DATA:")
            print(f"  Name: {result.animal.name}")
            print(f"  Species: {result.animal.species}")
            print(f"  Breed: {result.animal.breed}")
            print(f"  Age: {result.animal.age_display}")
            print(f"  Sex: {result.animal.sex}")
            
            print("\n📅 TEST SESSION:")
            print(f"  Report #: {result.session.report_number}")
            print(f"  Test Date: {result.session.test_date}")
            print(f"  Sample: {result.session.sample_type}")
            
            # Biochemistry results
            if result.biochemistry:
                print("\n🧪 BIOCHEMISTRY (Kidney Markers):")
                print(f"  UPC Ratio: {result.biochemistry.upc_ratio}")
                print(f"  Status: {result.biochemistry.upc_status}")
                print(f"  Urine Total Protein: {result.biochemistry.urine_total_protein} mg/dl")
                print(f"  Urine Creatinine: {result.biochemistry.urine_creatinine} mg/dl")
            
            # Protein results
            if result.results:
                print("\n🔬 PROTEINOGRAM:")
                print("-" * 60)
                print(f"{'Marker':<25} {'Value':<12} {'Abs (g/dL)':<12} {'Flag'}")
                print("-" * 60)
                for r in result.results:
                    value_str = f"{r.value:.1f}{r.unit}" if r.value else "N/A"
                    abs_str = f"{r.value_absolute:.2f}" if r.value_absolute else "N/A"
                    flag_icon = "✓" if r.flag == "normal" else ("↑" if r.flag == "high" else "↓")
                    print(f"  {r.marker_name:<23} {value_str:<12} {abs_str:<12} {flag_icon}")
            
            # Urinalysis results
            if result.urinalysis:
                ua = result.urinalysis
                print("\n💧 URINALYSIS (Urina Tipo II):")
                print(f"  Color: {ua.color}")
                print(f"  Appearance: {ua.appearance}")
                print(f"  Specific Gravity: {ua.specific_gravity}")
                print(f"  pH: {ua.ph}")
                print(f"  Proteins: {ua.proteins}")
                print(f"  Glucose: {ua.glucose}")
                print(f"  Crystals: {ua.crystals}")
                print(f"  Bacteria: {ua.bacteria}")
                if ua.observations:
                    print(f"  Observations: {ua.observations[:100]}...")
                if ua.flags:
                    print(f"  ⚠️ Flagged: {ua.flags}")
            
            if result.parse_warnings:
                print("\n⚠️ WARNINGS:")
                for w in result.parse_warnings:
                    print(f"  - {w}")
                    
        except FileNotFoundError:
            print(f"  File not found, skipping...")
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print("Parse complete!")
