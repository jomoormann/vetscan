import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import fitz

os.environ["ENVIRONMENT"] = "development"
os.environ["AUTH_SECRET_KEY"] = "test-secret-key"

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from email_importer import EmailImporter
from app import VetProteinService
from models import Animal, BiochemistryResult, TestSession as DomainTestSession, UrinalysisResult
from pdf_parser import DNAtechParser, VedisCytologyParser, detect_report_type, parse_lab_report
from pdf_parser import _extract_ordering_vet_from_text
from pdf_parser import ParsedReport
from pdf_validator import PDFValidator


SAMPLE_DIR = Path(__file__).resolve().parents[2] / "new reports"


class NewReportFormatTests(unittest.TestCase):
    def setUp(self):
        self.validator = PDFValidator()

    def test_ordering_vet_extractor_does_not_cross_blank_label_lines(self):
        self.assertIsNone(_extract_ordering_vet_from_text("Veterinário/a:\nTurbididade: transparente"))
        self.assertIsNone(_extract_ordering_vet_from_text("Veterinário:\nshrinkage and damaged cells). Few epithelial cells without atypia and"))
        self.assertIsNone(_extract_ordering_vet_from_text("Nome do Veterinário: Dr(a)."))
        self.assertIsNone(_extract_ordering_vet_from_text("Veterinário/a: Desconhecido Unknown"))
        self.assertEqual(_extract_ordering_vet_from_text("Attending Vet: Sofia Castro"), "Sofia Castro")
        self.assertEqual(_extract_ordering_vet_from_text("Nome do Veterinário: Dra. Maria Santos"), "Maria Santos")
        self.assertEqual(_extract_ordering_vet_from_text("Veterinário/a: Dr. João Costa"), "João Costa")
        parser = VedisCytologyParser()
        self.assertIsNone(parser._next_first_column_after_label("Veterinário/a\n\nTurbididade: transparente", "Veterinário/a"))
        self.assertIsNone(parser._next_first_column_after_label("Attending Vet\n\nDESCRIÇÃO MICROSCÓPICA", "Attending Vet"))
        self.assertIsNone(parser._next_first_column_after_label("Attending Vet\n- Size: 6.0x2.2x0.5 cm", "Attending Vet"))
        self.assertEqual(parser._next_first_column_after_label("Veterinário/a\nDra. Carina Marta", "Veterinário/a"), "Dra. Carina Marta")

    def test_dnatech_urine_biochemistry_imports_without_proteinogram(self):
        path = SAMPLE_DIR / "bolt22799_1589395.pdf"

        validation = self.validator.validate(str(path))
        self.assertTrue(validation.is_valid, validation.message)
        self.assertEqual(validation.details["report_type"], "dnatech_urine_biochemistry")
        self.assertEqual(validation.report_number, "22799/1589395")

        parsed = parse_lab_report(str(path))
        self.assertEqual(parsed.session.report_type, "biochemistry")
        self.assertEqual(parsed.session.panel_name, "urine_protein_creatinine_ratio")
        self.assertEqual(parsed.animal.name, "Lua")
        self.assertIsNone(parsed.animal.microchip)
        self.assertEqual(parsed.animal_identifiers, [])
        self.assertEqual(parsed.animal.age_years, 15.0)
        self.assertEqual(parsed.animal.sex, "F")
        self.assertEqual(len(parsed.results), 0)
        self.assertIsNotNone(parsed.biochemistry)
        self.assertEqual(parsed.biochemistry.upc_ratio, 0.22)
        self.assertEqual(parsed.biochemistry.upc_status, "suspeito")

    def test_vedis_histology_imports_pathology_finding(self):
        path = SAMPLE_DIR / "26004748 - Kika - Ana Martins.pdf"

        validation = self.validator.validate(str(path))
        self.assertTrue(validation.is_valid, validation.message)
        self.assertEqual(validation.details["report_type"], "vedis_histology")
        self.assertEqual(validation.report_number, "26004748")

        parsed = parse_lab_report(str(path))
        self.assertEqual(parsed.session.report_type, "histology")
        self.assertEqual(parsed.session.report_number, "VEDIS/26004748")
        self.assertEqual(parsed.animal.name, "Kika")
        self.assertEqual(parsed.animal.sex, "F")
        self.assertIsNone(parsed.animal.responsible_vet)
        self.assertEqual(parsed.session.ordering_vet, "Carina Marta")
        self.assertEqual(len(parsed.pathology_findings), 2)
        finding = parsed.pathology_findings[0]
        self.assertEqual(finding.section_type, "histology")
        self.assertEqual(finding.title, "Amostra Cirúrgica · Baço")
        self.assertIn("Hematoma esplénico", finding.diagnosis)
        self.assertIsNone(finding.microscopic_description)
        comment = parsed.pathology_findings[1]
        self.assertEqual(comment.section_type, "general_comment")
        self.assertIn("Nas secções examinadas", comment.comment)

    def test_vedis_cytology_imports_portuguese_translation_fields(self):
        path = SAMPLE_DIR / "26000620 - Finn - Maria Barreiros.pdf"

        parsed = parse_lab_report(str(path))

        diagnoses = [finding for finding in parsed.pathology_findings if finding.diagnosis]
        self.assertEqual(len(diagnoses), 2)
        self.assertEqual(diagnoses[0].title, "A- Citologia de rim esquerdo")
        self.assertIn("Não diagnóstico", diagnoses[0].diagnosis)
        self.assertEqual(diagnoses[1].title, "B- Citologia de retroperitoneu")
        self.assertIn("linfoma de grandes células", diagnoses[1].diagnosis)
        self.assertIn("amostras examinadas", parsed.pathology_findings[-1].comment)

    def test_genevet_urinalysis_imports_upc_and_urine_values(self):
        path = SAMPLE_DIR / "Resultado_53283_Fidel_TiagoGomes.pdf"

        validation = self.validator.validate(str(path))
        self.assertTrue(validation.is_valid, validation.message)
        self.assertEqual(validation.details["report_type"], "genevet_urinalysis")
        self.assertEqual(validation.report_number, "53283")

        parsed = parse_lab_report(str(path))
        self.assertEqual(parsed.session.report_type, "urinalysis")
        self.assertEqual(parsed.session.report_number, "GENEVET/53283")
        self.assertEqual(parsed.session.test_date, date(2025, 10, 3))
        self.assertEqual(parsed.animal.name, "Fidel")
        self.assertIsNone(parsed.animal.responsible_vet)
        self.assertEqual(parsed.session.ordering_vet, "Sofia Castro")
        self.assertIsNotNone(parsed.biochemistry)
        self.assertEqual(parsed.biochemistry.upc_ratio, 0.11)
        self.assertEqual(parsed.biochemistry.upc_status, "nao_proteinurico")
        self.assertIsNotNone(parsed.urinalysis)
        self.assertEqual(parsed.urinalysis.specific_gravity, 1.048)
        self.assertEqual(parsed.urinalysis.proteins_value, 30.0)

    def test_dnatech_no_cliente_is_not_used_as_microchip_or_exact_identifier(self):
        parser = DNAtechParser()
        text = """
        Folha de Trabalho Nº 26561/1598712
        Data 11/05/2026
        Dados do Animal
        Animal Camões
        Espécie Canideo
        Raça Westie
        Microchip No Cliente: V890
        Idade 14 A (M) Proprietário Maria C
        Amostra Lamina
        CITOLOGIA
        CITOLOGIA AURICULAR
        """

        animal = parser._parse_animal_data(text)

        self.assertIsNone(animal.microchip)
        self.assertEqual(parser._parse_animal_identifiers(text), [])

    def test_detects_portuguese_vedis_and_dnatech_cytology(self):
        self.assertEqual(
            detect_report_type("ID exame\n26005750\nPACIENTE RELATÓRIO CITOLOGIA\nLoki INFORMAÇÃO CLÍNICA"),
            "vedis_cytology",
        )
        self.assertEqual(
            detect_report_type("Folha de Trabalho Nº 26561/1598712\nDados do Animal\nCITOLOGIA\nCITOLOGIA AURICULAR"),
            "dnatech_cytology",
        )
        self.assertEqual(
            detect_report_type("Folha de Trabalho Nº 31371/1611462\nDados do Animal\nCITOLOGIA GERAL\nRelatório citológico"),
            "dnatech_cytology",
        )

    def test_parses_dnatech_narrative_cytology_as_pathology_findings(self):
        parser = DNAtechParser()
        text = (
            "Folha de Trabalho Nº 31371/1611462\n"
            "Dados do Animal\n"
            "Animal Amendoim\n"
            "Amostra Lamina | Pelos\n"
            "CITOLOGIA GERAL\n"
            "Relatório citológico\n"
            "Lâminas recebidas:\n"
            "Três lâminas não coradas com discreto material.\n"
            "Tipo de Amostra:\n"
            "Citologia geral. Sem mais informação. Esfregaço directo.\n"
            "Celularidade:\n"
            "Ao exame microscópico das lâminas recebida foi observada discreta hemodiluição.\n"
            "Conclusão:\n"
            "A imagem microscópica acima descrita não é suficiente para uma aproximação diagnóstica fiável.\n"
            "O Analista\n"
            "CITOLOGIA DERMATOLOGICA\n"
            "Relatório citológico\n"
            "Lâminas recebidas:\n"
            "Duas lâminas não coradas com discreto material (uma das quais com fita-cola).\n"
            "Tipo de Amostra:\n"
            "Citologia dermatológica. Sem informação. Esfregaço direto.\n"
            "Celularidade:\n"
            "Ao exame microscópico das lâminas recebidas verifica-se a presença de discreta componente inflamatória.\n"
            "Conclusão:\n"
            "A imagem microscópica acima descrita é compatível com inflamação supurativa séptica.\n"
            "O Analista\n"
        )

        self.assertEqual(parser._infer_report_classification(text), ("cytology", "cytology"))
        self.assertEqual(parser._parse_cytology_measurements(text), [])
        findings = parser._parse_cytology_findings(text)

        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].title, "Citologia geral")
        self.assertEqual(findings[0].specimen_label, "Três lâminas não coradas com discreto material.")
        self.assertIn("discreta hemodiluição", findings[0].microscopic_description)
        self.assertIn("não é suficiente", findings[0].diagnosis)
        self.assertEqual(findings[1].title, "Citologia dermatológica")
        self.assertIn("fita-cola", findings[1].specimen_label)
        self.assertIn("inflamação supurativa", findings[1].diagnosis)

    def test_detects_dnatech_general_lab_reports(self):
        text = """
        Folha de Trabalho Nº 28001/1602209
        Dados do Animal
        Animal Dartacão
        IMUNOLOGIA
        ANTICORPOS ANTI-LEISHMANIA Positivo (Fraco)
        """

        self.assertEqual(detect_report_type(text), "dnatech_lab_report")

    def test_parses_dnatech_immunology_measurements(self):
        parser = DNAtechParser()
        text = """
        IMUNOLOGIA
        ANTICORPOS ANTI-LEISHMANIA Positivo (Fraco)
        Imunofluorescência Indireta (IFI)
        Titulação 1/80 Positivo
        Titulação 1/160 Positivo (Fraco)
        Titulação 1/240 Negativo
        """

        measurements = parser._parse_generic_measurements(text, "immunology")

        codes = [measurement.measurement_code for measurement in measurements]
        # No duplicates, and each titration is grouped under its antibody subtitle.
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(measurements[0].measurement_code, "anticorpos_anti_leishmania")
        self.assertEqual(measurements[0].value_text, "Positivo (Fraco)")
        self.assertEqual(measurements[0].flag, "high")
        self.assertEqual(
            measurements[1].measurement_code,
            "anticorpos_anti_leishmania_titulacao_1_80",
        )
        self.assertEqual(measurements[1].value_text, "Positivo")
        self.assertEqual(measurements[-1].measurement_code,
                         "anticorpos_anti_leishmania_titulacao_1_240")
        self.assertEqual(measurements[-1].flag, "normal")

    def test_immunology_titers_never_mislabelled_as_leishmania(self):
        """Regression: a report about other diseases must not invent leishmania."""
        parser = DNAtechParser()
        text = """
        IMUNOLOGIA
        ANTICORPOS ANTI-RICKETSIA CONORII Positivo Criterio de valorização >1/40
        Imunofluorescência Indireta (IFI)
        Titulação 1/40 Positivo
        Titulação 1/80 Positivo
        ANTICORPOS ANTI-EHRLICHIA CANIS Negativo Criterio de valorização >1/50
        Imunofluorescência Indireta (IFI)
        Titulação 1/50 Negativo
        """

        measurements = parser._parse_generic_measurements(text, "immunology")
        codes = [m.measurement_code for m in measurements]

        # No invented leishmania analysis anywhere.
        self.assertFalse([c for c in codes if "leishmania" in c])
        # No duplicated rows.
        self.assertEqual(len(codes), len(set(codes)))
        # Each disease's titres are namespaced to that disease.
        self.assertIn("anticorpos_anti_ricketsia_conorii", codes)
        self.assertIn("anticorpos_anti_ricketsia_conorii_titulacao_1_40", codes)
        self.assertIn("anticorpos_anti_ehrlichia_canis_titulacao_1_50", codes)
        # The valorisation criterion is captured as the reference, not a unit.
        ricketsia = next(m for m in measurements
                         if m.measurement_code == "anticorpos_anti_ricketsia_conorii")
        self.assertEqual(ricketsia.reference_text, ">1/40")
        self.assertIsNone(ricketsia.unit)

    def test_parses_dnatech_culture_and_coprology_measurements(self):
        parser = DNAtechParser()
        culture_text = """
        MICROBIOLOGIA
        COPROCULTURA
        EXAME BACTERIOLÓGICO CULTURAL
        Microrganismo isolado Negativo
        """
        copro_text = """
        COPROLOGIA
        CITOLOGIA FLUTUAÇÃO
        Pesquisa de parasitas fecais
        Resultado: Negativo.
        Não foram observados ovos, ooquistos nem parasitas nas fezes pela técnica da flutuação fecal.
        Nota: Confirmar com antigénio se houver suspeita clínica.
        """

        culture = parser._parse_generic_measurements(culture_text, "fecal_culture")
        copro = parser._parse_generic_measurements(copro_text, "fecal_parasitology")

        self.assertEqual(culture[0].panel_name, "fecal_culture")
        self.assertEqual(culture[0].measurement_code, "microorganism_isolated")
        self.assertEqual(culture[0].value_text, "Negativo")
        self.assertEqual(copro[0].measurement_code, "fecal_parasites")
        self.assertEqual(copro[0].value_text, "Negativo")
        self.assertEqual(copro[1].measurement_code, "fecal_parasitology_observation")

    def test_dnatech_fallback_rejects_species_headings_and_prose_fragments(self):
        parser = DNAtechParser()
        text = """
        Canideos <15
        Cachorros <17
        tais como o ligeiro aumento da basofilia citoplasmática e discreta vacuolização
        sanguíneo. O resultado negativo não
        Hemoglobina 14,1 g/dL 8,0 -15,0
        pH 6,5 5,0-7,0
        """

        measurements = parser._parse_generic_measurements(text, "hematology")
        by_code = {measurement.measurement_code: measurement for measurement in measurements}

        self.assertNotIn("canideos", by_code)
        self.assertNotIn("cachorros", by_code)
        self.assertNotIn("tais_como_o_ligeiro_aumento_da_basofilia_citoplasmatica_e", by_code)
        self.assertNotIn("sanguineo_o_resultado", by_code)
        self.assertEqual(by_code["hemoglobina"].value_numeric, 14.1)
        self.assertEqual(by_code["ph"].value_text, "6,5")

    def test_dnatech_auricular_cytology_does_not_duplicate_metric_aliases(self):
        parser = DNAtechParser()
        text = (
            "CITOLOGIA\n"
            "CITOLOGIA AURICULAR\n"
            "Células epiteliais pavimentosas queratinizadas Presentes\n"
            "Bactérias < 2 /campo 100x\n"
            "Bactérias < 2 /campo 100x\n"
            "2+ Bactérias, leveduras ou células inflamatórias presentes em\n"
            "4+ Massiva quantidade de bactérias, leveduras ou células inflamatórias presentes e\n"
            "Malassezia sp. < 5 /campo 100x\n"
            "Ácaros Ausentes\n"
            "Neutrófilos Presentes Ausentes\n"
        )

        specific = parser._parse_cytology_measurements(text)
        generic = parser._parse_generic_measurements(
            text,
            "auricular_cytology",
            {measurement.measurement_code for measurement in specific},
        )
        measurements = specific + generic
        codes = [measurement.measurement_code for measurement in measurements]

        self.assertIn("epithelial_cells", codes)
        self.assertIn("bacteria", codes)
        self.assertNotIn("celulas_epiteliais_pavimentosas_queratinizadas", codes)
        self.assertNotIn("bacterias", codes)
        self.assertNotIn("2_bacterias_leveduras_ou_celulas_inflamatorias", codes)
        self.assertNotIn("4_massiva_quantidade_de_bacterias_leveduras_ou_celulas_inflamatorias", codes)
        self.assertEqual(codes.count("bacteria"), 1)

    def test_parses_new_dnatech_single_value_measurements(self):
        parser = DNAtechParser()
        text = """
        Folha de Trabalho Nº 31370/1611430
        Dados do Animal
        Animal Bobby
        Espécie Canideo
        ENDOCRINOLOGIA
        T4 TOTAL 2,4 ug/dL 1,0 - 4,0
        CORTISOL 6,1 (A) ug/dL < 5,0
        """

        measurements = parser._parse_generic_measurements(text, "endocrinology")

        by_code = {measurement.measurement_code: measurement for measurement in measurements}
        self.assertEqual(by_code["t4_total"].value_numeric, 2.4)
        self.assertEqual(by_code["t4_total"].reference_text, "1,0 - 4,0")
        self.assertEqual(by_code["cortisol"].value_numeric, 6.1)
        self.assertEqual(by_code["cortisol"].flag, "high")

    def test_parses_real_dnatech_fructosamine_report(self):
        path = SAMPLE_DIR / "bolt31370_1611430.pdf"

        validation = self.validator.validate(str(path))
        self.assertTrue(validation.is_valid, validation.message)
        self.assertEqual(validation.details["report_type"], "dnatech_lab_report")
        self.assertEqual(validation.report_number, "31370/1611430")

        parsed = parse_lab_report(str(path))
        self.assertEqual(parsed.session.report_type, "biochemistry")
        self.assertEqual(parsed.session.panel_name, "biochemistry")
        self.assertEqual(parsed.session.report_number, "31370/1611430")
        self.assertEqual(parsed.animal.name, "Simba")
        self.assertEqual(parsed.animal.species, "Canídeo")
        self.assertEqual(parsed.animal.breed, "Labrador")
        self.assertEqual(parsed.animal.owner_name, "Filipe Baptista")

        by_code = {measurement.measurement_code: measurement for measurement in parsed.measurements}
        self.assertIn("frutosamina", by_code)
        frutosamina = by_code["frutosamina"]
        self.assertEqual(frutosamina.value_numeric, 376.3)
        self.assertEqual(frutosamina.value_text, "376,3 (A)")
        self.assertEqual(frutosamina.unit, "µmol/L")
        self.assertEqual(frutosamina.reference_text, "< 340")
        self.assertEqual(frutosamina.flag, "high")
        self.assertNotIn("telefone", by_code)
        self.assertNotIn("dnatech_lda_estrada_do_paco_do_lumiar", by_code)
        self.assertNotIn("canideos", by_code)
        self.assertNotIn("cachorros", by_code)

    def test_dnatech_generic_measurements_filter_noise_and_keep_urine_references(self):
        path = SAMPLE_DIR / "bolt58630_1500951.pdf"

        parsed = parse_lab_report(str(path))
        by_code = {measurement.measurement_code: measurement for measurement in parsed.measurements}

        self.assertNotIn("telefone", by_code)
        self.assertNotIn("documento_procesado_electronicamente_e_deialab_slice_pagina", by_code)
        self.assertNotIn("dnatech_lda_estrada_do_paco_do_lumiar", by_code)
        self.assertNotIn("albumina", by_code)
        self.assertIn("ph", by_code)
        self.assertEqual(by_code["ph"].value_text, "8,0")
        self.assertEqual(by_code["ph"].reference_text, "5,0-7,0")
        self.assertIn("cel_epiteliais", by_code)
        self.assertEqual(by_code["cel_epiteliais"].value_text, "Raras")
        self.assertEqual(by_code["cel_epiteliais"].reference_text, "Raras")
        self.assertIn("cristais", by_code)
        self.assertEqual(by_code["cristais"].value_text, "Ausentes")

    def test_classifies_dnatech_same_work_order_panels(self):
        parser = DNAtechParser()

        self.assertEqual(
            parser._infer_report_classification("MICROBIOLOGIA\nCOPROCULTURA\nAguarda Resultado"),
            ("microbiology", "fecal_culture"),
        )
        self.assertEqual(
            parser._infer_report_classification("COPROLOGIA\nCITOLOGIA FLUTUAÇÃO\nResultado: Negativo."),
            ("coprology", "fecal_parasitology"),
        )
        self.assertEqual(
            parser._infer_report_classification("IMUNOLOGIA\nANTICORPOS ANTI-LEISHMANIA Positivo"),
            ("immunology", "immunology"),
        )

    def test_repeated_work_order_update_rules(self):
        service = VetProteinService()
        existing_upc = DomainTestSession(
            id=1,
            report_number="26931/1599678",
            source_system="dnatech",
            external_report_id="26931/1599678",
            report_type="biochemistry",
            panel_name="urine_protein_creatinine_ratio",
        )
        updated_urine = ParsedReport(
            animal=Animal(name="Gata"),
            session=DomainTestSession(
                report_number="26931/1599678",
                source_system="dnatech",
                external_report_id="26931/1599678",
                report_type="urinalysis",
                panel_name="urinalysis_upc",
            ),
            biochemistry=BiochemistryResult(upc_ratio=1.5),
            urinalysis=UrinalysisResult(specific_gravity=1.011),
        )
        fecal_culture = ParsedReport(
            animal=Animal(name="Zuky"),
            session=DomainTestSession(
                report_number="27563/1601295",
                source_system="dnatech",
                external_report_id="27563/1601295",
                report_type="microbiology",
                panel_name="fecal_culture",
            ),
        )

        self.assertTrue(service._is_compatible_update(existing_upc, updated_urine))
        self.assertFalse(service._is_compatible_update(existing_upc, fecal_culture))

    def test_near_exact_single_candidate_auto_matches(self):
        tempdir = Path(tempfile.mkdtemp(prefix="vetscan-near-match-"))
        try:
            with VetProteinService(db_path=str(tempdir / "test.db"), uploads_dir=str(tempdir / "uploads")) as service:
                animal_id = service.db.create_animal(Animal(name="Bobby", species="Canídeo"))
                decision = service.db.analyze_animal_match(Animal(name="Boby", species="Canídeo"))

                self.assertEqual(decision.action, "match_existing")
                self.assertEqual(decision.animal_id, animal_id)
                self.assertEqual(decision.reason, "near_exact_name_match")
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_parses_portuguese_vedis_cytology_patient_text(self):
        parser = VedisCytologyParser()
        text = """
        ID exame
        26005749
        2 1 PACIENTE RELATÓRIO CITOLOGIA
        9 3 Luna INFORMAÇÃO CLÍNICA
        Tutor: Helder Joaquim O nódulo cresceu ao longo de um ano.
        Espécie: Feline
        Raça: Common European
        Sexo: Female neutered
        DIAGNÓSTICO
        Lesão epitelial quística benigna de conteúdo queratínico.
        DN/Idade: 7 ANOS
        PRODUTO ENVIADO
        Punção não aspirativa.
        DESCRIÇÃO MICROSCÓPICA
        As lâminas apresentam conteúdo queratínico.
        """

        patient = parser._parse_patient(text)

        self.assertEqual(patient["name"], "Luna")
        self.assertEqual(patient["owner"], "Helder Joaquim")
        self.assertEqual(patient["species"], "Feline")
        self.assertEqual(patient["breed"], "Common European")
        self.assertEqual(patient["sex"], "F")
        self.assertTrue(patient["neutered"])
        self.assertEqual(patient["age_years"], 7.0)

    def test_email_importer_processes_new_report_formats(self):
        tempdir = Path(tempfile.mkdtemp(prefix="vetscan-new-format-import-"))
        try:
            uploads = tempdir / "uploads"
            uploads.mkdir()
            importer = EmailImporter(db_path=str(tempdir / "test.db"), uploads_dir=str(uploads))
            for filename in [
                "bolt22799_1589395.pdf",
                "26004748 - Kika - Ana Martins.pdf",
                "Resultado_53283_Fidel_TiagoGomes.pdf",
            ]:
                path = SAMPLE_DIR / filename
                result = importer.process_pdf(
                    filename,
                    path.read_bytes(),
                    "test-uid",
                    "new reports",
                    "test@example.com",
                )
                self.assertTrue(result.success, result.error_message)
                self.assertIn(result.validation_result, ("valid", "queued_manual_assignment"))
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    def test_email_importer_queues_clean_unknown_report_pdf(self):
        tempdir = Path(tempfile.mkdtemp(prefix="vetscan-unknown-import-"))
        try:
            uploads = tempdir / "uploads"
            uploads.mkdir()
            pdf_path = tempdir / "unknown_vedis_report.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text(
                (72, 72),
                "Exam ID 99999\nPATIENT\nRex\nOwner: Ana Silva\nSpecie: Canine\n"
                "GENERAL REPORT\nThis report body is not supported yet.",
            )
            doc.save(str(pdf_path))
            doc.close()

            importer = EmailImporter(db_path=str(tempdir / "test.db"), uploads_dir=str(uploads))
            result = importer.process_pdf(
                pdf_path.name,
                pdf_path.read_bytes(),
                "unknown-uid",
                "unknown report",
                "reports@example.com",
            )

            self.assertTrue(result.success, result.error_message)
            self.assertEqual(result.validation_result, "queued_manual_assignment")
            self.assertIsNotNone(result.unassigned_report_id)
            self.assertEqual(sorted(path.name for path in uploads.iterdir()), [pdf_path.name])
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
