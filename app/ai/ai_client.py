"""AI provider abstraction and Groq (OpenAI-compatible) implementation."""
from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import get_settings
from app.core.exceptions import AppError, ConfigurationError

logger = logging.getLogger(__name__)


class AIProviderError(AppError):
    error_code = "ai_provider_error"


class AIClient(Protocol):
    async def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str: ...


class GroqClient:
    """Groq Chat Completions client using the OpenAI-compatible API.

    Groq exposes the same request/response shape as OpenAI's Chat
    Completions endpoint (POST {base_url}/chat/completions with a
    `messages` array, returning `choices[0].message.content`), so this
    client works unmodified against Groq — only AI_API_KEY, AI_MODEL and
    AI_API_BASE_URL need to point at Groq (see app/core/config.py).
    """

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout_seconds: float) -> None:
        if not api_key:
            raise ConfigurationError("AI_API_KEY is not configured.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls) -> "GroqClient":
        settings = get_settings()
        return cls(
            api_key=settings.AI_API_KEY,
            model=settings.AI_MODEL,
            base_url=settings.AI_API_BASE_URL,
            timeout_seconds=settings.AI_REQUEST_TIMEOUT_SECONDS,
        )

    async def complete(self, *, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        import httpx

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/chat/completions", headers=headers, json=body)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            logger.error("Groq returned HTTP %s", exc.response.status_code)
            raise AIProviderError(f"Groq provider returned status {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Groq provider request failed: {exc}") from exc

        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Groq provider returned an unexpected response shape") from exc
        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("Groq provider returned an empty completion")
        return text


# Backward-compatible aliases for code/tests written against earlier
# provider naming (Phase 5 was xAI/Grok; before that, Anthropic).
XAIClient = GroqClient
AnthropicAIClient = GroqClient
