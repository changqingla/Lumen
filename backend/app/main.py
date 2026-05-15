"""Main FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config.settings import settings

# Configure logging - 确保所有 logger 的日志都能输出
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)
from config.database import engine, AsyncSessionLocal
from config.redis import get_redis_client, close_redis
from utils.token_usage_queue import init_token_usage_queue, shutdown_token_usage_queue

# Import routers through domain module entrypoints where available.
from modules.admin import router as admin_router
from modules.auth import router as auth_router
from modules.chat import router as chat_router
from modules.chat.model_controller import router as chat_model_router
from modules.chat.runtime_router import router as chat_runtime_router
from modules.favorites import router as favorite_router
from modules.knowledge import router as knowledge_router
from modules.knowledge.chunk_controller import router as knowledge_chunk_router
from modules.model_config import internal_router as model_config_internal_router, router as model_config_router
from modules.notes import router as note_router
from modules.organization import router as organization_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Lumen API...")
    
    # Initialize Redis
    redis_client = await get_redis_client()
    
    # Initialize token usage queue (async background worker)
    await init_token_usage_queue(redis_client, AsyncSessionLocal)
    
    logger.info("Application started successfully")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    from utils.http_client import close_http_client

    await shutdown_token_usage_queue()
    await close_http_client()
    await close_redis()
    await engine.dispose()
    logger.info("Cleanup completed")


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url=f"{settings.API_PREFIX}/docs",
    redoc_url=f"{settings.API_PREFIX}/redoc",
    openapi_url=f"{settings.API_PREFIX}/openapi.json"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all uncaught exceptions."""
    logger.exception("Uncaught exception while handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred" if not settings.DEBUG else str(exc)
            }
        }
    )


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": settings.APP_VERSION}


async def _probe_dependency(name: str, url: str, timeout_seconds: float) -> dict:
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url)
            response.raise_for_status()
        return {"name": name, "ok": True, "url": url}
    except Exception as exc:
        return {"name": name, "ok": False, "url": url, "error": str(exc)}


@app.get("/api/ready")
async def readiness_check():
    """Readiness endpoint covering critical runtime dependencies."""
    timeout_seconds = min(settings.INSIGHT_REQUEST_TIMEOUT_SECONDS, 3.0)
    checks = await asyncio.gather(
        _probe_dependency("gateway", f"{settings.INSIGHT_GATEWAY_URL.rstrip('/')}/health", timeout_seconds),
        _probe_dependency("langgraph", f"{settings.INSIGHT_LANGGRAPH_URL.rstrip('/')}/docs", timeout_seconds),
    )
    failed = [item for item in checks if not item["ok"]]
    if failed:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "degraded",
                "version": settings.APP_VERSION,
                "dependencies": checks,
            },
        )
    return {
        "status": "ready",
        "version": settings.APP_VERSION,
        "dependencies": checks,
    }


# Include routers
app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(organization_router, prefix=settings.API_PREFIX)
app.include_router(admin_router, prefix=settings.API_PREFIX)
app.include_router(note_router, prefix=settings.API_PREFIX)
app.include_router(favorite_router, prefix=settings.API_PREFIX)
app.include_router(knowledge_router, prefix=settings.API_PREFIX)
app.include_router(chat_router, prefix=settings.API_PREFIX)
app.include_router(chat_model_router, prefix=settings.API_PREFIX)
app.include_router(chat_runtime_router, prefix=settings.API_PREFIX)
app.include_router(model_config_router, prefix=settings.API_PREFIX)
app.include_router(model_config_internal_router, prefix=settings.API_PREFIX)
app.include_router(knowledge_chunk_router, prefix=settings.API_PREFIX)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=13000,
        reload=settings.DEBUG
    )
