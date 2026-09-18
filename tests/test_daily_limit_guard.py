"""
Tests for `app.instagram.daily_limit_guard`.

Needs a real Postgres (see `db_session` in conftest.py); skipped
automatically if one isn't reachable. Includes a genuine CONCURRENT
reservation test using two independent DB sessions + `asyncio.gather`,
not just sequential calls, to actually exercise Postgres's row-level
UPDATE serialization.
"""

import asyncio

import pytest

from app.db.session import AsyncSessionLocal
from app.instagram.daily_limit_guard import has_daily_limit_configured, release_slot, reserve_slot
from app.models.enums import ContentType
from tests.factories import make_daily_limit

pytestmark = pytest.mark.asyncio


async def test_reserve_succeeds_when_capacity_available(db_session):
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=0)

    reserved = await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    assert reserved is True
    await db_session.refresh(limit)
    assert limit.published_count == 1


async def test_reserve_fails_when_limit_is_zero(db_session):
    limit = await make_daily_limit(db_session, max_allowed=0, published_count=0)

    reserved = await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    assert reserved is False
    await db_session.refresh(limit)
    assert limit.published_count == 0


async def test_reserve_fails_when_already_at_capacity(db_session):
    limit = await make_daily_limit(db_session, max_allowed=2, published_count=2)

    reserved = await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    assert reserved is False
    await db_session.refresh(limit)
    assert limit.published_count == 2  # unchanged


async def test_reserve_fails_when_no_row_configured_at_all(db_session):
    from datetime import date

    reserved = await reserve_slot(db_session, target_date=date(2099, 1, 1), content_type=ContentType.POST)
    assert reserved is False


async def test_partially_consumed_limit_allows_remaining_capacity_only(db_session):
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=2)

    first = await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)
    await db_session.refresh(limit)
    second = await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    assert first is True
    assert second is False  # only 1 remaining slot (3-2), consumed by the first reservation
    await db_session.refresh(limit)
    assert limit.published_count == 3


async def test_release_decrements_published_count(db_session):
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=2)

    await release_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    await db_session.refresh(limit)
    assert limit.published_count == 1


async def test_release_never_goes_below_zero(db_session):
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=0)

    await release_slot(db_session, target_date=limit.date, content_type=limit.content_type)

    await db_session.refresh(limit)
    assert limit.published_count == 0


async def test_reserve_then_release_returns_to_original_count(db_session):
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=1)

    await reserve_slot(db_session, target_date=limit.date, content_type=limit.content_type)
    await db_session.refresh(limit)
    assert limit.published_count == 2

    await release_slot(db_session, target_date=limit.date, content_type=limit.content_type)
    await db_session.refresh(limit)
    assert limit.published_count == 1


async def test_has_daily_limit_configured(db_session):
    limit = await make_daily_limit(db_session, max_allowed=1)
    from datetime import date

    assert await has_daily_limit_configured(
        db_session, target_date=limit.date, content_type=limit.content_type
    ) is True
    assert await has_daily_limit_configured(
        db_session, target_date=date(2099, 1, 1), content_type=ContentType.POST
    ) is False


# --- Genuine concurrent reservation (two independent sessions) ---------


async def test_concurrent_reservations_never_exceed_the_limit(db_session):
    """Two independent DB sessions racing for the SAME single remaining
    slot must never both succeed - this is the actual concurrency
    guarantee the whole Phase 8 daily-limit design rests on."""
    limit = await make_daily_limit(db_session, max_allowed=1, published_count=0)
    target_date = limit.date
    content_type = limit.content_type

    async def _attempt() -> bool:
        async with AsyncSessionLocal() as session:
            return await reserve_slot(session, target_date=target_date, content_type=content_type)

    results = await asyncio.gather(_attempt(), _attempt())

    assert sorted(results) == [False, True]  # exactly one succeeded

    await db_session.refresh(limit)
    assert limit.published_count == 1  # never exceeded the limit of 1


async def test_concurrent_reservations_with_multiple_slots(db_session):
    """5 concurrent attempts against a limit of 3 must result in exactly
    3 successes and 2 failures - never more successes than capacity."""
    limit = await make_daily_limit(db_session, max_allowed=3, published_count=0)
    target_date = limit.date
    content_type = limit.content_type

    async def _attempt() -> bool:
        async with AsyncSessionLocal() as session:
            return await reserve_slot(session, target_date=target_date, content_type=content_type)

    results = await asyncio.gather(*[_attempt() for _ in range(5)])

    assert sum(1 for r in results if r) == 3
    assert sum(1 for r in results if not r) == 2

    await db_session.refresh(limit)
    assert limit.published_count == 3
