from fastapi import APIRouter

from .runtime_controller import router as thread_router
from .runtime_run_controller import router as run_router


router = APIRouter()
router.include_router(thread_router)
router.include_router(run_router)

__all__ = ["router"]
