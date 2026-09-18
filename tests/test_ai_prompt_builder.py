"""
Tests for `app.ai.prompt_builder.build_prompt`.

Pure logic, zero dependency — run with plain `pytest`, no database.
"""

from decimal import Decimal

import pytest

from app.ai.prompt_builder import build_prompt
from app.ai.schemas import SourceFacts


def test_prompt_never_contains_the_price_or_currency():
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD", price=Decimal("30"), currency="KWD")
    system_prompt, user_prompt = build_prompt(facts, n=3)

    # The price/currency are carried on SourceFacts only for the
    # *validator* to check against, per the requirement that the AI is
    # given ONLY raw_text. Confirm neither field is interpolated anywhere,
    # even though raw_text itself happens to mention a price (30 KWD is
    # part of the source text and IS allowed to appear, since the model
    # only ever sees raw_text as a single opaque block — what must never
    # happen is *product.price being interpolated separately*).
    assert "30" in user_prompt  # present because it's inside raw_text verbatim
    assert "KWD" in user_prompt  # ditto
    # But the system prompt (the actual instructions) must not leak them:
    assert "30" not in system_prompt
    assert "KWD" not in system_prompt


def test_user_prompt_contains_raw_text_verbatim():
    facts = SourceFacts(raw_text="Nike Air Max\nمقاس 42 متوفر")
    _, user_prompt = build_prompt(facts, n=2)
    assert "Nike Air Max" in user_prompt
    assert "مقاس 42 متوفر" in user_prompt


def test_system_prompt_states_the_hard_rules():
    facts = SourceFacts(raw_text="Nike Air Max")
    system_prompt, _ = build_prompt(facts, n=1)
    lowered = system_prompt.lower()
    assert "never" in lowered
    assert "price" in lowered
    assert "json" in lowered


def test_requested_candidate_count_is_reflected_in_prompt():
    facts = SourceFacts(raw_text="Nike Air Max")
    system_prompt, user_prompt = build_prompt(facts, n=5)
    assert "5" in system_prompt
    assert "5" in user_prompt


def test_rejects_empty_raw_text():
    facts = SourceFacts(raw_text="   ")
    with pytest.raises(ValueError):
        build_prompt(facts, n=3)


def test_rejects_non_positive_candidate_count():
    facts = SourceFacts(raw_text="Nike Air Max")
    with pytest.raises(ValueError):
        build_prompt(facts, n=0)


def test_prompt_is_deterministic_for_same_input():
    facts = SourceFacts(raw_text="Nike Air Max\n30 KWD")
    first = build_prompt(facts, n=3)
    second = build_prompt(facts, n=3)
    assert first == second
