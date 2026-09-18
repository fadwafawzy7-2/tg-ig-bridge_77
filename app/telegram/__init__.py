"""
Phase 3 — Telegram Monitor & Source Tracking.

- `schemas.py`          — client-agnostic DTOs (TelegramMessageDTO, ...)
- `client.py`            — TelegramClient protocol + TelethonClient impl
- `channel_service.py`   — dynamic channel add/enable/disable/list
- `ingestion_service.py` — idempotent source_messages/media persistence
- `scan_window.py`       — pure INITIAL/INCREMENTAL/CATCHUP decision logic
- `scan_service.py`      — one-channel scan orchestration
- `monitor_runner.py`    — scans every ACTIVE channel; CLI entrypoint
- `manual_capture.py`    — CURRENT ingestion path: builds a SourceMessage
  from a message forwarded to the dashboard bot (Bot API only, no
  Telethon/api_id/api_hash). See its own module docstring for why.

NOTE: `client.py`/`monitor_runner.py`/`scan_service.py`/`scan_window.py`
require a Telethon user session and are kept for reference/tests only —
`app/runtime/worker.py` and `app/runtime/worker_once.py` no longer call
them. `manual_capture.py` is the ingestion path actually used today.

No product parsing, duplicate detection, AI, or publishing logic lives
here — see the Phase 3 summary for exact scope boundaries.
"""
