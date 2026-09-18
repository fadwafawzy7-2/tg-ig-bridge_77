"""
Phase 5 — AI Caption Generation + Content Validation.

Generates Instagram caption candidates for a `products` row and enforces
a hard fact-check gate before any caption may be marked publish-ready.

Hard requirements this package exists to satisfy (do not weaken these
without an explicit product decision):

1.  The AI is given ONLY `source_messages.raw_text` for the product being
    captioned — never any other product's text, never image content
    (Phase 3/4 never extract anything from images either), never
    merchant/internal fields beyond what's in that raw text.
2.  Nothing may be invented: sizes, colors, materials, features, quality
    claims, shipping, warranty, or discounts that are not present in the
    source text are forbidden, regardless of how plausible they sound.
3.  The merchant price must NEVER appear in an Instagram caption, even
    though the source text may contain it and even though `products.price`
    is known internally.
4.  The AI's role is limited to rephrasing/reordering information that is
    already present in the source text, plus one generic, non-claim CTA
    (e.g. "check the link in bio" / "message us for details") — it must
    not add urgency claims, scarcity claims, or superlatives that aren't
    in the source.
5.  Every candidate caption is persisted as a versioned row (never
    silently discarded) so generation attempts are auditable.
6.  A caption may only be selected for publish (`is_selected=True`) after
    it has been validated against the source text and found to introduce
    no unsupported claim and no price. Validation failures are recorded
    with a clear, specific reason — never a generic "rejected".

Module map:

| Module               | Responsibility                                            |
|-----------------------|-----------------------------------------------------------|
| `schemas.py`          | Plain DTOs — zero DB/HTTP dependency.                     |
| `claim_lexicon.py`    | Arabic/English keyword lists used by the validator.        |
| `prompt_builder.py`   | Builds the strict system/user prompt from raw_text only.   |
| `validator.py`        | Deterministic (non-AI) fact-check of a caption vs. source.|
| `ai_client.py`        | `AIClient` protocol + real Groq (OpenAI-compatible) Chat Completions API client.  |
| `caption_generator.py`| Prompt -> AI client -> parsed candidate caption strings.   |
| `caption_service.py`  | DB orchestration: generate, validate, persist, select.     |
| `caption_runner.py`   | Batch driver over PARSED products; CLI entrypoint.         |

**Explicitly out of scope for this phase** (Phase 6 territory, not
started here): scheduling captions for publish, Instagram API calls,
image/media generation or selection, and any periodic-execution
scheduler. `generate_pending_captions()` is what a scheduler would call
later, exactly like `parser_runner.process_pending_messages()` before it.

The validator is deliberately NOT itself an AI call — a second model
asked to "check the first model's work" is unpredictable and expensive,
and it is not something this test suite could reliably exercise. Instead
it is plain, deterministic Python (regex/substring logic) that can be
unit-tested exhaustively and reasoned about with certainty, exactly like
`app/parsing/text_parser.py`.
"""
