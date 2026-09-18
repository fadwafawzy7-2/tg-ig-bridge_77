"""
Claim keyword lexicon used by `validator.py`.

Each category maps to a list of keyword substrings (Arabic and English)
that indicate a caption is making a claim of that kind. The validator's
rule is word-for-word, not category-level: if a caption contains one of
these keywords, that EXACT keyword must also appear in the source text —
having a *different* keyword from the same category in the source is not
enough (e.g. the source saying "قطن" does not license the caption saying
"جلد": both are "material" keywords, but only the specific one actually
stated is supported). This closes a cross-contamination gap a looser
category-level check would have, at the cost of being stricter — some
genuine paraphrases (a synonym not in these lists) will be rejected even
though the source arguably supports them. That tradeoff is intentional:
the requirement is "never invent," and a false rejection (caught,
loggable, retryable) is far cheaper than a false acceptance (an
unsupported claim published to real customers).

The "quality" category is deliberately broad — it covers not just overt
marketing words ("premium", "luxury") but everyday descriptive/product
claims ("comfortable", "practical", "durable", "مريح", "عملي", "متين",
"مناسب للاستخدام اليومي") that are easy for a model to add as harmless-
sounding filler even though the source never said them. Any adjective not
in this list can still slip through undetected (this is keyword matching,
not language understanding) — the list is expanded whenever a gap is
found, not treated as exhaustive.

Keep these lists lowercase for the English entries — the validator
lowercases caption/source text before matching English keywords. Arabic
has no case distinction, so Arabic entries are matched as-is.
"""

from __future__ import annotations

CLAIM_CATEGORIES: dict[str, list[str]] = {
    "size": [
        # English
        "size",
        "sizes",
        "small",
        "medium",
        "large",
        "x-large",
        "xl",
        "xxl",
        "one size",
        # Arabic
        "مقاس",
        "مقاسات",
        "صغير",
        "وسط",
        "كبير",
        "مقاس واحد",
    ],
    "color": [
        # English
        "color",
        "colour",
        "colors",
        "colours",
        "black",
        "white",
        "red",
        "blue",
        "green",
        "yellow",
        "pink",
        "beige",
        "grey",
        "gray",
        "gold",
        "silver",
        # Arabic
        "لون",
        "ألوان",
        "الوان",
        "أسود",
        "اسود",
        "أبيض",
        "ابيض",
        "أحمر",
        "احمر",
        "أزرق",
        "ازرق",
        "أخضر",
        "اخضر",
        "أصفر",
        "اصفر",
        "وردي",
        "بيج",
        "رمادي",
        "ذهبي",
        "فضي",
    ],
    "material": [
        # English
        "cotton",
        "leather",
        "silk",
        "wool",
        "polyester",
        "steel",
        "stainless",
        "plastic",
        "wood",
        "wooden",
        "genuine leather",
        "material",
        # Arabic
        "قطن",
        "جلد",
        "جلد طبيعي",
        "حرير",
        "صوف",
        "بوليستر",
        "ستانلس",
        "استانلس",
        "خشب",
        "خامة",
        "الخامة",
    ],
    "quality": [
        # English
        "premium",
        "high quality",
        "best quality",
        "top quality",
        "luxury",
        "original",
        "authentic",
        "durable",
        "high-end",
        "comfortable",
        "comfy",
        "sturdy",
        "distinctive",
        "unique",
        "practical",
        "functional",
        "versatile",
        "multi-purpose",
        "multipurpose",
        "stylish",
        "elegant",
        "modern",
        "trendy",
        "chic",
        "classy",
        "lightweight",
        "easy to use",
        "user-friendly",
        "ideal",
        "perfect",
        "great",
        "amazing",
        "excellent",
        "superior",
        "top-notch",
        "reliable",
        "dependable",
        "affordable",
        "value for money",
        "must-have",
        "exclusive",
        "special",
        "sleek",
        "ergonomic",
        "suitable for daily use",
        "everyday use",
        "for daily use",
        # Arabic
        "جودة عالية",
        "أفضل جودة",
        "افضل جودة",
        "فاخر",
        "فاخرة",
        "أصلي",
        "اصلي",
        "ممتاز",
        "ممتازة",
        "متين",
        "متينة",
        "خامة ممتازة",
        "مريح",
        "مريحة",
        "مميز",
        "مميزة",
        "مناسب للاستخدام اليومي",
        "مناسبة للاستخدام اليومي",
        "عملي",
        "عملية",
        "متعدد الاستخدامات",
        "متعددة الاستخدامات",
        "أنيق",
        "أنيقة",
        "عصري",
        "عصرية",
        "جذاب",
        "جذابة",
        "خفيف الوزن",
        "خفيفة الوزن",
        "سهل الاستخدام",
        "سهلة الاستخدام",
        "مثالي",
        "مثالية",
        "رائع",
        "رائعة",
        "فريد",
        "فريدة",
        "اقتصادي",
        "اقتصادية",
        "موثوق",
        "موثوقة",
        "احترافي",
        "احترافية",
        "يدوم طويلاً",
        "تصميم أنيق",
    ],
    "shipping": [
        # English
        "shipping",
        "delivery",
        "free shipping",
        "free delivery",
        "fast delivery",
        "worldwide shipping",
        "ships",
        # Arabic
        "شحن",
        "شحن مجاني",
        "توصيل",
        "توصيل مجاني",
        "توصيل سريع",
        "التوصيل",
    ],
    "warranty": [
        # English
        "warranty",
        "guarantee",
        "guaranteed",
        "money back",
        "return policy",
        # Arabic
        "ضمان",
        "كفالة",
        "استرجاع",
        "ضمان الاسترجاع",
    ],
    "discount": [
        # English
        "discount",
        "sale",
        "off",
        "% off",
        "special offer",
        "limited offer",
        "deal",
        # Arabic
        "خصم",
        "تخفيض",
        "تخفيضات",
        "عرض خاص",
        "عرض محدود",
        "أوفر",
    ],
}
