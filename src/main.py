"""Main FastAPI application entry point."""
import logging
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from config.settings import settings

# Configure logging - 确保所有 logger 的日志都能输出
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
from config.database import engine, Base, AsyncSessionLocal
from config.redis import get_redis_client, close_redis
from utils.external_services import close_http_client
from utils.token_usage_queue import init_token_usage_queue, shutdown_token_usage_queue

# Import controllers
from controllers import (
    auth_controller,
    note_controller,
    favorite_controller,
    kb_controller,
    chat_controller,
    organization_controller,
    admin_controller,
    token_usage_controller,
    chunk_controller,
)
from rag_chat import rag_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    print("🚀 Starting Reader API...")
    
    # Initialize database tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Initialize Redis
    redis_client = await get_redis_client()
    
    # Initialize token usage queue (async background worker)
    await init_token_usage_queue(redis_client, AsyncSessionLocal)
    
    print("✅ Application started successfully")
    
    yield
    
    # Shutdown
    print("🛑 Shutting down...")
    await shutdown_token_usage_queue()
    await close_http_client()
    await close_redis()
    await engine.dispose()
    print("✅ Cleanup completed")


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
    print(f"❌ Uncaught exception: {exc}")
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


# Include routers
app.include_router(auth_controller.router, prefix=settings.API_PREFIX)
app.include_router(organization_controller.router, prefix=settings.API_PREFIX)
app.include_router(admin_controller.router, prefix=settings.API_PREFIX)
app.include_router(note_controller.router, prefix=settings.API_PREFIX)
app.include_router(favorite_controller.router, prefix=settings.API_PREFIX)
app.include_router(kb_controller.router, prefix=settings.API_PREFIX)
app.include_router(chat_controller.router, prefix=settings.API_PREFIX)
app.include_router(token_usage_controller.router, prefix=settings.API_PREFIX)
app.include_router(rag_router, prefix=settings.API_PREFIX)
app.include_router(chunk_controller.router, prefix=settings.API_PREFIX)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=13000,
        reload=settings.DEBUG
    )
