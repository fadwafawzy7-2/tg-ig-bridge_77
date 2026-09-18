"""
The concurrency-safe "reserve a slot, publish, confirm-or-release"
mechanism around `daily_limits.published_count`. This is what makes
"never exceed the daily MAXIMUM under concurrent/retried/restarted
publishing" hold at the database level, not in application memory.

Design: reserve BEFORE calling Instagram, release (roll back) if the
publish attempt fails, leave it consumed only on confirmed success.

    reserved = await reserve_slot(session, date, content_type)
    if not reserved:
        # limit already full (or unconfigured -> 0 capacity, same as
        # Phase 6's scheduler.py philosophy) - do not attempt to publish
        return
    try:
        <call Instagram, create published_posts row, ...>
    except Exception:
        await release_slot(session, date, content_type)  # give the slot back
        raise

`reserve_slot` is a single atomic conditional `UPDATE ... WHERE
published_count < max_allowed RETURNING ...`. Postgres serializes
concurrent UPDATEs to the same row automatically (the second waits for
the first's transaction to commit, then re-evaluates the WHERE clause
against the now-current value) — this is what actually prevents two
concurrent publishers from both reserving the last slot, WITHOUT needing
to hold a lock open across the slow external HTTP call to Instagram
(holding a row lock across a network call would serialize all publishing
for the day behind that one call, which is both slower and riskier than
this reserve/release pattern).

`daily_limits.max_allowed` remains a strict MAXIMUM exactly as Phase 6
defined it: 0 (or no row at all for that date/content_type) means zero
capacity, never "unlimited" — `reserve_slot` returns `False` in both
cases. Nothing here changes `app/queue/scheduler.py`'s own reading of
this same column (already reading it since a Phase 6 fix — see that
module's docstring); this is simply the first phase to actually write to
it, exactly as the schema always intended.
"""

from __future__ import annotations

from datetime import date as date_

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_limit import DailyLimit
from app.models.enums import ContentType


async def reserve_slot(
    session: AsyncSession, *, target_date: date_, content_type: ContentType
) -> bool:
    """Atomically claim one slot of today's `daily_limits` capacity for
    `content_type`, if any remains. Returns whether the reservation
    succeeded. Commits on success (so the reservation is durable and the
    row is not left locked); does nothing (no commit needed) if it
    fails."""
    stmt = (
        update(DailyLimit)
        .where(
            DailyLimit.date == target_date,
            DailyLimit.content_type == content_type,
            DailyLimit.published_count < DailyLimit.max_allowed,
        )
        .values(published_count=DailyLimit.published_count + 1)
        .returning(DailyLimit.id)
    )
    result = await session.execute(stmt)
    reserved_id = result.scalar_one_or_none()
    if reserved_id is None:
        await session.rollback()
        return False
    await session.commit()
    return True


async def release_slot(
    session: AsyncSession, *, target_date: date_, content_type: ContentType
) -> None:
    """Give back a slot reserved by `reserve_slot` whose publish attempt
    then failed — "failed attempts never count as published". Never lets
    `published_count` go below 0 even under a duplicate/unexpected
    release call."""
    stmt = (
        update(DailyLimit)
        .where(
            DailyLimit.date == target_date,
            DailyLimit.content_type == content_type,
            DailyLimit.published_count > 0,
        )
        .values(published_count=DailyLimit.published_count - 1)
    )
    await session.execute(stmt)
    await session.commit()
