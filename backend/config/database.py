"""Database configuration and session management."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from .settings import settings

# Create async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,  # 检查连接是否有效，无效则重新创建
    pool_recycle=3600,  # 每小时回收连接，防止数据库服务器关闭长时间空闲连接
    echo=settings.DEBUG,
)

# Advisory locks need a dedicated physical connection for their full lifetime.
# NullPool keeps long-running Runtime preparation from starving request sessions
# and guarantees that a released guard connection is not reused by the lock path.
thread_materialization_lock_engine = (
    create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        pool_pre_ping=True,
        echo=settings.DEBUG,
    )
    if settings.THREAD_MATERIALIZATION_LOCK_BACKEND == "postgresql"
    else None
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for all models
Base = declarative_base()


async def get_db():
    """Dependency for getting database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
