"""HTTP boundary for Runtime thread guard failures."""

from contextlib import asynccontextmanager

from fastapi import HTTPException


@asynccontextmanager
async def runtime_thread_guard(materialization_service, thread_id: str):
    try:
        async with materialization_service.thread_guard(thread_id):
            yield
    except Exception as exc:
        from modules.chat.services.thread_materialization_service import (
            ThreadMaterializationLockError,
        )

        if not isinstance(exc, ThreadMaterializationLockError):
            raise
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "THREAD_GUARD_UNAVAILABLE",
                    "message": "The Runtime thread is temporarily unavailable",
                }
            },
        ) from exc
