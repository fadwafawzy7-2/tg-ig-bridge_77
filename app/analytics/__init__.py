"""
Phase 7 — Analytics + Best-Time Engine.

Reads ONLY real, already-stored Instagram data — `published_posts` /
`stories` (when they were actually published, and to which content type)
joined with their `analytics` snapshots (impressions/reach/likes/
comments/shares/saves/engagement_rate, captured by a later ingestion
step, out of scope here). Nothing here invents a metric, a "best time",
or a performance claim not backed by a stored number.

| Module | Responsibility |
|---|---|
| `metrics.py` | Pure: derives a usable engagement rate from real stored numbers only, or `None` if there isn't enough to derive one. Zero dependency. |
| `performance.py` | Pure: descriptive breakdown by hour / weekday / content type — reporting, not a recommendation, so no minimum-sample gating. Zero dependency. |
| `best_time_engine.py` | Pure: the actual Best-Time recommendation, WITH a minimum-sample confidence gate — below threshold, honestly falls back to the existing default scheduling window instead of claiming a learned "best time". Zero dependency. |
| `analytics_service.py` | DB orchestration: loads real published_posts/stories/analytics rows, converts `published_at` to Asia/Gaza local time, and feeds the pure modules above. |

This phase is explicitly read-only with respect to Phase 6: it imports
`app.queue.scheduler`'s existing default-window constants (for the
fallback) but never modifies `app/queue/*` — daily limits, queue
ordering, and scoring behavior are unchanged. Nothing here writes to
`scheduled_posts`/`products`/`daily_limits`, and nothing here is wired
into the scheduler's actual slot assignment — that wiring (if ever
desired) is a later phase's decision, not this one's.

Also out of scope for this phase (not started): actually fetching
metrics from Instagram's API (something else populates `analytics`),
and any UI/reporting surface — this package only computes and returns
data structures.
"""
