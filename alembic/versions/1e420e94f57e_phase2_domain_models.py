"""phase2: domain models (channels, source_messages, products, media,
captions, scheduled_posts, published_posts, stories, settings,
daily_limits, analytics, errors, scan_state)

Revision ID: 1e420e94f57e
Revises:
Create Date: 2026-09-02 00:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "1e420e94f57e"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Native PostgreSQL enum types.
#
# Each is created exactly once (checkfirst) before any table references it;
# every column definition below then uses create_type=False so Postgres
# doesn't try (and fail) to create the same type twice within one migration.
# ---------------------------------------------------------------------------

channel_status = postgresql.ENUM(
    "ACTIVE", "DISABLED", "ARCHIVED", name="channel_status"
)
scan_phase = postgresql.ENUM(
    "INITIAL", "INCREMENTAL", "CATCHUP", name="scan_phase"
)
source_message_status = postgresql.ENUM(
    "PENDING", "PROCESSED", "IGNORED", "FAILED", name="source_message_status"
)
product_status = postgresql.ENUM(
    "DISCOVERED",
    "PARSED",
    "VALIDATED",
    "ELIGIBLE",
    "QUEUED",
    "SCHEDULED",
    "PUBLISHED",
    "SKIPPED",
    "DUPLICATE",
    "REJECTED",
    "FAILED",
    name="product_status",
)
media_type = postgresql.ENUM(
    "PHOTO", "VIDEO", "ANIMATION", "DOCUMENT", name="media_type"
)
content_type = postgresql.ENUM("POST", "REEL", "STORY", name="content_type")
post_publish_status = postgresql.ENUM(
    "PENDING",
    "SCHEDULED",
    "PROCESSING",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
    name="post_publish_status",
)
published_content_status = postgresql.ENUM(
    "LIVE", "DELETED", "ARCHIVED", name="published_content_status"
)
story_status = postgresql.ENUM(
    "PENDING",
    "SCHEDULED",
    "PROCESSING",
    "PUBLISHED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
    name="story_status",
)
error_severity = postgresql.ENUM(
    "INFO", "WARNING", "ERROR", "CRITICAL", name="error_severity"
)

ALL_ENUMS = [
    channel_status,
    scan_phase,
    source_message_status,
    product_status,
    media_type,
    content_type,
    post_publish_status,
    published_content_status,
    story_status,
    error_severity,
]


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in ALL_ENUMS:
        enum_type.create(bind, checkfirst=True)

    # ------------------------------------------------------------------
    # channels
    # ------------------------------------------------------------------
    op.create_table(
        "channels",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_username", sa.String(length=255), nullable=True),
        sa.Column("channel_title", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ACTIVE", "DISABLED", "ARCHIVED", name="channel_status", create_type=False
            ),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("notes", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channels"),
    )
    op.create_index(
        "ix_channels_telegram_channel_id", "channels", ["telegram_channel_id"], unique=True
    )

    # ------------------------------------------------------------------
    # source_messages
    # ------------------------------------------------------------------
    op.create_table(
        "source_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_link", sa.String(length=500), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("has_media", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("media_group_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "PROCESSED",
                "IGNORED",
                "FAILED",
                name="source_message_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_messages"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name="fk_source_messages_channel_id_channels",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "channel_id", "telegram_message_id", name="uq_source_messages_channel_message"
        ),
    )
    op.create_index("ix_source_messages_channel_id", "source_messages", ["channel_id"])
    op.create_index("ix_source_messages_message_date", "source_messages", ["message_date"])
    op.create_index("ix_source_messages_media_group_id", "source_messages", ["media_group_id"])

    # ------------------------------------------------------------------
    # products
    # ------------------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("primary_source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("source_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("source_channel_name", sa.String(length=500), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_message_link", sa.String(length=500), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DISCOVERED",
                "PARSED",
                "VALIDATED",
                "ELIGIBLE",
                "QUEUED",
                "SCHEDULED",
                "PUBLISHED",
                "SKIPPED",
                "DUPLICATE",
                "REJECTED",
                "FAILED",
                name="product_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DISCOVERED",
        ),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("is_duplicate_of", sa.BigInteger(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.ForeignKeyConstraint(
            ["primary_source_message_id"],
            ["source_messages.id"],
            name="fk_products_primary_source_message_id_source_messages",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name="fk_products_channel_id_channels",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["is_duplicate_of"],
            ["products.id"],
            name="fk_products_is_duplicate_of_products",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_products_primary_source_message_id", "products", ["primary_source_message_id"]
    )
    op.create_index("ix_products_channel_id", "products", ["channel_id"])
    op.create_index("ix_products_status", "products", ["status"])
    op.create_index("ix_products_content_hash", "products", ["content_hash"])
    op.create_index("ix_products_is_duplicate_of", "products", ["is_duplicate_of"])

    # ------------------------------------------------------------------
    # scan_state
    # ------------------------------------------------------------------
    op.create_table(
        "scan_state",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "scan_phase",
            postgresql.ENUM(
                "INITIAL", "INCREMENTAL", "CATCHUP", name="scan_phase", create_type=False
            ),
            nullable=False,
            server_default="INITIAL",
        ),
        sa.Column("last_processed_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_processed_message_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_scan_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("initial_scan_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_state"),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name="fk_scan_state_channel_id_channels",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("channel_id", name="uq_scan_state_channel_id"),
    )

    # ------------------------------------------------------------------
    # product_source_messages
    # ------------------------------------------------------------------
    op.create_table(
        "product_source_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_product_source_messages"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_source_messages_product_id_products",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["source_messages.id"],
            name="fk_product_source_messages_source_message_id_source_messages",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "product_id", "source_message_id", name="uq_product_source_messages_pair"
        ),
    )
    op.create_index(
        "ix_product_source_messages_source_message_id",
        "product_source_messages",
        ["source_message_id"],
    )

    # ------------------------------------------------------------------
    # media
    # ------------------------------------------------------------------
    op.create_table(
        "media",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "media_type",
            postgresql.ENUM(
                "PHOTO", "VIDEO", "ANIMATION", "DOCUMENT", name="media_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.String(length=1000), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("perceptual_hash", sa.String(length=64), nullable=True),
        sa.Column("sha256_hash", sa.String(length=64), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media"),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["source_messages.id"],
            name="fk_media_source_message_id_source_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_media_product_id_products",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "source_message_id", "telegram_file_id", name="uq_media_source_message_file"
        ),
    )
    op.create_index("ix_media_source_message_id", "media", ["source_message_id"])
    op.create_index("ix_media_product_id", "media", ["product_id"])
    op.create_index(
        "ix_media_telegram_file_unique_id", "media", ["telegram_file_unique_id"]
    )
    op.create_index("ix_media_perceptual_hash", "media", ["perceptual_hash"])
    op.create_index("ix_media_sha256_hash", "media", ["sha256_hash"])

    # ------------------------------------------------------------------
    # captions
    # ------------------------------------------------------------------
    op.create_table(
        "captions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("generated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_captions"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_captions_product_id_products",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_captions_product_id", "captions", ["product_id"])
    # At most one selected caption per product (partial unique index).
    op.create_index(
        "uq_captions_one_selected_per_product",
        "captions",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_selected = true"),
    )

    # ------------------------------------------------------------------
    # scheduled_posts (POST / REEL only)
    # ------------------------------------------------------------------
    op.create_table(
        "scheduled_posts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "content_type",
            postgresql.ENUM("POST", "REEL", "STORY", name="content_type", create_type=False),
            nullable=False,
        ),
        sa.Column("caption_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "SCHEDULED",
                "PROCESSING",
                "PUBLISHED",
                "FAILED",
                "CANCELLED",
                name="post_publish_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scheduled_posts"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_scheduled_posts_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["caption_id"],
            ["captions.id"],
            name="fk_scheduled_posts_caption_id_captions",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_scheduled_posts_idempotency_key"),
        sa.CheckConstraint(
            "content_type IN ('POST', 'REEL')", name="ck_scheduled_posts_content_type"
        ),
    )
    op.create_index("ix_scheduled_posts_product_id", "scheduled_posts", ["product_id"])
    op.create_index("ix_scheduled_posts_status", "scheduled_posts", ["status"])
    op.create_index("ix_scheduled_posts_scheduled_for", "scheduled_posts", ["scheduled_for"])
    # Strong duplicate prevention for posts/reels: at most one ACTIVE
    # (not yet published/failed/cancelled) scheduled post per product per
    # content type.
    op.create_index(
        "uq_scheduled_posts_active_product_content_type",
        "scheduled_posts",
        ["product_id", "content_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'SCHEDULED', 'PROCESSING')"),
    )

    # ------------------------------------------------------------------
    # published_posts (POST / REEL only)
    # ------------------------------------------------------------------
    op.create_table(
        "published_posts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("scheduled_post_id", sa.BigInteger(), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("caption_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "content_type",
            postgresql.ENUM("POST", "REEL", "STORY", name="content_type", create_type=False),
            nullable=False,
        ),
        sa.Column("instagram_media_id", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "LIVE", "DELETED", "ARCHIVED", name="published_content_status", create_type=False
            ),
            nullable=False,
            server_default="LIVE",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_published_posts"),
        sa.ForeignKeyConstraint(
            ["scheduled_post_id"],
            ["scheduled_posts.id"],
            name="fk_published_posts_scheduled_post_id_scheduled_posts",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_published_posts_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["caption_id"],
            ["captions.id"],
            name="fk_published_posts_caption_id_captions",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "scheduled_post_id", name="uq_published_posts_scheduled_post_id"
        ),
        sa.CheckConstraint(
            "content_type IN ('POST', 'REEL')", name="ck_published_posts_content_type"
        ),
    )
    op.create_index("ix_published_posts_product_id", "published_posts", ["product_id"])
    op.create_index("ix_published_posts_published_at", "published_posts", ["published_at"])
    op.create_index("ix_published_posts_content_type", "published_posts", ["content_type"])
    op.create_index(
        "uq_published_posts_instagram_media_id",
        "published_posts",
        ["instagram_media_id"],
        unique=True,
        postgresql_where=sa.text("instagram_media_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # stories (no duplicate-prevention constraint - repeats are allowed)
    # ------------------------------------------------------------------
    op.create_table(
        "stories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("media_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "SCHEDULED",
                "PROCESSING",
                "PUBLISHED",
                "FAILED",
                "CANCELLED",
                "EXPIRED",
                name="story_status",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instagram_story_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stories"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_stories_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"], ["media.id"], name="fk_stories_media_id_media", ondelete="SET NULL"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_stories_idempotency_key"),
    )
    op.create_index("ix_stories_product_id", "stories", ["product_id"])
    op.create_index("ix_stories_status", "stories", ["status"])
    op.create_index("ix_stories_scheduled_for", "stories", ["scheduled_for"])
    op.create_index("ix_stories_published_at", "stories", ["published_at"])
    op.create_index(
        "uq_stories_instagram_story_id",
        "stories",
        ["instagram_story_id"],
        unique=True,
        postgresql_where=sa.text("instagram_story_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    op.create_table(
        "settings",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_settings"),
        sa.UniqueConstraint("key", name="uq_settings_key"),
    )

    # ------------------------------------------------------------------
    # daily_limits
    # ------------------------------------------------------------------
    op.create_table(
        "daily_limits",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column(
            "content_type",
            postgresql.ENUM("POST", "REEL", "STORY", name="content_type", create_type=False),
            nullable=False,
        ),
        sa.Column("max_allowed", sa.Integer(), nullable=False),
        sa.Column("published_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_daily_limits"),
        sa.UniqueConstraint("date", "content_type", name="uq_daily_limits_date_content_type"),
    )

    # ------------------------------------------------------------------
    # analytics
    # ------------------------------------------------------------------
    op.create_table(
        "analytics",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("published_post_id", sa.BigInteger(), nullable=True),
        sa.Column("story_id", sa.BigInteger(), nullable=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=True),
        sa.Column("reach", sa.Integer(), nullable=True),
        sa.Column("likes", sa.Integer(), nullable=True),
        sa.Column("comments", sa.Integer(), nullable=True),
        sa.Column("shares", sa.Integer(), nullable=True),
        sa.Column("saves", sa.Integer(), nullable=True),
        sa.Column("engagement_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analytics"),
        sa.ForeignKeyConstraint(
            ["published_post_id"],
            ["published_posts.id"],
            name="fk_analytics_published_post_id_published_posts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["story_id"], ["stories.id"], name="fk_analytics_story_id_stories", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "(published_post_id IS NOT NULL)::int + (story_id IS NOT NULL)::int = 1",
            name="ck_analytics_exactly_one_parent",
        ),
    )
    op.create_index("ix_analytics_metric_date", "analytics", ["metric_date"])
    op.create_index(
        "uq_analytics_published_post_metric_date",
        "analytics",
        ["published_post_id", "metric_date"],
        unique=True,
        postgresql_where=sa.text("published_post_id IS NOT NULL"),
    )
    op.create_index(
        "uq_analytics_story_metric_date",
        "analytics",
        ["story_id", "metric_date"],
        unique=True,
        postgresql_where=sa.text("story_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # errors
    # ------------------------------------------------------------------
    op.create_table(
        "errors",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM(
                "INFO", "WARNING", "ERROR", "CRITICAL", name="error_severity", create_type=False
            ),
            nullable=False,
            server_default="ERROR",
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("source_message_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_errors"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_errors_product_id_products",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["channels.id"],
            name="fk_errors_channel_id_channels",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["source_messages.id"],
            name="fk_errors_source_message_id_source_messages",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_errors_source", "errors", ["source"])
    op.create_index("ix_errors_severity", "errors", ["severity"])
    op.create_index("ix_errors_resolved", "errors", ["resolved"])


def downgrade() -> None:
    op.drop_table("errors")
    op.drop_table("analytics")
    op.drop_table("daily_limits")
    op.drop_table("settings")
    op.drop_table("stories")
    op.drop_table("published_posts")
    op.drop_table("scheduled_posts")
    op.drop_table("captions")
    op.drop_table("media")
    op.drop_table("product_source_messages")
    op.drop_table("scan_state")
    op.drop_table("products")
    op.drop_table("source_messages")
    op.drop_table("channels")

    bind = op.get_bind()
    for enum_type in reversed(ALL_ENUMS):
        enum_type.drop(bind, checkfirst=True)
