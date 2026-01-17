# 🐕 Veterinary Protein Analysis Application

A Python application for veterinary clinics to manage and analyze blood test results from protein electrophoresis, urinalysis, and kidney markers. Designed to work with DNAtech lab reports (Portuguese format).

## Features

- **PDF Import**: Parse DNAtech reports automatically, extracting:
  - Animal information (name, species, breed, age, sex)
  - Protein electrophoresis (proteinogram) results
  - Biochemistry results (UPC ratio for kidney disease staging)
  - Complete urinalysis (Urina Tipo II)
- **Database Storage**: SQLite-based storage for animals, tests, symptoms, and observations
- **Result Tracking**: Track all marker values over time for each animal
- **Change Detection**: Automatically flag values outside reference ranges
- **Comparison Reports**: Compare results between test sessions
- **Symptom Correlation**: Associate clinical symptoms with test results
- **Smart Animal Matching**: Automatically groups tests for the same animal

## Supported Test Types

### 1. Proteinogram (Protein Electrophoresis)
| Marker | Reference Range | Description |
|--------|-----------------|-------------|
| Proteinas totais | 5.5-7.5 g/dL | Total serum protein |
| Albumina | 41-56% | Main plasma protein |
| Alfa 1 | 5.9-9.8% | Alpha-1 globulin |
| Alfa 2 | 9.0-15.2% | Alpha-2 globulin |
| Beta | 16.2-26.0% | Beta globulin |
| Gama | 7.0-13.3% | Gamma globulin (immunoglobulins) |
| A/G Ratio | 0.6-1.1 | Albumin/Globulin ratio |

### 2. Biochemistry (Kidney Markers)
| Marker | Interpretation |
|--------|----------------|
| UPC Ratio | <0.2: Not proteinuric, 0.2-0.5: Suspect, >0.5: Proteinuric |
| Urine Total Protein | mg/dL |
| Urine Creatinine | mg/dL |

### 3. Urinalysis (Urina Tipo II)
- **General**: Color, Appearance
- **Biochemistry**: Glucose, Bilirubin, Ketones, Specific Gravity, pH, Proteins, Urobilinogen, Nitrites
- **Sediment**: Leukocytes, Erythrocytes, Epithelial cells, Casts, Crystals, Mucus, Bacteria
- **Observations**: Lab comments and findings

## Project Structure

```
vet_protein_app/
├── src/
│   ├── models.py      # Data models, database schema, and ORM
│   ├── pdf_parser.py  # DNAtech PDF parser
│   ├── app.py         # Application service layer
│   └── web_server.py  # FastAPI web server
├── templates/         # HTML templates (Jinja2)
│   ├── base.html      # Base template with navigation
│   ├── index.html     # Dashboard
│   ├── animals.html   # Animals list
│   ├── animal_detail.html  # Animal details & history
│   ├── session_detail.html # Test session results
│   ├── upload.html    # PDF upload page
│   └── compare.html   # Test comparison view
├── data/              # Database files (created at runtime)
├── uploads/           # Stored PDF files
├── run_server.py      # Web server launcher
├── requirements.txt   # Python dependencies
└── README.md
```

## Installation

### Requirements

- Python 3.8+
- pdfplumber (for PDF parsing)
- FastAPI + Uvicorn (for web interface)
- Jinja2 (for templates)

### Setup

```bash
# Clone or extract the project
cd vet_protein_app

# Install dependencies
pip install -r requirements.txt

# Run the web server
python run_server.py
```

Then open http://localhost:8000 in your browser.

## Web Interface Features

### Dashboard
- Overview of total animals and tests
- Quick access to recent tests
- Direct links to upload and view animals

### Animals List
- View all registered animals
- See test count and last test date
- Quick access to comparison view

### Animal Details
- Complete animal information
- Test history with all sessions
- Add symptoms and observations
- View UPC ratio trends (kidney markers)

### Test Session View
- Full proteinogram results with reference ranges
- Biochemistry results (UPC ratio, IRIS staging)
- Complete urinalysis (Urina Tipo II)
- Automatic comparison with previous test

### PDF Upload
- Drag-and-drop PDF upload
- Automatic data extraction
- Smart animal matching (groups tests by animal)

### Test Comparison
- Side-by-side comparison of all tests
- Visual chart showing trends over time
- Automatic trend detection (improving/worsening/stable)

## Data Model

### Entities

```
Animal
├── id, name, species, breed
├── microchip, age, sex, weight
├── medical_history, notes
└── timestamps

TestSession
├── id, animal_id
├── report_number (e.g., "66790/1521038")
├── test_date, closing_date
├── sample_type, lab_name
└── pdf_path

ProteinResult
├── id, session_id
├── marker_name (e.g., "Albumina", "Gama")
├── value (%), value_absolute (g/dL)
├── reference_min/max (both % and g/dL)
└── flag (normal/high/low)

Symptom
├── id, animal_id
├── description, severity, category
├── observed_date, resolved_date
└── notes

Observation
├── id, animal_id
├── observation_type (weight, medication, etc.)
├── details, value, unit
└── observation_date
```

