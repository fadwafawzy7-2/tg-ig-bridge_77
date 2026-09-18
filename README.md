# tg-ig-bridge

## Phase 1 — Foundation

Production-ready project skeleton: configuration, logging, error handling,
database connectivity, migrations, health checks, and tests. No Telegram,
Instagram, or AI logic.

## Phase 2 — Database & Domain Models

Full relational schema for the whole project's data lifecycle: Telegram
ingestion, products, media, captions, scheduling/publishing (posts, reels,
stories), daily limits, analytics, settings, and error logging. Still no
Telegram, Instagram, AI, or scheduler *logic* — this phase is schema only.

## Phase 3 — Telegram Monitor & Source Tracking

Connects to real Telegram channels and durably records what they post.
Dynamic channel management (add/enable/disable), a restart-safe scan
cycle (INITIAL 5-day backfill -> INCREMENTAL -> CATCHUP after downtime,
bounded the same way), and idempotent ingestion into `source_messages` /
`media`. See `app/telegram/` module docstrings for details.

**Explicitly out of scope for this phase** (by design, not oversight):
product parsing/creation, duplicate detection, AI, Instagram publishing,
and any periodic-execution scheduler (cron/APScheduler) — `run_monitor_once()`
is the entrypoint such a scheduler would call later. No product specs are
ever extracted from images — only `raw_text` is captured; media is
recorded as metadata only (file id/type), never downloaded or analyzed.

## Phase 4 — Product Parser & Duplicate Detection

Turns `source_messages` into `products`, using ONLY deterministic
rule-based text parsing (no AI) over `raw_text` — never images. See
`app/parsing/` module docstrings for details. Duplicate prevention works
by checking for an existing product with the same content fingerprint
*before* ever creating a row: a genuine duplicate is linked via
`product_source_messages` instead of creating a second product.

**Explicitly out of scope**: AI, product validation, scheduler, Instagram
publishing (unchanged from Phase 3's list). No DB schema changes were
needed — Phase 2 already had everything this phase uses.

## Phase 5 — AI Caption Generation + Content Validation

Turns a `products` row (status=`PARSED`) into one or more versioned
`captions` rows, and enforces a hard fact-check gate so nothing gets
marked publish-ready unless it is fully traceable to
`source_messages.raw_text`. See `app/ai/` module docstrings for details.

Hard requirements enforced here: the AI is given **only** the product's
`source_messages.raw_text` (never `products.price`, never any other
field); nothing may be invented (sizes, colors, materials, features,
quality, shipping, warranty, discounts); the merchant price must never
appear in an Instagram caption; the AI's role is limited to rephrasing
what's already in the source plus one generic, non-claim CTA. Every
candidate is persisted (never silently discarded); only a caption that
passed validation may be selected, and this is enforced with a DB `CHECK`
constraint, not just application code.

The validator (`app/ai/validator.py`) is deliberately **not** itself an AI
call — it's plain, deterministic Python (substring/regex matching against
a bilingual Arabic/English claim-keyword lexicon plus a numeric-overlap
check), fully unit-testable without a network connection, exactly like
Phase 4's `text_parser.py`.

