"""
Builds the strict system/user prompt sent to the AI model.

Pure string assembly, zero I/O and zero third-party dependency — exactly
like `app/parsing/text_parser.py` — so it is directly and exhaustively
unit-testable with plain `python`/`pytest`, no database or network
required.

CRITICAL: `SourceFacts.price` / `.currency` are NEVER interpolated into
the prompt text. Only `raw_text` is. This is what makes the "AI uses only
source_messages.raw_text" requirement structurally true rather than just
a request we hope the model honors — the price simply isn't in the input
the model receives. (The validator then double-checks the *output* for
any price/claim leakage anyway — see `validator.py` — because a prompt
instruction alone is not a guarantee.)
"""

from __future__ import annotations

from app.ai.schemas import SourceFacts

_SYSTEM_PROMPT = """You write Instagram captions for a resale/dropshipping page that reposts product listings originally posted in Telegram channels.

You will be given exactly one block of SOURCE TEXT taken verbatim from the original Telegram post. That SOURCE TEXT is the ONLY information you know about this product. You have no other knowledge of it.

STRICT RULES — follow every one of these, with no exceptions:
1. Do not invent or assume ANY detail that is not explicitly present in the SOURCE TEXT. This includes (but is not limited to): sizes, colors, materials, features, quality level, shipping/delivery terms, warranty/guarantee, or discounts/offers. If the source text does not mention a detail, your caption must not mention it either.
2. Never state, hint at, or restate any price, cost, or currency amount, even if a price appears in the SOURCE TEXT. Prices are handled elsewhere and must never appear in the caption.
3. Your only job is to rephrase and reorganize information that is already present in the SOURCE TEXT, for an Instagram audience, plus exactly one short, generic, non-claim call to action (for example: inviting people to check the link in bio or to message the page for details). The call to action must not promise anything, create urgency, or imply a discount, limited stock, or special deal unless the SOURCE TEXT itself says so.
4. Do not add superlatives, urgency, or scarcity language ("best", "limited time", "hurry", "only a few left", etc.) unless that exact claim is already present in the SOURCE TEXT.
5. Write in the same language as the SOURCE TEXT (Arabic source -> Arabic caption, English source -> English caption). Keep it natural for Instagram: short lines, tasteful use of emoji is fine, no markdown.
6. Output ONLY a JSON array of {n} different caption strings, nothing else — no explanation, no preamble, no markdown code fences. Example shape: ["caption one", "caption two"].
""".strip()

_USER_PROMPT_TEMPLATE = """SOURCE TEXT:
---
{raw_text}
---

Write {n} different Instagram caption candidates following every rule above. Respond with only the JSON array."""


def build_prompt(facts: SourceFacts, *, n: int) -> tuple[str, str]:
    """Return `(system_prompt, user_prompt)` for `facts`, asking for `n` candidates.

    `n` must be >= 1. `facts.raw_text` must be non-empty — callers are
    expected to have already filtered out products with no source text
    (the same guarantee `text_parser.py` / `product_service.py` already
    provide upstream: a product only reaches PARSED status when its
    source message had non-empty `raw_text`).
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if not facts.raw_text or not facts.raw_text.strip():
        raise ValueError("SourceFacts.raw_text must be non-empty")

    system_prompt = _SYSTEM_PROMPT.format(n=n)
    user_prompt = _USER_PROMPT_TEMPLATE.format(raw_text=facts.raw_text.strip(), n=n)
    return system_prompt, user_prompt
