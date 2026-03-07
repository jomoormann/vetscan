"""
Database Schema for VetScan

Contains the SQLite schema definition as a SQL constant.
"""

SCHEMA_SQL = """
-- Animals table
CREATE TABLE IF NOT EXISTS animals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    species TEXT DEFAULT 'Canídeo',
    breed TEXT,
    microchip TEXT UNIQUE,
    owner_name TEXT,
    age_years REAL,
    age_months INTEGER,
    sex TEXT DEFAULT 'U',
    weight_kg REAL,
    neutered INTEGER,
    medical_history TEXT,
    notes TEXT,
    responsible_vet TEXT,
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
    source_system TEXT DEFAULT 'dnatech',
    report_type TEXT DEFAULT 'dnatech_proteinogram',
    external_report_id TEXT,
    report_source TEXT,
    reported_at TIMESTAMP,
    received_at TIMESTAMP,
    clinic_name TEXT,
    panel_name TEXT,
    raw_metadata_json TEXT,
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
    author_user_id INTEGER,
    updated_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE,
    FOREIGN KEY (author_user_id) REFERENCES users(id) ON DELETE SET NULL,
    FOREIGN KEY (updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
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
CREATE INDEX IF NOT EXISTS idx_test_sessions_external_id ON test_sessions(source_system, external_report_id);
CREATE INDEX IF NOT EXISTS idx_protein_results_session_id ON protein_results(session_id);
CREATE INDEX IF NOT EXISTS idx_protein_results_marker ON protein_results(marker_name);
CREATE INDEX IF NOT EXISTS idx_symptoms_animal_id ON symptoms(animal_id);
CREATE INDEX IF NOT EXISTS idx_observations_animal_id ON observations(animal_id);
CREATE INDEX IF NOT EXISTS idx_clinical_notes_animal_id ON clinical_notes(animal_id);

-- Responsible vet ownership / handover history
CREATE TABLE IF NOT EXISTS animal_vet_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    vet_name TEXT NOT NULL,
    start_date DATE DEFAULT CURRENT_DATE,
    end_date DATE,
    change_reason TEXT,
    changed_by_user_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_animal_vet_assignments_animal ON animal_vet_assignments(animal_id, start_date DESC);
CREATE INDEX IF NOT EXISTS idx_animal_vet_assignments_current ON animal_vet_assignments(animal_id, end_date);

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

-- External animal identifiers
CREATE TABLE IF NOT EXISTS animal_identifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL,
    source_system TEXT NOT NULL,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (animal_id) REFERENCES animals(id) ON DELETE CASCADE,
    UNIQUE(source_system, identifier_type, identifier_value)
);
CREATE INDEX IF NOT EXISTS idx_animal_identifiers_animal ON animal_identifiers(animal_id);

-- Generic measurements for analyzer and other structured reports
CREATE TABLE IF NOT EXISTS session_measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    panel_name TEXT,
    measurement_code TEXT NOT NULL,
    measurement_name TEXT NOT NULL,
    value_numeric REAL,
    value_text TEXT,
    unit TEXT,
    reference_min REAL,
    reference_max REAL,
    reference_text TEXT,
    flag TEXT DEFAULT 'normal',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_measurements_session ON session_measurements(session_id);

-- Narrative pathology findings and specimen-level sections
CREATE TABLE IF NOT EXISTS pathology_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    section_type TEXT NOT NULL,
    specimen_label TEXT,
    title TEXT,
    sample_site TEXT,
    sample_method TEXT,
    clinical_history TEXT,
    microscopic_description TEXT,
    diagnosis TEXT,
    comment TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pathology_findings_session ON pathology_findings(session_id);

-- Extracted report assets such as cytology images
CREATE TABLE IF NOT EXISTS session_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    asset_type TEXT NOT NULL,
    label TEXT,
    file_path TEXT NOT NULL,
    page_number INTEGER,
    sort_order INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_session_assets_session ON session_assets(session_id);

-- Reports that need manual assignment before a session is created
CREATE TABLE IF NOT EXISTS unassigned_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    pdf_path TEXT NOT NULL,
    source_system TEXT,
    report_type TEXT,
    report_number TEXT,
    external_report_id TEXT,
    report_source TEXT,
    animal_name TEXT,
    species TEXT,
    owner_name TEXT,
    clinic_name TEXT,
    report_date DATE,
    panel_name TEXT,
    match_reason TEXT,
    parsed_summary_json TEXT,
    candidate_matches_json TEXT,
    status TEXT DEFAULT 'pending',
    assigned_animal_id INTEGER,
    session_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    assigned_at TIMESTAMP,
    FOREIGN KEY (assigned_animal_id) REFERENCES animals(id) ON DELETE SET NULL,
    FOREIGN KEY (session_id) REFERENCES test_sessions(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_unassigned_reports_status ON unassigned_reports(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_unassigned_reports_external ON unassigned_reports(source_system, external_report_id);
CREATE INDEX IF NOT EXISTS idx_unassigned_reports_number ON unassigned_reports(report_number);

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
    -- OpenAI (GPT) results
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

-- Users table (multi-user authentication)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    email_normalized TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    is_active INTEGER DEFAULT 1,
    is_approved INTEGER DEFAULT 0,
    is_superuser INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP,
    approved_at TIMESTAMP,
    approved_by_user_id INTEGER,
    FOREIGN KEY (approved_by_user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_email_normalized ON users(email_normalized);

-- Password reset tokens
CREATE TABLE IF NOT EXISTS password_reset_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reset_tokens_hash ON password_reset_tokens(token_hash);

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
