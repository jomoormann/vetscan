"""
VetScan API Routes

Contains route modules for different functional areas.

Usage:
    from api.routes import auth_router, admin_router, animals_router

    app.include_router(auth_router)
    app.include_router(admin_router, prefix="/admin")
"""

from .auth import router as auth_router
from .admin import router as admin_router
from .animals import router as animals_router
from .sessions import router as sessions_router
from .upload import router as upload_router
from .diagnosis import router as diagnosis_router

__all__ = [
    'auth_router',
    'admin_router',
    'animals_router',
    'sessions_router',
    'upload_router',
    'diagnosis_router',
]