**DB schema change** (justified in full in `app/models/caption.py`'s
docstring and the migration's docstring): `captions` gained
`validation_status` and `rejection_reason` columns, plus a `CHECK`
constraint that `is_selected` implies `validation_status='PASSED'`. This
was judged necessary because multiple candidate *versions* are stored per
product, so the validation outcome (and, on rejection, why) has to live
per-version — a single product-level field can't express "why was
version 2 rejected" separately from "why was version 3 rejected." Both
columns are nullable/defaulted; no Phase 2/3/4 table, column, or query is
affected.

**Explicitly out of scope for this phase** (Phase 6 territory, not
started here): scheduling captions for publish, Instagram API calls,
image/media generation or selection, and any periodic-execution
scheduler. `generate_pending_captions()` is what a scheduler would call
later, exactly like `parser_runner.process_pending_messages()` before it.

## Stack

- Python 3.12
- FastAPI + Uvicorn
- SQLAlchemy 2.0 (async, `asyncpg`) + Alembic (sync, `psycopg2`) for migrations
- PostgreSQL
- pytest

## Project structure

```
.
├── app/
│   ├── main.py              # FastAPI app entrypoint
│   ├── core/
│   │   ├── config.py         # Settings (env vars) — single source of truth
│   │   ├── logging.py        # Logging configuration
│   │   └── exceptions.py     # Error hierarchy + global exception handlers
│   ├── db/
│   │   ├── base.py           # SQLAlchemy declarative Base
│   │   └── session.py        # Async engine/session, get_db(), DB health check
│   ├── models/                # Phase 2: all domain models (see below)
│   ├── telegram/               # Phase 3: monitor & source tracking (see below)
│   └── api/
│       ├── health.py         # /health/live, /health/ready
│       └── channels.py       # Phase 3: channel management endpoints
├── alembic/                  # Migrations (env.py wired to app settings)
├── tests/                    # pytest suite
├── Dockerfile
├── docker-compose.yml        # app + postgres for local dev
├── requirements.txt
├── requirements-dev.txt
├── .env.example
└── pyproject.toml
```

## Running locally (with Docker — recommended)

```bash
cp .env.example .env
# edit .env if you want non-default values (defaults work out of the box)

docker compose up --build
```

- API: http://localhost:8000
- Liveness: http://localhost:8000/health/live
- Readiness (checks DB): http://localhost:8000/health/ready

## Running locally (without Docker)

Requires a local PostgreSQL instance.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt

cp .env.example .env
# edit .env with your local Postgres credentials

uvicorn app.main:app --reload
```

## Database migrations (Alembic)

Migrations connect using the same settings as the app (`app/core/config.py`),
so there is nothing to configure separately.

```bash
# create a new migration after adding/changing models
alembic revision --autogenerate -m "describe the change"

# apply migrations
alembic upgrade head

# roll back one migration
alembic downgrade -1
```

The Phase 2 migration (`alembic/versions/1e420e94f57e_phase2_domain_models.py`)
creates every table described below, along with all enums, foreign keys,
`CHECK` constraints, and partial unique indexes. It was written by hand
rather than autogenerated (no Postgres instance was reachable in the
environment it was authored in) — run `alembic upgrade head` against a
real database and `pytest` before relying on it in production.

## Tests

```bash
pytest
```

Tests use a separate in-process env (set in `tests/conftest.py`) and never
read your real `.env`. The readiness test tolerates the absence of a real
database connection (asserts on 200 **or** 503) since no Postgres is
guaranteed to be running in a bare test environment. Model/constraint,
Telegram-monitor, and caption-service/runner tests need a real Postgres —
they are skipped automatically (not failed) if one isn't reachable; start
one with `docker compose up -d db` first. `tests/test_scan_window.py`,
`tests/test_ai_validator.py`, `tests/test_ai_prompt_builder.py`,
`tests/test_queue_scoring.py`, `tests/test_queue_eligibility.py`,
`tests/test_analytics_metrics.py`, `tests/test_analytics_performance.py`,
`tests/test_best_time_engine.py`, `tests/test_retry_policy.py`, and
`tests/test_instagram_safety.py` have no DB dependency at all (pure
logic); `tests/test_caption_generator.py` needs no DB either but does
exercise the async `AIClient` protocol via an in-memory fake.
`tests/test_publisher_service.py`/`test_instagram_runner.py` use
`tests.fakes.FakeInstagramClient` (no real Graph API calls, per the
requirement to mock Instagram in tests) but still need a real Postgres.

## Notes for future phases

- Add new domain packages under `app/` (e.g. `app/instagram/`, `app/ai/`),
  each with its own `router.py` (FastAPI `APIRouter`) that imports the
  models it needs from `app/models/`.
- Register new routers in `app/main.py` via `app.include_router(...)`.
- New models go in `app/models/` and must be imported/re-exported from
  `app/models/__init__.py` so Alembic's autogenerate can see them.
- Add new env vars to `app/core/config.py` (`Settings`) and document them in
  `.env.example` — never hardcode secrets in code.
- Product parsing (turning `source_messages.raw_text` into `products` rows)
  was built in Phase 4 (`app/parsing/`); AI captioning + validation was
  built in Phase 5 (`app/ai/`); scoring, eligibility, queueing, and
  scheduling were built in Phase 6 (`app/queue/`); analytics + the
  Best-Time Engine were built in Phase 7 (`app/analytics/`); the actual
  Instagram Graph API publisher (POST/REEL/STORY, AUTO/REVIEW mode) was
  built in Phase 8 (`app/instagram/`). A Telegram review/approval
  dashboard for REVIEW-mode items, and Instagram Insights ingestion to
  populate `analytics` from real metrics, are the next logical phases.

## Domain schema (Phase 2)

All tables live under `app/models/`, one file per entity, sharing
`TimestampMixin` (`created_at`/`updated_at`) and the enums in
`app/models/enums.py`. Full detail is in each model's module docstring;
summary:

| Table | Purpose |
|---|---|
| `channels` | Monitored Telegram channels. Never hard-deleted — disabled via `status`. |
| `scan_state` | One row per channel: restart-safe scan progress (`INITIAL` / `INCREMENTAL` / `CATCHUP`). |
| `source_messages` | Raw ingested Telegram messages — the literal source of truth. Ingestion is idempotent (`unique(channel_id, telegram_message_id)`). |
| `products` | Canonical product entity. Traceable to a specific source message via a real FK, plus explicit snapshot columns (`source_channel_id`, `source_channel_name`, `source_message_id`, `source_message_date`, `source_message_link`). Full 11-state lifecycle enum. `score` (0-100, Phase 6) drives queue ordering. |
| `product_source_messages` | Links a product to *additional* messages it also appeared in (reposts/mirrors), without breaking primary traceability. |
| `media` | Photos/videos, separate from both `products` and any publication table. Carries `telegram_file_unique_id`, `perceptual_hash`, `sha256_hash` for duplicate detection. |
| `captions` | Versioned AI-generated captions per product; at most one `is_selected` per product (DB-enforced), and `is_selected` requires `validation_status='PASSED'` (DB-enforced, Phase 5). |
| `scheduled_posts` | Publish queue for **POST/REEL only** (DB `CHECK` constraint). Strong duplicate prevention: at most one active entry per (product, content_type). Idempotent via `idempotency_key`. |
| `published_posts` | Permanent audit ledger of published posts/reels. |
| `stories` | Separate table for stories — **repeats explicitly allowed**, no duplicate-prevention constraint. |
| `settings` | Generic admin-configurable key/value settings (plain text, not JSON). |
| `daily_limits` | MAXIMUM allowed count per day per content type (`POST`/`REEL`/`STORY`), plus running count. |
| `analytics` | Engagement metric snapshots per day, tied to exactly one of `published_posts` / `stories` (DB `CHECK` constraint). |
| `errors` | System-wide error log across all subsystems (separate from the Foundation's in-request `AppError` handling). |

No JSON/JSONB columns are used anywhere in this schema.

## Telegram Monitor (Phase 3)

`app/telegram/` — see each module's docstring for full detail:

| Module | Responsibility |
|---|---|
| `schemas.py` | Client-agnostic DTOs (`TelegramMessageDTO`, `TelegramMediaDTO`, `TelegramChannelInfo`). |
| `manual_capture.py` | **Live ingestion path.** Builds a `SourceMessage` + `Media` from a message forwarded (or sent directly) to the dashboard bot, using only the Bot API (`getFile`/file download) — no Telethon, no api_id/api_hash, no user session. |
| `client.py` | Legacy/unused by the live runtime. `TelegramClient` protocol + `TelethonClient` (real, MTProto **user session** — would be required for automatic history backfill; the Bot API can't read messages from before the bot joined). Kept for reference/tests only. |
| `channel_service.py` | Dynamic add/enable/disable/list. `add_channel()` is idempotent and never hard-deletes. |
| `ingestion_service.py` | Idempotent `source_messages`/`media` persistence (`INSERT ... ON CONFLICT DO NOTHING`) — original source data is never updated or overwritten. |
| `scan_window.py` | Legacy/unused. Pure INITIAL/INCREMENTAL/CATCHUP decision logic — zero I/O, fully unit-tested. |
| `scan_service.py` | Legacy/unused. One-channel scan orchestration; isolates and logs per-channel failures to the `errors` table without losing already-ingested progress. |
| `monitor_runner.py` | Legacy/unused. Scanned every ACTIVE channel via Telethon. |

Setup: no Telegram API credentials needed at all beyond `TELEGRAM_BOT_TOKEN`
(a normal BotFather token) — see "Dashboard Bot" below. To capture a
product: forward it (or send a photo/video + caption directly) to the
dashboard bot, then tap POST / REEL / STORY on the buttons it replies
with.

## AI Caption Generation + Validation (Phase 5)

`app/ai/` — see each module's docstring for full detail:

| Module | Responsibility |
|---|---|
| `schemas.py` | Plain DTOs (`SourceFacts`, `CaptionCandidate`, `ValidationResult`) — zero DB/HTTP dependency. |
| `claim_lexicon.py` | Arabic/English claim-keyword lists (size/color/material/quality/shipping/warranty/discount) used by the validator. |
| `prompt_builder.py` | Builds the strict system/user prompt from `raw_text` only — pure string assembly, no dependency. |
| `validator.py` | Deterministic (non-AI) fact-check of a caption against its source: price/currency ban, unsupported-number check (matched as whole numeric tokens, not substrings), unsupported-claim-category check. Pure logic, fully unit-tested. |
| `ai_client.py` | `AIClient` protocol + `GroqClient` (real Chat Completions client over `httpx`). |
| `caption_generator.py` | Prompt -> `AIClient` -> parsed `CaptionCandidate` list (robust to markdown-fenced or malformed JSON responses). |
| `caption_service.py` | DB orchestration: generate, validate every candidate, persist all as versioned rows, select the first that passes, advance `products.status` to `VALIDATED`. Isolates per-product failures like `product_service.py` does. |
| `caption_runner.py` | Batch driver over `PARSED` products; `python -m app.ai.caption_runner` to run manually. |

Setup: set `AI_API_KEY` (Groq API key) in `.env`; `AI_MODEL` /
`AI_CAPTION_CANDIDATES` / `AI_CAPTION_MAX_TOKENS` have working defaults.
The real client (`GroqClient`) could not be exercised against the
live Groq API in the environment it was written in (no network
access) — verify it against a real key before relying on it in
production.

## Scoring + Queue + Scheduler (Phase 6)

`app/queue/` — see each module's docstring for full detail. Status flow
(all values already defined in `ProductStatus` since Phase 2 — no new
product status was added):

    VALIDATED -> ELIGIBLE -> QUEUED -> SCHEDULED -> PUBLISHED

| Module | Responsibility |
|---|---|
| `eligibility.py` | Pure eligibility gate (selected+PASSED caption, valid downloaded media, not duplicate/rejected). Zero dependency — no `app.models` import either, so it's testable with plain `python`. |
| `scoring.py` | Pure 0-100 scoring engine (caption 40 + media 30 + price 10 + description 10 + freshness 10). Zero dependency, same rationale. |
| `time_utils.py` | Timezone helpers (stdlib `zoneinfo`, no extra dependency). Zero dependency. |
| `eligibility_service.py` | DB orchestration: VALIDATED -> ELIGIBLE, computes+stores `products.score`. Isolates per-product failures like `caption_service.py`. |
| `queue_service.py` | DB orchestration: ELIGIBLE -> QUEUED, ordered by score. |
| `scheduler.py` | DB orchestration: QUEUED -> SCHEDULED, respecting `daily_limits` as a strict MAXIMUM (never a target — 0 configured or unconfigured both mean "schedule nothing"), DB-enforced idempotency (`scheduled_posts.idempotency_key` + its partial unique index, both from Phase 2), and `Asia/Gaza` timezone throughout. |
| `operations.py` | The five domain operations: `publish_now`, `schedule`, `skip`, `retry_failed`, `reprioritize`. None call the Instagram API. |
| `runner.py` | Batch driver stitching all three automatic stages; `python -m app.queue.runner` to run manually. |

**Schema change**: one nullable `products.score` column (+ CHECK 0-100 +
index) — migration `88c9bd0e38da_phase6_product_score.py`. Justified in
full in the migration's and `product.py`'s docstrings: queue ordering and
the `reprioritize` operation both need a persisted, sortable, manually-
overridable value; nothing else in Phase 6 needed a schema change —
`scheduled_posts.idempotency_key`/partial-unique-index and the
`daily_limits` table already existed from Phase 2 and needed no changes.

Setup: `SCHEDULER_TIMEZONE=Asia/Gaza` and
`SCHEDULER_DEFAULT_CONTENT_TYPE=POST` have working defaults in
`.env.example`. A `daily_limits` row must be explicitly inserted for a
given `(date, content_type)` before the scheduler will schedule anything
for it — an unconfigured day is treated as zero capacity, never as
unlimited (see `scheduler.py`'s docstring).

Explicitly out of scope for this phase (not started): calling the
Instagram API (SCHEDULED -> PUBLISHED is left for a later phase to
execute and confirm), any Telegram UI, any AI involvement, and Best-Time
Learning (slot-time assignment here is a simple fixed daily window spread
evenly by score, not a learned model).

## Analytics + Best-Time Engine (Phase 7)

`app/analytics/` — see each module's docstring for full detail. Reads
ONLY real, already-stored `published_posts` / `stories` (actually
published ones only) joined with their `analytics` snapshots — nothing
here invents a metric or a "best time".

| Module | Responsibility |
|---|---|
| `metrics.py` | Pure: derives a usable engagement rate from real stored numbers (uses stored `engagement_rate` if present, else likes+comments+shares+saves / reach-or-impressions), or `None` if there isn't enough — `None` is never treated as zero. Zero dependency. |
| `performance.py` | Pure: descriptive breakdown by hour / weekday / content type. Reporting, so no minimum-sample gating — every bucket shows its own `sample_count`. Zero dependency. |
| `best_time_engine.py` | Pure: the actual Best-Time recommendation, WITH a minimum-sample confidence gate (`MIN_TOTAL_SAMPLES=5`, `MIN_SAMPLES_PER_HOUR=3`). Below threshold, honestly falls back to the existing default scheduling window instead of claiming a learned "best time" — `is_learned=False` and `notes` spell out why. Zero dependency, no persisted model state: every call recomputes from whatever data is passed in, so it naturally "learns" as more real data accumulates over time. |
| `analytics_service.py` | DB orchestration: loads real data, converts `published_at` to Asia/Gaza local time, uses only the LATEST `analytics` snapshot per post/story (not every snapshot — avoids double-counting/bias toward longer-tracked posts), and feeds the pure modules above. |

**No schema change** — `analytics`/`published_posts`/`stories` already
had everything this phase needed since the Phase 2 migration.

**Read-only with respect to Phase 6**: this phase imports
`app.queue.scheduler`'s `SCHEDULING_WINDOW_START_HOUR`/`_END_HOUR`
constants (for the Best-Time Engine's fallback window) and
`settings.SCHEDULER_TIMEZONE`, but never modifies `app/queue/*` — daily
limits, queue ordering, and scoring behavior are unchanged, and nothing
in this phase is wired into the scheduler's actual slot assignment (that
remains the fixed, simple window from Phase 6 unless a later phase
decides to connect them).

Explicitly out of scope for this phase (not started): actually fetching
metrics from Instagram's API (something else must populate `analytics`
rows — out of scope here), any UI/reporting surface, and wiring the
Best-Time Engine's output into the scheduler's actual slot assignment.

## Instagram Publisher (Phase 8)

`app/instagram/` — see each module's docstring for full detail. Turns a
`scheduled_posts` row (POST/REEL) or a `stories` row into an actual
Instagram publication via the OFFICIAL Meta Graph API Content Publishing
flow — never browser automation, never scraping.

| Module | Responsibility |
|---|---|
| `client.py` | `InstagramClient` protocol + `GraphAPIInstagramClient` (real Graph API client: create container -> poll status -> publish container) + the error hierarchy (`InstagramAuthError`/`InstagramMediaError` permanent, `InstagramRateLimitError`/`InstagramTimeoutError`/`InstagramTransientError` retryable). |
| `retry_policy.py` | Pure: exponential backoff delay + "is this attempt due yet", reusing `scheduled_posts.attempt_count`/`last_attempt_at` — no new column needed. Zero dependency. |
| `media_resolver.py` | Resolves a local `media.file_path` to the PUBLIC URL the Graph API requires — see its docstring for this real API constraint and the honest `ConfigurationError` when `INSTAGRAM_MEDIA_BASE_URL` isn't configured, rather than guessing. |
| `safety.py` | Pure pre-publish safety gate: caption must be selected+PASSED, product must not be duplicate/rejected/skipped, and the EXACT SAME Phase 5 `validate_caption` is re-run against fresh source data as a live merchant-price/unsupported-claim re-check — defense in depth, not a re-decision. Zero dependency. |
| `daily_limit_guard.py` | Concurrency-safe reserve/release of one `daily_limits` slot via a single atomic conditional `UPDATE ... WHERE published_count < max_allowed` — no lock held across the slow network call, yet concurrent publishers can never jointly exceed the MAXIMUM (Postgres serializes the UPDATEs). Reservation is released if the attempt doesn't end in confirmed success. |
| `publisher_service.py` | Orchestrates one scheduled_post/story publish end to end; atomically claims the row (`PENDING`/`SCHEDULED` -> `PROCESSING`) so two workers can never process the same item; isolates one item's failure like every prior phase's service. |
| `runner.py` | Batch driver gated by `INSTAGRAM_PUBLISH_MODE`; `python -m app.instagram.runner` to run manually. |

**AUTO vs REVIEW** (`INSTAGRAM_PUBLISH_MODE`, default `REVIEW`): in
`REVIEW` mode the batch runner does nothing automatically — every due
item is left exactly as Phase 6 left it (`status=SCHEDULED`), no new
status invented, no approval UI built (that's Phase 9's job).
`publish_scheduled_post()`/`publish_story()` remain directly callable by
a human action, an admin endpoint, or a test at any time — REVIEW mode
only gates the automatic batch path in `runner.py`. In `AUTO` mode the
runner publishes every due, eligible item automatically.

**Duplicate/restart/idempotency safety, all DB-level**: `published_posts.scheduled_post_id`
is UNIQUE (Phase 2 — a scheduled post can have at most one publication,
full stop); the atomic claim UPDATE prevents two workers from ever
processing the same row; `daily_limit_guard.py`'s atomic reserve/release
guarantees the daily MAXIMUM holds under retry/restart/concurrency;
failed attempts release their reservation, so `published_count` only
ever reflects confirmed publishes.

**No schema change** — `products`/`media`/`captions`/`scheduled_posts`/
`published_posts`/`stories`/`analytics`/`errors`/`settings`/`daily_limits`
already had every column this phase needed (`scheduled_posts.attempt_count`/
`last_attempt_at`/`last_error`, `published_posts.instagram_media_id`
(unique)/`scheduled_post_id` (unique), `stories.instagram_story_id`
(unique)/`attempt_count`/`last_error`, `daily_limits.published_count`) —
all from the Phase 2 migration.

**Phase 1-7 changes**: only `app/core/config.py` and `.env.example` were
touched, purely additive (new `INSTAGRAM_*`/`SCHEDULER_DEFAULT_CONTENT_TYPE`-
adjacent settings) — no existing setting's default or behavior changed,
no other Phase 1-7 file was modified.

Explicitly out of scope for this phase (not started): Phase 9's
Telegram review/approval dashboard/UI; Best-Time Engine wiring into
scheduling; Instagram Insights/analytics ingestion (Phase 7's `analytics`
table is only ever read from, never written to, by this phase).

