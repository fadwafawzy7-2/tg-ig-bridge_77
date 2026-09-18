"""
An in-memory fake satisfying `app.telegram.client.TelegramClient`, so
`scan_service`/`monitor_runner` tests never touch the real Telegram
network. Messages are pre-loaded by the test; `iter_messages` filters them
exactly the way a real client would (by `min_id` / `since`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from app.telegram.schemas import TelegramChannelInfo, TelegramMessageDTO


class FakeTelegramClient:
    def __init__(self, messages_by_channel: dict[int, list[TelegramMessageDTO]] | None = None):
        self.messages_by_channel = messages_by_channel or {}
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def resolve_channel(self, identifier: str | int) -> TelegramChannelInfo:
        return TelegramChannelInfo(
            telegram_channel_id=int(identifier) if isinstance(identifier, int) else 1,
            channel_username=None,
            channel_title="Fake Channel",
        )

    async def iter_messages(
        self,
        telegram_channel_id: int,
        *,
        min_id: int | None = None,
        since: datetime | None = None,
    ) -> AsyncIterator[TelegramMessageDTO]:
        messages = self.messages_by_channel.get(telegram_channel_id, [])
        for message in sorted(messages, key=lambda m: m.telegram_message_id):
            if min_id is not None and message.telegram_message_id <= min_id:
                continue
            if since is not None and message.message_date < since:
                continue
            yield message


def make_message(
    telegram_message_id: int,
    message_date: datetime,
    raw_text: str | None = "Sample text",
    media_group_id: int | None = None,
) -> TelegramMessageDTO:
    return TelegramMessageDTO(
        telegram_message_id=telegram_message_id,
        message_date=message_date,
        raw_text=raw_text,
        media_group_id=media_group_id,
    )


class FlakyFakeTelegramClient(FakeTelegramClient):
    """Like `FakeTelegramClient`, but raises after yielding a fixed number
    of messages — used to test that a scan failure partway through leaves
    `scan_state` unadvanced (so the next run safely retries/resumes)."""

    def __init__(
        self,
        messages_by_channel: dict[int, list[TelegramMessageDTO]] | None,
        fail_after: int,
    ):
        super().__init__(messages_by_channel)
        self.fail_after = fail_after

    async def iter_messages(
        self,
        telegram_channel_id: int,
        *,
        min_id: int | None = None,
        since: datetime | None = None,
    ) -> AsyncIterator[TelegramMessageDTO]:
        count = 0
        async for message in super().iter_messages(
            telegram_channel_id, min_id=min_id, since=since
        ):
            if count >= self.fail_after:
                raise RuntimeError("Simulated Telegram API failure")
            yield message
            count += 1


class FakeAIClient:
    """In-memory fake satisfying `app.ai.ai_client.AIClient` (Phase 5), so
    caption generation tests never touch the real Groq API.

    `responses` is a queue of raw completion strings (each already in the
    "JSON array of caption strings" shape the real prompt asks for, or
    deliberately malformed to test the parser's fallback) returned in
    order, one per `complete()` call. If the queue is exhausted, the last
    response is repeated. `calls` records every `(system_prompt,
    user_prompt)` pair for assertions about what was actually sent to the
    model (e.g. "the price never appears in the prompt").
    """

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        self.calls.append((system_prompt, user_prompt))
        if not self.responses:
            raise RuntimeError("FakeAIClient has no more queued responses")
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


class FailingFakeAIClient:
    """`AIClient` fake that always raises `AIProviderError` — used to test
    that an AI call failure is isolated (logged, product left unchanged)
    rather than crashing a batch run."""

    async def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        from app.ai.ai_client import AIProviderError

        raise AIProviderError("simulated AI provider failure")


class FakeInstagramClient:
    """In-memory fake satisfying `app.instagram.client.InstagramClient`
    (Phase 8), so publisher tests never touch the real Graph API.

    Every successful `create_container` call gets an auto-incrementing
    container id and is immediately "FINISHED" (no real processing
    delay) unless `stuck_in_progress_containers` names it explicitly
    (used to test the polling-timeout path). `publish_container` returns
    an auto-incrementing fake Instagram media id. `fail_with`, if set, is
    raised on the NEXT call to `create_container` or `publish_container`
    (then cleared) — used to script a single failure into an otherwise
    successful flow (e.g. testing retry-then-succeed).
    """

    def __init__(self) -> None:
        self.created_containers: list[dict] = []
        self.published_container_ids: list[str] = []
        self.stuck_in_progress_containers: set[str] = set()
        self.fail_with: Exception | None = None
        self._next_container_id = 1
        self._next_media_id = 1

    def _next_id(self, prefix: str, counter_attr: str) -> str:
        value = getattr(self, counter_attr)
        setattr(self, counter_attr, value + 1)
        return f"{prefix}-{value}"

    async def create_container(
        self,
        *,
        media_url: str,
        media_type: str | None = None,
        caption: str | None = None,
        is_carousel_item: bool = False,
        children: list[str] | None = None,
    ) -> str:
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        container_id = self._next_id("container", "_next_container_id")
        self.created_containers.append(
            {
                "id": container_id,
                "media_url": media_url,
                "media_type": media_type,
                "caption": caption,
                "is_carousel_item": is_carousel_item,
                "children": children,
            }
        )
        return container_id

    async def get_container_status(self, container_id: str) -> str:
        if container_id in self.stuck_in_progress_containers:
            return "IN_PROGRESS"
        return "FINISHED"

    async def publish_container(self, creation_id: str) -> str:
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        media_id = self._next_id("media", "_next_media_id")
        self.published_container_ids.append(creation_id)
        return media_id


class ErrorContainerFakeInstagramClient(FakeInstagramClient):
    """Like `FakeInstagramClient`, but every container it creates
    immediately reports status `ERROR` — used to test the
    container-ended-in-error path distinctly from a timeout."""

    async def get_container_status(self, container_id: str) -> str:
        return "ERROR"