### Supported Protein Markers (DNAtech Proteinogram)

| Marker | Portuguese Name | Unit | Description |
|--------|----------------|------|-------------|
| Total Proteins | Proteinas totais | g/dL | Total serum protein |
| Albumin | Albumina | % + g/dL | Main plasma protein |
| Alpha-1 | Alfa 1 | % + g/dL | Alpha-1 globulin fraction |
| Alpha-2 | Alfa 2 | % + g/dL | Alpha-2 globulin fraction |
| Beta | Beta | % + g/dL | Beta globulin fraction |
| Gamma | Gama | % + g/dL | Immunoglobulin fraction |
| A/G Ratio | Rel. Albumina/Globulina | ratio | Albumin to globulin ratio |

## Installation

### Requirements

- Python 3.8+
- pdfplumber (for PDF parsing)
- SQLite (included with Python)

### Setup

```bash
# Clone or copy the project
cd vet_protein_app

# Install dependencies
pip install pdfplumber

# Run the demo
cd src
python app.py
```

## Usage

### Basic Usage

```python
from app import VetProteinService

# Initialize the service
with VetProteinService(db_path="vet_proteins.db") as service:
    
    # Import a PDF report
    animal_id, session_id, parsed = service.import_pdf("report.pdf")
    
    # Add symptoms for context
    service.add_symptom(animal_id, "Lethargy", severity="mild")
    
    # Generate analysis report
    report = service.generate_analysis_report(session_id)
    
    # View results
    for result in report.results:
        print(f"{result.marker_name}: {result.value}% - {result.flag}")
```

### Comparing Results Over Time

```python
# Get all sessions for an animal
sessions = service.db.get_sessions_for_animal(animal_id)

# Compare two sessions
if len(sessions) >= 2:
    comparisons = service.compare_sessions(
        sessions[0].id,  # current
        sessions[1].id   # previous
    )
    
    for comp in comparisons:
        if comp.clinical_significance != "none":
            print(f"{comp.marker_name}: {comp.trend} ({comp.change_percent:.1f}%)")
```

### Tracking Marker History

```python
# Get albumin history for an animal
history = service.get_marker_trend(animal_id, "Albumina")

for entry in history:
    print(f"{entry['test_date']}: {entry['value']}% - {entry['flag']}")
```

## PDF Format Support

Currently supports **DNAtech** laboratory reports with:
- Portuguese language
- Protein electrophoresis (PROTEINOGRAMA)
- Format: "Folha de Trabalho Nº XXXXX/XXXXXXX"

### Sample PDF Structure

```
Folha de Trabalho Nº 66790/1521038
Data 07/12/2025

Dados do Animal
Animal      Júlia
Espécie     Felideo
Raça        Sphynx
Idade       7 A (F)
Amostra     Soro

PROTEINOGRAMA
Análise                   Resultado    Un.    Ref.         Histórico
Proteinas totais          6,4          g/dL   5,7 - 7,9
Albumina                  53,7 % 3,4   g/dL   36,8 - 50,6  2,10 - 4,00
...
```

## Future Development

### Phase 2: Web Interface
- FastAPI backend with REST API
- React/Vue frontend for easy interaction
- Dashboard with visualizations

### Phase 3: AI Interpretation
- Integration with Claude API for result interpretation
- Veterinary research knowledge base
- Contextual analysis considering symptoms and history

### Phase 4: Advanced Features
- Multi-lab format support
- Breed-specific reference ranges
- PDF report generation
- Export to common formats (CSV, Excel)

## API Reference

### VetProteinService

| Method | Description |
|--------|-------------|
| `import_pdf(path)` | Import and parse a PDF report |
| `get_animal_history(id)` | Get complete history for an animal |
| `get_marker_trend(animal_id, marker)` | Get historical values for a marker |
| `compare_sessions(current_id, previous_id)` | Compare two test sessions |
| `generate_analysis_report(session_id)` | Generate complete analysis report |
| `add_symptom(animal_id, description, ...)` | Record a symptom |
| `add_observation(animal_id, type, ...)` | Record an observation |

### Database

| Method | Description |
|--------|-------------|
| `create_animal(animal)` | Insert new animal |
| `get_animal(id)` | Get animal by ID |
| `find_animal_by_name(name)` | Search animals by name |
| `create_test_session(session)` | Insert new test session |
| `get_sessions_for_animal(id)` | Get all tests for an animal |
| `create_protein_result(result)` | Insert a protein result |
| `get_results_for_session(id)` | Get results for a session |

## License

Private/Internal Use

## Contributing

This is an internal veterinary practice tool. For modifications or feature requests, contact the development team.
