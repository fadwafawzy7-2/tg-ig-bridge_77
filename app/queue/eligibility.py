"""
Pure Phase 6 eligibility gate.

Zero I/O, zero third-party dependency — and deliberately zero dependency
on `app.models` too (importing `app.models.enums` alone would still pull
in the whole `app.models` package `__init__.py`, which imports every
SQLAlchemy model). That keeps this module directly and exhaustively
unit-testable with plain `python`/`pytest`, exactly like
`app/ai/validator.py` and `app/parsing/text_parser.py` — no database, no
sqlalchemy import required at all. `eligibility_service.py` is the thin
DB-facing layer that loads a real Product/Caption/Media and calls into
this module with plain booleans.

A product becomes ELIGIBLE only when ALL of:
1. It has a selected caption that passed validation
   (`captions.is_selected=True`, which — by the Phase 5 DB CHECK
   constraint `ck_captions_selected_implies_passed` — already implies
   `validation_status='PASSED'`; there is no way for a selected caption
   to NOT have passed).
2. It has at least one "valid" media item: a publishable media type
   (PHOTO or VIDEO — Instagram posts/reels don't take ANIMATION/DOCUMENT)
   whose file has actually been downloaded (`file_path is not None`).
3. It is not a duplicate of another product, and not REJECTED.

No image-content/quality validation happens here — "valid" means
"structurally usable for publishing" (right type, file present on disk),
not "passes a visual quality check" (no such engine exists in this
project yet; out of scope).
"""

from __future__ import annotations

from dataclasses import dataclass

# Media types Instagram can actually publish as a post/reel. A plain
# string set rather than an import of app.models.enums.MediaType — see
# module docstring for why.
PUBLISHABLE_MEDIA_TYPES = {"PHOTO", "VIDEO"}


@dataclass(frozen=True)
class EligibilityResult:
    is_eligible: bool
    reasons: tuple[str, ...] = ()

    @classmethod
    def ok(cls) -> "EligibilityResult":
        return cls(is_eligible=True, reasons=())

    @classmethod
    def fail(cls, *reasons: str) -> "EligibilityResult":
        return cls(is_eligible=False, reasons=tuple(reasons))


def is_valid_media_item(*, media_type: str, file_path: str | None) -> bool:
    """Whether one media row counts as "valid" for publishing: a
    publishable type AND actually downloaded to disk. `media_type` is
    typed loosely as `str` on purpose — a `MediaType` enum member (itself
    a `str` subclass) compares equal to its value, so callers can pass
    either without this module importing the enum class."""
    return media_type in PUBLISHABLE_MEDIA_TYPES and bool(file_path)


def evaluate_eligibility(
    *,
    has_selected_passed_caption: bool,
    has_valid_media: bool,
    is_duplicate: bool,
    is_rejected: bool,
) -> EligibilityResult:
    """The pure eligibility gate. Never raises; collects every failing
    reason (not just the first) so a rejection is always specific and
    actionable, matching `app.ai.validator`'s convention."""
    reasons: list[str] = []

    if not has_selected_passed_caption:
        reasons.append(
            "no selected caption with validation_status=PASSED (Phase 5 gate not satisfied)"
        )
    if not has_valid_media:
        reasons.append(
            "no valid media linked to the product (needs a downloaded PHOTO or VIDEO)"
        )
    if is_duplicate:
        reasons.append("product is marked as a duplicate of another product")
    if is_rejected:
        reasons.append("product status is REJECTED")

    if reasons:
        return EligibilityResult.fail(*reasons)
    return EligibilityResult.ok()
