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
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()
        logger.info(f"Database initialized: {self.db_path}")

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
