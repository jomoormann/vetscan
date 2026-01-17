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

import pdfplumber
import re
from datetime import date
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

# Import our models
from models import (
    Animal, TestSession, ProteinResult, BiochemistryResult, UrinalysisResult,
    parse_portuguese_date, parse_age
)


@dataclass
class ParsedReport:
    """Container for all data extracted from a PDF report"""
    animal: Animal
    session: TestSession
    results: List[ProteinResult]
    biochemistry: Optional[BiochemistryResult] = None
    urinalysis: Optional[UrinalysisResult] = None
    raw_text: str = ""
    parse_warnings: List[str] = field(default_factory=list)


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
        'microchip': r'Microchip\s+(\d+)',
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
        
        # Parse additional sections
        biochemistry = self._parse_biochemistry(full_text)
        urinalysis = self._parse_urinalysis(full_text)
        
        return ParsedReport(
            animal=animal,
            session=session,
            results=results,
            biochemistry=biochemistry,
            urinalysis=urinalysis,
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
        age_str = self._extract_pattern(text, 'age') or ""
        
        # Parse age and extract sex
        age_years, age_months, sex = parse_age(age_str)
        
        # Clean up breed (remove trailing whitespace/newlines)
        breed = breed.strip()
        
        animal = Animal(
            name=name,
            species=species,
            breed=breed,
            microchip=microchip,
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
            pdf_path=pdf_path
        )
        
        return session
    
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


def parse_dnatech_report(pdf_path: str) -> ParsedReport:
    """
    Convenience function to parse a DNAtech report.
    
    Args:
        pdf_path: Path to the PDF file
        
    Returns:
        ParsedReport with all extracted data
    """
    parser = DNAtechParser()
    return parser.parse_pdf(pdf_path)


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
