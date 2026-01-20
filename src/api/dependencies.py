"""
Dependency Injection for VetScan API

Provides FastAPI-compatible dependency injection for database access and services.

Usage in routes:
    from api.dependencies import get_container, ServiceContainer

    @app.get("/animals")
    async def list_animals(container: ServiceContainer = Depends(get_container)):
        animals = container.animal_repo.list_all()
        return animals

Or with context manager pattern:
    async def some_route():
        async with get_service_context() as container:
            animals = container.animal_repo.list_all()
"""

from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Generator, Optional

from database import Database
from database.repositories import (
    AnimalRepository,
    SessionRepository,
    UserRepository,
    DiagnosisRepository,
)
from config import settings
from logging_config import get_logger

logger = get_logger("dependencies")


class ServiceContainer:
    """
    Dependency injection container for VetScan services.

    Manages database connection and repository instances.
    Should be used as a context manager or via FastAPI Depends().

    Attributes:
        db: Database connection instance
        animal_repo: Repository for Animal operations
        session_repo: Repository for TestSession/Result operations
        user_repo: Repository for User operations
        diagnosis_repo: Repository for DiagnosisReport operations
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize service container.

        Args:
            db_path: Path to SQLite database file. If None, uses settings.
        """
        self.db_path = db_path or str(settings.paths.database)
        self._db: Optional[Database] = None
        self._animal_repo: Optional[AnimalRepository] = None
        self._session_repo: Optional[SessionRepository] = None
        self._user_repo: Optional[UserRepository] = None
        self._diagnosis_repo: Optional[DiagnosisRepository] = None

    def connect(self) -> 'ServiceContainer':
        """
        Establish database connection and initialize repositories.

        Returns:
            Self for method chaining
        """
        self._db = Database(self.db_path)
        self._db.connect()
        self._db.initialize()

        # Initialize repositories
        self._animal_repo = AnimalRepository(self._db)
        self._session_repo = SessionRepository(self._db)
        self._user_repo = UserRepository(self._db)
        self._diagnosis_repo = DiagnosisRepository(self._db)

        logger.debug(f"ServiceContainer connected to {self.db_path}")
        return self

    def close(self):
        """Close database connection and cleanup."""
        if self._db:
            self._db.close()
            self._db = None
            logger.debug("ServiceContainer closed")

    def __enter__(self) -> 'ServiceContainer':
        """Context manager entry."""
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    @property
    def db(self) -> Database:
        """Get database instance."""
        if not self._db:
            raise RuntimeError("ServiceContainer not connected. Call connect() first.")
        return self._db

    @property
    def animal_repo(self) -> AnimalRepository:
        """Get animal repository."""
        if not self._animal_repo:
            raise RuntimeError("ServiceContainer not connected. Call connect() first.")
        return self._animal_repo

    @property
    def session_repo(self) -> SessionRepository:
        """Get session repository."""
        if not self._session_repo:
            raise RuntimeError("ServiceContainer not connected. Call connect() first.")
        return self._session_repo

    @property
    def user_repo(self) -> UserRepository:
        """Get user repository."""
        if not self._user_repo:
            raise RuntimeError("ServiceContainer not connected. Call connect() first.")
        return self._user_repo

    @property
    def diagnosis_repo(self) -> DiagnosisRepository:
        """Get diagnosis repository."""
        if not self._diagnosis_repo:
            raise RuntimeError("ServiceContainer not connected. Call connect() first.")
        return self._diagnosis_repo


def get_container() -> Generator[ServiceContainer, None, None]:
    """
    FastAPI dependency that provides a ServiceContainer.

    Usage:
        @app.get("/animals")
        async def list_animals(container: ServiceContainer = Depends(get_container)):
            return container.animal_repo.list_all()

    Yields:
        Connected ServiceContainer instance
    """
    container = ServiceContainer()
    try:
        container.connect()
        yield container
    finally:
        container.close()


@contextmanager
def get_service_context(db_path: Optional[str] = None) -> Generator[ServiceContainer, None, None]:
    """
    Context manager for getting a service container.

    Usage:
        with get_service_context() as container:
            animals = container.animal_repo.list_all()

    Args:
        db_path: Optional database path override

    Yields:
        Connected ServiceContainer instance
    """
    container = ServiceContainer(db_path)
    try:
        container.connect()
        yield container
    finally:
        container.close()


@asynccontextmanager
async def get_async_service_context(db_path: Optional[str] = None):
    """
    Async context manager for getting a service container.

    Usage:
        async with get_async_service_context() as container:
            animals = container.animal_repo.list_all()

    Args:
        db_path: Optional database path override

    Yields:
        Connected ServiceContainer instance
    """
    container = ServiceContainer(db_path)
    try:
        container.connect()
        yield container
    finally:
        container.close()


# =============================================================================
# BACKWARDS COMPATIBILITY
# =============================================================================

def get_service():
    """
    Legacy function for backwards compatibility with existing code.

    Returns a VetProteinService-like object. The caller is responsible
    for calling close() when done.

    DEPRECATED: Use get_container() with FastAPI Depends() or
    get_service_context() context manager instead.

    Returns:
        ServiceContainer instance that must be closed manually
    """
    from app import VetProteinService

    service = VetProteinService(
        db_path=str(settings.paths.database),
        uploads_dir=str(settings.paths.uploads_dir)
    )
    service.initialize()
    return service
