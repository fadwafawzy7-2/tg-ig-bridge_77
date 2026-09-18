"""
Tests for `app.ai.caption_generator.generate_captions`.

Uses `tests.fakes.FakeAIClient` — no network, no database.
"""

import pytest

from app.ai.caption_generator import generate_captions
from app.ai.schemas import SourceFacts
from tests.fakes import FailingFakeAIClient, FakeAIClient

pytestmark = pytest.mark.asyncio


async def test_parses_clean_json_array_response():
    client = FakeAIClient(responses=['["Caption one", "Caption two", "Caption three"]'])
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")

    candidates = await generate_captions(
        client, facts, n=3, max_tokens=600, generated_by="test-model"
    )

    assert [c.text for c in candidates] == ["Caption one", "Caption two", "Caption three"]
    assert all(c.generated_by == "test-model" for c in candidates)


async def test_parses_response_wrapped_in_markdown_code_fence():
    client = FakeAIClient(responses=['```json\n["Caption one", "Caption two"]\n```'])
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")

    candidates = await generate_captions(
        client, facts, n=2, max_tokens=600, generated_by="test-model"
    )

    assert [c.text for c in candidates] == ["Caption one", "Caption two"]


async def test_falls_back_to_single_candidate_when_not_valid_json():
    client = FakeAIClient(responses=["Just a plain caption, not JSON at all."])
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")

    candidates = await generate_captions(
        client, facts, n=2, max_tokens=600, generated_by="test-model"
    )

    assert len(candidates) == 1
    assert candidates[0].text == "Just a plain caption, not JSON at all."


async def test_empty_response_yields_no_candidates():
    client = FakeAIClient(responses=["   "])
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")

    candidates = await generate_captions(
        client, facts, n=2, max_tokens=600, generated_by="test-model"
    )

    assert candidates == []


async def test_price_is_never_sent_to_the_client_prompt():
    client = FakeAIClient(responses=['["Caption"]'])
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD", price=None, currency=None)

    await generate_captions(client, facts, n=1, max_tokens=600, generated_by="test-model")

    assert len(client.calls) == 1
    system_prompt, user_prompt = client.calls[0]
    assert "never" in system_prompt.lower()


async def test_provider_failure_propagates_as_ai_provider_error():
    from app.ai.ai_client import AIProviderError

    client = FailingFakeAIClient()
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")

    with pytest.raises(AIProviderError):
        await generate_captions(client, facts, n=2, max_tokens=600, generated_by="test-model")
