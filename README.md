# VetScan - Veterinary Protein Analysis Application

A comprehensive Python application for veterinary clinics to manage and analyze blood test results from protein electrophoresis, urinalysis, and kidney markers. Features AI-powered diagnosis generation, multi-user authentication, and automated email import.

## Features

### Core Functionality
- **PDF Import**: Parse DNAtech lab reports automatically (Portuguese format)
- **Email Import**: Automatically fetch and process lab reports from email
- **Database Storage**: SQLite-based storage with repository pattern
- **Result Tracking**: Track all marker values over time for each animal
- **Comparison Reports**: Side-by-side comparison between test sessions
- **Clinical Notes**: Add and manage clinical notes per animal

### AI-Powered Diagnosis
- **Claude AI Integration**: Generate comprehensive diagnosis reports using Claude API
- **GPT-5-mini Fallback**: Automatic fallback to OpenAI when Claude is unavailable
- **Contextual Analysis**: AI considers symptoms, history, and all test results
- **Multi-language**: Reports generated in Portuguese or English

### Multi-User Authentication
- **User Registration**: Email-based registration with admin approval workflow
- **Role-Based Access**: Superuser and regular user roles
- **Password Reset**: Secure email-based password recovery
- **Session Management**: Secure cookie-based sessions with automatic expiry

### Security Features
- **CSRF Protection**: All forms protected against cross-site request forgery
- **XSS Prevention**: Input sanitization and output escaping
- **Rate Limiting**: Login attempt throttling to prevent brute force
- **PDF Validation**: Magic byte verification and malicious content scanning
- **Sensitive Data Filtering**: API keys and passwords redacted from logs

### Internationalization
- **Bilingual Interface**: Full support for English and Portuguese
- **Date Formats**: Localized date parsing and display
- **Number Formats**: Locale-aware number formatting

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

## Project Structure

```
vet_protein_app/
├── src/
│   ├── api/
│   │   ├── dependencies.py      # Dependency injection container
│   │   └── routes/
│   │       ├── admin.py         # User management routes
│   │       ├── animals.py       # Animal CRUD routes
│   │       ├── auth.py          # Authentication routes
│   │       ├── diagnosis.py     # AI diagnosis routes
│   │       ├── sessions.py      # Test session routes
│   │       └── upload.py        # PDF upload routes
│   ├── database/
│   │   ├── base.py              # Database connection manager
│   │   └── repositories/
│   │       ├── animal_repository.py
│   │       ├── diagnosis_repository.py
│   │       ├── session_repository.py
│   │       └── user_repository.py
│   ├── middleware/
│   │   ├── auth.py              # Authentication middleware
│   │   ├── csrf.py              # CSRF protection
│   │   └── error_handler.py     # Global error handling
│   ├── models/
│   │   ├── domain.py            # Dataclasses (Animal, TestSession, etc.)
│   │   ├── enums.py             # Enums (Species, ResultFlag, etc.)
│   │   └── schema.py            # SQL schema definition
│   ├── utils/
│   │   ├── dates.py             # Date parsing and formatting
│   │   └── template_filters.py  # Jinja2 template filters
│   ├── app.py                   # Application service layer
│   ├── config.py                # Centralized configuration
│   ├── diagnosis_ai.py          # AI diagnosis generation
│   ├── email_sender.py          # Email sending (SMTP)
│   ├── exceptions.py            # Custom exception classes
│   ├── logging_config.py        # Structured logging setup
│   ├── pdf_parser.py            # DNAtech PDF parser
│   ├── pdf_validator.py         # PDF security validation
│   └── web_server.py            # FastAPI application
├── templates/
│   ├── auth/                    # Authentication templates
│   │   ├── base_auth.html       # Auth page base template
│   │   ├── forgot_password.html
│   │   ├── pending_approval.html
│   │   ├── register.html
│   │   └── reset_password.html
│   ├── base.html                # Main base template
│   ├── index.html               # Dashboard
│   ├── animals.html             # Animals list
│   ├── animal_detail.html       # Animal details & history
│   ├── session_detail.html      # Test session results
│   ├── compare.html             # Test comparison view
│   ├── upload.html              # PDF upload page
│   ├── diagnosis.html           # AI diagnosis view
│   └── login.html               # Login page
├── static/
│   └── css/
│       └── auth.css             # Authentication styles
├── translations/
│   ├── en.json                  # English translations
│   └── pt.json                  # Portuguese translations
├── scripts/
│   └── run_email_import.py      # Email import script
├── data/                        # Database files (created at runtime)
├── uploads/                     # Stored PDF files
├── logs/                        # Application logs
├── deploy/                      # Deployment configuration
├── run_server.py                # Web server launcher
└── requirements.txt             # Python dependencies
```

## Installation

### Requirements

- Python 3.8+
- SQLite (included with Python)

### Dependencies

