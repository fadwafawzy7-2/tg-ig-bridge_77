"""
Turns one `SourceFacts` into a list of `CaptionCandidate`s by calling an
`AIClient` and parsing its response.

Depends only on the `AIClient` Protocol (never a concrete HTTP client), so
this is unit-testable with `tests/fakes.FakeAIClient` — no network needed.
"""

from __future__ import annotations

import json
import logging
import re

from app.ai.ai_client import AIClient
from app.ai.prompt_builder import build_prompt
from app.ai.schemas import CaptionCandidate, SourceFacts

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_candidates(raw_response: str) -> list[str]:
    """Parse the model's response into a list of caption strings.

    Expected shape (per `prompt_builder.py`'s instructions) is a bare JSON
    array of strings, but models occasionally wrap it in a markdown code
    fence or add stray whitespace. If no valid JSON array can be found at
    all, the entire response is treated as a single caption candidate
    rather than discarding the generation attempt outright — validation
    downstream will still catch anything wrong with it.
    """
    cleaned = _CODE_FENCE_RE.sub("", raw_response).strip()

    match = _JSON_ARRAY_RE.search(cleaned)
    if match is not None:
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            candidates = [item.strip() for item in parsed if item.strip()]
            if candidates:
                return candidates

    logger.warning(
        "AI response was not a parseable JSON array of strings; falling back to "
        "treating the whole response as a single caption candidate."
    )
    return [cleaned] if cleaned else []


async def generate_captions(
    client: AIClient,
    facts: SourceFacts,
    *,
    n: int,
    max_tokens: int,
    generated_by: str,
) -> list[CaptionCandidate]:
    """Call `client` for `n` caption candidates built from `facts`.

    May return fewer than `n` candidates if the model returns fewer, or
    an empty list if the response could not be parsed into anything
    usable at all. Never raises for a parsing problem — only propagates
    `AIProviderError` from a failed underlying call, which callers
    (`caption_service.py`) are expected to catch and log like any other
    isolated per-product failure.
    """
    system_prompt, user_prompt = build_prompt(facts, n=n)
    raw_response = await client.complete(
        system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=max_tokens
    )
    texts = _parse_candidates(raw_response)
    return [CaptionCandidate(text=text, generated_by=generated_by) for text in texts]
