"""
Shared pytest fixtures.

Test-safe environment variables are set BEFORE `app.main` is imported
anywhere, so `Settings()` never accidentally reads a developer's real
local `.env` during the test run.
"""

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")
os.environ.setdefault("POSTGRES_USER", "test_user")
os.environ.setdefault("POSTGRES_PASSWORD", "test_password")
os.environ.setdefault("POSTGRES_DB", "test_db")

import pytest
from fastapi.testclient import TestClient

import app.models  # noqa: F401 - populates Base.metadata for the model tests
from app.db.base import Base
from app.db.session import AsyncSessionLocal, check_db_connection, engine
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
async def db_session():
    """
    A real, isolated Postgres session for model/constraint tests.

    Phase 2's schema relies on PostgreSQL-specific features (native enum
    types, CHECK constraints, partial unique indexes) that SQLite cannot
    express, so — unlike `client`, which needs no database — these tests
    need an actual Postgres instance. If one isn't reachable (e.g. running
    `pytest` without `docker compose up -d db` first), the test is skipped
    rather than failing with a confusing connection error.

    The schema is created fresh before each test and dropped after, so
    tests can't see each other's data and don't depend on run order.
    """
    if not await check_db_connection():
        pytest.skip(
            "PostgreSQL is not reachable; start it with `docker compose up -d db` "
            "to run model/constraint tests."
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
