"""
Instagram client abstraction — the official Meta Graph API's Content
Publishing flow, never browser automation or scraping.

`InstagramClient` is a `Protocol` `publisher_service.py` codes against,
the same pattern as `app.telegram.client.TelegramClient` /
`app.ai.ai_client.AIClient`: business logic never imports `httpx`
directly, which keeps `publisher_service.py` fully unit-testable with an
in-memory fake (see `tests/fakes.py`).

`GraphAPIInstagramClient` is the real implementation, calling the Graph
API over HTTP. The three-step Content Publishing flow (all officially
documented Meta endpoints):

1. `create_container(...)` -> `POST /{ig-user-id}/media` with
   `image_url` (image posts / carousel items) or `video_url` (Reels /
   video stories), plus `media_type` (`REELS`, `STORIES`, `CAROUSEL`, or
   omitted for a plain image post/carousel item) and `caption` (top-level
   containers only — never on a carousel child) or `children` (carousel
   parent only) -> returns a container id.
2. `get_container_status(...)` -> `GET /{container-id}?fields=status_code`
   -> one of `IN_PROGRESS` / `FINISHED` / `ERROR` / `EXPIRED` /
   `PUBLISHED`. Polled by `publisher_service.py` until it leaves
   `IN_PROGRESS`.
3. `publish_container(...)` -> `POST /{ig-user-id}/media_publish` with
   `creation_id` -> returns the real, permanent Instagram media id.

IMPORTANT: this module could not be exercised against the real Graph API
in the environment it was written in (no network access in this
sandbox). The request/response shapes below follow the publicly
documented Content Publishing API as of this writing, but have not been
run against a live endpoint — verify against a real access token before
relying on this in production, the same caveat already recorded for
Phase 3's `TelethonClient` and Phase 5's `GroqClient`.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import get_settings
from app.core.exceptions import AppError, ConfigurationError

logger = logging.getLogger(__name__)


# --- Error hierarchy: classifies what publisher_service.py may retry ---


class InstagramAPIError(AppError):
    """Base class for all Instagram publishing failures. Treated as
    PERMANENT (not retried) unless a more specific transient subclass
    below is raised instead — an unrecognized error is safer to surface
    loudly than to retry blindly forever."""

    error_code = "instagram_api_error"
    is_transient = False


class InstagramAuthError(InstagramAPIError):
    """Bad/expired access token, missing permission. Never retried —
    retrying with the same bad credential cannot succeed."""

    error_code = "instagram_auth_error"
    is_transient = False


class InstagramMediaError(InstagramAPIError):
    """The media itself was rejected (bad format, unreachable URL,
    unsupported aspect ratio, etc). Never retried — retrying the exact
    same media cannot succeed; the underlying media/caption needs to
    change first."""

    error_code = "instagram_media_error"
    is_transient = False


class InstagramRateLimitError(InstagramAPIError):
    """Graph API rate limit hit. Transient — retried with backoff."""

    error_code = "instagram_rate_limit"
    is_transient = True


class InstagramTimeoutError(InstagramAPIError):
    """A request, or container processing, did not complete in time.
    Transient — retried with backoff."""

    error_code = "instagram_timeout"
    is_transient = True


class InstagramTransientError(InstagramAPIError):
    """Network-level failure (connection reset, DNS, 5xx from Meta).
    Transient — retried with backoff."""

    error_code = "instagram_transient_error"
    is_transient = True


class InstagramClient(Protocol):
    """Structural interface every Instagram client implementation
    follows."""

    async def create_container(
        self,
        *,
        media_url: str,
        media_type: str | None = None,
        caption: str | None = None,
        is_carousel_item: bool = False,
        children: list[str] | None = None,
    ) -> str:
        """Create a media container. `media_type` is one of `None`
        (plain image), `"REELS"`, `"STORIES"`, or `"CAROUSEL"`.
        `media_url` is used as `image_url` for images/carousel-items and
        `video_url` for Reels/video stories — implementations decide
        which based on the underlying content. Returns the container id.
        Raises an `InstagramAPIError` subclass on failure."""
        ...

    async def get_container_status(self, container_id: str) -> str:
        """One of `IN_PROGRESS` / `FINISHED` / `ERROR` / `EXPIRED` /
        `PUBLISHED`."""
        ...

    async def publish_container(self, creation_id: str) -> str:
        """Publish a `FINISHED` container. Returns the real Instagram
        media id."""
        ...


class GraphAPIInstagramClient:
    """Real Instagram client, calling the official Meta Graph API."""

    def __init__(
        self,
        *,
        access_token: str,
        ig_user_id: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        if not access_token:
            raise ConfigurationError(
                "INSTAGRAM_ACCESS_TOKEN is not configured. The real Instagram client "
                "refuses to run without one rather than silently skipping publishing."
            )
        if not ig_user_id:
            raise ConfigurationError(
                "INSTAGRAM_BUSINESS_ACCOUNT_ID is not configured. The real Instagram "
                "client refuses to run without one rather than silently skipping publishing."
            )
        self._access_token = access_token
        self._ig_user_id = ig_user_id
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "GraphAPIInstagramClient":
        settings = get_settings()
        return cls(
            access_token=settings.INSTAGRAM_ACCESS_TOKEN,
            ig_user_id=settings.INSTAGRAM_BUSINESS_ACCOUNT_ID,
            base_url=settings.INSTAGRAM_GRAPH_API_BASE_URL,
            timeout_seconds=settings.INSTAGRAM_REQUEST_TIMEOUT_SECONDS,
        )

    async def create_container(
        self,
        *,
        media_url: str,
        media_type: str | None = None,
        caption: str | None = None,
        is_carousel_item: bool = False,
        children: list[str] | None = None,
    ) -> str:
        params: dict[str, str] = {}
        if media_type == "REELS":
            params["media_type"] = "REELS"
            params["video_url"] = media_url
        elif media_type == "STORIES":
            params["media_type"] = "STORIES"
            lower_url = media_url.lower().split("?", 1)[0]
            if lower_url.endswith((".mp4", ".mov", ".m4v")):
                params["video_url"] = media_url
            else:
                params["image_url"] = media_url
        elif media_type == "CAROUSEL":
            params["media_type"] = "CAROUSEL"
            params["children"] = ",".join(children or [])
        else:
            params["image_url"] = media_url

        if caption is not None:
            params["caption"] = caption
        if is_carousel_item:
            params["is_carousel_item"] = "true"

        data = await self._post(f"/{self._ig_user_id}/media", params)
        container_id = data.get("id")
        if not container_id:
            raise InstagramAPIError(f"Graph API did not return a container id: {data!r}")
        return container_id

    async def get_container_status(self, container_id: str) -> str:
        data = await self._get(f"/{container_id}", {"fields": "status_code"})
        status = data.get("status_code")
        if not status:
            raise InstagramAPIError(f"Graph API did not return a status_code: {data!r}")
        return status

    async def publish_container(self, creation_id: str) -> str:
        data = await self._post(
            f"/{self._ig_user_id}/media_publish", {"creation_id": creation_id}
        )
        media_id = data.get("id")
        if not media_id:
            raise InstagramAPIError(f"Graph API did not return a media id on publish: {data!r}")
        return media_id

    async def _post(self, path: str, params: dict[str, str]) -> dict:
        import httpx

        url = f"{self._base_url}{path}"
        body = {**params, "access_token": self._access_token}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(url, data=body)
                return self._handle_response(response)
        except httpx.TimeoutException as exc:
            raise InstagramTimeoutError(f"Instagram API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise InstagramTransientError(f"Instagram API request failed: {exc}") from exc

    async def _get(self, path: str, params: dict[str, str]) -> dict:
        import httpx

        url = f"{self._base_url}{path}"
        query = {**params, "access_token": self._access_token}
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(url, params=query)
                return self._handle_response(response)
        except httpx.TimeoutException as exc:
            raise InstagramTimeoutError(f"Instagram API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise InstagramTransientError(f"Instagram API request failed: {exc}") from exc

    def _handle_response(self, response) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code < 400:
            return data

        error = data.get("error", {}) if isinstance(data, dict) else {}
        code = error.get("code")
        message = error.get("message", f"Instagram API returned status {response.status_code}")
        is_transient_flag = bool(error.get("is_transient"))

        logger.error("Instagram Graph API error: status=%s body=%r", response.status_code, data)

        if response.status_code == 401 or code == 190:
            raise InstagramAuthError(message)
        if response.status_code == 429 or code in (4, 17, 32, 613) or is_transient_flag:
            raise InstagramRateLimitError(message)
        if response.status_code >= 500:
            raise InstagramTransientError(message)
        if response.status_code == 400 and any(
            kw in message.lower() for kw in ("media", "url", "format", "download", "aspect")
        ):
            raise InstagramMediaError(message)
        raise InstagramAPIError(message)
