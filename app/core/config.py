"""
Centralized configuration management.

All environment-driven settings live here. Nothing else in the codebase
should call `os.environ` directly — import `settings` from this module
instead, so configuration stays in one place and stays testable.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # allow future phases to add vars without breaking this phase
    )

    # --- Application ---
    APP_NAME: str = "tg-ig-bridge"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # --- API server ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # --- PostgreSQL ---
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "tg_ig_bridge"
    POSTGRES_SSL_MODE: str = "disable"

    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_ECHO: bool = False

    # --- Telegram Dashboard (Phase 9) ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_DASHBOARD_ADMIN_IDS: str = ""

    # --- Telegram (Phase 3) ---
    # LEGACY / UNUSED BY THE LIVE RUNTIME: these were for Telethon-based
    # channel scanning. app/runtime/worker.py and worker_once.py no longer
    # use them — ingestion is now manual, via the dashboard bot's
    # forward-to-capture flow (app/telegram/manual_capture.py), which only
    # needs TELEGRAM_BOT_TOKEN above. Kept only so app/telegram/client.py
    # and its tests still work standalone if needed later.
    # A user session (MTProto, via Telethon) would be required — not a bot
    # token — because only a user/userbot session can read a channel's
    # message HISTORY. The Bot API cannot fetch messages sent before the
    # bot joined a channel.
    TELEGRAM_API_ID: int = 0
    TELEGRAM_API_HASH: str = ""
    TELEGRAM_SESSION_NAME: str = "tg_ig_bridge"
    WORKER_INTERVAL_SECONDS: int = 900
    MEDIA_STORAGE_DIR: str = "./runtime/media"
    MEDIA_STORAGE_BACKEND: str = "local"
    # Days to keep media files (photos/videos) and generated captions for a
    # product after it reaches a terminal status (PUBLISHED, REJECTED,
    # DUPLICATE, SKIPPED, FAILED). After this many days, the heavy
    # attachments are deleted to save storage — but the Product row itself
    # (product_code, source_channel_name, source_message_id, title, ...) is
    # never deleted, so the customer-facing "send me the code" lookup in
    # the dashboard bot keeps working forever. Set to 0 to disable purging.
    MEDIA_RETENTION_DAYS: int = 5
    # S3-compatible object storage for persistent media (photos/videos)
    # across ephemeral GitHub Actions runners. Works with any S3-compatible
    # provider — Supabase Storage (no credit card required), Cloudflare R2,
    # AWS S3, Backblaze B2, etc. — just point these at that provider.
    MEDIA_S3_ENDPOINT_URL: str = ""
    MEDIA_S3_REGION: str = "auto"
    MEDIA_S3_ACCESS_KEY_ID: str = ""
    MEDIA_S3_SECRET_ACCESS_KEY: str = ""
    MEDIA_S3_BUCKET_NAME: str = ""
    # Public HTTPS URL prefix used to build a link Instagram can fetch the
    # file from, e.g. https://<project_ref>.supabase.co/storage/v1/object/public/<bucket>
    MEDIA_PUBLIC_BASE_URL: str = ""
    TELEGRAM_SESSION_B64: str = ""
    DEFAULT_DAILY_POST_LIMIT: int = 1
    DEFAULT_DAILY_REEL_LIMIT: int = 1
    DEFAULT_DAILY_STORY_LIMIT: int = 10
    STORY_COOLDOWN_HOURS: int = 24

    # How many days of history to backfill on a channel's first-ever scan,
    # and the ceiling for a catch-up scan after downtime — never more than
    # this regardless of how long the bot was offline.
    TELEGRAM_INITIAL_SCAN_DAYS: int = 5

    # If more than this many minutes have passed since the last successful
    # scan of a channel, the next scan is treated as CATCHUP (bounded to
    # TELEGRAM_INITIAL_SCAN_DAYS) instead of plain INCREMENTAL.
    TELEGRAM_CATCHUP_GAP_MINUTES: int = 30

    # --- AI captioning (Phase 5) ---
    # Groq API (OpenAI-compatible Chat Completions). AI_API_KEY intentionally
    # has no default — the real client refuses to run without one (see
    # app/ai/ai_client.py) rather than silently no-op or fall back to a
    # placeholder key.
    AI_API_KEY: str = ""
    AI_MODEL: str = "llama-3.3-70b-versatile"
    AI_API_BASE_URL: str = "https://api.groq.com/openai/v1"
    AI_API_VERSION: str = ""
    AI_REQUEST_TIMEOUT_SECONDS: float = 30.0

    # How many candidate captions to generate per product per run. Several
    # are generated because validation may reject some (an unsupported
    # claim slips in, a stray number, etc.) — see app/ai/validator.py.
    AI_CAPTION_CANDIDATES: int = 3
    AI_CAPTION_MAX_TOKENS: int = 600

    # Instagram's own hard caption limit.
    AI_MAX_CAPTION_LENGTH: int = 2200
    AI_MIN_CAPTION_LENGTH: int = 10

    # --- Scoring + Queue + Scheduler (Phase 6) ---
    # All scheduling decisions (which calendar day/time a slot falls on,
    # what "today" means for daily_limits lookups) are made in this
    # timezone via the stdlib `zoneinfo` module — no extra dependency.
    SCHEDULER_TIMEZONE: str = "Asia/Gaza"

    # Content type the batch scheduler assigns when creating scheduled_posts
    # rows. Phase 6 does not yet decide POST vs REEL vs STORY per product
    # (that judgment call — e.g. based on media type/count — is future
    # work); everything the scheduler queues in this phase is a POST.
    # `app/queue/operations.py` domain operations (`schedule`,
    # `publish_now`) accept an explicit content_type override per call.
    SCHEDULER_DEFAULT_CONTENT_TYPE: str = "POST"

    # --- Instagram Publisher (Phase 8) ---
    # Official Meta Graph API only — no browser automation, no scraping.
    # INSTAGRAM_ACCESS_TOKEN intentionally has no default: the real client
    # refuses to run without one (see app/instagram/client.py), same
    # convention as AI_API_KEY.
    INSTAGRAM_ACCESS_TOKEN: str = ""
    # The IG User ID (Business/Creator account) to publish as.
    INSTAGRAM_BUSINESS_ACCOUNT_ID: str = ""
    INSTAGRAM_GRAPH_API_BASE_URL: str = "https://graph.facebook.com/v23.0"
    INSTAGRAM_REQUEST_TIMEOUT_SECONDS: float = 30.0

    # Container processing can take a few seconds for images, longer for
    # video/reels — polled at this interval, up to this many attempts,
    # before the publish attempt is treated as a timeout (transient,
    # retryable).
    INSTAGRAM_CONTAINER_POLL_INTERVAL_SECONDS: float = 2.0
    INSTAGRAM_CONTAINER_POLL_MAX_ATTEMPTS: int = 15

    # Retry policy for TRANSIENT failures only (rate limits, timeouts,
    # network errors) — see app/instagram/retry_policy.py. Permanent
    # failures (auth, invalid media) are never retried regardless of
    # these settings.
    INSTAGRAM_MAX_PUBLISH_ATTEMPTS: int = 5
    INSTAGRAM_RETRY_BASE_DELAY_SECONDS: float = 30.0
    INSTAGRAM_RETRY_MAX_DELAY_SECONDS: float = 3600.0

    # The Graph API's image_url/video_url parameters require a PUBLICLY
    # reachable HTTPS URL — it cannot accept a local file. This project
    # only stores media locally (media.file_path) so far (no CDN/upload
    # phase exists yet). If set, a media file's public URL is built as
    # f"{INSTAGRAM_MEDIA_BASE_URL}/{file_path}"; if empty, publishing
    # fails with a clear ConfigurationError instead of guessing a URL
    # that wouldn't actually work — see app/instagram/media_resolver.py.
    INSTAGRAM_MEDIA_BASE_URL: str = ""

    # REVIEW: nothing is auto-published — every due SCHEDULED item is left
    # exactly as Phase 6 leaves it, waiting for a human/Phase-9 approval
    # action to call the publish function directly.
    # AUTO: the batch runner publishes every due, eligible item automatically.
    INSTAGRAM_PUBLISH_MODE: Literal["REVIEW", "AUTO"] = "REVIEW"

    @property
    def DATABASE_URL(self) -> str:
        """Async SQLAlchemy URL with safe credential escaping."""
        from sqlalchemy.engine import URL
        query = {"ssl": "require"} if self.POSTGRES_SSL_MODE == "require" else {}
        return URL.create(
            "postgresql+asyncpg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
            query=query,
        ).render_as_string(hide_password=False)

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """Sync Alembic URL with safe credential escaping."""
        from sqlalchemy.engine import URL
        query = {"sslmode": "require"} if self.POSTGRES_SSL_MODE == "require" else {}
        return URL.create(
            "postgresql+psycopg2",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD,
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
            query=query,
        ).render_as_string(hide_password=False)

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor — env is read once per process."""
    return Settings()


settings = get_settings()
