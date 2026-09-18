"""
Tests for `app.queue.eligibility`.

Pure logic, zero dependency (like `app.queue.scoring` / `app.ai.validator`)
— run with plain `python`/`pytest`, no database, no sqlalchemy import.
"""

from app.queue.eligibility import (
    PUBLISHABLE_MEDIA_TYPES,
    EligibilityResult,
    evaluate_eligibility,
    is_valid_media_item,
)


# --- is_valid_media_item ------------------------------------------------


def test_photo_with_downloaded_file_is_valid():
    assert is_valid_media_item(media_type="PHOTO", file_path="/mnt/media/x.jpg") is True


def test_video_with_downloaded_file_is_valid():
    assert is_valid_media_item(media_type="VIDEO", file_path="/mnt/media/x.mp4") is True


def test_photo_without_downloaded_file_is_invalid():
    assert is_valid_media_item(media_type="PHOTO", file_path=None) is False


def test_photo_with_empty_string_file_path_is_invalid():
    assert is_valid_media_item(media_type="PHOTO", file_path="") is False


def test_document_type_is_never_valid_even_with_file():
    assert is_valid_media_item(media_type="DOCUMENT", file_path="/mnt/media/x.pdf") is False


def test_animation_type_is_never_valid_even_with_file():
    assert is_valid_media_item(media_type="ANIMATION", file_path="/mnt/media/x.gif") is False


def test_publishable_media_types_is_exactly_photo_and_video():
    assert PUBLISHABLE_MEDIA_TYPES == {"PHOTO", "VIDEO"}


# --- evaluate_eligibility ------------------------------------------------


def test_fully_eligible_product_passes():
    result = evaluate_eligibility(
        has_selected_passed_caption=True,
        has_valid_media=True,
        is_duplicate=False,
        is_rejected=False,
    )
    assert result.is_eligible
    assert result.reasons == ()


def test_missing_caption_fails_with_specific_reason():
    result = evaluate_eligibility(
        has_selected_passed_caption=False,
        has_valid_media=True,
        is_duplicate=False,
        is_rejected=False,
    )
    assert not result.is_eligible
    assert any("caption" in r for r in result.reasons)
    assert len(result.reasons) == 1


def test_missing_media_fails_with_specific_reason():
    result = evaluate_eligibility(
        has_selected_passed_caption=True,
        has_valid_media=False,
        is_duplicate=False,
        is_rejected=False,
    )
    assert not result.is_eligible
    assert any("media" in r for r in result.reasons)


def test_duplicate_product_fails_even_with_caption_and_media():
    result = evaluate_eligibility(
        has_selected_passed_caption=True,
        has_valid_media=True,
        is_duplicate=True,
        is_rejected=False,
    )
    assert not result.is_eligible
    assert any("duplicate" in r for r in result.reasons)


def test_rejected_product_fails_even_with_caption_and_media():
    result = evaluate_eligibility(
        has_selected_passed_caption=True,
        has_valid_media=True,
        is_duplicate=False,
        is_rejected=True,
    )
    assert not result.is_eligible
    assert any("REJECTED" in r for r in result.reasons)


def test_collects_all_failing_reasons_not_just_the_first():
    result = evaluate_eligibility(
        has_selected_passed_caption=False,
        has_valid_media=False,
        is_duplicate=True,
        is_rejected=True,
    )
    assert not result.is_eligible
    assert len(result.reasons) == 4


def test_never_raises_on_any_boolean_combination():
    for caption in (True, False):
        for media in (True, False):
            for dup in (True, False):
                for rej in (True, False):
                    result = evaluate_eligibility(
                        has_selected_passed_caption=caption,
                        has_valid_media=media,
                        is_duplicate=dup,
                        is_rejected=rej,
                    )
                    assert isinstance(result, EligibilityResult)
                    expected_eligible = caption and media and not dup and not rej
                    assert result.is_eligible == expected_eligible


def test_ok_and_fail_constructors():
    ok = EligibilityResult.ok()
    assert ok.is_eligible and ok.reasons == ()

    fail = EligibilityResult.fail("reason one", "reason two")
    assert not fail.is_eligible
    assert fail.reasons == ("reason one", "reason two")
