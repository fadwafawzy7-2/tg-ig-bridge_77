from app.products_codes import generate_product_code
from app.instagram.publisher_service import build_instagram_caption

def test_product_code_shape_and_letters_only_suffix():
    code = generate_product_code()
    assert code.startswith("PRD-")
    assert len(code) == 12
    assert code[4:].isalpha() and code[4:].isupper()

def test_instagram_caption_always_contains_product_code():
    result = build_instagram_caption("منتج ممتاز، متوفر للتفاصيل راسلنا", "PRD-ABCDEFGH")
    assert result.endswith("رمز المنتج: PRD-ABCDEFGH")
    assert "PRD-ABCDEFGH" in result

def test_product_code_does_not_introduce_price_number():
    result = build_instagram_caption("منتج متوفر للتفاصيل راسلنا", "PRD-ABCDEFGH")
    assert "رمز المنتج" in result
    assert not any(ch.isdigit() for ch in result.split("رمز المنتج:", 1)[1])
