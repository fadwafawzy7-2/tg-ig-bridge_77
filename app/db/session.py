"""
Database connection/session management.

Exposes:
- `engine`:        the process-wide async SQLAlchemy engine (connection pool)
- `AsyncSessionLocal`: session factory
- `get_db()`:       FastAPI dependency that yields a request-scoped session
- `check_db_connection()`: lightweight connectivity probe for health checks
"""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

logger = logging.getLogger(__name__)

# DATABASE_URL already points at Supabase's own connection pooler
# (pooler.supabase.com), so a second pool layered on top of it here is
# redundant - and, worse, under this worker's long-running process (many
# short cycles over ~55 minutes) pooled connections would go stale on the
# pooler's side between cycles, which previously surfaced as an opaque
# `MissingGreenlet` error from pool_pre_ping's connection check. NullPool
# opens a fresh connection per checkout instead, letting Supabase's own
# pooler do the actual pooling - which is what it's there for.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    poolclass=NullPool,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped DB session.

    Usage:
        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_db_connection() -> bool:
    """Run a trivial query to confirm the database is reachable.

    Used by the readiness health-check endpoint. Returns False instead of
    raising so callers can decide how to report the failure.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database connectivity check failed")
        return False
