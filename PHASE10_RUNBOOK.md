# Phase 10 — Reliability, Security, Testing & Production

## Runtime

- `app`: FastAPI + health + public `/media/...` endpoint.
- `worker`: continuous Telegram → Parser → AI → Queue/Scheduler → Instagram pipeline.
- `dashboard`: Arabic Telegram admin control panel.
- `postgres`: production database.

## Required production values

Set real values in `.env`: PostgreSQL credentials, Telegram API ID/hash + authorized Telethon session, Telegram bot token/admin IDs, Groq API key, Instagram access token/IG user ID, and `INSTAGRAM_MEDIA_BASE_URL` as a public HTTPS origin that serves the same `/app/runtime/media` files.

The production preflight refuses to start the worker when required secrets or the public HTTPS media URL are missing.

## Deployment

1. Create/configure a reverse proxy with HTTPS. Point `INSTAGRAM_MEDIA_BASE_URL` at the public `/media` endpoint.
2. Put the authorized Telethon `.session` file in the `telegram_session` Docker volume (or change the mounted path).
3. Run `alembic upgrade head`.
4. Start `docker compose up -d --build`.
5. Verify `/health/live` and `/health/ready`.
6. Use Telegram `/start` from an admin account and add at least one source channel.
7. Start with `INSTAGRAM_PUBLISH_MODE=REVIEW`, publish one real test manually, then enable AUTO only after the real Instagram E2E check succeeds.

## Verification performed in the development environment

- Python `compileall`: passed.
- Pure/unit tests that do not require PostgreSQL/Telethon live services: passed.
- PostgreSQL-backed tests and live Telegram/Groq/Instagram calls were not executable in the provided sandbox because `asyncpg` and `Telethon` are not installed and external network access is unavailable. The production Docker image installs the pinned dependencies from `requirements.txt`.
