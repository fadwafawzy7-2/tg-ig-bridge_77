"""
Phase 6 — Scoring, Eligibility, Queue, and Scheduler.

Turns a VALIDATED product (Phase 5's output: a selected caption that
passed the fact-check gate) into a scheduled, publish-ready
`scheduled_posts` row, without ever calling Instagram, without AI, and
without inventing any content — see each module's docstring for detail.

Status flow used throughout this package (all values already defined in
`app.models.enums.ProductStatus` — no new product status was added):

    VALIDATED -> ELIGIBLE -> QUEUED -> SCHEDULED -> PUBLISHED

| Module | Responsibility |
|---|---|
| `eligibility.py` | Pure gate: does this product qualify for ELIGIBLE? Zero dependency. |
| `scoring.py` | Pure 0-100 scoring engine, used to order the queue. Zero dependency. |
| `eligibility_service.py` | DB orchestration: VALIDATED -> ELIGIBLE, computes+stores `products.score`. |
| `queue_service.py` | DB orchestration: ELIGIBLE -> QUEUED. |
| `scheduler.py` | DB orchestration: QUEUED -> SCHEDULED, respecting `daily_limits` (a MAXIMUM, never a target) and DB-level idempotency (`scheduled_posts.idempotency_key` + its partial unique index — both from the Phase 2 migration, unchanged here). Timezone-aware (`Asia/Gaza` by default) via the stdlib `zoneinfo`. |
| `operations.py` | The five explicit domain operations: `publish_now`, `schedule`, `skip`, `retry_failed`, `reprioritize`. |
| `runner.py` | Batch driver stitching eligibility -> queue -> scheduler; `python -m app.queue.runner`. |

Explicitly OUT of scope for this phase (not started): actually calling
the Instagram API (SCHEDULED -> PUBLISHED is left for a later phase to
execute and confirm), any Telegram UI, any AI involvement, and Best-Time
learning (the scheduler here uses a simple deterministic slot-assignment
rule, not a learned model — see `scheduler.py`).
"""
