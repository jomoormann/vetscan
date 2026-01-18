#!/usr/bin/env python3
"""
Email Import Cron Job Entry Point

Run this script periodically (e.g., every 10 minutes via cron/systemd timer)
to automatically import DNAtech lab reports from email.

Usage:
    python scripts/run_email_import.py

Exit codes:
    0 - Success (all imports successful or no emails to process)
    1 - Partial failure (some imports failed)
    2 - Fatal error (could not connect or process)

Logs:
    - Console output: Summary of operations
    - logs/email_import.log: Detailed rotating log (5MB max, 3 backups)
    - logs/last_import_result.json: JSON result of last run
"""

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Add src directory to path for imports
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_DIR = SCRIPT_DIR.parent
SRC_DIR = PROJECT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))

from email_importer import EmailImporter, BatchResult
from email_config import get_email_config


def setup_logging() -> logging.Logger:
    """Set up rotating file logger and console output."""
    logs_dir = PROJECT_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("email_import")
    logger.setLevel(logging.INFO)

    # Rotating file handler (5MB max, 3 backups)
    file_handler = RotatingFileHandler(
        logs_dir / "email_import.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    logger.addHandler(file_handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(console_handler)

    return logger


def write_result_json(batch: BatchResult, logs_dir: Path):
    """Write batch result to JSON file."""
    result = {
        "timestamp": datetime.now().isoformat(),
        "start_time": batch.start_time.isoformat() if batch.start_time else None,
        "end_time": batch.end_time.isoformat() if batch.end_time else None,
        "duration_seconds": (
            (batch.end_time - batch.start_time).total_seconds()
            if batch.end_time and batch.start_time else None
        ),
        "emails_processed": batch.emails_processed,
        "pdfs_found": batch.pdfs_found,
        "imports_successful": batch.imports_successful,
        "imports_failed": batch.imports_failed,
        "imports_skipped": batch.imports_skipped,
        "error": batch.error,
        "results": [
            {
                "email_uid": r.email_uid,
                "attachment": r.attachment_name,
                "success": r.success,
                "validation_result": r.validation_result,
                "report_number": r.report_number,
                "error": r.error_message
            }
            for r in batch.results
        ]
    }

    with open(logs_dir / "last_import_result.json", 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


def main() -> int:
    """
    Main entry point for email import cron job.

    Returns:
        Exit code (0=success, 1=partial failure, 2=fatal error)
    """
    logger = setup_logging()
    logs_dir = PROJECT_DIR / "logs"

    logger.info("=" * 60)
    logger.info("Starting email import batch")
    logger.info("=" * 60)

    # Load and validate configuration
    config = get_email_config()
    valid, error_msg = config.validate()

    if not valid:
        logger.error(f"Configuration error: {error_msg}")
        return 2

    logger.info(f"IMAP Server: {config.imap_host}")
    logger.info(f"Email: {config.email_address}")
    logger.info(f"Rate limit: {config.import_rate_limit}/hour")

    # Set up paths
    db_path = str(PROJECT_DIR / "data" / "vet_proteins.db")
    uploads_dir = str(PROJECT_DIR / "uploads")

    # Ensure directories exist
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    os.makedirs(uploads_dir, exist_ok=True)

    # Create importer with logger
    importer = EmailImporter(
        config=config,
        db_path=db_path,
        uploads_dir=uploads_dir
    )
    importer.set_logger(lambda msg: logger.info(msg))

    # Run import batch
    try:
        batch = importer.run_import_batch()
    except Exception as e:
        logger.exception(f"Fatal error during import: {e}")
        return 2

    # Write result JSON
    try:
        write_result_json(batch, logs_dir)
    except Exception as e:
        logger.warning(f"Could not write result JSON: {e}")

    # Log summary
    logger.info("-" * 60)
    logger.info("Import batch complete")
    logger.info(f"  Emails processed: {batch.emails_processed}")
    logger.info(f"  PDFs found: {batch.pdfs_found}")
    logger.info(f"  Successful imports: {batch.imports_successful}")
    logger.info(f"  Failed imports: {batch.imports_failed}")
    logger.info(f"  Skipped (duplicates/rate limit): {batch.imports_skipped}")

    if batch.end_time and batch.start_time:
        duration = (batch.end_time - batch.start_time).total_seconds()
        logger.info(f"  Duration: {duration:.1f} seconds")

    if batch.error:
        logger.error(f"  Error: {batch.error}")

    logger.info("=" * 60)

    # Determine exit code
    if batch.error:
        return 2
    elif batch.imports_failed > 0:
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())
