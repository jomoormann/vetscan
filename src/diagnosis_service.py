"""
Diagnosis Service for Veterinary Protein Analysis Application

This module provides AI-powered differential diagnosis using Claude API.
It analyzes clinical notes and test data to generate diagnostic reports.
"""

import os
from datetime import date
from typing import Optional, List, Dict, Any

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from dotenv import load_dotenv

from models import (
    Animal, ClinicalNote, TestSession, ProteinResult,
    BiochemistryResult, UrinalysisResult, DiagnosisReport, Database
)


# Load environment variables
load_dotenv()


# =============================================================================
# SYSTEM PROMPT
# =============================================================================

VETERINARY_SYSTEM_PROMPT_NOTES_ONLY = """You are a board-certified veterinary internal medicine specialist (DACVIM) with extensive expertise in clinical pathology and diagnostic medicine. Your role is to provide differential diagnoses based on the clinical information provided.

When analyzing cases, consider:
- Species, breed, age, and sex-specific disease predispositions
- Clinical presentation patterns and their correlations
- Pattern recognition in clinical signs

Reference current veterinary standards including:
- IRIS (International Renal Interest Society) staging for kidney disease
- ACVIM (American College of Veterinary Internal Medicine) consensus statements
- Current peer-reviewed veterinary literature

Structure your response with the following sections:

## Diagnósticos Diferenciais (Differential Diagnoses)
Provide 5-7 ranked differential diagnoses, each with:
- **Diagnosis name** (in Portuguese with English in parentheses)
- **Likelihood**: Alta (High), Moderada (Moderate), or Baixa (Low)
- **Supporting evidence**: Key findings that support this diagnosis
- **Against**: Findings that make this less likely (if any)

## Recomendações (Recommendations)
Suggest next diagnostic steps or immediate clinical actions:
- Priority tests to confirm/rule out top differentials
- Monitoring recommendations
- Urgent findings requiring immediate attention (if any)

## Referências (References)
Cite relevant veterinary literature, guidelines, or consensus statements that support your analysis.

Important notes:
- Be thorough but practical in your recommendations
- Highlight any critical or urgent findings
- Consider both common and important rare conditions
- Write in Portuguese but include English terms in parentheses where helpful for clarity"""


VETERINARY_SYSTEM_PROMPT_COMPREHENSIVE = """You are a board-certified veterinary internal medicine specialist (DACVIM) with extensive expertise in clinical pathology and diagnostic medicine. Your role is to provide a comprehensive analysis of laboratory results and differential diagnoses.

When analyzing cases, consider:
- Species, breed, age, and sex-specific disease predispositions
- Clinical presentation patterns and their correlations
- Laboratory abnormalities and their diagnostic significance
- Pattern recognition across multiple test parameters
- How clinical findings correlate with laboratory results

Reference current veterinary standards including:
- IRIS (International Renal Interest Society) staging for kidney disease
- ACVIM (American College of Veterinary Internal Medicine) consensus statements
- Current peer-reviewed veterinary literature

Structure your response with the following sections:

## Interpretação dos Resultados Laboratoriais (Laboratory Results Interpretation)
For each test category (Proteinogram, Biochemistry/UPC, Urinalysis), provide:

### Proteinograma (if present)
- Analyze each protein fraction (Albumin, Alpha-1, Alpha-2, Beta, Gamma globulins)
- Explain the clinical significance of any abnormal values
- Describe patterns (e.g., acute inflammation, chronic inflammation, gammopathy, protein loss)
- Correlate findings with the clinical notes (e.g., "The elevated gamma globulins are consistent with the chronic inflammatory process described in the clinical notes")

### Bioquímica - Rácio UPC (if present)
- Interpret the UPC ratio according to IRIS guidelines
- Explain what the proteinuria level indicates about kidney function
- Correlate with clinical signs of kidney disease if present

### Urianálise (if present)
- Interpret specific gravity in context of hydration and kidney function
- Analyze sediment findings (cells, crystals, bacteria)
- Explain clinical significance of any abnormalities
- Correlate with clinical presentation

For each parameter, explain:
1. What the value means physiologically
2. Why it might be abnormal in this patient
3. How it correlates with the clinical notes
4. What conditions commonly cause this pattern

## Diagnósticos Diferenciais (Differential Diagnoses)
Provide 5-7 ranked differential diagnoses, each with:
- **Diagnosis name** (in Portuguese with English in parentheses)
- **Likelihood**: Alta (High), Moderada (Moderate), or Baixa (Low)
- **Supporting evidence**: Key clinical AND laboratory findings that support this diagnosis
- **Against**: Findings that make this less likely (if any)

## Recomendações (Recommendations)
Suggest next diagnostic steps or immediate clinical actions:
- Priority tests to confirm/rule out top differentials
- Monitoring recommendations
- Urgent findings requiring immediate attention (if any)
- Specific follow-up tests based on laboratory abnormalities

## Referências (References)
Cite relevant veterinary literature, guidelines, or consensus statements that support your analysis.

Important notes:
- Be thorough and educational in your test interpretation
- Explain the pathophysiology behind abnormal findings
- Always correlate laboratory findings with clinical presentation
- Highlight any critical or urgent findings
- Write in Portuguese but include English terms in parentheses where helpful for clarity"""


