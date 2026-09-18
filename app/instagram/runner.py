"""
Batch driver for Phase 8, gated by `INSTAGRAM_PUBLISH_MODE`:

- `AUTO`: calls `publish_scheduled_post()`/`publish_story()` for every
  due (`scheduled_for <= now`, or unscheduled stories) PENDING/SCHEDULED
  item.
- `REVIEW` (the default): does NOTHING automatically — every due item is
  left exactly as Phase 6 left it (`status=SCHEDULED`), which already
  means "queued, not yet published". No status is invented for
  "awaiting review"; see `app/instagram/__init__.py`'s docstring for the
  full reasoning. `publish_scheduled_post()`/`publish_story()` remain
  directly callable by a human action, an admin endpoint, or a test —
  REVIEW mode only gates this automatic batch path.

Like every prior phase's runner, this has no periodic-execution
machinery of its own — `run_publisher()` is what a scheduler/cron would
call repeatedly.

Run manually with:
    python -m app.instagram.runner
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.instagram.client import InstagramClient
from app.instagram.publisher_service import PublishOutcome, publish_scheduled_post, publish_story
from app.models.enums import PostPublishStatus, StoryStatus
from app.models.scheduled_post import ScheduledPost
from app.models.story import Story
from app.queue.time_utils import utcnow

logger = logging.getLogger(__name__)

_ATTEMPTABLE_SCHEDULED_STATUSES = (PostPublishStatus.PENDING, PostPublishStatus.SCHEDULED)
_ATTEMPTABLE_STORY_STATUSES = (StoryStatus.PENDING, StoryStatus.SCHEDULED)


@dataclass(frozen=True)
class PublisherRunSummary:
    mode: str
    scheduled_post_results: list[PublishOutcome]
    story_results: list[PublishOutcome]

    @property
    def published(self) -> int:
        return sum(
            1 for r in (*self.scheduled_post_results, *self.story_results) if r.outcome == "published"
        )

    @property
    def due_but_awaiting_review(self) -> int:
        return (
            len(self.scheduled_post_results) + len(self.story_results) if self.mode == "REVIEW" else 0
        )


async def _due_scheduled_post_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(
        select(ScheduledPost.id)
        .where(
            ScheduledPost.status.in_(_ATTEMPTABLE_SCHEDULED_STATUSES),
            ScheduledPost.scheduled_for <= utcnow(),
        )
        .order_by(ScheduledPost.scheduled_for.asc())
    )
    return list(result.scalars().all())


async def _due_story_ids(session: AsyncSession) -> list[int]:
    result = await session.execute(
        select(Story.id)
        .where(
            Story.status.in_(_ATTEMPTABLE_STORY_STATUSES),
            or_(Story.scheduled_for.is_(None), Story.scheduled_for <= utcnow()),
        )
        .order_by(Story.id.asc())
    )
    return list(result.scalars().all())


async def run_publisher(session: AsyncSession, client: InstagramClient) -> PublisherRunSummary:
    settings = get_settings()
    # Phase 9 dashboard may override REVIEW/AUTO in the relational settings table.
    # Environment configuration remains the safe default when no dashboard setting exists.
    from app.dashboard.settings_service import get_value
    mode = (await get_value(session, "instagram_publish_mode", settings.INSTAGRAM_PUBLISH_MODE)) or settings.INSTAGRAM_PUBLISH_MODE
    mode = mode if mode in {"REVIEW", "AUTO"} else settings.INSTAGRAM_PUBLISH_MODE

    scheduled_post_ids = await _due_scheduled_post_ids(session)
    story_ids = await _due_story_ids(session)

    if mode == "REVIEW":
        logger.info(
            "INSTAGRAM_PUBLISH_MODE=REVIEW: %d scheduled post(s) and %d stor(y/ies) are due "
            "but will NOT be auto-published - left at SCHEDULED, awaiting manual/Phase-9 approval.",
            len(scheduled_post_ids),
            len(story_ids),
        )
        return PublisherRunSummary(mode=mode, scheduled_post_results=[], story_results=[])

    scheduled_post_results: list[PublishOutcome] = []
    for scheduled_post_id in scheduled_post_ids:
        outcome = await publish_scheduled_post(session, scheduled_post_id, client)
        scheduled_post_results.append(outcome)
        logger.info(
            "Publish scheduled_post_id=%s outcome=%s instagram_media_id=%s",
            scheduled_post_id,
            outcome.outcome,
            outcome.instagram_media_id,
        )

    story_results: list[PublishOutcome] = []
    for story_id in story_ids:
        outcome = await publish_story(session, story_id, client)
        story_results.append(outcome)
        logger.info(
            "Publish story_id=%s outcome=%s instagram_media_id=%s",
            story_id,
            outcome.outcome,
            outcome.instagram_media_id,
        )

    return PublisherRunSummary(
        mode=mode, scheduled_post_results=scheduled_post_results, story_results=story_results
    )


async def _main() -> None:  # pragma: no cover - manual/CLI entrypoint
    from app.core.logging import configure_logging
    from app.db.session import AsyncSessionLocal
    from app.instagram.client import GraphAPIInstagramClient

    configure_logging()
    client = GraphAPIInstagramClient.from_settings()

    async with AsyncSessionLocal() as session:
        summary = await run_publisher(session, client)
        logger.info(
            "Instagram publisher run complete: mode=%s published=%d awaiting_review=%d",
            summary.mode,
            summary.published,
            summary.due_but_awaiting_review,
        )


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
