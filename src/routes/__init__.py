from .download import router as download_router
from .tasks import router as tasks_router
from .info import router as info_router
from .admin import router as admin_router

__all__ = [
    "download_router",
    "tasks_router",
    "info_router",
    "admin_router",
]
