"""
Phase 8 — Instagram Publisher.

Turns a SCHEDULED `scheduled_posts` row (Phase 6's output — image POST or
REEL, with a selected+PASSED caption from Phase 5) into an actual
Instagram publication via the OFFICIAL Meta/Instagram Graph API's
Content Publishing endpoints — no browser automation, no scraping.
Stories (`stories` table, from Phase 2) are published through the same
official API and the same safety/idempotency machinery.

| Module | Responsibility |
|---|---|
| `client.py` | `InstagramClient` Protocol + `GraphAPIInstagramClient`, the real implementation calling the official Graph API Content Publishing endpoints (create container -> poll status -> publish container). Error hierarchy (`InstagramAuthError`, `InstagramMediaError`, `InstagramRateLimitError`, `InstagramTimeoutError`, `InstagramTransientError`, `InstagramAPIError`) classifies failures as retryable or not. |
| `retry_policy.py` | Pure: exponential backoff delay computation + "is this attempt due for retry yet" check. Zero dependency. |
| `media_resolver.py` | Resolves a `Media` row to the public URL the Graph API requires (`image_url`/`video_url` — the real API does not accept local file uploads). See its docstring for an important, honestly-documented limitation. |
| `daily_limit_guard.py` | The concurrency-safe "reserve a slot, publish, confirm or release" mechanism around `daily_limits.published_count` — this is what makes "never exceed the daily MAXIMUM under concurrent/retried/restarted publishing" hold at the database level, not in memory. |
| `publisher_service.py` | Orchestrates ONE scheduled_post (POST/REEL) or ONE story publish end-to-end: safety checks, concurrency-safe state transition, daily-limit reservation, container creation/polling/publish, and recording the result (`published_posts`/`stories` + `products.status`). |
| `runner.py` | Batch driver over due SCHEDULED items, gated by `INSTAGRAM_PUBLISH_MODE` (`REVIEW`/`AUTO` — see its docstring). `python -m app.instagram.runner` to run manually. |

## AUTO vs REVIEW — where the gate lives

`REVIEW` (the default) means a product "stops before Instagram and waits
for Phase 9's approval", per the Phase 8 spec. No new database column or
status was added for this: in `REVIEW` mode, `runner.run_publisher()`
simply does not process anything — every due `SCHEDULED` row is left
exactly as Phase 6 already leaves it (that IS "stopped, waiting"). The
per-item function `publisher_service.publish_scheduled_post()` remains
directly callable regardless of mode — that is the exact hook a future
Phase 9 approval action would call once a human approves an item. `AUTO`
mode means `run_publisher()` calls that same function automatically for
every due item. No Telegram dashboard, approval UI, or notification
system is built here — that is explicitly Phase 9.

## Nothing here changes Phase 6/7 behavior

`daily_limits.published_count` was already present in the schema
(Phase 2) and already read by Phase 6's `scheduler.remaining_capacity()`
as part of consumed capacity (a Phase 6 correctness fix already
anticipated this — see that module's docstring) — Phase 8 is simply the
first phase to actually WRITE to it, exactly as the schema always
intended. No Phase 6/7 file is modified. Queue ordering, scoring, and
analytics are untouched.

Explicitly out of scope for this phase (not started): the Phase 9
approval dashboard/UI, Carousel is implemented in `client.py` (the
official API supports it) but is only used automatically when a product
has more than one valid image — there is no separate content-type/queue
concept for "carousel vs single image", by design (see
`publisher_service.py`).
"""
