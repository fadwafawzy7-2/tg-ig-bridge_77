# GitHub Actions Free Scheduled Runtime

The production mode is one-shot: `.github/workflows/bot.yml` runs every 15 minutes and executes `python -m app.runtime.worker_once`.

Ingestion is manual: forward (or send directly) an item to the dashboard bot and pick POST/REEL/STORY from its buttons — see `app/telegram/manual_capture.py`. There is no Telethon/api_id/api_hash/session dependency anywhere in this runtime.

## Required persistent services

- External PostgreSQL (Supabase Free is supported by using its Postgres connection values).
- An S3-compatible bucket for media. Recommended: **Supabase Storage** (same free account as the database, no credit card required). Cloudflare R2 also works but generally requires a card on file to activate.
- Groq API key.
- Meta Instagram access token + Instagram professional account ID.
- Telegram Bot API token + admin user IDs (this is the *only* Telegram credential needed — a normal BotFather token, not a user session).

## GitHub Secrets

`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_DASHBOARD_ADMIN_IDS`,
`GROQ_API_KEY`, `GROQ_MODEL` (optional),
`INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID`,
`MEDIA_S3_ENDPOINT_URL`, `MEDIA_S3_REGION`, `MEDIA_S3_ACCESS_KEY_ID`,
`MEDIA_S3_SECRET_ACCESS_KEY`, `MEDIA_S3_BUCKET_NAME`, `MEDIA_PUBLIC_BASE_URL`.

The workflow keeps publishing in `REVIEW` until the first real end-to-end test is complete.

## Runtime model

Each run:

1. Processes pending Telegram Dashboard updates from the persistent DB offset — including any newly forwarded/captured items and POST/REEL/STORY button presses.
2. Creates default daily limits (1 Post, 1 Reel, 10 Stories) if today's rows do not exist.
3. Parses pending captured items and links all album media to the correct source/product.
4. Generates and validates Groq-generated captions.
5. Scores and queues products.
6. Schedules each product as exactly the content type its capturer chose (POST/REEL); items marked STORY are excluded from this step and handled by step 7 instead. Items with no explicit choice fall back to: video → Reel (if capacity exists, else Post), photo-only → Post.
7. Creates repeatable Story rows subject to the Story cooldown and daily limit.
8. Publishes due items only when mode is AUTO; REVIEW leaves them for Telegram approval.
9. Purges media/captions for finished products older than `MEDIA_RETENTION_DAYS` (the Product row + its code/channel are kept forever).

No local runner state is required at all. Durable state lives in PostgreSQL and S3-compatible storage.
