"""
Plain DTOs shared by `prompt_builder.py`, `validator.py`,
`caption_generator.py`, and `caption_service.py`.

Zero DB/HTTP dependency on purpose, mirroring `app/parsing/schemas.py` —
these are pure data containers, not ORM models, so the pure-logic modules
that use them (`prompt_builder.py`, `validator.py`) stay independently
unit-testable without SQLAlchemy or a running database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class SourceFacts:
    """Everything the AI (and the validator) are allowed to treat as
    ground truth for one product — deliberately narrow.

    `raw_text` is the ONLY field the prompt is built from (requirement:
    "AI يستخدم فقط بيانات المنتج المستخرجة من source_messages.raw_text").
    `price` / `currency` are carried separately, NOT to be handed to the
    AI, purely so the validator can positively confirm the merchant price
    never leaked into a caption even if it happens to also appear as a
    number inside `raw_text` itself.
    """

    raw_text: str
    price: Decimal | None = None
    currency: str | None = None


@dataclass(frozen=True)
class CaptionCandidate:
    """One generated caption, not yet validated."""

    text: str
    generated_by: str


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of checking one caption against its `SourceFacts`.

    `reasons` is always a list (possibly empty) rather than a single
    string so multiple independent problems in one caption are never
    silently collapsed into just the first one found — the persisted
    `rejection_reason` joins all of them.
    """

    is_valid: bool
    reasons: list[str] = field(default_factory=list)

    @staticmethod
    def ok() -> "ValidationResult":
        return ValidationResult(is_valid=True, reasons=[])

    @staticmethod
    def fail(*reasons: str) -> "ValidationResult":
        return ValidationResult(is_valid=False, reasons=list(reasons))
