"""
Shared enums for the domain model layer.

Each enum maps 1:1 to a native PostgreSQL ENUM type (created by the Phase 2
migration). Using native enums instead of free-text columns gives us
DB-level validation of allowed values in addition to type-checked Python
code.

IMPORTANT: values are the exact strings stored in the database. Do not
reorder/rename members without a migration to match.
"""

from enum import Enum


class ChannelStatus(str, Enum):
    """Lifecycle of a monitored Telegram channel."""

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class ScanPhase(str, Enum):
    """Where a channel's scanner currently is in its scan lifecycle.

    INITIAL    -> first-ever scan of a newly added channel, bounded to the
                  last N days (business rule enforced in application code,
                  not the DB).
    INCREMENTAL-> normal steady-state monitoring, resuming from
                  `scan_state.last_processed_message_id`.
    CATCHUP    -> the bot was offline for a while; resuming from where it
                  left off, bounded to the same lookback window as INITIAL.
    """

    INITIAL = "INITIAL"
    INCREMENTAL = "INCREMENTAL"
    CATCHUP = "CATCHUP"


class SourceMessageStatus(str, Enum):
    """Whether a raw ingested Telegram message has been parsed yet."""

    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    IGNORED = "IGNORED"
    FAILED = "FAILED"


class ProductStatus(str, Enum):
    """Full product lifecycle, exactly as specified for Phase 2."""

    DISCOVERED = "DISCOVERED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    ELIGIBLE = "ELIGIBLE"
    QUEUED = "QUEUED"
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    SKIPPED = "SKIPPED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class MediaType(str, Enum):
    PHOTO = "PHOTO"
    VIDEO = "VIDEO"
    ANIMATION = "ANIMATION"
    DOCUMENT = "DOCUMENT"


class ContentType(str, Enum):
    """Shared across scheduled_posts / published_posts / daily_limits.

    scheduled_posts and published_posts are restricted (via a CHECK
    constraint) to POST and REEL only — stories have their own dedicated
    table because their business rules differ (repeats allowed, no strong
    duplicate prevention, they expire). daily_limits tracks all three.
    """

    POST = "POST"
    REEL = "REEL"
    STORY = "STORY"


class PostPublishStatus(str, Enum):
    """Per-attempt publish lifecycle for a scheduled post/reel."""

    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PublishedContentStatus(str, Enum):
    """Post-publish state of a live Instagram post/reel."""

    LIVE = "LIVE"
    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"


class StoryStatus(str, Enum):
    PENDING = "PENDING"
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ErrorSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class CaptionValidationStatus(str, Enum):
    """Per-caption-version outcome of the Phase 5 fact-check gate.

    PENDING  -> generated but not yet validated (transient; a caption row
                should never be committed in this state in practice, but
                it exists so the column has a safe non-nullable default).
    PASSED   -> validated against `source_messages.raw_text`: every claim
                is traceable to the source and the merchant price does not
                appear. Only a PASSED caption may have `is_selected=True`
                (enforced by a DB CHECK constraint — see Caption model).
    REJECTED -> failed validation; `rejection_reason` explains why. Kept as
                a row (not deleted) for audit/debugging of what the AI
                produced and why it was refused.
    """

    PENDING = "PENDING"
    PASSED = "PASSED"
    REJECTED = "REJECTED"