```
fastapi
uvicorn
jinja2
pdfplumber
python-multipart
anthropic
openai
bleach
bcrypt
```

### Setup

```bash
# Clone the repository
git clone https://github.com/jomoormann/vetscan.git
cd vetscan

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create .env file with required settings
cp .env.example .env
# Edit .env with your configuration

# Run the web server
python run_server.py
```

Then open http://localhost:8000 in your browser.

## Configuration

Create a `.env` file in the project root:

```env
# Authentication
AUTH_SECRET_KEY=your-32-byte-hex-secret-key

# AI Services (at least one required for diagnosis)
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...

# Email Sending (for password reset, notifications)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM_EMAIL=your-email@gmail.com
SMTP_FROM_NAME=VetScan

# Email Import (optional - for automatic report fetching)
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_PORT=993
EMAIL_ADDRESS=your-email@gmail.com
EMAIL_PASSWORD=your-app-password

# Application
LOG_LEVEL=INFO
ALLOWED_HOSTS=localhost,127.0.0.1
```

## Web Interface

### Dashboard
- Overview of total animals and tests
- Recent test sessions
- Quick access to upload and view animals

### Animals List
- View all registered animals
- Search and filter
- Test count and last test date
- Quick access to comparison view

### Animal Details
- Complete animal information
- Test history with all sessions
- Clinical notes management
- Symptoms and observations
- UPC ratio trends (kidney markers)
- AI diagnosis generation

### Test Session View
- Full proteinogram results with reference ranges
- Biochemistry results (UPC ratio, IRIS staging)
- Complete urinalysis (Urina Tipo II)
- Automatic comparison with previous test
- Visual trend indicators

### AI Diagnosis
- Generate comprehensive diagnosis reports
- Considers all test results, symptoms, and history
- Available in English or Portuguese
- Powered by Claude AI with GPT-5-mini fallback

### PDF Upload
- Drag-and-drop PDF upload
- Automatic data extraction
- Smart animal matching
- Security validation (magic bytes, content scanning)

### User Management (Admin)
- View all users
- Approve pending registrations
- Enable/disable user accounts
- Role management

## API Usage

### VetProteinService

```python
from app import VetProteinService

with VetProteinService(db_path="vet_proteins.db") as service:
    # Import a PDF report
    animal_id, session_id, parsed = service.import_pdf("report.pdf")

    # Add symptoms
    service.add_symptom(animal_id, "Lethargy", severity="mild")

    # Generate AI diagnosis
    diagnosis = service.generate_ai_diagnosis(animal_id, language="en")

    # Compare sessions
    comparisons = service.compare_sessions(session_id, previous_session_id)
```

### Database Repositories

```python
from database import Database

db = Database("vet_proteins.db")
db.connect()
db.initialize()

# Animal operations
animals = db.list_animals()
animal = db.get_animal(1)

# Session operations
sessions = db.get_sessions_for_animal(animal_id)
results = db.get_results_for_session(session_id)

# User operations
user = db.get_user_by_email("user@example.com")
pending = db.get_pending_users()
```

## Logging

The application uses structured logging with:
- **File rotation**: Logs rotate at 10MB, keeping 7 backup files
- **Sensitive data filtering**: API keys and passwords automatically redacted
- **Log levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL

Logs are stored in `logs/vetscan.log`.

```python
from logging_config import get_logger

logger = get_logger("my_module")
logger.info("Processing animal", animal_id=123)
```

## Security

### Authentication
- Passwords hashed with bcrypt (work factor 12)
- Session tokens with HMAC-SHA256 signing
- Automatic session expiry (7 days)
- Rate limiting on login attempts

### Input Validation
- All user input sanitized
- PDF files validated (magic bytes, size limits)
- SQL injection prevention via parameterized queries
- XSS prevention via output escaping

### CSRF Protection
- All state-changing forms require CSRF token
- Tokens tied to user session
- SameSite cookie attribute set

## Email Import

Automatically fetch lab reports from email:

```bash
# Run manually
python scripts/run_email_import.py

# Or set up as cron job / systemd timer
# See deploy/vetscan-email-import.service
```

## PDF Format Support

Currently supports **DNAtech** laboratory reports:
- Portuguese language
- Protein electrophoresis (PROTEINOGRAMA)
- Biochemistry (BIOQUIMICA)
- Urinalysis (URINA TIPO II)

## Development

### Running Tests

```bash
python test_all.py
```

### Adding New Routes

1. Create route module in `src/api/routes/`
2. Use dependency injection for database access
3. Register router in `web_server.py`

### Adding New Repositories

1. Create repository class in `src/database/repositories/`
2. Add wrapper methods to `src/database/__init__.py`
3. Register in `ServiceContainer`

## License

Private/Internal Use

## Support

For issues or feature requests, contact the development team or open an issue on GitHub.