# =============================================================================
# DIAGNOSIS SERVICE
# =============================================================================

class DiagnosisService:
    """Service for generating AI-powered differential diagnoses"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the diagnosis service.

        Args:
            api_key: Anthropic API key. If not provided, will try to load from
                     ANTHROPIC_API_KEY environment variable.
        """
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = None
        self.model = "claude-sonnet-4-20250514"

        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "The 'anthropic' package is not installed. "
                "Please install it with: pip install anthropic"
            )

    def _get_client(self):
        """Get or create the Anthropic client"""
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not configured. "
                "Please set the environment variable or pass the API key directly."
            )

        if self.client is None:
            self.client = anthropic.Anthropic(api_key=self.api_key)

        return self.client

    def _format_animal_info(self, animal: Animal) -> str:
        """Format animal signalment for the prompt"""
        lines = ["### Dados do Paciente (Patient Data)"]

        # Name and species
        species_map = {"Canídeo": "Dog", "Felídeo": "Cat"}
        species_en = species_map.get(animal.species, animal.species)
        lines.append(f"- **Nome (Name)**: {animal.name}")
        lines.append(f"- **Espécie (Species)**: {animal.species} ({species_en})")

        # Breed
        if animal.breed:
            lines.append(f"- **Raça (Breed)**: {animal.breed}")

        # Age
        if animal.age_years:
            if animal.age_years >= 1:
                lines.append(f"- **Idade (Age)**: {int(animal.age_years)} anos (years)")
            elif animal.age_months:
                lines.append(f"- **Idade (Age)**: {animal.age_months} meses (months)")

        # Sex
        sex_map = {"M": "Macho (Male)", "F": "Fêmea (Female)", "U": "Desconhecido (Unknown)"}
        if animal.sex:
            lines.append(f"- **Sexo (Sex)**: {sex_map.get(animal.sex, animal.sex)}")

        # Weight
        if animal.weight_kg:
            lines.append(f"- **Peso (Weight)**: {animal.weight_kg} kg")

        # Neutered status
        if animal.neutered is not None:
            status = "Sim (Yes)" if animal.neutered else "Não (No)"
            lines.append(f"- **Castrado/Esterilizado (Neutered)**: {status}")

        return "\n".join(lines)

    def _format_clinical_notes(self, notes: List[ClinicalNote]) -> str:
        """Format clinical notes chronologically"""
        if not notes:
            return ""

        lines = ["### Notas Clínicas (Clinical Notes)"]

        def parse_note_date(note):
            """Parse note date, handling string or date objects"""
            if note.note_date is None:
                return date.min
            if isinstance(note.note_date, str):
                try:
                    from datetime import datetime
                    return datetime.strptime(note.note_date, "%Y-%m-%d").date()
                except:
                    return date.min
            return note.note_date

        # Sort by date (oldest first for chronological narrative)
        sorted_notes = sorted(notes, key=parse_note_date)

        for note in sorted_notes:
            # Format date string
            if note.note_date:
                if isinstance(note.note_date, str):
                    try:
                        from datetime import datetime
                        d = datetime.strptime(note.note_date, "%Y-%m-%d").date()
                        date_str = d.strftime("%d/%m/%Y")
                    except:
                        date_str = note.note_date
                else:
                    date_str = note.note_date.strftime("%d/%m/%Y")
            else:
                date_str = "Data desconhecida"

            title = note.title or "Nota Clínica"
            lines.append(f"\n**{date_str} - {title}**")
            lines.append(note.content)

        return "\n".join(lines)

    def _format_proteinogram(self, results: List[ProteinResult]) -> str:
        """Format proteinogram results"""
        if not results:
            return ""

        lines = ["### Proteinograma (Protein Electrophoresis)"]
        lines.append("")
        lines.append("| Marcador | Valor (%) | Ref (%) | Valor (g/dL) | Ref (g/dL) | Estado |")
        lines.append("|----------|-----------|---------|--------------|------------|--------|")

        for r in results:
            # Format percentage value and reference
            val_pct = f"{r.value:.1f}" if r.value is not None else "--"
            ref_pct = ""
            if r.reference_min is not None and r.reference_max is not None:
                ref_pct = f"{r.reference_min:.1f}-{r.reference_max:.1f}"

            # Format absolute value and reference
            val_abs = f"{r.value_absolute:.2f}" if r.value_absolute is not None else "--"
            ref_abs = ""
            if r.reference_min_absolute is not None and r.reference_max_absolute is not None:
                ref_abs = f"{r.reference_min_absolute:.2f}-{r.reference_max_absolute:.2f}"

            # Status flag
            flag_map = {
                "normal": "Normal",
                "high": "ALTO (HIGH)",
                "low": "BAIXO (LOW)"
            }
            status = flag_map.get(r.flag, r.flag)
            if r.flag_absolute and r.flag_absolute != "normal":
                status = flag_map.get(r.flag_absolute, r.flag_absolute)

            lines.append(f"| {r.marker_name} | {val_pct} | {ref_pct} | {val_abs} | {ref_abs} | {status} |")

        return "\n".join(lines)

    def _format_biochemistry(self, biochem: BiochemistryResult) -> str:
        """Format biochemistry results (UPC ratio)"""
        if not biochem:
            return ""

        lines = ["### Bioquímica - Rácio UPC (Biochemistry - UPC Ratio)"]

        if biochem.upc_ratio is not None:
            lines.append(f"- **Rácio UPC (UPC Ratio)**: {biochem.upc_ratio:.2f}")

            # Interpretation based on IRIS guidelines
            if biochem.upc_ratio < 0.2:
                lines.append("  - Interpretação: **Não proteinúrico** (Non-proteinuric)")
            elif biochem.upc_ratio <= 0.5:
                lines.append("  - Interpretação: **Suspeito de proteinúria** (Proteinuria suspect)")
            else:
                lines.append("  - Interpretação: **Proteinúrico** (Proteinuric)")

        if biochem.urine_total_protein is not None:
            lines.append(f"- **Proteínas Totais (Urina)**: {biochem.urine_total_protein} mg/dL")

        if biochem.urine_creatinine is not None:
            lines.append(f"- **Creatinina (Urina)**: {biochem.urine_creatinine} mg/dL")

        if biochem.iris_stage:
            lines.append(f"- **IRIS Stage**: {biochem.iris_stage}")

        return "\n".join(lines)

    def _format_urinalysis(self, urinalysis: UrinalysisResult) -> str:
        """Format urinalysis results"""
        if not urinalysis:
            return ""

        lines = ["### Urina Tipo II (Urinalysis)"]

        # General characteristics
        lines.append("\n**Caracteres Gerais (General Characteristics)**")
        if urinalysis.color:
            lines.append(f"- Cor (Color): {urinalysis.color}")
        if urinalysis.appearance:
            lines.append(f"- Aspecto (Appearance): {urinalysis.appearance}")

        # Biochemistry
        lines.append("\n**Bioquímica Urinária (Urinary Biochemistry)**")
        if urinalysis.specific_gravity:
            lines.append(f"- Densidade (Specific Gravity): {urinalysis.specific_gravity}")
        if urinalysis.ph:
            lines.append(f"- pH: {urinalysis.ph}")
        if urinalysis.proteins:
            lines.append(f"- Proteínas (Proteins): {urinalysis.proteins}")
        if urinalysis.glucose:
            lines.append(f"- Glucose: {urinalysis.glucose}")
        if urinalysis.bilirubin:
            lines.append(f"- Bilirrubina (Bilirubin): {urinalysis.bilirubin}")
        if urinalysis.ketones:
            lines.append(f"- Corpos Cetónicos (Ketones): {urinalysis.ketones}")

        # Microscopic sediment
        lines.append("\n**Exame Microscópico do Sedimento (Microscopic Sediment)**")
        if urinalysis.leukocytes:
            lines.append(f"- Leucócitos (Leukocytes): {urinalysis.leukocytes}")
        if urinalysis.erythrocytes:
            lines.append(f"- Eritrócitos (Erythrocytes): {urinalysis.erythrocytes}")
        if urinalysis.epithelial_cells:
            lines.append(f"- Células Epiteliais (Epithelial Cells): {urinalysis.epithelial_cells}")
        if urinalysis.crystals:
            lines.append(f"- Cristais (Crystals): {urinalysis.crystals}")
        if urinalysis.bacteria:
            lines.append(f"- Bactérias (Bacteria): {urinalysis.bacteria}")
        if urinalysis.casts:
            lines.append(f"- Cilindros (Casts): {urinalysis.casts}")

        return "\n".join(lines)

    def _build_prompt(
        self,
        animal: Animal,
        clinical_notes: List[ClinicalNote],
        sessions_data: Optional[List[Dict[str, Any]]] = None,
        report_type: str = "clinical_notes_only"
    ) -> str:
        """Build the complete prompt for diagnosis generation"""
        sections = []

        # Animal info
        sections.append(self._format_animal_info(animal))

        # Clinical notes
        notes_section = self._format_clinical_notes(clinical_notes)
        if notes_section:
            sections.append(notes_section)

        # Test data for comprehensive analysis
        if report_type == "comprehensive" and sessions_data:
            sections.append("\n## Resultados de Testes (Test Results)\n")

            for session_info in sessions_data:
                session = session_info.get("session")
                if session:
                    # Handle date formatting (string or date object)
                    date_str = "Data desconhecida"
                    if hasattr(session, 'test_date') and session.test_date:
                        if isinstance(session.test_date, str):
                            try:
                                from datetime import datetime
                                d = datetime.strptime(session.test_date, "%Y-%m-%d").date()
                                date_str = d.strftime("%d/%m/%Y")
                            except:
                                date_str = session.test_date
                        else:
                            date_str = session.test_date.strftime("%d/%m/%Y")
                    sections.append(f"#### Teste de {date_str}")

                # Proteinogram
                results = session_info.get("results", [])
                if results:
                    sections.append(self._format_proteinogram(results))

                # Biochemistry
                biochem = session_info.get("biochemistry")
                if biochem:
                    sections.append(self._format_biochemistry(biochem))

                # Urinalysis
                urinalysis = session_info.get("urinalysis")
                if urinalysis:
                    sections.append(self._format_urinalysis(urinalysis))

        # Request
        sections.append("\n---\n")
        if report_type == "comprehensive":
            sections.append(
                "Com base nos dados do paciente, notas clínicas e resultados dos testes acima, "
                "forneça uma análise de diagnóstico diferencial completa."
            )
        else:
            sections.append(
                "Com base nos dados do paciente e notas clínicas acima, "
                "forneça uma análise de diagnóstico diferencial."
            )

        return "\n\n".join(sections)

    def generate_diagnosis(
        self,
        animal: Animal,
        clinical_notes: List[ClinicalNote],
        sessions_data: Optional[List[Dict[str, Any]]] = None,
        report_type: str = "clinical_notes_only"
    ) -> Dict[str, str]:
        """
        Generate a differential diagnosis using Claude API.

        Args:
            animal: The animal patient data
            clinical_notes: List of clinical notes for the animal
            sessions_data: Optional list of test session data (for comprehensive analysis)
            report_type: "clinical_notes_only" or "comprehensive"

        Returns:
            Dictionary with keys: differential_diagnosis, recommendations, references, input_summary
        """
        client = self._get_client()

        # Build the prompt
        user_prompt = self._build_prompt(animal, clinical_notes, sessions_data, report_type)

        # Build input summary
        input_summary_parts = [f"Paciente: {animal.name} ({animal.species})"]
        input_summary_parts.append(f"Notas clínicas analisadas: {len(clinical_notes)}")
        if sessions_data:
            input_summary_parts.append(f"Sessões de teste: {len(sessions_data)}")
        input_summary = " | ".join(input_summary_parts)

        # Select appropriate system prompt based on report type
        if report_type == "comprehensive":
            system_prompt = VETERINARY_SYSTEM_PROMPT_COMPREHENSIVE
        else:
            system_prompt = VETERINARY_SYSTEM_PROMPT_NOTES_ONLY

        # Call Claude API
        message = client.messages.create(
            model=self.model,
            max_tokens=20000,
            system=system_prompt,
            messages=[
                {"role": "user", "content": user_prompt}
            ]
        )

        # Extract response
        response_text = message.content[0].text

        # Parse sections from response
        result = self._parse_response(response_text)
        result["input_summary"] = input_summary

        return result

    def _parse_response(self, response_text: str) -> Dict[str, str]:
        """Parse the structured response from Claude"""
        import re

        result = {
            "test_interpretation": "",
            "differential_diagnosis": "",
            "recommendations": "",
            "references": ""
        }

        # Use regex to find sections by their headers
        # Test interpretation section (for comprehensive analysis)
        interpretation_pattern = r'(## (?:Interpretação dos Resultados Laboratoriais|Laboratory Results Interpretation).*?)(?=## (?:Diagnósticos Diferenciais|Differential Diagnoses)|$)'

        # Differential diagnosis section
        diagnosis_pattern = r'(## (?:Diagnósticos Diferenciais|Differential Diagnoses).*?)(?=## (?:Recomendações|Recommendations)|## (?:Referências|References)|$)'

        # Recommendations section
        recommendations_pattern = r'(## (?:Recomendações|Recommendations).*?)(?=## (?:Referências|References)|$)'

        # References section
        references_pattern = r'(## (?:Referências|References).*?)$'

        # Find test interpretation section (may not exist for notes-only reports)
        match = re.search(interpretation_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            result["test_interpretation"] = match.group(1).strip()

        # Find differential diagnosis section
        match = re.search(diagnosis_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            result["differential_diagnosis"] = match.group(1).strip()

        # Find recommendations section
        match = re.search(recommendations_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            result["recommendations"] = match.group(1).strip()

        # Find references section
        match = re.search(references_pattern, response_text, re.DOTALL | re.IGNORECASE)
        if match:
            result["references"] = match.group(1).strip()

        # If parsing failed, put everything in differential_diagnosis
        if not result["differential_diagnosis"] and not result["test_interpretation"]:
            result["differential_diagnosis"] = response_text

        return result


# =============================================================================
# EXECUTIVE SUMMARY PROMPT
# =============================================================================

EXECUTIVE_SUMMARY_PROMPT = """You are a veterinary specialist creating an executive summary for a clinical case.
Based on the two AI analyses provided (from Claude and OpenAI), create a concise, clinician-friendly summary.

Structure your response with the following sections:

## Resumo das Alterações no Proteinograma (Proteinogram Changes Summary)
Provide a simple physiology explanation of the changes observed in the proteinogram:
- What proteins are altered (albumin, globulins, etc.)
- What these changes mean in simple physiological terms
- The pattern observed (e.g., hypoalbuminemia, hyperglobulinemia, etc.)

## Mecanismos Fisiopatológicos (Pathophysiological Mechanisms)
Explain in simple terms the general reasons why these changes could happen in the animal's body:
- Decreased production (liver disease, malnutrition)
- Increased loss (kidney disease, GI loss, hemorrhage)
- Malabsorption (intestinal disease)
- Increased consumption (inflammation, infection)
- Redistribution (third spacing)

## Correlação Clínico-Laboratorial (Clinical-Laboratory Correlation)
Correlate the laboratory findings with the clinical notes:
- How do the test results explain the clinical signs?
- What clinical findings support the laboratory patterns?
- Are there any discrepancies to consider?

## Diagnósticos Diferenciais Consolidados (Consolidated Differential Diagnoses)
Provide a ranked list of the most likely diagnoses, considering both AI reports:
1. Most likely diagnosis - brief justification
2. Second most likely - brief justification
3. Third most likely - brief justification
(continue as needed, typically 5-7 diagnoses)

## Plano de Investigação Sugerido (Suggested Investigation Plan)
Suggest a clear path forward for clinical investigation:
- Immediate priority tests
- Secondary tests if initial results are inconclusive
- Monitoring recommendations
- Any urgent actions needed

Keep the language accessible but professional. Write in Portuguese with English terms in parentheses where helpful.
Be concise but thorough - this is an executive summary for busy clinicians."""


# =============================================================================
# OPENAI DIAGNOSIS SERVICE
# =============================================================================

class OpenAIDiagnosisService:
    """Service for generating AI-powered differential diagnoses using OpenAI GPT-5 mini"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the OpenAI diagnosis service.

        Args:
            api_key: OpenAI API key. If not provided, will try to load from
                     OPENAI_API_KEY environment variable.
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.client = None
        self.model = "gpt-5-mini"

        if not OPENAI_AVAILABLE:
            raise ImportError(
                "The 'openai' package is not installed. "
                "Please install it with: pip install openai"
            )

    def _get_client(self):
        """Get or create the OpenAI client"""
        if not self.api_key or self.api_key == "your_openai_api_key_here":
            raise ValueError(
                "OPENAI_API_KEY not configured. "
                "Please set the environment variable or pass the API key directly."
            )

        if self.client is None:
            self.client = openai.OpenAI(api_key=self.api_key)

        return self.client

    def generate_diagnosis(
        self,
        animal: Animal,
        clinical_notes: List[ClinicalNote],
        sessions_data: Optional[List[Dict[str, Any]]] = None,
        report_type: str = "clinical_notes_only",
        prompt_builder  = None  # Use shared prompt builder from DiagnosisService
    ) -> Dict[str, str]:
        """
        Generate a differential diagnosis using OpenAI API.

        Args:
            animal: The animal patient data
            clinical_notes: List of clinical notes for the animal
            sessions_data: Optional list of test session data (for comprehensive analysis)
            report_type: "clinical_notes_only" or "comprehensive"
            prompt_builder: DiagnosisService instance to use for building prompts

        Returns:
            Dictionary with keys: differential_diagnosis, recommendations, references, input_summary
        """
        client = self._get_client()

        # Use the Claude service's prompt builder
        if prompt_builder is None:
            prompt_builder = DiagnosisService.__new__(DiagnosisService)
            prompt_builder.api_key = None
            prompt_builder.client = None
            prompt_builder.model = None

        # Build the prompt
        user_prompt = prompt_builder._build_prompt(animal, clinical_notes, sessions_data, report_type)

        # Build input summary
        input_summary_parts = [f"Paciente: {animal.name} ({animal.species})"]
        input_summary_parts.append(f"Notas clínicas analisadas: {len(clinical_notes)}")
        if sessions_data:
            input_summary_parts.append(f"Sessões de teste: {len(sessions_data)}")
        input_summary = " | ".join(input_summary_parts)

        # Select appropriate system prompt based on report type
        if report_type == "comprehensive":
            system_prompt = VETERINARY_SYSTEM_PROMPT_COMPREHENSIVE
        else:
            system_prompt = VETERINARY_SYSTEM_PROMPT_NOTES_ONLY

        # Call OpenAI API
        response = client.chat.completions.create(
            model=self.model,
            max_completion_tokens=20000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )

        # Extract response
        response_text = response.choices[0].message.content

        # Parse sections from response (reuse Claude's parser)
        result = prompt_builder._parse_response(response_text)
        result["input_summary"] = input_summary

        return result

    def generate_executive_summary(
        self,
        animal: Animal,
        clinical_notes: List[ClinicalNote],
        claude_result: Dict[str, str],
        openai_result: Dict[str, str],
        sessions_data: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generate an executive summary combining both AI analyses.

        Args:
            animal: The animal patient data
            clinical_notes: List of clinical notes
            claude_result: Results from Claude analysis
            openai_result: Results from OpenAI analysis
            sessions_data: Test session data

        Returns:
            Executive summary as markdown string
        """
        client = self._get_client()

        # Build the prompt with both analyses
        prompt_parts = []

        # Animal info
        prompt_parts.append(f"## Paciente: {animal.name}")
        prompt_parts.append(f"- Espécie: {animal.species}")
        if animal.breed:
            prompt_parts.append(f"- Raça: {animal.breed}")
        if animal.age_years:
            prompt_parts.append(f"- Idade: {int(animal.age_years)} anos")

        # Clinical notes summary
        prompt_parts.append("\n## Notas Clínicas:")
        for note in clinical_notes:
            prompt_parts.append(f"- {note.title or 'Nota'}: {note.content[:500]}...")

        # Claude analysis
        prompt_parts.append("\n## Análise do Claude (Anthropic):")
        if claude_result.get("test_interpretation"):
            prompt_parts.append(f"### Interpretação dos Testes:\n{claude_result['test_interpretation'][:2000]}")
        if claude_result.get("differential_diagnosis"):
            prompt_parts.append(f"### Diagnósticos Diferenciais:\n{claude_result['differential_diagnosis'][:2000]}")
        if claude_result.get("recommendations"):
            prompt_parts.append(f"### Recomendações:\n{claude_result['recommendations'][:1000]}")

        # OpenAI analysis
        prompt_parts.append("\n## Análise do OpenAI (GPT-5 mini):")
        if openai_result.get("test_interpretation"):
            prompt_parts.append(f"### Interpretação dos Testes:\n{openai_result['test_interpretation'][:2000]}")
        if openai_result.get("differential_diagnosis"):
            prompt_parts.append(f"### Diagnósticos Diferenciais:\n{openai_result['differential_diagnosis'][:2000]}")
        if openai_result.get("recommendations"):
            prompt_parts.append(f"### Recomendações:\n{openai_result['recommendations'][:1000]}")

        user_prompt = "\n".join(prompt_parts)
        user_prompt += "\n\n---\nCom base nas duas análises acima, crie um resumo executivo consolidado."

        # Call OpenAI API
        response = client.chat.completions.create(
            model=self.model,
            max_completion_tokens=8000,
            messages=[
                {"role": "system", "content": EXECUTIVE_SUMMARY_PROMPT},
                {"role": "user", "content": user_prompt}
            ]
        )

        return response.choices[0].message.content


def create_diagnosis_report(
    db: Database,
    animal_id: int,
    report_type: str = "clinical_notes_only",
    anthropic_api_key: Optional[str] = None,
    openai_api_key: Optional[str] = None
) -> DiagnosisReport:
    """
    High-level function to generate and save a diagnosis report using both Claude and OpenAI.

    Args:
        db: Database instance
        animal_id: ID of the animal
        report_type: "clinical_notes_only" or "comprehensive"
        anthropic_api_key: Optional Anthropic API key (uses env var if not provided)
        openai_api_key: Optional OpenAI API key (uses env var if not provided)

    Returns:
        The created DiagnosisReport
    """
    # Get animal
    animal = db.get_animal(animal_id)
    if not animal:
        raise ValueError(f"Animal with ID {animal_id} not found")

    # Get clinical notes
    clinical_notes = db.get_clinical_notes_for_animal(animal_id)
    if not clinical_notes:
        raise ValueError("No clinical notes found for this animal. Please add clinical notes first.")

    # Get test data for comprehensive analysis
    sessions_data = None
    if report_type == "comprehensive":
        sessions = db.get_sessions_for_animal(animal_id)
        sessions_data = []
        for session in sessions:
            session_info = {
                "session": session,
                "results": db.get_results_for_session(session.id),
                "biochemistry": db.get_biochemistry_for_session(session.id),
                "urinalysis": db.get_urinalysis_for_session(session.id)
            }
            sessions_data.append(session_info)

    # Initialize results
    claude_result = {
        "test_interpretation": "",
        "differential_diagnosis": "",
        "recommendations": "",
        "references": "",
        "input_summary": ""
    }
    openai_result = {
        "test_interpretation": "",
        "differential_diagnosis": "",
        "recommendations": "",
        "references": ""
    }
    claude_model = "claude-sonnet-4-20250514"
    openai_model = "gpt-5-mini"

    # Generate diagnosis with Claude (Anthropic)
    try:
        claude_service = DiagnosisService(api_key=anthropic_api_key)
        claude_result = claude_service.generate_diagnosis(
            animal=animal,
            clinical_notes=clinical_notes,
            sessions_data=sessions_data,
            report_type=report_type
        )
        claude_model = claude_service.model
    except Exception as e:
        print(f"Claude API error: {e}")
        claude_result["differential_diagnosis"] = f"Erro ao gerar diagnóstico com Claude: {str(e)}"

    # Generate diagnosis with OpenAI (GPT-5 mini)
    openai_service = None
    try:
        openai_service = OpenAIDiagnosisService(api_key=openai_api_key)
        # Create a prompt builder from Claude service for shared methods
        prompt_builder = DiagnosisService.__new__(DiagnosisService)
        prompt_builder.api_key = None
        prompt_builder.client = None
        prompt_builder.model = None

        openai_result = openai_service.generate_diagnosis(
            animal=animal,
            clinical_notes=clinical_notes,
            sessions_data=sessions_data,
            report_type=report_type,
            prompt_builder=prompt_builder
        )
        openai_model = openai_service.model
    except Exception as e:
        print(f"OpenAI API error: {e}")
        openai_result["differential_diagnosis"] = f"Erro ao gerar diagnóstico com OpenAI: {str(e)}"

    # Generate Executive Summary using OpenAI (combining both reports)
    executive_summary = ""
    if openai_service and report_type == "comprehensive":
        try:
            executive_summary = openai_service.generate_executive_summary(
                animal=animal,
                clinical_notes=clinical_notes,
                claude_result=claude_result,
                openai_result=openai_result,
                sessions_data=sessions_data
            )
        except Exception as e:
            print(f"Executive summary generation error: {e}")
            executive_summary = f"Erro ao gerar resumo executivo: {str(e)}"

    # Create and save report with both results
    report = DiagnosisReport(
        animal_id=animal_id,
        report_date=date.today(),
        report_type=report_type,
        input_summary=claude_result.get("input_summary", ""),
        executive_summary=executive_summary,
        # Claude results
        test_interpretation=claude_result.get("test_interpretation", ""),
        differential_diagnosis=claude_result.get("differential_diagnosis", ""),
        recommendations=claude_result.get("recommendations", ""),
        references=claude_result.get("references", ""),
        model_used=claude_model,
        # OpenAI results
        openai_test_interpretation=openai_result.get("test_interpretation", ""),
        openai_differential_diagnosis=openai_result.get("differential_diagnosis", ""),
        openai_recommendations=openai_result.get("recommendations", ""),
        openai_references=openai_result.get("references", ""),
        openai_model_used=openai_model
    )

    report.id = db.create_diagnosis_report(report)
    return report
