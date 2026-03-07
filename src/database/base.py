"""
Database Connection Manager for VetScan

Provides the base Database class for SQLite connection management.
Repositories use this class for data access.
"""

import sqlite3
from pathlib import Path
from typing import Optional

from models.schema import SCHEMA_SQL
from logging_config import get_logger

logger = get_logger("database")


class Database:
    """
    Database connection manager for VetScan.

    Provides connection lifecycle management and schema initialization.
    Repositories inject this class for database operations.
    """

    def __init__(self, db_path: str = "vet_proteins.db"):
        """
        Initialize database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """
        Establish database connection.

        Returns:
            SQLite connection with row factory enabled
        """
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        logger.debug(f"Connected to database: {self.db_path}")
        return self.conn

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.debug(f"Closed database connection: {self.db_path}")

    def initialize(self):
        """Create database schema if not exists."""
        if not self.conn:
            self.connect()

        try:
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()
        except sqlite3.OperationalError as exc:
            if "no such column" not in str(exc):
                raise

            # Older production databases may be missing columns referenced by
            # new indexes in SCHEMA_SQL. Add legacy columns first, then retry.
            logger.warning(f"Schema apply hit legacy-column issue, retrying migrations: {exc}")
            self.conn.rollback()
            self._run_migrations()
            self.conn.executescript(SCHEMA_SQL)
            self.conn.commit()

        self._run_migrations()
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

    def _run_migrations(self):
        """Run database migrations for schema changes."""
        def ensure_column(table: str, column: str, definition: str):
            cursor = self.conn.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cursor.fetchall()]
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                self.conn.commit()
                logger.info(f"Migration: Added {column} column to {table} table")

        ensure_column("animals", "responsible_vet", "TEXT")
        ensure_column("animals", "owner_name", "TEXT")

        ensure_column("test_sessions", "source_system", "TEXT DEFAULT 'dnatech'")
        ensure_column("test_sessions", "report_type", "TEXT DEFAULT 'dnatech_proteinogram'")
        ensure_column("test_sessions", "external_report_id", "TEXT")
        ensure_column("test_sessions", "report_source", "TEXT")
        ensure_column("test_sessions", "reported_at", "TIMESTAMP")
        ensure_column("test_sessions", "received_at", "TIMESTAMP")
        ensure_column("test_sessions", "clinic_name", "TEXT")
        ensure_column("test_sessions", "panel_name", "TEXT")
        ensure_column("test_sessions", "raw_metadata_json", "TEXT")

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a SQL query.

        Args:
            query: SQL query string
            params: Query parameters

        Returns:
            Cursor with query results
        """
        if not self.conn:
            self.connect()
        return self.conn.execute(query, params)

    def executemany(self, query: str, params_list: list) -> sqlite3.Cursor:
        """
        Execute a SQL query with multiple parameter sets.

        Args:
            query: SQL query string
            params_list: List of parameter tuples

        Returns:
            Cursor with query results
        """
        if not self.conn:
            self.connect()
        return self.conn.executemany(query, params_list)

    def commit(self):
        """Commit the current transaction."""
        if self.conn:
            self.conn.commit()

    def rollback(self):
        """Rollback the current transaction."""
        if self.conn:
            self.conn.rollback()
